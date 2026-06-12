"""Trivia Captain 2T1L web UI (FastAPI sub-app, mounted at /trivia-captain-2t1l).

Sister of scripts/trivia_captain/web/server.py — same shape and button vocabulary
(Add / Generate / Review / Publish / Mark as Published), different pipeline:
"Captain's Two Truths & a Lie" on the Posts_2T1L tab of the @dailytrivia.tc Post
Calendar, single-15s-clip + the kinetic TriviaTwoTruthsK3 overlay.

Buttons → jobs:
  - Add:       add_row.py (form: place + 3 claims + lie + demographic) -> Draft row
  - Generate:  [build_prompt if needed] -> openart_generate -> assemble -> render
  - Publish:   publish.py <slug> -> Drive + Queue Drive Link / Status writes
  - Mark as Published: status-only flip (human signal the post is live)

Mounted by the parent launcher — the index.html's <base href> resolves under the
mount. The server only binds 127.0.0.1. Never auto-runs publish.
"""
from __future__ import annotations

import asyncio
import json
import os
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
SA_PATH = Path.home() / ".google" / "claude-sheets-sa.json"

sys.path.insert(0, str(REPO))
from scripts.trivia_captain_2t1l import queue_row  # noqa: E402
from scripts.trivia_captain_2t1l.paths import project_dir  # noqa: E402

QUEUE_SHEET_URL = f"https://docs.google.com/spreadsheets/d/{queue_row.QUEUE_SHEET}/edit"

JobKind = Literal["add", "generate", "publish", "mark_published"]
JobStatus = Literal["queued", "running", "success", "error"]


@dataclass
class Job:
    id: str
    kind: JobKind
    slug: str
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
            "id": self.id, "kind": self.kind, "slug": self.slug, "status": self.status,
            "created_at": self.created_at, "started_at": self.started_at,
            "finished_at": self.finished_at, "error": self.error,
            "output_path": self.output_path, "log_lines": len(self.log),
        }


jobs: dict[str, Job] = {}
recent_job_ids: deque[str] = deque(maxlen=200)
log_subscribers: dict[str, list[asyncio.Queue[str]]] = {}
worker_lock = asyncio.Lock()


def _emit(job: Job, line: str) -> None:
    line = line.rstrip("\n")
    job.log.append(line)
    for q in log_subscribers.get(job.id, []):
        try:
            q.put_nowait(line)
        except asyncio.QueueFull:
            pass


def _ro_sheets():
    return queue_row.build_sheets(write=False)


def _project_state(slug: str) -> dict:
    if not slug:
        return {"clip_exists": False, "bg_exists": False, "render_exists": False,
                "render_path": None, "render_mtime": None}
    p = project_dir(slug)
    clip = p / "assets" / "video" / "clip.mp4"
    bg = p / "assets" / "video" / "bg.mp4"
    render = p / "renders" / f"{slug}.mp4"
    rp = render if render.exists() else None
    return {
        "clip_exists": clip.exists(),
        "bg_exists": bg.exists(),
        "render_exists": rp is not None,
        "render_path": str(rp.relative_to(REPO)) if rp else None,
        "render_mtime": rp.stat().st_mtime if rp else None,
    }


def read_rows() -> list[dict]:
    sheets = _ro_sheets()
    rows = queue_row.read_queue_bulk(sheets)
    out = []
    for r in rows:
        slug = (r.get("slug") or "").strip()
        try:
            idx = int((r.get("idx") or "").strip())
        except (ValueError, AttributeError):
            idx = None
        out.append({**r, "idx_int": idx,
                    "prompt_set": bool((r.get("openart_prompt") or "").strip()),
                    "files": _project_state(slug)})
    out.sort(key=lambda d: (d["idx_int"] is None, d["idx_int"] or 0))
    return out


async def _run_subprocess(job: Job, cmd: list[str], cwd: Path) -> int:
    _emit(job, f"$ {' '.join(cmd)}")
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=str(cwd), stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT, env={**os.environ},
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


def _find_row_by_slug(sheets, slug: str) -> int | None:
    return queue_row.find_row_by_slug(sheets, slug)


async def _run_add(job: Job) -> None:
    e = job.extra
    cmd = [sys.executable, "scripts/trivia_captain_2t1l/add_row.py",
           "--slug", e["slug"], "--place", e["place"],
           "--claim1", e["claim1"], "--claim2", e["claim2"], "--claim3", e["claim3"],
           "--lie", str(e["lie"]), "--lie-model", e.get("lie_model", "invented"),
           "--demographic", e.get("demographic", "most men"),
           "--theme", e.get("theme", "goldround")]
    if (rc := await _run_subprocess(job, cmd, REPO)) != 0:
        raise RuntimeError(f"add_row failed (exit {rc})")


async def _run_generate(job: Job) -> None:
    if not job.slug:
        raise RuntimeError("generate job missing 'slug'")
    py = sys.executable

    # Phase 1/4: build the prompt + labels + caption if not yet set.
    sheets = await asyncio.to_thread(queue_row.build_sheets, False)
    row = await asyncio.to_thread(_find_row_by_slug, sheets, job.slug)
    prompt_set = bool((queue_row.read_queue_row(sheets, row).get("openart_prompt") or "").strip()) if row else False
    if not prompt_set:
        _emit(job, "=== Phase 1/4: build_prompt (Seedance prompt + labels + caption) ===")
        if (rc := await _run_subprocess(job, [py, "scripts/trivia_captain_2t1l/build_prompt.py", job.slug], REPO)) != 0:
            raise RuntimeError(f"build_prompt failed (exit {rc})")
    else:
        _emit(job, "=== Phase 1/4: prompt already set — skipping build_prompt ===")

    # NOTE: run HEADED (no --headless). Headless dropped the saved "Captain
    # Archibald" character (generic person rendered); the visible browser
    # reliably attaches the character. A Chromium window will open during this.
    _emit(job, "=== Phase 2/4: openart_generate (single 15s clip, headed) ===")
    cmd = [py, "scripts/trivia_captain_2t1l/openart_generate.py", job.slug]
    if job.extra.get("force"):
        cmd.append("--force")
    if (rc := await _run_subprocess(job, cmd, REPO)) != 0:
        raise RuntimeError(f"openart_generate failed (exit {rc})")

    _emit(job, "=== Phase 3/4: assemble (normalize + reveal times -> props.json) ===")
    if (rc := await _run_subprocess(job, [py, "scripts/trivia_captain_2t1l/assemble.py", job.slug], REPO)) != 0:
        raise RuntimeError(f"assemble failed (exit {rc})")

    _emit(job, "=== Phase 4/4: render (TriviaTwoTruthsK3 overlay) ===")
    if (rc := await _run_subprocess(job, [py, "scripts/trivia_captain_2t1l/render.py", job.slug], REPO)) != 0:
        raise RuntimeError(f"render failed (exit {rc})")

    out_path = project_dir(job.slug) / "renders" / f"{job.slug}.mp4"
    if not out_path.exists():
        raise RuntimeError(f"render reported success but {out_path} is missing")
    job.output_path = str(out_path.relative_to(REPO))
    _emit(job, f"\nrender ready for local review: {out_path.relative_to(REPO)}")
    _emit(job, "review frames + verify banner sync before clicking Publish.")

    try:
        ws = queue_row.build_sheets(write=True)
        r = _find_row_by_slug(ws, job.slug)
        if r:
            queue_row.update_cells(ws, r, status=queue_row.STATUS_READY_TO_PUBLISH)
            _emit(job, f"  status -> {queue_row.STATUS_READY_TO_PUBLISH}")
    except Exception as ex:  # noqa: BLE001
        _emit(job, f"  ⚠ status update skipped: {ex}")


async def _run_publish(job: Job) -> None:
    if not job.slug:
        raise RuntimeError("publish job missing 'slug'")
    if (rc := await _run_subprocess(
            job, [sys.executable, "scripts/trivia_captain_2t1l/publish.py", job.slug], REPO)) != 0:
        raise RuntimeError(f"publish failed (exit {rc})")


async def _run_mark_published(job: Job) -> None:
    ws = await asyncio.to_thread(queue_row.build_sheets, True)
    r = await asyncio.to_thread(_find_row_by_slug, ws, job.slug)
    if not r:
        raise RuntimeError(f"no Queue row for slug={job.slug!r}")
    await asyncio.to_thread(queue_row.update_cells, ws, r, status=queue_row.STATUS_PUBLISHED)
    _emit(job, f"status -> {queue_row.STATUS_PUBLISHED}")


async def _worker(job: Job) -> None:
    async with worker_lock:
        job.status = "running"
        job.started_at = datetime.now(timezone.utc).isoformat()
        try:
            runner = {"add": _run_add, "generate": _run_generate,
                      "publish": _run_publish, "mark_published": _run_mark_published}[job.kind]
            await runner(job)
            job.status = "success"
        except Exception as e:  # noqa: BLE001
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


app = FastAPI(title="Trivia Captain 2T1L runner")


@app.get("/")
async def index():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/health")
async def api_health():
    return {"ok": True, "repo": str(REPO), "queue_sheet_id": queue_row.QUEUE_SHEET,
            "queue_sheet_url": QUEUE_SHEET_URL, "queue_tab": queue_row.QUEUE_TAB,
            "sa_present": SA_PATH.exists(), "python": sys.executable}


@app.get("/api/rows")
async def api_rows():
    try:
        return await asyncio.to_thread(read_rows)
    except Exception as e:
        raise HTTPException(500, f"sheet read failed: {e}")


@app.post("/api/run")
async def api_run(payload: dict):
    kind = payload.get("kind", "")
    if kind not in ("add", "generate", "publish", "mark_published"):
        raise HTTPException(400, f"bad kind: {kind!r}")
    slug = (payload.get("slug") or "").strip()
    extra: dict = {}
    if kind == "add":
        for k in ("slug", "place", "claim1", "claim2", "claim3", "lie"):
            if not str(payload.get(k) or "").strip():
                raise HTTPException(400, f"add requires {k!r}")
        extra = {k: str(payload.get(k) or "").strip() for k in
                 ("slug", "place", "claim1", "claim2", "claim3", "lie",
                  "lie_model", "demographic", "theme")}
        slug = extra["slug"]
    else:
        if not slug:
            raise HTTPException(400, f"{kind} requires 'slug'")
        if kind == "generate":
            extra["force"] = bool(payload.get("force", False))
        elif kind == "publish":
            if not _project_state(slug)["render_exists"]:
                raise HTTPException(409, f"no render to publish for {slug} — run Generate first")

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
    by_slug: dict[str, dict[str, str]] = {}
    for jid in recent_job_ids:
        job = jobs.get(jid)
        if job is None or job.status in ("success", "error"):
            continue
        if job.slug:
            by_slug.setdefault(job.slug, {})[job.kind] = job.status
    return {"by_slug": by_slug,
            "running_count": sum(1 for j in jobs.values() if j.status == "running"),
            "queued_count": sum(1 for j in jobs.values() if j.status == "queued")}


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

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no",
        "Connection": "keep-alive"})


@app.get("/api/render/{slug}")
async def api_render(slug: str):
    if "/" in slug or ".." in slug:
        raise HTTPException(400, "bad slug")
    state = _project_state(slug)
    if not state["render_exists"]:
        raise HTTPException(404, f"no render for {slug}")
    return FileResponse(REPO / state["render_path"], media_type="video/mp4")
