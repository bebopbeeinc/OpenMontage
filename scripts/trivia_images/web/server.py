"""Trivia images web UI (FastAPI sub-app, mounted by web/server.py at /trivia-images).

Surfaces the question tabs of the trivia-questions sheet (default `1-100`; every
tab matching the 1-100 header layout is selectable) as a per-row table and
exposes the two stages of the trivia-images pipeline:

  - Generate Question:  question-image prompt -> openart_image -> q{N}.<ext>
  - Generate Answer:    answer-image prompt + q{N}.<ext> as reference -> openart_image -> q{N}_answer.<ext>

Each call runs the OpenArt Playwright driver in a worker thread (the tool
itself is sync), streams its stdout to a per-job log subscriber, and returns
a job summary the UI can poll or live-stream via SSE.

Mounted by web/server.py — do not run this app standalone. The index.html's
<base href="/trivia-images/"> would resolve incorrectly without the mount.

The server only binds 127.0.0.1.
"""
from __future__ import annotations

import asyncio
import json
import sys
import threading
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional
from urllib.parse import quote

from fastapi import FastAPI, HTTPException
from fastapi.responses import (
    FileResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)

REPO = Path(__file__).resolve().parents[3]
WEB_DIR = Path(__file__).resolve().parent
PKG_DIR = Path(__file__).resolve().parent.parent   # scripts/trivia_images/
SA_PATH = Path.home() / ".google" / "claude-sheets-sa.json"

# Shared Drive client. Lives under tools/publishers/ alongside other
# publish-target helpers (the directory will host BaseTool wrappers too
# when those land — see tools/audio/piper_tts.py for the helper+BaseTool
# coexistence pattern).
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(PKG_DIR))
from tools.publishers.google_drive import FileMeta, get_client  # noqa: E402
from drive_config import QUESTION_IMAGES_ROOT_ID  # noqa: E402
from image_optimize import (  # noqa: E402
    GAME_HEIGHT,
    GAME_WIDTH,
    optimize_image_bytes,
)
from sheet_schema import (  # noqa: E402
    DATA_START_ROW,
    FIELD_TO_HEADER,
    SHEET_ID,
    SHEET_TAB,
    SheetSchema,
    a1_tab,
    index_to_letter,
)


def _validate_tab(tab: str | None) -> str:
    """Coerce a request-supplied tab name into the discovered allowlist.

    Pulls the live list of trivia-images tabs from the spreadsheet (see
    _discover_tabs) so a tab rename in Sheets flows through without code
    edits — just a server restart, or pass refresh=1 to /api/rows.
    """
    names = [t["name"] for t in _discover_tabs()]
    if not tab:
        return SHEET_TAB if SHEET_TAB in names else (names[0] if names else SHEET_TAB)
    if tab not in names:
        raise HTTPException(400, f"unknown tab {tab!r}; allowed: {names}")
    return tab

# Trivia-images Drive layout (root ID in drive_config.py — shared with the
# batch optimizer + migration). One folder per COUNTRY code lives directly
# under the "Question Images" root; each country folder has a "Resized"
# subfolder for the 512×384 game copies (created on first use). There is no
# WIP/staging folder anymore: an image lives in exactly ONE country folder,
# and "approved vs WIP" is a STATUS in the sheet (the `Q/A Image Approved`
# columns), NOT a folder location.
_RESIZED_SUBFOLDER = "Resized"
_country_folder_ids: dict[str, str] = {}     # code -> folder id
_resized_folder_ids: dict[str, str] = {}     # parent folder id -> Resized folder id
_folder_lock = threading.Lock()
# Listing-cache lifetime for state resolution. Safe to keep long because every
# mutation (upload_or_replace / trash) invalidates the folder's listing.
_STATE_LIST_TTL_S = 120.0


def country_folder_id(code: str) -> str:
    """Resolve (find-or-create + cache) the per-country folder under the
    "Question Images" root. `code` is the COUNTRY column value (US, IN, FR…).

    Made anyone-with-link readable so files inside inherit public access (lets
    /api/image redirect straight to Google's CDN). Idempotent."""
    code = (code or "").strip()
    if not code:
        raise ValueError("country_folder_id: empty country code")
    cached = _country_folder_ids.get(code)
    if cached:
        return cached
    with _folder_lock:
        cached = _country_folder_ids.get(code)
        if cached:
            return cached
        client = get_client()
        meta = client.find_or_create_folder(QUESTION_IMAGES_ROOT_ID, code)
        client.ensure_anyone_reader(meta.id)
        _country_folder_ids[code] = meta.id
        return meta.id


def resized_folder_id(parent_id: str) -> str:
    """Resolve (find-or-create + cache) the "Resized" subfolder under `parent_id`
    (a country folder). Made anyone-with-link readable so the files inside
    inherit public access. Idempotent."""
    cached = _resized_folder_ids.get(parent_id)
    if cached:
        return cached
    with _folder_lock:
        cached = _resized_folder_ids.get(parent_id)
        if cached:
            return cached
        client = get_client()
        meta = client.find_or_create_folder(parent_id, _RESIZED_SUBFOLDER)
        client.ensure_anyone_reader(meta.id)
        _resized_folder_ids[parent_id] = meta.id
        return meta.id


def country_resized_folder_id(code: str) -> str:
    """Resized subfolder for a country code: Question Images/<CODE>/Resized."""
    return resized_folder_id(country_folder_id(code))


def drive_name(number: str, kind: str) -> str:
    """Map (row.number, job.kind) to the canonical Drive filename.

    Examples:
      ("1",  "question_image") -> "1Q.png"
      ("12", "answer_image")   -> "12A.png"

    Matches the names already in use in the approved "Question Images"
    Shared Drive folder.
    """
    if kind == "question_image":
        suffix = "Q"
    elif kind == "answer_image":
        suffix = "A"
    else:
        raise ValueError(f"unknown kind: {kind!r}")
    return f"{number}{suffix}.png"


def find_original(code: str, name: str) -> Optional[FileMeta]:
    """The full-res original `name` inside the country folder, or None."""
    # Longer-lived listing cache than the Drive client's 8s default: a country
    # folder only changes via this app's own upload/trash calls (which
    # invalidate the cache), so a stale read can't outlast a mutation.
    return get_client().list_folder(
        country_folder_id(code), ttl_s=_STATE_LIST_TTL_S
    ).get(name)


def find_resized(code: str, name: str) -> Optional[FileMeta]:
    """The 512×384 resized copy `name` inside the country Resized folder, or None."""
    return get_client().list_folder(
        country_resized_folder_id(code), ttl_s=_STATE_LIST_TTL_S
    ).get(name)


def migrate_resized(code: str, name: str, original_meta: FileMeta) -> FileMeta:
    """Create the 512×384 resized copy for `name` from its full-res original
    and upload it to the country's Resized subfolder.

    On-demand backfill for images that don't have a resized copy yet:
    /api/image calls this when a resized copy is missing but the original is
    present, so such images migrate themselves the first time they're viewed.
    The bulk equivalent is scripts/trivia_images/optimize_drive.py.
    """
    import tempfile

    client = get_client()
    data, _mime, _ = client.download_bytes(original_meta.id, original_meta.modified_time)
    png = optimize_image_bytes(data)
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
        tf.write(png)
        tmp = Path(tf.name)
    try:
        meta = client.upload_or_replace(
            country_resized_folder_id(code), name, tmp, mime_type="image/png",
        )
    finally:
        tmp.unlink(missing_ok=True)
    return meta   # inherits public from the Resized folder


# File IDs already made anyone-with-link readable, so we don't re-check sharing
# on every request (the check is idempotent but still a Drive round-trip).
_public_ids: set[str] = set()
_public_lock = threading.Lock()


def ensure_public(file_id: str) -> None:
    """Grant anyone-with-link read on a Drive file (cached per process).

    Lets the browser fetch it straight from Google's CDN (see _cdn_url) instead
    of streaming through this server. The trivia image assets are intentionally
    public — see the /api/image redirect path.
    """
    with _public_lock:
        if file_id in _public_ids:
            return
    get_client().ensure_anyone_reader(file_id)
    with _public_lock:
        _public_ids.add(file_id)


def _cdn_url(file_id: str, *, thumb: bool, version: str | None = None) -> str:
    """Public Drive CDN URL for a file. `thumbnail` serves a CDN-resized image
    (Google does the downscale), so even a full-res original is cheap to fetch.

    `version` is appended as an otherwise-ignored query param so the browser
    keys its cache on it. This matters because upload_or_replace reuses the
    file_id when re-rendering an image (files.update), so the CDN URL is
    byte-identical across edits. Without a version token the browser serves the
    stale cached bytes — clearing the cache was the only way to see the new
    image (refresh fetched fresh metadata but redirected to the same CDN URL).
    Pass the row's modified_time so a re-render produces a fresh cache key."""
    size = 256 if thumb else 1024
    url = f"https://drive.google.com/thumbnail?id={file_id}&sz=w{size}"
    if version:
        url += f"&v={quote(str(version), safe='')}"
    return url


# Names with an in-flight background migration, so concurrent /api/image
# requests for the same image don't each kick off a redundant download+resize.
_migrating: set[str] = set()
_migrating_lock = threading.Lock()


def _schedule_resized_migration(code: str, name: str, original_meta: FileMeta) -> None:
    """Fire-and-forget backfill of the resized copy.

    Lets /api/image return immediately (serving the original's already-generated
    CDN thumbnail) instead of blocking on a 2–5 MB download + optimize + upload.
    Deduped per (code, name) so a grid full of un-resized images doesn't migrate
    the same one twice at once.
    """
    key = f"{code}/{name}"
    with _migrating_lock:
        if key in _migrating:
            return
        _migrating.add(key)

    async def _run() -> None:
        try:
            await asyncio.to_thread(migrate_resized, code, name, original_meta)
        except Exception:
            pass
        finally:
            with _migrating_lock:
                _migrating.discard(key)

    asyncio.create_task(_run())

# Sheet location is owned by sheet_schema.py — the schema resolves
# column letters by reading the header rows at runtime so adding a
# column (or switching between tabs with different layouts) doesn't
# break this app. The fields we read/write are listed below; see
# FIELD_TO_HEADER in sheet_schema for the accepted labels per field.
ROW_FIELDS = [
    "number", "complete", "country", "category", "mode", "question",
    "answer_correct", "answer_2", "answer_3", "answer_4",
    "response_correct", "response_incorrect", "hint",
    "prompt_q", "prompt_r", "approved_q", "approved_r",
]

# Defaults for OpenArt — match scripts/trivia_images/generate.py.
MODEL = "Nano Banana Pro"
ASPECT = "4:3"
RESOLUTION = "2K"

# Importing the registry triggers discovery of openart_image. Done lazily
# inside the worker so a fresh server start isn't blocked by tool init.
sys.path.insert(0, str(REPO))


JobKind = Literal["question_image", "answer_image"]
JobStatus = Literal["queued", "running", "success", "error"]


@dataclass
class Job:
    id: str
    kind: JobKind
    row: int
    slug: str                                      # 'q{N}'
    tab: str = SHEET_TAB
    status: JobStatus = "queued"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    started_at: str | None = None
    finished_at: str | None = None
    log: list[str] = field(default_factory=list)
    error: str | None = None
    output_path: str | None = None
    extra: dict = field(default_factory=dict)

    def summary(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "row": self.row,
            "slug": self.slug,
            "tab": self.tab,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "output_path": self.output_path,
            "log_lines": len(self.log),
        }


jobs: dict[str, Job] = {}
recent_job_ids: deque[str] = deque(maxlen=200)
log_subscribers: dict[str, list[asyncio.Queue[str]]] = {}

# Serializes the worker — the OpenArt Playwright driver opens a fresh
# Chromium per call and uses a single persisted login state at
# .playwright/openart-state.json. Running multiple workers concurrently would
# race on that file (and saturate OpenArt's rate limits). One Chromium at a
# time; jobs queue naturally behind this lock.
worker_lock = asyncio.Lock()


def _emit(job: Job, line: str) -> None:
    line = line.rstrip("\n")
    job.log.append(line)
    for q in log_subscribers.get(job.id, []):
        try:
            q.put_nowait(line)
        except asyncio.QueueFull:
            pass


# ---------------------------------------------------------------------------
# Sheets read
# ---------------------------------------------------------------------------
# Process-wide singletons. Per the schema module's docstring, header
# layout is meant to be resolved "once per process" — but the prior
# wiring re-instantiated SheetSchema on every request and burned ~1s on
# a fresh values.get() of the header rows each time. Caching here is
# what makes that intent actually true.
#
# The sheets client itself is per-thread (not a process singleton): the
# google-api-python-client wraps a single httplib2.Http instance which
# is NOT thread-safe, and /api/rows runs through asyncio.to_thread so
# multiple worker threads were sharing one client. That's what produced
# the intermittent "sheet read failed … [Errno 32] Broken pipe" 500s
# when Google's LB closed an idle connection between two thread reads.
_sheets_local = threading.local()
_schema_cache: dict[str, SheetSchema] = {}
_schema_lock = threading.Lock()
# Discovered tabs: [{"name": str, "gid": str, "index": int}, ...]
_tabs_cache: list[dict] | None = None
_tabs_lock = threading.Lock()

# Short-TTL cache of assembled /api/rows payloads, keyed by validated tab.
# read_rows is dominated by a Sheets values().get() that runs 2-9s for the
# larger country tabs, so flipping between tabs and back re-pays that cost
# every time. A short TTL collapses those repeat loads to an in-memory hit
# while bounding staleness, and every row-mutating path drops the affected
# tab's entry (see _set_approval / _write_prompts) so an action's result is
# never hidden behind the cache. refresh=1 also bypasses and repopulates it.
_ROWS_CACHE_TTL_S = 15.0
_rows_cache: dict[str, tuple[float, list[dict]]] = {}
_rows_cache_lock = threading.Lock()


def _rows_cache_get(tab: str) -> list[dict] | None:
    import time
    with _rows_cache_lock:
        hit = _rows_cache.get(tab)
        if hit is None:
            return None
        ts, rows = hit
        if time.monotonic() - ts > _ROWS_CACHE_TTL_S:
            _rows_cache.pop(tab, None)
            return None
        return rows


def _rows_cache_put(tab: str, rows: list[dict]) -> None:
    import time
    with _rows_cache_lock:
        _rows_cache[tab] = (time.monotonic(), rows)


def _rows_cache_drop(tab: str | None = None) -> None:
    """Evict one tab's cached rows (or all, when tab is None). Called by every
    path that mutates a row so the next /api/rows reflects the change at once."""
    with _rows_cache_lock:
        if tab is None:
            _rows_cache.clear()
        else:
            _rows_cache.pop(tab, None)


def _build_sheets():
    cli = getattr(_sheets_local, "client", None)
    if cli is not None:
        return cli
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    creds = service_account.Credentials.from_service_account_file(
        str(SA_PATH),
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    cli = build("sheets", "v4", credentials=creds, cache_discovery=False)
    _sheets_local.client = cli
    return cli


def _reset_sheets() -> None:
    """Drop the thread-local sheets client so the next _build_sheets()
    call materializes a fresh httplib2 connection. Called by
    _sheets_execute when a transport error indicates the cached
    httplib2.Http is poisoned (its persistent socket was closed by the
    server but the client doesn't know yet)."""
    _sheets_local.client = None


# httplib2 surfaces a reaped idle TLS connection as one of these. We
# retry after rebuilding the client so the user-visible /api/rows 500
# stops being the user's problem.
_TRANSIENT_TRANSPORT_ERRNOS = {32, 104}   # EPIPE, ECONNRESET


def _is_transient_transport_error(exc: BaseException) -> bool:
    if isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError)):
        return True
    if isinstance(exc, OSError) and exc.errno in _TRANSIENT_TRANSPORT_ERRNOS:
        return True
    # httplib2 sometimes wraps the OSError in a generic ConnectionError
    # or surfaces an ssl.SSLEOFError on the same root cause. Match on
    # the message as a last-resort signal — better to over-retry than
    # to show the user a 500 we could have recovered from.
    msg = str(exc).lower()
    if "broken pipe" in msg or "connection reset" in msg:
        return True
    if "ssl" in msg and ("eof" in msg or "unexpected" in msg):
        return True
    return False


def _sheets_execute(build_req, *, num_retries: int = 2, max_attempts: int = 3):
    """Execute a Sheets API request with retries for transient transport errors.

    `build_req(sheets) -> request` materializes the request from the
    current thread-local client. Wrapping the request in a callable
    lets us drop a poisoned client and rebuild the request against a
    fresh socket on retry — you can't re-execute a request whose
    underlying http was already closed.

    `num_retries=2` inside .execute() handles 429/5xx via
    googleapiclient's own exponential backoff. `max_attempts=3` is the
    additional outer loop that rebuilds the client on transport errors
    (broken pipe, connection reset)."""
    import time
    last: BaseException | None = None
    for attempt in range(max_attempts):
        try:
            return build_req(_build_sheets()).execute(num_retries=num_retries)
        except Exception as e:
            if not _is_transient_transport_error(e):
                raise
            last = e
            _reset_sheets()
            if attempt < max_attempts - 1:
                time.sleep(0.2 * (2 ** attempt))
    assert last is not None
    raise last


def _discover_tabs(refresh: bool = False) -> list[dict]:
    """List sheet tabs whose column layout matches the trivia-images schema.

    Two HTTP calls total regardless of tab count:
      1. spreadsheets.get for the workbook's tab metadata
      2. values.batchGet for every tab's header rows (rows 1-2) in a
         single request
    We then resolve each candidate's schema in pure Python — tabs
    without the 1-100 layout's required header labels (the "Topics"
    pivot, the legacy Brian-style tabs, and the differently-labelled
    "RN" tab) are skipped silently. Resolved schemas are warmed into
    _schema_cache as a side effect so /api/rows doesn't pay another
    header fetch when the user clicks a tab.

    Returned shape: [{"name": str, "gid": str, "index": int}, ...]
    sorted by the workbook's tab order so the UI strip matches Sheets.
    Cached at module scope; pass refresh=True to re-discover after the
    workbook gets a new/renamed/removed tab.
    """
    from sheet_schema import HEADER_ROWS

    global _tabs_cache
    if not refresh and _tabs_cache is not None:
        return _tabs_cache
    with _tabs_lock:
        if not refresh and _tabs_cache is not None:
            return _tabs_cache
        meta = _sheets_execute(
            lambda sheets: sheets.spreadsheets().get(
                spreadsheetId=SHEET_ID,
                fields="sheets.properties(title,sheetId,index)",
            )
        )
        tab_props: list[dict] = []
        for s in meta.get("sheets", []):
            p = s.get("properties") or {}
            if p.get("title"):
                tab_props.append(p)
        if not tab_props:
            _tabs_cache = []
            return _tabs_cache

        lo, hi = min(HEADER_ROWS), max(HEADER_ROWS)
        ranges = [f"{a1_tab(p['title'])}!{lo}:{hi}" for p in tab_props]
        batch = _sheets_execute(
            lambda sheets: sheets.spreadsheets().values().batchGet(
                spreadsheetId=SHEET_ID,
                ranges=ranges,
            )
        )
        value_ranges = batch.get("valueRanges") or []
        # Defensive: pair by index, fall back to empty rows if the
        # response is shorter than the request (shouldn't happen but
        # cheaper than a hard crash on a malformed payload).
        result: list[dict] = []
        for i, p in enumerate(tab_props):
            name = p["title"]
            rows = (value_ranges[i].get("values") if i < len(value_ranges) else None) or []
            schema = SheetSchema(_build_sheets(), tab=name)
            try:
                schema.populate_from_rows(rows)
            except Exception:
                continue   # not a trivia-images tab
            # Warm the schema cache so the first /api/rows call for
            # this tab skips its own header round-trip.
            with _schema_lock:
                _schema_cache[name] = schema
            result.append({
                "name": name,
                "gid": str(p["sheetId"]),
                "index": int(p.get("index", 0)),
            })
        result.sort(key=lambda t: t["index"])
        _tabs_cache = result
    return result


def _prewarm_schemas() -> None:
    """Discover tabs + resolve every matching schema in the background at startup.

    The first user request would otherwise pay ~400ms for the
    header-row fetch on each tab. Done in a daemon thread so startup
    isn't blocked; silent on failure (Drive/Sheets unauthed in dev is
    OK — the next real request surfaces the error properly).
    """
    def go():
        try:
            _build_sheets()
            _discover_tabs()
        except Exception:
            pass
    t = threading.Thread(target=go, name="trivia-images-prewarm", daemon=True)
    t.start()


def _get_schema(tab: str, refresh: bool = False) -> SheetSchema:
    """Cached SheetSchema for `tab`. Resolves headers exactly once per
    process unless `refresh=True` (or until the in-instance retry
    triggers a re-resolve because a tracked header label moved). If you
    reorder columns without renaming any tracked label, restart the
    server or hit /api/rows?refresh=1 — there is no automatic detection
    for "same label, different column position".
    """
    from sheet_schema import HEADER_ROWS

    if refresh:
        with _schema_lock:
            _schema_cache.pop(tab, None)
    cached = _schema_cache.get(tab)
    if cached is not None:
        return cached
    with _schema_lock:
        cached = _schema_cache.get(tab)
        if cached is None:
            # Route the header fetch through _sheets_execute so a poisoned
            # httplib2 socket gets rebuilt on retry. SheetSchema._resolve
            # retries on broken pipe but reuses the same dead client, so
            # this path is the one that has to own client recovery.
            lo, hi = min(HEADER_ROWS), max(HEADER_ROWS)
            resp = _sheets_execute(
                lambda sheets: sheets.spreadsheets().values().get(
                    spreadsheetId=SHEET_ID,
                    range=f"{a1_tab(tab)}!{lo}:{hi}",
                )
            )
            rows = resp.get("values") or []
            schema = SheetSchema(_build_sheets(), tab=tab)
            schema.populate_from_rows(rows)
            _schema_cache[tab] = schema
            cached = schema
    return cached


def _drive_state_for(code: str, number: str, kind: str, approved: bool) -> dict:
    """Resolve Drive state for one (country, number, kind) image.

    Returns {state, drive_name, file_id, modified_time, thumbnail_link}.

    `state` is one of:
      - 'none'     — no file in the country folder
      - 'wip'      — file exists in the country folder but is NOT approved in the sheet
      - 'approved' — file exists AND the row's `Q/A Image Approved` column is ✓

    "WIP" is now a sheet status, not a folder: the file always lives in the one
    country folder. `approved` is the sheet flag for this (row, kind), read by
    the caller. file_id + modified_time let the frontend bust its image cache
    when the underlying file changes.
    """
    name = drive_name(number, kind)
    try:
        meta = find_original(code, name) if code else None
    except Exception:
        # Drive transient — surface 'none' rather than crash the row list.
        meta = None
    state = "none" if meta is None else ("approved" if approved else "wip")
    out = {
        "state": state, "drive_name": name,
        "file_id": None, "modified_time": None, "thumbnail_link": None,
    }
    if meta is not None:
        out["file_id"] = meta.id
        out["modified_time"] = meta.modified_time
        # Bump =s220 (Drive's default) to =s256 — enough for 2x retina at the
        # 96x72 css render size, ~1/4 the bytes of =s512.
        if meta.thumbnail_link:
            out["thumbnail_link"] = meta.thumbnail_link.replace("=s220", "=s256")
    return out


def read_rows(tab: str = SHEET_TAB, min_row: int = DATA_START_ROW, max_row: int = 1000, refresh_schema: bool = False) -> list[dict]:
    schema = _get_schema(tab, refresh=refresh_schema)
    # Read up to the rightmost column the schema cares about, with a
    # small cushion so an inserted column to the right of the tracked
    # range doesn't truncate the read on the next sheet edit.
    last_letter = _last_letter(schema.max_index() + 4)
    rng = f"{a1_tab(tab)}!A{min_row}:{last_letter}{max_row}"
    resp = _sheets_execute(
        lambda sheets: sheets.spreadsheets().values().get(spreadsheetId=SHEET_ID, range=rng)
    )
    raw = resp.get("values", [])
    # Tab-level country fallback for rows whose own COUNTRY cell is blank (common
    # mid-authoring). Without this the read path (per-row code) would render the
    # row as 'none' while the write/image paths (which use the tab's code) would
    # still target the country folder — a silent divergence. Best-effort: a
    # genuinely country-less tab leaves tab_code empty and rows fall back to ''.
    try:
        tab_code = _tab_country_code(tab)
    except Exception:
        tab_code = ""
    # Warm the Drive listing once per /api/rows call — find_original goes
    # through the per-folder cache so all rows of a country = a couple Drive
    # API calls, not two per row.
    rows: list[dict] = []
    for i, v in enumerate(raw):
        sheet_row = min_row + i
        f = schema.extract(v, ROW_FIELDS)
        if not f["number"]:
            continue
        code = f["country"] or tab_code
        slug = f"q{f['number']}"
        q_approved = f["approved_q"] == "✓"
        a_approved = f["approved_r"] == "✓"
        q_drive = _drive_state_for(code, f["number"], "question_image", q_approved)
        a_drive = _drive_state_for(code, f["number"], "answer_image", a_approved)
        rows.append({
            "row": sheet_row,
            "number": f["number"],
            "slug": slug,
            "country": code,
            "category": f["category"],
            "mode": f["mode"],
            "question": f["question"],
            # Answer columns — surfaced for context so the UI can show the user
            # what their image is supposed to depict without leaving the tool.
            "answer_correct": f["answer_correct"],
            "answer_2": f["answer_2"],
            "answer_3": f["answer_3"],
            "answer_4": f["answer_4"],
            "response_correct": f["response_correct"],
            "response_incorrect": f["response_incorrect"],
            "hint": f["hint"],
            "prompt_q": f["prompt_q"],
            "prompt_r": f["prompt_r"],
            "question_complete": f["complete"] == "✓",
            "question_drive": q_drive,
            "answer_drive": a_drive,
        })
    return rows


# Tab -> COUNTRY code, cached. A country tab carries one code across all its
# rows, so we read it once from the first numbered data row. Used by endpoints
# (approve/discard/image) that know the tab but want the Drive folder code.
_tab_country_cache: dict[str, str] = {}
_tab_country_lock = threading.Lock()


def _tab_country_code(tab: str) -> str:
    """The COUNTRY code for a tab (e.g. 'US'), read from its first data row."""
    cached = _tab_country_cache.get(tab)
    if cached:
        return cached
    schema = _get_schema(tab)
    try:
        col = schema.letter("country")
    except KeyError:
        raise HTTPException(400, f"tab {tab!r} has no COUNTRY column")
    resp = _sheets_execute(
        lambda sheets: sheets.spreadsheets().values().get(
            spreadsheetId=SHEET_ID,
            range=f"{a1_tab(tab)}!{col}{DATA_START_ROW}:{col}1000",
        )
    )
    for row in resp.get("values", []):
        code = (row[0] if row else "").strip()
        if code:
            with _tab_country_lock:
                _tab_country_cache[tab] = code
            return code
    raise HTTPException(400, f"tab {tab!r} has no COUNTRY value in any data row")


# Serializes the read-width → append-header → refresh sequence in
# _ensure_approval_column. Without it, approving Q and A on a tab that has
# never had approval columns can race: both reads see the same row-2 width and
# both write their header into the SAME column. Process-wide because the
# endpoints run on a thread pool (asyncio.to_thread).
_approval_col_lock = threading.Lock()


def _ensure_approval_column(tab: str, field: str) -> str:
    """Column letter for an approval field, creating the column if absent.

    The `Q/A Image Approved` columns don't exist on a tab until its first
    approve. When missing, we append the header label into row 2 at the first
    free column, refresh the schema, and return the new letter. Resolving by
    header label (not fixed index) keeps inserts/reorders safe, same as every
    other column.
    """
    schema = _get_schema(tab)
    try:
        return schema.letter(field)
    except KeyError:
        pass
    label = FIELD_TO_HEADER[field]
    with _approval_col_lock:
        # Re-check under the lock: another approve (e.g. the sibling Q/A field)
        # may have created this column — or shifted row-2 width — while we waited.
        try:
            return _get_schema(tab).letter(field)
        except KeyError:
            pass
        # First free column = current width of row 2 (Sheets trims trailing empties).
        resp = _sheets_execute(
            lambda sheets: sheets.spreadsheets().values().get(
                spreadsheetId=SHEET_ID, range=f"{a1_tab(tab)}!2:2",
            )
        )
        row2 = (resp.get("values") or [[]])
        width = len(row2[0]) if row2 else 0
        letter = index_to_letter(width)
        _sheets_execute(
            lambda sheets: sheets.spreadsheets().values().update(
                spreadsheetId=SHEET_ID,
                range=f"{a1_tab(tab)}!{letter}2",
                valueInputOption="RAW",
                body={"values": [[label]]},
            )
        )
        # Re-resolve so the new column is known, then return its letter.
        return _get_schema(tab, refresh=True).letter(field)


def _set_approval(tab: str, row: int, field: str, value: str) -> str:
    """Write `value` ('✓' to approve, '' to clear) into the approval column.

    Returns the column letter written. Creates the column on first use.
    """
    letter = _ensure_approval_column(tab, field)
    _sheets_execute(
        lambda sheets: sheets.spreadsheets().values().update(
            spreadsheetId=SHEET_ID,
            range=f"{a1_tab(tab)}!{letter}{row}",
            valueInputOption="RAW",
            body={"values": [[value]]},
        )
    )
    # The row's approval state just changed — drop the cached /api/rows payload
    # for this tab so the next fetch reflects it instead of a stale snapshot.
    _rows_cache_drop(tab)
    return letter


def _last_letter(idx: int) -> str:
    """Spreadsheet A1 column letter for the rightmost column we want to read."""
    from sheet_schema import index_to_letter
    return index_to_letter(idx)


def _write_prompts(tab: str, row: int, prompt_q: str | None, prompt_r: str | None) -> dict[str, str]:
    """Write the question and/or answer prompt columns for `row`.

    Returns {col_letter: value} for whatever was actually written. The
    sheet is the source of truth — this function is the only place that
    mutates the prompts. After this returns successfully the row's
    prompts on disk differ from what the UI cached, so callers reload
    /api/rows.

    Pass `None` for any field you don't want to touch. Passing both as
    None is a programming error (caller should validate before calling).
    Column letters are resolved at runtime from the tab's header rows
    so inserting/reordering columns in the sheet stays safe.
    """
    if prompt_q is None and prompt_r is None:
        raise ValueError("nothing to write (both prompts are None)")

    schema = _get_schema(tab)
    q_letter = schema.letter("prompt_q")
    r_letter = schema.letter("prompt_r")
    data: list[dict] = []
    written: dict[str, str] = {}
    if prompt_q is not None:
        data.append({
            "range": f"{a1_tab(tab)}!{q_letter}{row}",
            "values": [[prompt_q]],
        })
        written[q_letter] = prompt_q
    if prompt_r is not None:
        data.append({
            "range": f"{a1_tab(tab)}!{r_letter}{row}",
            "values": [[prompt_r]],
        })
        written[r_letter] = prompt_r
    _sheets_execute(
        lambda sheets: sheets.spreadsheets().values().batchUpdate(
            spreadsheetId=SHEET_ID,
            body={"valueInputOption": "USER_ENTERED", "data": data},
        )
    )
    # Prompts changed — drop this tab's cached rows so the edit shows up at once.
    _rows_cache_drop(tab)
    return written


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------
# Drive is the source of truth — we never persist generated images locally.
# The OpenArt driver still needs to write to disk (it downloads via the
# browser's authenticated request context), and Playwright's set_input_files
# for the reference upload needs a real path too. We give it a tempfile, push
# the result to Drive, then unlink. Tempfile dir defaults to /tmp on macOS;
# the OS reaps it eventually even if we leak one on a hard crash.

def _run_generation_sync(job: Job, prompt: str,
                         reference_image_path: Path | None) -> None:
    """Synchronous worker body. Runs in a thread via asyncio.to_thread.

    Drive-only: writes to a tempfile, uploads to STAGING, then unlinks.
    The local tempfile never outlives the function call on the success path,
    and on failure paths the OS tmp cleanup eventually reaps it.

    Raises on upload failure — we don't silently keep a stranded local copy
    that the rest of the UI can't see.
    """
    import tempfile

    from tools.tool_registry import registry
    registry.discover()
    tool = registry._tools.get("openart_image")
    if tool is None:
        raise RuntimeError("openart_image tool not registered (registry discovery failed)")

    # Allocate a tempfile path. We close the fd immediately because the
    # OpenArt driver opens its own write handle (and rewrites the extension
    # to match the CDN's served format).
    fd, tmp_str = tempfile.mkstemp(prefix=f"trivia-{job.slug}-{job.kind}-", suffix=".jpg")
    import os as _os
    _os.close(fd)
    output_path = Path(tmp_str)

    _emit(job, f"  prompt: {prompt[:140].replace(chr(10), ' ')}...")
    _emit(job, f"  model: {MODEL}  aspect: {ASPECT}  resolution: {RESOLUTION}")
    if reference_image_path is not None:
        _emit(job, f"  reference: {reference_image_path.name}")
    _emit(job, f"  tempfile: {output_path}")
    _emit(job, "  -> calling openart_image (this is headless and may take 30-90s)")

    inputs = {
        "prompt": prompt,
        "model": MODEL,
        "aspect": ASPECT,
        "resolution": RESOLUTION,
        "output_path": str(output_path),
        "headless": True,    # SSH-tunneled UI; no display for headed
    }
    if reference_image_path is not None:
        inputs["reference_image_path"] = str(reference_image_path)

    saved_path: Path | None = None
    resized_path: Path | None = None
    try:
        result = tool.execute(inputs)
        if not result.success:
            raise RuntimeError(result.error or "openart_image returned no error string")

        saved = result.data.get("saved_paths") or []
        if not saved:
            raise RuntimeError("openart_image returned no saved_paths")
        saved_path = Path(saved[0])
        _emit(job, f"  ✓ generated ({result.duration_seconds:.1f}s)")

        code = (job.extra.get("country") or "").strip()
        if not code:
            raise RuntimeError("no country code on job — cannot resolve Drive folder")
        dest_name = drive_name(job.slug.lstrip("q"), job.kind)
        client = get_client()

        # 1) Full-res original → the country folder. Kept "just in case" and
        #    used as the answer-remix reference (best fidelity).
        meta = client.upload_or_replace(country_folder_id(code), dest_name, saved_path)
        job.extra["drive_file_id"] = meta.id
        job.extra["drive_name"] = meta.name
        job.output_path = f"drive://{code}/{meta.name}"
        _emit(job, f"  ✓ original uploaded to {code}/: {meta.name} (id={meta.id})")

        # 2) 512×384 lossless-PNG copy → the country's Resized subfolder. This is
        #    what the UI shows and the game consumes.
        resized_bytes = optimize_image_bytes(saved_path.read_bytes())
        import tempfile as _tf
        fd, rstr = _tf.mkstemp(prefix=f"trivia-{job.slug}-{job.kind}-resized-", suffix=".png")
        _os.close(fd)
        resized_path = Path(rstr)
        resized_path.write_bytes(resized_bytes)
        rmeta = client.upload_or_replace(
            country_resized_folder_id(code), dest_name, resized_path,
            mime_type="image/png",
        )
        job.extra["resized_file_id"] = rmeta.id
        _emit(job, f"  ✓ resized → {GAME_WIDTH}×{GAME_HEIGHT} PNG "
                   f"({len(resized_bytes) // 1024} KB) uploaded to {code}/Resized")
    finally:
        # Always unlink the tempfile(s): the path we allocated, the path the
        # driver actually wrote (it may have rewritten the extension from .jpg
        # to .png/.webp), and the resized temp.
        for p in {output_path, saved_path, resized_path} - {None}:
            try:
                if p and p.exists():
                    p.unlink()
            except OSError:
                pass


async def _worker(job: Job) -> None:
    # Queue behind any in-flight job — Chromium can only run one at a time.
    async with worker_lock:
        await _run_job(job)


def _stage_reference_from_drive(code: str, slug: str) -> Path:
    """Download the question image for `slug` from the country folder to a tempfile.

    The OpenArt driver needs a real local path for Playwright's
    set_input_files. We pull bytes from Drive and write them to a tempfile that
    the caller is responsible for deleting.

    Raises if the question image isn't in the country folder.
    """
    import tempfile
    name = drive_name(slug.lstrip("q"), "question_image")
    meta = find_original(code, name) if code else None
    if meta is None:
        raise RuntimeError(
            f"question image {code}/{name} not found on Drive — generate it first"
        )
    data, mime, _ = get_client().download_bytes(meta.id, meta.modified_time)
    suffix = ".png" if "png" in mime else ".jpg"
    fd, tmp_str = tempfile.mkstemp(prefix=f"trivia-ref-{slug}-", suffix=suffix)
    import os as _os
    _os.close(fd)
    tmp = Path(tmp_str)
    tmp.write_bytes(data)
    return tmp


async def _run_job(job: Job) -> None:
    job.status = "running"
    job.started_at = datetime.now(timezone.utc).isoformat()
    ref_tempfile: Path | None = None
    try:
        if job.kind == "question_image":
            prompt = job.extra.get("prompt", "").strip()
            if not prompt:
                raise RuntimeError("question-image prompt is empty for this row")
            await asyncio.to_thread(_run_generation_sync, job, prompt, None)
            field = "approved_q"
        elif job.kind == "answer_image":
            prompt = job.extra.get("prompt", "").strip()
            if not prompt:
                raise RuntimeError("answer-image prompt is empty for this row")
            _emit(job, "  → downloading reference (question image) from Drive…")
            code = (job.extra.get("country") or "").strip()
            ref_tempfile = await asyncio.to_thread(_stage_reference_from_drive, code, job.slug)
            await asyncio.to_thread(
                _run_generation_sync, job, prompt, ref_tempfile,
            )
            field = "approved_r"
        else:
            raise RuntimeError(f"unknown job kind: {job.kind}")
        # A freshly (re)generated image is unapproved by definition — clear the
        # row's approval flag so a changed image isn't left marked approved.
        try:
            await asyncio.to_thread(_set_approval, job.tab, job.row, field, "")
            _emit(job, f"  · approval reset (regenerate clears approval) on {job.tab} row {job.row}")
        except Exception as e:
            _emit(job, f"  ⚠ approval reset failed: {e}")
        job.status = "success"
    except Exception as e:
        job.status = "error"
        job.error = str(e)
        _emit(job, f"FAILED: {e}")
    finally:
        if ref_tempfile is not None:
            try:
                if ref_tempfile.exists():
                    ref_tempfile.unlink()
            except OSError:
                pass
        job.finished_at = datetime.now(timezone.utc).isoformat()
        for q in log_subscribers.get(job.id, []):
            try:
                q.put_nowait("__END__")
            except asyncio.QueueFull:
                pass


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="Trivia images runner")

# Kick off a background schema pre-warm for every known tab. Daemon
# thread so startup isn't blocked; by the time the user clicks a tab,
# the header fetch is already done.
_prewarm_schemas()


@app.get("/")
async def index():
    return FileResponse(WEB_DIR / "index.html")


def _rows_sync(tab: str | None, refresh: bool):
    """Off-loop work for /api/rows: discovery + validate + read.

    All three steps touch Google APIs (refresh=True path) or hit caches
    that get populated by Google API calls. Running them on the event
    loop blocks every other request — most painfully /api/active, which
    the frontend polls every 3s. Wrap this in asyncio.to_thread so
    in-memory endpoints can slip through while Sheets is responding.

    refresh=True also drops the cached Drive folder listings for the country
    folders we've resolved (and their Resized subfolders) so manual Drive
    edits — the user copying/moving/deleting a file via Drive's UI rather than
    this app — show up on the next poll instead of sitting stale for the rest
    of the 120s TTL.
    """
    if refresh:
        try:
            _discover_tabs(refresh=True)
        except Exception:
            pass
        try:
            client = get_client()
            # Country + Resized folders are lazy — only invalidate the ones
            # already resolved this session (don't force round-trips for
            # folders we may never touch).
            for fid in list(_country_folder_ids.values()):
                client.invalidate_listing(fid)
            for fid in list(_resized_folder_ids.values()):
                client.invalidate_listing(fid)
        except Exception:
            pass
        # Drop the tab→COUNTRY cache so a corrected COUNTRY code in the sheet
        # takes effect on the next approve/discard/image without a restart.
        with _tab_country_lock:
            _tab_country_cache.clear()
    validated = _validate_tab(tab)
    if not refresh:
        cached = _rows_cache_get(validated)
        if cached is not None:
            return validated, cached
    rows = read_rows(tab=validated, refresh_schema=refresh)
    _rows_cache_put(validated, rows)
    return validated, rows


@app.get("/api/health")
async def api_health():
    try:
        tabs = [t["name"] for t in await asyncio.to_thread(_discover_tabs)]
    except Exception:
        tabs = []
    return {
        "ok": True,
        "repo": str(REPO),
        "sheet_id": SHEET_ID,
        "sheet_tab": SHEET_TAB,
        "available_tabs": tabs,
        "question_images_root_id": QUESTION_IMAGES_ROOT_ID,
        "sa_path": str(SA_PATH),
        "sa_present": SA_PATH.exists(),
        "python": sys.executable,
    }


@app.get("/api/tabs")
async def api_tabs(refresh: int = 0):
    """List the sheet tabs the UI can switch between, plus the default.

    Discovered live from the workbook (see _discover_tabs). Pass
    refresh=1 to re-probe — useful after adding/renaming a tab in Sheets.
    """
    try:
        tabs = await asyncio.to_thread(_discover_tabs, bool(refresh))
    except Exception as e:
        raise HTTPException(500, f"tab discovery failed: {e}")
    names = [t["name"] for t in tabs]
    default = SHEET_TAB if SHEET_TAB in names else (names[0] if names else SHEET_TAB)
    return {"tabs": tabs, "default": default}


@app.get("/api/rows")
async def api_rows(tab: str | None = None, refresh: int = 0):
    try:
        validated, rows = await asyncio.to_thread(_rows_sync, tab, bool(refresh))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"sheet read failed on tab {tab!r}: {e}")
    return rows


@app.post("/api/prompts")
async def api_prompts(payload: dict):
    """Persist prompt edits back to the selected question tab.

    Payload: {tab?, row, prompt_q?, prompt_r?}. `tab` defaults to SHEET_TAB
    when omitted. At least one of prompt_q / prompt_r must be present. The
    sheet is the source of truth — this endpoint writes the question-image
    and/or answer-image prompt column (resolved by header label) for the given
    row, and the next /api/rows fetch will reflect the new values. Editing here
    does NOT regenerate the images; the user re-runs generate to refresh the
    renders.

    A whitespace-only prompt clears the cell (sheet stores '' — same effect
    as deleting the cell content in the Sheets UI). Empty string is OK; we
    don't auto-trim because the sheet's existing prompts often have leading
    "STYLE: ..." prefixes the user may legitimately delete.
    """
    tab = _validate_tab(payload.get("tab"))
    try:
        row = int(payload["row"])
    except (KeyError, ValueError, TypeError):
        raise HTTPException(400, "row must be an integer")
    if row < DATA_START_ROW:
        raise HTTPException(400, f"row must be >= {DATA_START_ROW} (data rows)")

    prompt_q = payload.get("prompt_q", None)
    prompt_r = payload.get("prompt_r", None)
    if prompt_q is None and prompt_r is None:
        raise HTTPException(400, "at least one of prompt_q or prompt_r is required")
    # Cast non-None to str so callers can't sneak in non-strings.
    prompt_q = None if prompt_q is None else str(prompt_q)
    prompt_r = None if prompt_r is None else str(prompt_r)

    try:
        written = await asyncio.to_thread(_write_prompts, tab, row, prompt_q, prompt_r)
    except Exception as e:
        raise HTTPException(500, f"sheet write failed on tab {tab!r}: {e}")
    return {"ok": True, "tab": tab, "row": row, "written": written}


@app.post("/api/run")
async def api_run(payload: dict):
    kind = payload.get("kind", "")
    if kind not in ("question_image", "answer_image"):
        raise HTTPException(400, "kind must be 'question_image' or 'answer_image'")
    tab = _validate_tab(payload.get("tab"))
    try:
        row = int(payload["row"])
    except (KeyError, ValueError, TypeError):
        raise HTTPException(400, "row must be an integer")
    slug = str(payload.get("slug", "")).strip()
    if not slug:
        raise HTTPException(400, "slug required (e.g. 'q1')")
    prompt = str(payload.get("prompt", "")).strip()
    if not prompt:
        raise HTTPException(400, "prompt required")

    # Country code names the Drive folder the image is written to. Prefer the
    # payload's value (the UI has it per row); fall back to the tab's code.
    code = str(payload.get("country", "")).strip() or _tab_country_code(tab)

    extra: dict = {"prompt": prompt, "country": code}

    job = Job(id=uuid.uuid4().hex[:8], kind=kind, row=row, slug=slug, tab=tab, extra=extra)
    jobs[job.id] = job
    recent_job_ids.append(job.id)
    log_subscribers.setdefault(job.id, [])
    asyncio.create_task(_worker(job))
    return job.summary()


@app.get("/api/jobs")
async def api_jobs():
    return [jobs[jid].summary() for jid in reversed(recent_job_ids) if jid in jobs]


@app.get("/api/active")
async def api_active():
    """In-flight jobs grouped for fast UI lookup: {slug: {kind: status}}.

    The UI uses this to paint per-row badges (queued / running) without
    polling every job individually. Includes both queued and running.
    """
    active: dict[str, dict[str, str]] = {}
    queue_position = 0
    for jid in recent_job_ids:
        job = jobs.get(jid)
        if job is None or job.status in ("success", "error"):
            continue
        active.setdefault(job.slug, {})[job.kind] = job.status
        if job.status == "queued":
            queue_position += 1
    return {
        "active": active,
        "running_count": sum(1 for j in jobs.values() if j.status == "running"),
        "queued_count": queue_position,
    }


@app.get("/api/jobs/{job_id}")
async def api_job(job_id: str):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404)
    return {**job.summary(), "log": job.log}


@app.get("/api/jobs/{job_id}/stream")
async def api_job_stream(job_id: str):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404)

    q: asyncio.Queue[str] = asyncio.Queue(maxsize=4096)
    log_subscribers.setdefault(job_id, []).append(q)

    async def gen():
        try:
            if job.status in ("success", "error"):
                yield "event: end\ndata: already-finished\n\n"
                return
            while True:
                try:
                    line = await asyncio.wait_for(q.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if line == "__END__":
                    yield "event: end\ndata: done\n\n"
                    return
                yield f"data: {json.dumps(line)}\n\n"
        finally:
            try:
                log_subscribers.get(job_id, []).remove(q)
            except ValueError:
                pass

    # SSE-friendly headers: tell any intermediary proxy NOT to buffer.
    # Without these, nginx-style proxies often hold the response body
    # until the worker finishes — which looks like "the log isn't
    # working" because lines only appear at the end.
    headers = {
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",   # disables nginx response buffering
        "Connection": "keep-alive",
    }
    return StreamingResponse(gen(), media_type="text/event-stream", headers=headers)


def _kind_alias(kind: str) -> str:
    """Accept short aliases ('Q'/'A') in the URL for shorter thumb src."""
    if kind in ("Q", "q", "question_image"):
        return "question_image"
    if kind in ("A", "a", "answer_image"):
        return "answer_image"
    raise HTTPException(400, f"bad kind: {kind!r}")


@app.get("/api/image/{slug}/{kind}")
async def api_image(slug: str, kind: str, tab: str | None = None, thumb: int = 0,
                    v: str | None = None):
    """Stream an image for one (row slug, kind) pair, sourced from Drive.

    Serves the **512×384 resized** copy (what the game uses), so the UI shows
    exactly what ships. Falls back to the full-res original when a resized copy
    doesn't exist yet (and kicks off a background migration so it does next time).

    The image lives in the country folder; `tab` (the query param the UI sends)
    resolves to the COUNTRY code. 404 when the country folder has no such file.

    Pass `?thumb=1` to get a downsized JPEG (~256 px on longest edge, ~30 KB)
    suitable for the row thumbnails. The full-resolution path is used for the
    click-to-zoom modal.
    """
    if "/" in slug or ".." in slug or not slug.startswith("q"):
        raise HTTPException(400, "bad slug")
    kind = _kind_alias(kind)
    number = slug[1:]
    if not number.isdigit():
        raise HTTPException(400, "bad slug")
    name = drive_name(number, kind)
    # `tab` is required: it resolves the country folder. Defaulting it would
    # mis-route — a bare number like 250 exists under several countries, so a
    # missing tab could serve the wrong country's image. The UI always sends it.
    if not tab:
        raise HTTPException(400, "tab query param required")
    code = await asyncio.to_thread(_tab_country_code, _validate_tab(tab))

    # Prefer the 512×384 resized copy; fall back to the full-res original and
    # kick off a background migration so the resized exists next time.
    target = await asyncio.to_thread(find_resized, code, name)
    from_resized = target is not None   # resized files inherit public from the folder
    if target is None:
        ometa = await asyncio.to_thread(find_original, code, name)
        if ometa is None:
            raise HTTPException(404, f"no image for {code}/{slug}/{kind}")
        target = ometa
        _schedule_resized_migration(code, name, target)

    # Serve straight from Google's CDN: 302 to the Drive CDN URL so the browser
    # fetches the image directly (the CDN does any downscaling via `?sz=`) and
    # this server stays out of the byte path. Resized files inherit public
    # access from the Resized folder; an original served via fallback needs to
    # be made public itself.
    if not from_resized:
        await asyncio.to_thread(ensure_public, target.id)
    return RedirectResponse(
        _cdn_url(target.id, thumb=bool(thumb), version=v),
        status_code=302,
        headers={
            "Cache-Control": "private, max-age=600",
            "X-Drive-File-Id": target.id,
        },
    )


@app.post("/api/approve")
async def api_approve(payload: dict):
    """Approve an image by writing ✓ to the row's `Q/A Image Approved` column.

    Payload: {tab, row, slug, kind} where kind ∈ {"question_image",
    "answer_image"} (or "Q"/"A" aliases). No Drive mutation — the file stays in
    its country folder; approval is purely a sheet status now.

    404 if the file isn't in the country folder (generate it first).
    409 if it's already approved.
    """
    tab = _validate_tab(payload.get("tab"))
    try:
        row = int(payload["row"])
    except (KeyError, ValueError, TypeError):
        raise HTTPException(400, "row must be an integer")
    slug = str(payload.get("slug", "")).strip()
    if not slug or not slug.startswith("q"):
        raise HTTPException(400, "slug required (e.g. 'q1')")
    number = slug[1:]
    if not number.isdigit():
        raise HTTPException(400, "bad slug")
    kind = _kind_alias(str(payload.get("kind", "")).strip())
    field = "approved_q" if kind == "question_image" else "approved_r"
    name = drive_name(number, kind)

    code = await asyncio.to_thread(_tab_country_code, tab)
    meta = await asyncio.to_thread(find_original, code, name)
    if meta is None:
        raise HTTPException(404, f"{code}/{name} not on Drive — generate it first")

    letter = await asyncio.to_thread(_set_approval, tab, row, field, "✓")
    return {
        "ok": True,
        "tab": tab,
        "row": row,
        "slug": slug,
        "kind": kind,
        "drive_name": name,
        "approved_column": letter,
        "state": "approved",
    }


@app.post("/api/discard")
async def api_discard(payload: dict):
    """Trash an image and clear its approval status.

    Payload: {tab, row, slug, kind}. Trashes the file (and its resized copy) in
    the country folder — recoverable via Drive trash for ~30 days — and clears
    the row's approval flag.

    Returns 404 if the file isn't on Drive (nothing to discard).
    """
    tab = _validate_tab(payload.get("tab"))
    try:
        row = int(payload["row"])
    except (KeyError, ValueError, TypeError):
        raise HTTPException(400, "row must be an integer")
    slug = str(payload.get("slug", "")).strip()
    if not slug or not slug.startswith("q"):
        raise HTTPException(400, "slug required (e.g. 'q1')")
    number = slug[1:]
    if not number.isdigit():
        raise HTTPException(400, "bad slug")
    kind = _kind_alias(str(payload.get("kind", "")).strip())
    field = "approved_q" if kind == "question_image" else "approved_r"
    name = drive_name(number, kind)

    code = await asyncio.to_thread(_tab_country_code, tab)
    meta = await asyncio.to_thread(find_original, code, name)
    if meta is None:
        raise HTTPException(404, f"{code}/{name} not on Drive — nothing to discard")

    trashed = await asyncio.to_thread(get_client().trash, meta.id)
    # Best-effort: trash the resized copy too, and clear the approval flag.
    rmeta = await asyncio.to_thread(find_resized, code, name)
    if rmeta is not None:
        try:
            await asyncio.to_thread(get_client().trash, rmeta.id)
        except Exception:
            pass
    try:
        await asyncio.to_thread(_set_approval, tab, row, field, "")
    except Exception:
        pass
    return {
        "ok": True,
        "tab": tab,
        "row": row,
        "slug": slug,
        "kind": kind,
        "drive_name": trashed.name,
        "file_id": trashed.id,
        "state": "none",
    }
