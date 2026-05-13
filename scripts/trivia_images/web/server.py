"""Trivia images web UI (FastAPI sub-app, mounted by web/server.py at /trivia-images).

Surfaces the Brian tab of the trivia-questions sheet as a per-row table and
exposes the two stages of the trivia-images pipeline:

  - Generate Question:  col Q -> openart_image -> q{N}.<ext>
  - Generate Answer:    col R + q{N}.<ext> as reference -> openart_image -> q{N}_answer.<ext>

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
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response, StreamingResponse

REPO = Path(__file__).resolve().parents[3]
WEB_DIR = Path(__file__).resolve().parent
SA_PATH = Path.home() / ".google" / "claude-sheets-sa.json"

# Shared Drive client. Lives under tools/publishers/ alongside other
# publish-target helpers (the directory will host BaseTool wrappers too
# when those land — see tools/audio/piper_tts.py for the helper+BaseTool
# coexistence pattern).
sys.path.insert(0, str(REPO))
from tools.publishers.google_drive import FileMeta, get_client  # noqa: E402

# Trivia-images Drive layout. Approved is the visible root ("Question
# Images"); staging is the "WIP" subfolder underneath where freshly-
# generated images land before the human approves them.
APPROVED_FOLDER_ID = "1wENmER7aQ6wk23jP6wOggc7mviLAB_pw"
STAGING_FOLDER_ID = "1NMb2WeJp7HVsO-gzvOA-B0wK83uFta9k"


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


def state_for(name: str) -> tuple[str, Optional[FileMeta]]:
    """Resolve a single trivia-images filename across both folders.

    Approved wins when a file is in both (a manual-cleanup case). Approved
    is computed as "directly under APPROVED but NOT under STAGING", since
    the staging folder itself lives under approved and we don't want files
    in WIP to read as approved by parent transitivity.
    """
    client = get_client()
    approved = client.find_in_folder(APPROVED_FOLDER_ID, name)
    if approved and APPROVED_FOLDER_ID in approved.parent_ids \
       and STAGING_FOLDER_ID not in approved.parent_ids:
        return "approved", approved
    staging = client.find_in_folder(STAGING_FOLDER_ID, name)
    if staging:
        return "staging", staging
    return "none", None

SHEET_ID = "1Kh9Ai9-sKyyK1q24jVkQqeIz-Y-0rdNVIjPc2EF8hPk"
SHEET_TAB = "Brian"

# Column indices (0-based) in the Brian tab. Mirrors scripts/trivia_images/generate.py
# so the web UI and the CLI agree on what each column means.
COL_NUMBER = 2          # C  — used as the image slug `q{N}`
COL_COMPLETE = 3        # D  — '✓' on successful question-image generation
COL_CATEGORY = 4        # E
COL_MODE = 5            # F  — Speed Round / People Think That... etc.
COL_QUESTION_TEXT = 6   # G  — visible question on the trivia card
COL_PROMPT_Q = 16       # Q  — Question IMAGE prompt
COL_PROMPT_R = 17       # R  — Answer IMAGE (CORRECT) prompt
DATA_START_ROW = 3      # rows 1-2 are headers

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
def _build_sheets():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    creds = service_account.Credentials.from_service_account_file(
        str(SA_PATH),
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    return build("sheets", "v4", credentials=creds)


def _row_fields(row: list[str]) -> dict[str, str]:
    """Pad short rows and extract the cells we care about."""
    padded = row + [""] * (max(0, COL_PROMPT_R + 1 - len(row)))
    return {
        "number": (padded[COL_NUMBER] or "").strip(),
        "complete": (padded[COL_COMPLETE] or "").strip(),
        "category": (padded[COL_CATEGORY] or "").strip(),
        "mode": (padded[COL_MODE] or "").strip(),
        "question": (padded[COL_QUESTION_TEXT] or "").strip(),
        "prompt_q": (padded[COL_PROMPT_Q] or "").strip(),
        "prompt_r": (padded[COL_PROMPT_R] or "").strip(),
    }


def _drive_state_for(number: str, kind: str) -> dict:
    """Resolve Drive state for one (number, kind) pair.

    Returns {state, drive_name, file_id, modified_time}. `state` is one of
    'approved' | 'staging' | 'none'. file_id + modified_time, when present,
    let the frontend bust its image cache when the underlying file changes.
    """
    name = drive_name(number, kind)
    try:
        state, meta = state_for(name)
    except Exception:
        # Drive transient — surface 'none' rather than crash the row list.
        state, meta = "none", None
    out = {"state": state, "drive_name": name, "file_id": None, "modified_time": None}
    if meta is not None:
        out["file_id"] = meta.id
        out["modified_time"] = meta.modified_time
    return out


def read_rows(min_row: int = DATA_START_ROW, max_row: int = 1000) -> list[dict]:
    sheets = _build_sheets()
    rng = f"{SHEET_TAB}!A{min_row}:R{max_row}"
    resp = sheets.spreadsheets().values().get(spreadsheetId=SHEET_ID, range=rng).execute()
    raw = resp.get("values", [])
    # Warm the Drive listing once per /api/rows call — state_for goes through
    # the per-folder cache so 241 rows = 2 Drive API calls, not 482.
    rows: list[dict] = []
    for i, v in enumerate(raw):
        sheet_row = min_row + i
        f = _row_fields(v)
        if not f["number"]:
            continue
        slug = f"q{f['number']}"
        q_drive = _drive_state_for(f["number"], "question_image")
        a_drive = _drive_state_for(f["number"], "answer_image")
        rows.append({
            "row": sheet_row,
            "number": f["number"],
            "slug": slug,
            "category": f["category"],
            "mode": f["mode"],
            "question": f["question"],
            "prompt_q": f["prompt_q"],
            "prompt_r": f["prompt_r"],
            "question_complete": f["complete"] == "✓",
            "question_drive": q_drive,
            "answer_drive": a_drive,
        })
    return rows


def _mark_question_complete(row: int) -> None:
    sheets = _build_sheets()
    sheets.spreadsheets().values().update(
        spreadsheetId=SHEET_ID,
        range=f"{SHEET_TAB}!D{row}",
        valueInputOption="USER_ENTERED",
        body={"values": [["✓"]]},
    ).execute()


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
    try:
        result = tool.execute(inputs)
        if not result.success:
            raise RuntimeError(result.error or "openart_image returned no error string")

        saved = result.data.get("saved_paths") or []
        if not saved:
            raise RuntimeError("openart_image returned no saved_paths")
        saved_path = Path(saved[0])
        _emit(job, f"  ✓ generated ({result.duration_seconds:.1f}s)")

        # Upload to staging. The Drive name is the canonical {N}{Q|A}.png
        # regardless of what extension the CDN gave us.
        dest_name = drive_name(job.slug.lstrip("q"), job.kind)
        client = get_client()
        meta = client.upload_or_replace(STAGING_FOLDER_ID, dest_name, saved_path)
        job.extra["drive_file_id"] = meta.id
        job.extra["drive_name"] = meta.name
        job.output_path = f"drive://{meta.name}"
        _emit(job, f"  ✓ uploaded to staging: {meta.name} (id={meta.id})")
    finally:
        # Always unlink the tempfile(s). Both the path we allocated and the
        # path the driver actually wrote (it may have rewritten the extension
        # from .jpg to .png/.webp).
        for p in {output_path, saved_path} - {None}:
            try:
                if p and p.exists():
                    p.unlink()
            except OSError:
                pass


async def _worker(job: Job) -> None:
    # Queue behind any in-flight job — Chromium can only run one at a time.
    async with worker_lock:
        await _run_job(job)


def _stage_reference_from_drive(slug: str) -> Path:
    """Download the question image for `slug` from Drive to a tempfile.

    The OpenArt driver needs a real local path for Playwright's
    set_input_files. We pull bytes from Drive (approved or staging — either
    is fine as a reference) and write them to a tempfile that the caller
    is responsible for deleting.

    Raises if the question image isn't on Drive in either folder.
    """
    import tempfile
    name = drive_name(slug.lstrip("q"), "question_image")
    state, meta = state_for(name)
    if state == "none" or meta is None:
        raise RuntimeError(
            f"question image {name} not found on Drive — generate it first"
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
                raise RuntimeError("col Q prompt is empty for this row")
            await asyncio.to_thread(_run_generation_sync, job, prompt, None)
            # Mark col D = ✓ after the file lands (matches generate.py default).
            if not job.extra.get("no_mark"):
                try:
                    await asyncio.to_thread(_mark_question_complete, job.row)
                    _emit(job, f"  ✓ marked Brian!D{job.row} = ✓")
                except Exception as e:
                    _emit(job, f"  ⚠ sheet update failed: {e}")
        elif job.kind == "answer_image":
            prompt = job.extra.get("prompt", "").strip()
            if not prompt:
                raise RuntimeError("col R prompt is empty for this row")
            _emit(job, "  → downloading reference (question image) from Drive…")
            ref_tempfile = await asyncio.to_thread(_stage_reference_from_drive, job.slug)
            await asyncio.to_thread(
                _run_generation_sync, job, prompt, ref_tempfile,
            )
        else:
            raise RuntimeError(f"unknown job kind: {job.kind}")
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


@app.get("/")
async def index():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/health")
async def api_health():
    return {
        "ok": True,
        "repo": str(REPO),
        "sheet_id": SHEET_ID,
        "sheet_tab": SHEET_TAB,
        "approved_folder_id": APPROVED_FOLDER_ID,
        "staging_folder_id": STAGING_FOLDER_ID,
        "sa_path": str(SA_PATH),
        "sa_present": SA_PATH.exists(),
        "python": sys.executable,
    }


@app.get("/api/rows")
async def api_rows():
    try:
        return read_rows()
    except Exception as e:
        raise HTTPException(500, f"sheet read failed: {e}")


@app.post("/api/run")
async def api_run(payload: dict):
    kind = payload.get("kind", "")
    if kind not in ("question_image", "answer_image"):
        raise HTTPException(400, "kind must be 'question_image' or 'answer_image'")
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

    extra: dict = {"prompt": prompt}
    if kind == "question_image":
        extra["no_mark"] = bool(payload.get("no_mark", False))

    job = Job(id=uuid.uuid4().hex[:8], kind=kind, row=row, slug=slug, extra=extra)
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

    return StreamingResponse(gen(), media_type="text/event-stream")


def _kind_alias(kind: str) -> str:
    """Accept short aliases ('Q'/'A') in the URL for shorter thumb src."""
    if kind in ("Q", "q", "question_image"):
        return "question_image"
    if kind in ("A", "a", "answer_image"):
        return "answer_image"
    raise HTTPException(400, f"bad kind: {kind!r}")


@app.get("/api/image/{slug}/{kind}")
async def api_image(slug: str, kind: str):
    """Stream an image for one (row slug, kind) pair, sourced from Drive.

    Approved wins over staging (the canonical version is what the UI should
    show by default). 404 when neither folder has it — there is no local
    library to fall back to; Drive is the source of truth.
    """
    if "/" in slug or ".." in slug or not slug.startswith("q"):
        raise HTTPException(400, "bad slug")
    kind = _kind_alias(kind)
    number = slug[1:]
    if not number.isdigit():
        raise HTTPException(400, "bad slug")
    name = drive_name(number, kind)

    state, meta = await asyncio.to_thread(state_for, name)
    if meta is None:
        raise HTTPException(404, f"no image for {slug}/{kind}")

    data, mime, mtime = await asyncio.to_thread(
        get_client().download_bytes, meta.id, meta.modified_time,
    )
    return Response(
        content=data,
        media_type=mime,
        headers={
            "Cache-Control": "private, max-age=300",
            "X-Drive-State": state,
            "X-Drive-File-Id": meta.id,
            "X-Drive-Modified": mtime,
        },
    )


@app.post("/api/approve")
async def api_approve(payload: dict):
    """Move a staged image from STAGING to APPROVED.

    Payload: {row, kind} where kind ∈ {"question_image","answer_image"} (or
    "Q"/"A" aliases). Looks up `{N}{Q|A}.png` in the staging folder and
    re-parents it to approved.

    409 if already approved (nothing to do but worth flagging to the UI).
    404 if neither folder has it (the image doesn't exist yet or Drive is
    out of sync; refresh /api/rows).
    """
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
    name = drive_name(number, kind)

    state, meta = await asyncio.to_thread(state_for, name)
    if state == "approved":
        raise HTTPException(409, f"{name} is already approved")
    if state == "none" or meta is None:
        raise HTTPException(404, f"{name} not found in staging — generate it first")

    new_meta = await asyncio.to_thread(
        get_client().move, meta.id,
        add_parents=[APPROVED_FOLDER_ID],
        remove_parents=[STAGING_FOLDER_ID],
    )
    return {
        "ok": True,
        "row": row,
        "slug": slug,
        "kind": kind,
        "drive_name": new_meta.name,
        "file_id": new_meta.id,
        "modified_time": new_meta.modified_time,
        "state": "approved",
    }


@app.post("/api/discard")
async def api_discard(payload: dict):
    """Trash a STAGING image (recoverable via Drive trash for ~30 days).

    Payload: {row, slug, kind}. Refuses to discard an approved file — that
    would be a destructive operation on canonical content and should be
    done explicitly through Drive's UI if intended.

    Returns 404 if the file isn't on Drive (nothing to discard), 409 if
    it's approved (refused).
    """
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
    name = drive_name(number, kind)

    state, meta = await asyncio.to_thread(state_for, name)
    if state == "none" or meta is None:
        raise HTTPException(404, f"{name} not on Drive — nothing to discard")
    if state == "approved":
        raise HTTPException(
            409,
            f"{name} is already approved — discard from Drive directly if intended",
        )

    trashed = await asyncio.to_thread(get_client().trash, meta.id)
    return {
        "ok": True,
        "row": row,
        "slug": slug,
        "kind": kind,
        "drive_name": trashed.name,
        "file_id": trashed.id,
        "state": "none",
    }
