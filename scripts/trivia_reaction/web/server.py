"""Trivia reaction web UI (FastAPI sub-app, mounted by web/server.py at /trivia-reaction).

Surfaces the TriviaReactionQueue sheet as a per-row table and exposes the
pipeline stages as button-driven jobs:

  - Select:           select_row.py --day N           -> brief.json + queue upsert
  - Generate clip:    openart_generate.py <slug>      -> Seedance avatar mp4
  - Assemble+render:  assemble.py + transcribe.py +
                      stage meta.json into public/ +
                      npx remotion render TriviaWithBg -> final.mp4
  - Publish:          publish.py <slug>               -> Drive + Queue!C/J writes

Mounted by web/server.py — do not run this app standalone. The index.html's
<base href="/trivia-reaction/"> would resolve incorrectly without the mount.

The script-director stage is agent-driven (the agent drafts script.json from
the brief). This UI surfaces the script.json contents and lets a human edit
the OpenArt prompt + the three VO beats; saving syncs Hook/Fact/Kicker VO
back to Queue!G/H/I. It does NOT run the agent — that happens out-of-band.

Per user memory: never auto-run publish.py — Publish is a deliberate button.

The server only binds 127.0.0.1.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

REPO = Path(__file__).resolve().parents[3]
WEB_DIR = Path(__file__).resolve().parent
PKG_DIR = Path(__file__).resolve().parent.parent   # scripts/trivia_reaction/
SA_PATH = Path.home() / ".google" / "claude-sheets-sa.json"

sys.path.insert(0, str(REPO))
sys.path.insert(0, str(PKG_DIR))
from scripts.trivia_reaction import daily_trivia, queue_row  # noqa: E402
from scripts.trivia_reaction.paths import project_dir  # noqa: E402

LIBRARY_DIR = REPO / "scripts" / "trivia_reaction" / "library" / "clips"
REMOTION_PUBLIC = REPO / "remotion-composer" / "public"
DRIVE_FOLDER_ID = "1uDneOUH21xUqh4oifQTh5sqgIVk6EREg"
QUEUE_SHEET_URL = (
    f"https://docs.google.com/spreadsheets/d/{queue_row.QUEUE_SHEET}/edit"
)

JobKind = Literal["select", "generate", "publish", "mark_published"]
JobStatus = Literal["queued", "running", "success", "error"]


@dataclass
class Job:
    id: str
    kind: JobKind
    slug: str                          # "" for select jobs that haven't resolved yet
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
            "slug": self.slug,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "output_path": self.output_path,
            "log_lines": len(self.log),
            "day": self.extra.get("day"),
        }


jobs: dict[str, Job] = {}
recent_job_ids: deque[str] = deque(maxlen=200)
log_subscribers: dict[str, list[asyncio.Queue[str]]] = {}

# Single global worker lock. The openart_generate path runs Playwright +
# Chromium with a shared login-state file; render runs npx remotion which
# saturates CPU. Run jobs one at a time.
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
# Sheet reads
# ---------------------------------------------------------------------------
def _ro_sheets():
    return queue_row.build_sheets(write=False)


def _explanation_cache() -> dict[int, str]:
    """Day -> CorrectExplanation EN. One DailyTriviaConfig pass per /api/rows
    call. Cached for the duration of the request via the simple dict return —
    callers that want stale-tolerant data should hold the reference instead of
    refetching."""
    sheets = daily_trivia.build_sheets()
    # We don't know which Days are in the Queue without reading it first, so
    # bulk-read DailyTriviaConfig columns A-L for a generous row range and
    # pluck out the rows we need. This is one Sheets call instead of N.
    tab = daily_trivia.DAILY_TRIVIA_TAB_DEV
    start = daily_trivia.DAILY_TRIVIA_DATA_START_ROW
    end = start + 400
    rng = f"'{tab}'!A{start}:L{end}"
    r = sheets.spreadsheets().values().get(
        spreadsheetId=daily_trivia.DAILY_TRIVIA_SHEET, range=rng,
    ).execute()
    out: dict[int, str] = {}
    # Resolve Uids lazily — only call resolve_uid once we know the row's day.
    for raw in r.get("values", []):
        row = list(raw) + [""] * (12 - len(raw))
        try:
            day = int((row[1] or "").strip())
        except ValueError:
            continue
        uid_expl = (row[11] or "").strip()
        if not uid_expl:
            out[day] = ""
            continue
        out[day] = daily_trivia.resolve_uid(sheets, uid_expl)
    return out


def _project_state(slug: str) -> dict:
    """File-state snapshot for one project. Cheap — just os.path.exists checks."""
    if not slug:
        return {
            "brief_exists": False, "script_exists": False, "clip_exists": False,
            "bg_exists": False, "render_exists": False, "render_path": None,
            "render_mtime": None,
        }
    project = project_dir(slug)
    brief = project / "artifacts" / "brief.json"
    script = project / "artifacts" / "script.json"
    bg = project / "assets" / "video" / "bg.mp4"
    # Render is named after the slug — self-identifying when downloaded
    # or moved out of the workspace.
    render_final = project / "renders" / f"{slug}.mp4"
    render_path = render_final if render_final.exists() else None
    clip = LIBRARY_DIR / f"{slug}.mp4"
    return {
        "brief_exists": brief.exists(),
        "script_exists": script.exists(),
        "clip_exists": clip.exists(),
        "bg_exists": bg.exists(),
        "render_exists": render_path is not None,
        "render_path": str(render_path.relative_to(REPO)) if render_path else None,
        "render_mtime": render_path.stat().st_mtime if render_path else None,
    }


def read_rows() -> list[dict]:
    """Read Queue rows; enrich each with the CorrectExplanation and local file state."""
    sheets = _ro_sheets()
    rows = queue_row.read_queue_bulk(sheets)
    explanations = _explanation_cache()
    out: list[dict] = []
    for r in rows:
        try:
            day = int((r.get("day") or "").strip())
        except (ValueError, AttributeError):
            day = None
        slug = (r.get("slug") or "").strip()
        d = {
            **r,
            "day_int": day,
            "correct_explanation_en": explanations.get(day, "") if day is not None else "",
            "files": _project_state(slug),
        }
        out.append(d)
    # Stable order: by day ascending. Rows with no day fall to the end.
    out.sort(key=lambda d: (d["day_int"] is None, d["day_int"] or 0))
    return out


# ---------------------------------------------------------------------------
# Sub-process runner
# ---------------------------------------------------------------------------
async def _run_subprocess(job: Job, cmd: list[str], cwd: Path) -> int:
    _emit(job, f"$ {' '.join(cmd)}")
    _emit(job, f"  cwd={cwd}")
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env={**os.environ},
    )
    assert proc.stdout is not None
    while True:
        line = await proc.stdout.readline()
        if not line:
            break
        _emit(job, line.decode(errors="replace"))
    rc = await proc.wait()
    _emit(job, f"(exit code: {rc})")
    return rc


# ---------------------------------------------------------------------------
# Job runners
# ---------------------------------------------------------------------------
async def _run_select(job: Job) -> None:
    day = job.extra.get("day")
    if day is None:
        raise RuntimeError("select job missing 'day'")
    slug_override = (job.extra.get("slug_override") or "").strip()
    py = sys.executable
    cmd = [py, "scripts/trivia_reaction/select_row.py", "--day", str(day)]
    if slug_override:
        cmd += ["--slug", slug_override]
    rc = await _run_subprocess(job, cmd, REPO)
    if rc != 0:
        raise RuntimeError(f"select_row failed (exit {rc})")
    # Job.slug isn't known up-front for select (the script derives it). The
    # UI's loadRows() will discover the new Queue row on the next refresh.


async def _run_generate(job: Job) -> None:
    """Full clip-to-render chain — openart_generate → assemble → transcribe
    → remotion render. Triggered by the 'Generate' button. On Draft rows
    this flips status to 'Ready to review' as soon as the chain starts;
    on success the row ends at 'Ready to publish'."""
    if not job.slug:
        raise RuntimeError("generate job missing 'slug'")
    py = sys.executable

    # Lock the row to 'Ready to review' at the start — signals "user committed
    # to spending OpenArt credits on this row; chain is in flight". This is
    # idempotent if the row was already in Ready to review.
    try:
        ws = await asyncio.to_thread(queue_row.build_sheets, True)
        existing = await asyncio.to_thread(_find_row_by_slug, ws, job.slug)
        if existing:
            await asyncio.to_thread(
                queue_row.update_cells, ws, existing,
                status=queue_row.STATUS_READY_TO_REVIEW,
            )
            _emit(job, f"  Queue!C{existing} -> {queue_row.STATUS_READY_TO_REVIEW}")
    except Exception as e:  # noqa: BLE001
        _emit(job, f"  ⚠ Queue status lock skipped: {e}")

    _emit(job, "=== Phase 1/5: openart_generate (Seedance clip) ===")
    variants = int(job.extra.get("variants", 1))
    cmd = [
        py, "scripts/trivia_reaction/openart_generate.py", job.slug,
        "--headless",
        "--variants", str(variants),
    ]
    if job.extra.get("force"):
        cmd.append("--force")
    rc = await _run_subprocess(job, cmd, REPO)
    if rc != 0:
        raise RuntimeError(f"openart_generate failed (exit {rc})")

    _emit(job, "=== Phase 2/5: assemble (clip -> bg.mp4 + meta.json) ===")
    rc = await _run_subprocess(
        job, [py, "scripts/trivia_reaction/assemble.py", job.slug], REPO,
    )
    if rc != 0:
        raise RuntimeError(f"assemble failed (exit {rc})")

    _emit(job, "=== Phase 3/5: transcribe (bg.mp4 -> words.json) ===")
    rc = await _run_subprocess(
        job,
        [py, "scripts/common/transcribe.py", job.slug,
         "--root", "projects/trivia-reaction"],
        REPO,
    )
    if rc != 0:
        raise RuntimeError(f"transcribe failed (exit {rc})")

    _emit(job, "=== Phase 4/5: stage meta.json into remotion-composer/public/ ===")
    src_meta = project_dir(job.slug) / "assets" / "meta.json"
    if not src_meta.exists():
        raise RuntimeError(f"assemble didn't write meta.json at {src_meta}")
    REMOTION_PUBLIC.mkdir(parents=True, exist_ok=True)
    shutil.copy(src_meta, REMOTION_PUBLIC / "meta.json")
    _emit(job, f"  staged {src_meta.relative_to(REPO)} -> remotion-composer/public/meta.json")

    _emit(job, "=== Phase 5/5: remotion render (TriviaWithBg) ===")
    out_path = project_dir(job.slug) / "renders" / f"{job.slug}.mp4"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rc = await _run_subprocess(
        job,
        ["npx", "remotion", "render", "src/index-trivia.tsx", "TriviaWithBg", str(out_path)],
        REPO / "remotion-composer",
    )
    if rc != 0:
        raise RuntimeError(f"remotion render failed (exit {rc})")
    if not out_path.exists():
        raise RuntimeError(f"render reported success but {out_path} is missing")
    job.output_path = str(out_path.relative_to(REPO))
    _emit(job, "")
    _emit(job, f"render ready for local review: {out_path.relative_to(REPO)}")
    _emit(job, "review frames + verify captions before clicking Publish.")

    # Flip Queue!C -> Ready to publish.
    try:
        ws = queue_row.build_sheets(write=True)
        existing = next(
            (r for r in queue_row.read_queue_bulk(ws) if r.get("slug") == job.slug),
            None,
        )
        if existing:
            queue_row.update_cells(
                ws, existing["row"], status=queue_row.STATUS_READY_TO_PUBLISH,
            )
            _emit(job, f"  Queue!C{existing['row']} -> {queue_row.STATUS_READY_TO_PUBLISH}")
    except Exception as e:  # noqa: BLE001
        _emit(job, f"  ⚠ Queue status update skipped: {e}")


async def _run_publish(job: Job) -> None:
    """Upload final.mp4 to Drive and write the link back. publish.py keeps
    Status at 'Ready to publish' — the row only flips to 'Published' via
    the separate Mark-as-Published button, which is the human's signal
    that the Instagram post is actually live."""
    if not job.slug:
        raise RuntimeError("publish job missing 'slug'")
    py = sys.executable
    rc = await _run_subprocess(
        job, [py, "scripts/trivia_reaction/publish.py", job.slug], REPO,
    )
    if rc != 0:
        raise RuntimeError(f"publish failed (exit {rc})")


async def _run_mark_published(job: Job) -> None:
    """Status-only write: 'Ready to publish' -> 'Published'. Human signal
    that the Instagram post is live."""
    if not job.slug:
        raise RuntimeError("mark_published job missing 'slug'")
    ws = await asyncio.to_thread(queue_row.build_sheets, True)
    existing = await asyncio.to_thread(_find_row_by_slug, ws, job.slug)
    if not existing:
        raise RuntimeError(f"no Queue row for slug={job.slug!r}")
    await asyncio.to_thread(
        queue_row.update_cells, ws, existing, status=queue_row.STATUS_PUBLISHED,
    )
    _emit(job, f"Queue!C{existing} -> {queue_row.STATUS_PUBLISHED}")


async def _worker(job: Job) -> None:
    async with worker_lock:
        job.status = "running"
        job.started_at = datetime.now(timezone.utc).isoformat()
        try:
            if job.kind == "select":
                await _run_select(job)
            elif job.kind == "generate":
                await _run_generate(job)
            elif job.kind == "publish":
                await _run_publish(job)
            elif job.kind == "mark_published":
                await _run_mark_published(job)
            else:
                raise RuntimeError(f"unknown job kind: {job.kind}")
            job.status = "success"
        except Exception as e:
            job.status = "error"
            job.error = str(e)
            _emit(job, f"FAILED: {e}")
        finally:
            job.finished_at = datetime.now(timezone.utc).isoformat()
            for q in log_subscribers.get(job.id, []):
                try:
                    q.put_nowait("__END__")
                except asyncio.QueueFull:
                    pass


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="Trivia reaction runner")


@app.get("/")
async def index():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/health")
async def api_health():
    return {
        "ok": True,
        "repo": str(REPO),
        "queue_sheet_id": queue_row.QUEUE_SHEET,
        "queue_sheet_url": QUEUE_SHEET_URL,
        "drive_folder_id": DRIVE_FOLDER_ID,
        "drive_folder_url": f"https://drive.google.com/drive/folders/{DRIVE_FOLDER_ID}",
        "sa_path": str(SA_PATH),
        "sa_present": SA_PATH.exists(),
        "python": sys.executable,
    }


@app.get("/api/rows")
async def api_rows():
    try:
        return await asyncio.to_thread(read_rows)
    except Exception as e:
        raise HTTPException(500, f"sheet read failed: {e}")


def _find_row_by_slug(sheets, slug: str) -> int | None:
    for r in queue_row.read_queue_bulk(sheets):
        if (r.get("slug") or "").strip() == slug:
            return r["row"]
    return None


@app.post("/api/queue_status")
async def api_queue_status(payload: dict):
    """Manually set Queue!C for a slug. Useful when the agent needs to
    walk a row backward to Draft for another pass."""
    slug = (payload.get("slug") or "").strip()
    status = (payload.get("status") or "").strip()
    if not slug or not status:
        raise HTTPException(400, "slug and status required")
    valid = {
        queue_row.STATUS_DRAFT, queue_row.STATUS_READY_TO_REVIEW,
        queue_row.STATUS_READY_TO_PUBLISH, queue_row.STATUS_PUBLISHED,
    }
    if status not in valid:
        raise HTTPException(400, f"invalid status {status!r}; pick one of {sorted(valid)}")
    try:
        ws = await asyncio.to_thread(queue_row.build_sheets, True)
        target = await asyncio.to_thread(_find_row_by_slug, ws, slug)
        if not target:
            raise HTTPException(404, f"no Queue row for slug={slug!r}")
        await asyncio.to_thread(queue_row.update_cells, ws, target, status=status)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"sheet write failed: {e}")
    return {"ok": True, "slug": slug, "status": status}


@app.post("/api/run")
async def api_run(payload: dict):
    kind = payload.get("kind", "")
    if kind not in ("select", "generate", "publish", "mark_published"):
        raise HTTPException(400, f"bad kind: {kind!r}")
    slug = (payload.get("slug") or "").strip()
    extra: dict = {}

    if kind == "select":
        try:
            extra["day"] = int(payload["day"])
        except (KeyError, ValueError, TypeError):
            raise HTTPException(400, "select requires integer 'day'")
        extra["slug_override"] = (payload.get("slug_override") or "").strip()
    else:
        if not slug:
            raise HTTPException(400, f"{kind} requires 'slug'")
        if kind == "generate":
            extra["variants"] = int(payload.get("variants", 1))
            extra["force"] = bool(payload.get("force", False))
        elif kind == "publish":
            # Refuse if no render exists yet — saves a confusing publish.py crash.
            state = _project_state(slug)
            if not state["render_exists"]:
                raise HTTPException(
                    409,
                    f"no render to publish for {slug} — run Generate first",
                )

    job = Job(id=uuid.uuid4().hex[:8], kind=kind, slug=slug, extra=extra)
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
    """In-flight jobs grouped by slug for fast row-badge painting."""
    by_slug: dict[str, dict[str, str]] = {}
    by_day: dict[int, dict[str, str]] = {}
    for jid in recent_job_ids:
        job = jobs.get(jid)
        if job is None or job.status in ("success", "error"):
            continue
        if job.slug:
            by_slug.setdefault(job.slug, {})[job.kind] = job.status
        elif job.kind == "select":
            day = job.extra.get("day")
            if isinstance(day, int):
                by_day.setdefault(day, {})[job.kind] = job.status
    running = sum(1 for j in jobs.values() if j.status == "running")
    queued = sum(1 for j in jobs.values() if j.status == "queued")
    return {
        "by_slug": by_slug,
        "by_day": by_day,
        "running_count": running,
        "queued_count": queued,
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

    headers = {
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    return StreamingResponse(gen(), media_type="text/event-stream", headers=headers)


@app.get("/api/render/{slug}")
async def api_render(slug: str):
    if "/" in slug or ".." in slug:
        raise HTTPException(400, "bad slug")
    state = _project_state(slug)
    if not state["render_exists"]:
        raise HTTPException(404, f"no render for {slug}")
    return FileResponse(REPO / state["render_path"], media_type="video/mp4")
