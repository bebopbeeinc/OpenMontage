"""Shared Google Drive client for OpenMontage.

A thin, thread-safe wrapper over googleapiclient's Drive v3 Resource.
Lives under tools/publishers/ as a freestanding helper module — not a
BaseTool subclass, but co-located so a BaseTool wrapper (e.g.
`DrivePublisher`) can land in this same file when a registry-discoverable
publish entry point becomes useful (mirrors tools/audio/piper_tts.py,
which puts the PiperTTS BaseTool next to freestanding helpers like
fetch_voice / voice_files_present).

Reusable across pipelines: pull it in wherever you need to list folders,
upload files, move parents, trash files, or download bytes. Several
existing scripts (scripts/trivia/publish.py, scripts/trivia/post_row.py,
scripts/trivia_images/web/server.py) build their own Drive/Sheets clients
inline — those can migrate to this in a follow-up.

What it handles:

  - Shared Drive semantics (`supportsAllDrives=True` everywhere; pagination
    on list).
  - The googleapiclient SSL thread-safety footgun: the underlying
    httplib2.Http is NOT thread-safe, and concurrent calls from multiple
    threads on the same Resource crash inside OpenSSL with a SIGSEGV in
    `BIO_copy_next_retry`. We hand each thread its own Drive Resource via
    `threading.local()` (see `_drive()`), so read paths (list/download)
    run in parallel. `_api_lock` is kept on the mutation paths
    (upload/move/trash) to preserve check-then-act atomicity — two
    threads can't race on "find existing file → update or create".
  - In-process caching:
      - Folder listings (8s TTL by default; tunable per call).
      - Downloaded bytes keyed on (file_id, modified_time) so a re-uploaded
        file invalidates naturally.

What it does NOT handle (intentionally):

  - Pipeline-specific naming conventions (e.g. trivia's `{N}{Q|A}.png`).
    Build those next to the pipeline code that owns them and pass concrete
    names + folder IDs in.
  - Sheets. Use googleapiclient.discovery.build("sheets", "v4", ...)
    directly for sheet ops, or extend this module if a shared sheets
    helper becomes useful.
  - Long-lived auth refresh. The service account credentials are read
    once at first use; recreate the client if the underlying SA file
    changes.

Auth defaults to a service-account JSON at ~/.google/claude-sheets-sa.json,
override with the `OPENMONTAGE_SA_PATH` env var or pass `sa_path=` to the
constructor.
"""

from __future__ import annotations

import os
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Iterable, Optional

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# Transient transport failures worth retrying: Google's load balancer drops the
# idle persistent TLS connection httplib2 reuses, so the next call surfaces
# EPIPE / ECONNRESET / an SSL error before any HTTP response. Re-issuing the
# request makes httplib2 reopen the socket. (HTTP 5xx / 429 are handled
# separately by googleapiclient's built-in `num_retries` backoff.)
_TRANSIENT_TRANSPORT = (
    BrokenPipeError, ConnectionResetError, ConnectionAbortedError,
    TimeoutError, ssl.SSLError, socket.timeout,
)
_TRANSIENT_ERRNOS = {32, 104}   # EPIPE, ECONNRESET

DEFAULT_SA_PATH = Path.home() / ".google" / "claude-sheets-sa.json"
DEFAULT_SCOPES: tuple[str, ...] = ("https://www.googleapis.com/auth/drive",)

# Cache lifetimes (seconds). Tweak via `list_folder(..., ttl=N)` if needed.
DEFAULT_LIST_TTL_S = 8.0
DEFAULT_BYTES_TTL_S = 600.0


@dataclass(frozen=True)
class FileMeta:
    """Snapshot of a Drive file's identity + structural metadata.

    Whatever caller needs to interpret state (e.g. "is this in the approved
    folder?") should do it with `parent_ids` — this dataclass is intentionally
    pipeline-agnostic.

    `thumbnail_link` is Drive's auto-generated thumbnail URL (lh3.googleusercontent.com).
    Empty for non-image files or when the field wasn't requested. The URL is
    signed and embeds an access token, so a plain HTTPS GET fetches it — no
    Drive bearer header needed. Trivia-images fetches this server-side via
    `_fetch_drive_thumbnail` and proxies the small (~30 KB) result through
    `/api/image?thumb=1`; handing the CDN URL directly to a browser `<img>`
    tag works for owner-shared files but Drive soft-throttles anon hits
    against SA-owned files in Shared Drives.
    """
    id: str
    name: str
    mime_type: str
    modified_time: str
    parent_ids: tuple[str, ...]
    thumbnail_link: str = ""


def _sa_path() -> Path:
    return Path(os.environ.get("OPENMONTAGE_SA_PATH", str(DEFAULT_SA_PATH)))


class DriveClient:
    """Thread-safe wrapper around the Drive v3 API.

    The underlying httplib2.Http is not thread-safe (OpenSSL segfaults
    on concurrent reads of a shared instance), so each worker thread
    gets its own Drive Resource via `_drive()`. Reads (list/download)
    run in parallel; mutations (upload/move/trash) still serialize on
    `_api_lock` to keep check-then-act sequences atomic. See module
    docstring for the full rationale.
    """

    def __init__(
        self,
        *,
        sa_path: Optional[Path] = None,
        scopes: Iterable[str] = DEFAULT_SCOPES,
        list_ttl_s: float = DEFAULT_LIST_TTL_S,
        bytes_ttl_s: float = DEFAULT_BYTES_TTL_S,
    ) -> None:
        sa = Path(sa_path) if sa_path else _sa_path()
        if not sa.is_file():
            raise FileNotFoundError(f"service account file not found: {sa}")
        self._creds = service_account.Credentials.from_service_account_file(
            str(sa), scopes=list(scopes),
        )
        # Per-thread Drive client. The googleapiclient Resource wraps an
        # httplib2.Http that segfaults libssl under concurrent use, so
        # we used to serialize every call behind _api_lock — which made
        # /api/image painfully sequential when many thumbnails loaded.
        # A thread-local client gives each worker thread its own Http
        # and lets downloads parallelize for real.
        self._local = threading.local()
        self._list_ttl_s = list_ttl_s
        self._bytes_ttl_s = bytes_ttl_s
        # Cache mutex — microsecond holds.
        self._lock = Lock()
        # API mutex — held only by mutation paths now (upload/move/trash)
        # to preserve check-then-act atomicity across threads (e.g.
        # upload_or_replace's "list folder → update or create" sequence).
        # Read paths no longer take it; their thread-local clients can
        # safely call Drive in parallel.
        self._api_lock = Lock()
        self._list_cache: dict[str, tuple[float, dict[str, FileMeta]]] = {}
        self._bytes_cache: dict[tuple[str, str], tuple[float, bytes, str]] = {}

    def _drive(self):
        d = getattr(self._local, "client", None)
        if d is not None:
            return d
        d = build("drive", "v3", credentials=self._creds, cache_discovery=False)
        self._local.client = d
        return d

    def _execute(self, request, *, num_retries: int = 4):
        """Execute a Drive API request with transient-failure retries.

        `num_retries` is forwarded to googleapiclient's `.execute()`, which
        retries HTTP 5xx and 429 with randomized exponential backoff. On top of
        that we catch dropped-connection errors (EPIPE/ECONNRESET/SSL) — which
        raise before any HTTP status and so aren't covered by `num_retries` —
        and re-issue the request, letting httplib2 reopen the socket. Without
        this, a Drive blip surfaces as an HTTP 500 (or an empty listing → a
        spurious 404) to the user.
        """
        last: BaseException | None = None
        for attempt in range(4):
            try:
                return request.execute(num_retries=num_retries)
            except _TRANSIENT_TRANSPORT as e:
                last = e
            except OSError as e:
                if e.errno not in _TRANSIENT_ERRNOS:
                    raise
                last = e
            if attempt < 3:
                time.sleep(0.3 * (2 ** attempt))
        assert last is not None
        raise last

    # ----- low-level helpers -----

    def _meta_from_api(self, f: dict) -> FileMeta:
        return FileMeta(
            id=f["id"],
            name=f["name"],
            mime_type=f.get("mimeType", ""),
            modified_time=f.get("modifiedTime", ""),
            parent_ids=tuple(f.get("parents") or ()),
            thumbnail_link=f.get("thumbnailLink", ""),
        )

    # ----- list -----

    def list_folder(
        self,
        folder_id: str,
        *,
        force: bool = False,
        ttl_s: Optional[float] = None,
    ) -> dict[str, FileMeta]:
        """Return {name: FileMeta} for files directly under `folder_id`.

        Listings are cached for `ttl_s` (default 8s). Pass `force=True` to
        bypass the cache (e.g. immediately after a known-mutating operation
        the caller knows hasn't propagated yet).
        """
        ttl = self._list_ttl_s if ttl_s is None else ttl_s
        now = time.time()
        with self._lock:
            if not force:
                cached = self._list_cache.get(folder_id)
                if cached and now - cached[0] < ttl:
                    return cached[1]

        files: dict[str, FileMeta] = {}
        page_token: Optional[str] = None
        drive = self._drive()
        while True:
            resp = self._execute(drive.files().list(
                q=f"'{folder_id}' in parents and trashed=false",
                fields="nextPageToken,files(id,name,mimeType,modifiedTime,parents,thumbnailLink)",
                pageSize=200,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
                pageToken=page_token,
            ))
            for f in resp.get("files", []):
                files[f["name"]] = self._meta_from_api(f)
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

        with self._lock:
            self._list_cache[folder_id] = (now, files)
        return files

    def find_in_folder(self, folder_id: str, name: str) -> Optional[FileMeta]:
        """Convenience: return FileMeta for `name` under `folder_id`, or None."""
        return self.list_folder(folder_id).get(name)

    def invalidate_listing(self, folder_id: str) -> None:
        """Drop a cached listing — useful after callers chain operations that
        depend on a fresh state."""
        with self._lock:
            self._list_cache.pop(folder_id, None)

    def find_or_create_folder(self, parent_id: str, name: str) -> FileMeta:
        """Return the subfolder `name` directly under `parent_id`, creating it
        if absent.

        Folder-typed lookup (vs. `find_in_folder`, which matches any file by
        name). Serialized under `_api_lock` so two callers racing to create the
        same subfolder can't end up with duplicates.
        """
        safe = name.replace("'", r"\'")
        with self._api_lock:
            drive = self._drive()
            resp = self._execute(drive.files().list(
                q=(f"'{parent_id}' in parents and trashed=false "
                   f"and mimeType='application/vnd.google-apps.folder' "
                   f"and name='{safe}'"),
                fields="files(id,name,mimeType,modifiedTime,parents)",
                pageSize=10,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            ))
            files = resp.get("files", [])
            if files:
                return self._meta_from_api(files[0])
            f = self._execute(drive.files().create(
                body={
                    "name": name,
                    "parents": [parent_id],
                    "mimeType": "application/vnd.google-apps.folder",
                },
                fields="id,name,mimeType,modifiedTime,parents",
                supportsAllDrives=True,
            ))
            self.invalidate_listing(parent_id)
            return self._meta_from_api(f)

    # ----- upload -----

    def upload_or_replace(
        self,
        folder_id: str,
        dest_name: str,
        local_path: Path,
        *,
        mime_type: str = "image/png",
    ) -> FileMeta:
        """Upload `local_path` as `dest_name` under `folder_id`.

        If a file by that name already exists in the folder, its content is
        replaced via `files.update` so the file_id stays stable. Otherwise a
        new file is created. The folder's cached listing is invalidated.
        """
        if not local_path.is_file():
            raise FileNotFoundError(f"local file not found: {local_path}")

        listing = self.list_folder(folder_id, force=True)
        existing = listing.get(dest_name)

        media = MediaFileUpload(str(local_path), mimetype=mime_type, resumable=False)
        # _api_lock still serializes mutations so two upload_or_replace
        # calls for the same dest_name can't race on "find existing vs
        # create new" — even with thread-local httplib2, that race would
        # produce duplicate files in the folder.
        with self._api_lock:
            drive = self._drive()
            if existing:
                f = self._execute(drive.files().update(
                    fileId=existing.id,
                    media_body=media,
                    fields="id,name,mimeType,modifiedTime,parents",
                    supportsAllDrives=True,
                ))
            else:
                f = self._execute(drive.files().create(
                    body={"name": dest_name, "parents": [folder_id]},
                    media_body=media,
                    fields="id,name,mimeType,modifiedTime,parents",
                    supportsAllDrives=True,
                ))

        meta = self._meta_from_api(f)
        self.invalidate_listing(folder_id)
        return meta

    # ----- parent moves -----

    def move(
        self,
        file_id: str,
        *,
        add_parents: Iterable[str] = (),
        remove_parents: Iterable[str] = (),
    ) -> FileMeta:
        """Adjust a file's parents.

        Idempotent: parents already present aren't re-added and missing-
        parents-to-remove are skipped, so callers can pass full intent
        without checking first. Returns the file's new metadata. Any
        folder listings touched are invalidated.
        """
        add_set = set(add_parents)
        remove_set = set(remove_parents)
        drive = self._drive()
        # The "get current parents → compute delta → update" sequence
        # stays under _api_lock to keep it atomic against concurrent
        # moves of the same file. Two threads racing here without the
        # lock would each see the pre-move parents and double-apply.
        with self._api_lock:
            cur = self._execute(drive.files().get(
                fileId=file_id,
                fields="id,name,mimeType,modifiedTime,parents",
                supportsAllDrives=True,
            ))
            cur_parents = set(cur.get("parents") or ())
            adds = [p for p in add_set if p not in cur_parents]
            removes = [p for p in remove_set if p in cur_parents]

            if not adds and not removes:
                return self._meta_from_api(cur)

            kwargs: dict = {
                "fileId": file_id,
                "fields": "id,name,mimeType,modifiedTime,parents",
                "supportsAllDrives": True,
            }
            if adds: kwargs["addParents"] = ",".join(adds)
            if removes: kwargs["removeParents"] = ",".join(removes)
            f = self._execute(drive.files().update(**kwargs))

        for fid in adds + removes:
            self.invalidate_listing(fid)
        return self._meta_from_api(f)

    # ----- trash (recoverable delete) -----

    def trash(self, file_id: str) -> FileMeta:
        """Move a file to Drive's trash via `files.update(trashed=True)`.

        Uses the `canTrash` capability, not `canDelete` — handy when the
        service account is Content Manager on a Shared Drive (canDelete is
        usually withheld but canTrash is granted). Trashed files are
        recoverable for ~30 days from the Shared Drive's trash UI.
        """
        with self._api_lock:
            f = self._execute(self._drive().files().update(
                fileId=file_id,
                body={"trashed": True},
                fields="id,name,mimeType,modifiedTime,parents,trashed",
                supportsAllDrives=True,
            ))
        # We don't know which folders this lived under without a metadata
        # round-trip, so blow the whole listing cache. Cheap (rebuilt on next
        # access).
        with self._lock:
            self._list_cache.clear()
        return self._meta_from_api(f)

    # ----- sharing -----

    def ensure_anyone_reader(self, file_id: str) -> None:
        """Idempotently grant `anyone-with-link: reader` on a file.

        Lets a browser fetch the file straight from Google's CDN
        (`drive.google.com/thumbnail?id=…`, `…/uc?id=…`) without an auth header
        and without the soft-throttling Drive applies to anonymous hits on
        *non-public* service-account files in a Shared Drive. Checks for an
        existing `anyone` permission first so re-calls are a single list and no
        write.
        """
        with self._api_lock:
            drive = self._drive()
            perms = self._execute(drive.permissions().list(
                fileId=file_id,
                fields="permissions(id,type,role)",
                supportsAllDrives=True,
            )).get("permissions", [])
            if any(p.get("type") == "anyone" for p in perms):
                return
            self._execute(drive.permissions().create(
                fileId=file_id,
                body={"type": "anyone", "role": "reader"},
                fields="id",
                supportsAllDrives=True,
            ))

    # ----- download -----

    def download_bytes(
        self, file_id: str, modified_time: str = "", *, mime_hint: Optional[str] = None,
    ) -> tuple[bytes, str, str]:
        """Fetch the file content.

        Cached by (file_id, modified_time) so a re-uploaded file (which
        updates modified_time) invalidates naturally. Returns (bytes,
        mime_type, modified_time).

        Pass `mime_hint` when the caller already knows the content type (e.g.
        from the listing it just read) to skip a `files.get` metadata round-trip
        — that halves the Drive calls per uncached fetch. Without it we look the
        mime up first.

        Read path: no `_api_lock`. Each calling thread has its own
        httplib2.Http via `_drive()`, so parallel downloads stream
        concurrently — what previously took 17s per 5 MB thumbnail
        because everything was serialized now overlaps across threads.

        `.execute()` on the media request pulls the whole file in a
        single streamed response. The earlier `MediaIoBaseDownload`
        chunked loop opened a fresh HTTPS GET per 1 MB chunk — five
        TLS handshakes for a 5 MB file — which we don't need.
        """
        key = (file_id, modified_time)
        now = time.time()
        with self._lock:
            cached = self._bytes_cache.get(key)
            if cached and now - cached[0] < self._bytes_ttl_s:
                return cached[1], cached[2], modified_time

        drive = self._drive()
        if mime_hint:
            mime, mtime = mime_hint, modified_time
        else:
            meta = self._execute(drive.files().get(
                fileId=file_id,
                fields="mimeType,modifiedTime",
                supportsAllDrives=True,
            ))
            mime = meta.get("mimeType", "application/octet-stream")
            mtime = meta.get("modifiedTime", "")

        data = self._execute(drive.files().get_media(
            fileId=file_id, supportsAllDrives=True,
        ))

        with self._lock:
            self._bytes_cache[key] = (now, data, mime)
        return data, mime, mtime


# Module-level singleton — lazy so importing this file is cheap and doesn't
# touch the filesystem or network until something actually uses it.
_singleton: Optional[DriveClient] = None
_singleton_lock = Lock()


def get_client() -> DriveClient:
    """Get the process-wide DriveClient singleton (built on first call)."""
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = DriveClient()
        return _singleton
