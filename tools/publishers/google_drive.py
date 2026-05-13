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
    `BIO_copy_next_retry`. We serialize all API calls behind `_api_lock`
    and keep cache mutations on the much-faster `_lock`.
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

import io
import os
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Iterable, Optional

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

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
    """
    id: str
    name: str
    mime_type: str
    modified_time: str
    parent_ids: tuple[str, ...]


def _sa_path() -> Path:
    return Path(os.environ.get("OPENMONTAGE_SA_PATH", str(DEFAULT_SA_PATH)))


class DriveClient:
    """Thread-safe wrapper around the Drive v3 API.

    See module docstring for the thread-safety rationale (OpenSSL segfaults
    on concurrent reads of a shared httplib2.Http).
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
        creds = service_account.Credentials.from_service_account_file(
            str(sa), scopes=list(scopes),
        )
        self._drive = build("drive", "v3", credentials=creds, cache_discovery=False)
        self._list_ttl_s = list_ttl_s
        self._bytes_ttl_s = bytes_ttl_s
        # Cache mutex — microsecond holds.
        self._lock = Lock()
        # API mutex — every googleapiclient call must run under this. The
        # underlying httplib2.Http is shared across calls and concurrent use
        # from multiple threads segfaults libssl. See module docstring.
        self._api_lock = Lock()
        self._list_cache: dict[str, tuple[float, dict[str, FileMeta]]] = {}
        self._bytes_cache: dict[tuple[str, str], tuple[float, bytes, str]] = {}

    # ----- low-level helpers -----

    def _meta_from_api(self, f: dict) -> FileMeta:
        return FileMeta(
            id=f["id"],
            name=f["name"],
            mime_type=f.get("mimeType", ""),
            modified_time=f.get("modifiedTime", ""),
            parent_ids=tuple(f.get("parents") or ()),
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
        with self._api_lock:
            while True:
                resp = self._drive.files().list(
                    q=f"'{folder_id}' in parents and trashed=false",
                    fields="nextPageToken,files(id,name,mimeType,modifiedTime,parents)",
                    pageSize=200,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                    pageToken=page_token,
                ).execute()
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
        with self._api_lock:
            if existing:
                f = self._drive.files().update(
                    fileId=existing.id,
                    media_body=media,
                    fields="id,name,mimeType,modifiedTime,parents",
                    supportsAllDrives=True,
                ).execute()
            else:
                f = self._drive.files().create(
                    body={"name": dest_name, "parents": [folder_id]},
                    media_body=media,
                    fields="id,name,mimeType,modifiedTime,parents",
                    supportsAllDrives=True,
                ).execute()

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
        with self._api_lock:
            cur = self._drive.files().get(
                fileId=file_id,
                fields="id,name,mimeType,modifiedTime,parents",
                supportsAllDrives=True,
            ).execute()
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
        with self._api_lock:
            f = self._drive.files().update(**kwargs).execute()

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
            f = self._drive.files().update(
                fileId=file_id,
                body={"trashed": True},
                fields="id,name,mimeType,modifiedTime,parents,trashed",
                supportsAllDrives=True,
            ).execute()
        # We don't know which folders this lived under without a metadata
        # round-trip, so blow the whole listing cache. Cheap (rebuilt on next
        # access).
        with self._lock:
            self._list_cache.clear()
        return self._meta_from_api(f)

    # ----- download -----

    def download_bytes(self, file_id: str, modified_time: str = "") -> tuple[bytes, str, str]:
        """Fetch the file content.

        Cached by (file_id, modified_time) so a re-uploaded file (which
        updates modified_time) invalidates naturally. Returns (bytes,
        mime_type, modified_time).

        Holds `_api_lock` for the entire metadata + download flow because
        `MediaIoBaseDownload.next_chunk()` issues an HTTPS GET per chunk
        and we must keep the shared httplib2.Http single-threaded.
        """
        key = (file_id, modified_time)
        now = time.time()
        with self._lock:
            cached = self._bytes_cache.get(key)
            if cached and now - cached[0] < self._bytes_ttl_s:
                return cached[1], cached[2], modified_time

        with self._api_lock:
            meta = self._drive.files().get(
                fileId=file_id,
                fields="mimeType,modifiedTime",
                supportsAllDrives=True,
            ).execute()
            mime = meta.get("mimeType", "application/octet-stream")
            mtime = meta.get("modifiedTime", "")

            req = self._drive.files().get_media(fileId=file_id, supportsAllDrives=True)
            buf = io.BytesIO()
            dl = MediaIoBaseDownload(buf, req)
            done = False
            while not done:
                _, done = dl.next_chunk()
            data = buf.getvalue()

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
