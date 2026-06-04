"""Trivia quiz web UI (FastAPI sub-app, mounted by web/server.py at /trivia-quiz).

Surfaces the Posts_Quiz sheet tab as a per-row table and exposes the
trivia-quiz pipeline stages as button-driven jobs:

  - Build & Render:  build.py --slug <slug> --from-sheet <flags> +
                     npx remotion render TriviaQuiz -> final_quiz.mp4
  - Publish:         publish.py --slug <slug>      -> Drive + Posts_Quiz writes
  - Set status:      write Final Status back to the row (manual walk-back)

Mounted by web/server.py — do not run this app standalone. The index.html's
<base href="/trivia-quiz/"> would resolve incorrectly without the mount.

Each Posts_Quiz row is fully self-contained — identity, hook variant, all 3
questions inline, post metadata, publish state. The row IS the source of
truth (per user memory feedback_trivia_quiz_sheet_source); this UI never
authors content, it only drives the build/publish stages off the sheet.

Build & Render always runs the production flag set defined by the pipeline:
OpenArt backdrops (headless) + full audio (--with-vo --with-music --with-sfx,
piper TTS). The build stages bg.mp4 + quiz_meta.json into the shared
remotion-composer/public/ dir, then we run the Remotion render — so jobs run
one at a time under a global lock (the public/ dir is global state and
`npx remotion` saturates CPU).

Per user memory feedback_trivia_local_approval: never auto-run publish —
Publish is a deliberate button, never chained off the build.

The server only binds 127.0.0.1.
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
PKG_DIR = Path(__file__).resolve().parent.parent   # scripts/trivia_quiz/

sys.path.insert(0, str(REPO))
from scripts.trivia_quiz import sheets as quiz_sheets  # noqa: E402
from scripts.trivia_quiz.publish import DRIVE_FOLDER_ID  # noqa: E402

SA_PATH = quiz_sheets.SA_PATH
PROJECTS_ROOT = REPO / "projects" / "trivia-quiz"
QUIZ_SHEET_URL = (
    f"https://docs.google.com/spreadsheets/d/{quiz_sheets.QUIZ_SHEET_ID}/edit"
)

# Production flag set for Build & Render. OpenArt backdrops + full audio is the
# pipeline default (the edit-director's "Default flags applied" line + the
# user's standing trivia-assembly preference). --no-render still stages
# bg.mp4 + quiz_meta.json into remotion-composer/public/; we run the actual
# Remotion render ourselves so we can stream its log.
#
# --reuse-question-assets is ALWAYS on: a Build & Render re-click (e.g. after a
# partial OpenArt failure) reuses the Q1/Q2/Q3 JPGs already on disk instead of
# re-rolling them — saves credits and avoids re-hitting transient poll timeouts.
# To force fresh question backdrops, the "Re-build & Render" path (regen=True)
# deletes those JPGs first in _run_build; with nothing on disk to reuse, build.py
# regenerates them.
BUILD_FLAGS = [
    "--from-sheet",
    "--with-openart", "--openart-headless",
    "--reuse-question-assets",
    "--with-vo", "--with-music", "--with-sfx",
    "--no-render",
]

# Per-question backdrop filenames cleared on a regen (Re-build & Render). The
# show backdrop (hook_bg/score_bg, copied from the library cache each build) is
# NOT cleared here — that's governed separately by --regen-show-assets.
QUESTION_BACKDROPS = ("q1_bg.jpg", "q2_bg.jpg", "q3_bg.jpg")
REMOTION_ENTRY = "src/index-trivia-quiz.tsx"
REMOTION_COMP = "TriviaQuiz"

# Valid Final Status values (Posts_Quiz column AA). Mirrors the enum documented
# in scripts/trivia_quiz/sheets.py.
VALID_STATUSES = {"Draft", "Ready to publish", "Approved", "Published"}

JobKind = Literal["build", "publish", "mark_published"]
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
        }


jobs: dict[str, Job] = {}
recent_job_ids: deque[str] = deque(maxlen=200)
log_subscribers: dict[str, list[asyncio.Queue[str]]] = {}

# Single global worker lock. build.py stages bg.mp4 + quiz_meta.json into the
# shared remotion-composer/public/ dir, OpenArt drives Playwright with a shared
# login state, and `npx remotion render` saturates CPU. Run jobs one at a time.
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
# Sheet reads + local file state
# ---------------------------------------------------------------------------
def _project_state(slug: str) -> dict:
    """File-state snapshot for one project. Cheap — just os.path.exists checks."""
    if not slug:
        return {
            "artifacts_exist": False, "bg_exists": False,
            "render_exists": False, "render_path": None, "render_mtime": None,
        }
    project = PROJECTS_ROOT / slug
    quiz_meta = project / "artifacts" / "quiz_meta.json"
    bg = project / "assets" / "video" / "bg.mp4"
    render = project / "renders" / "final_quiz.mp4"
    has_render = render.exists()
    return {
        "artifacts_exist": quiz_meta.exists(),
        "bg_exists": bg.exists(),
        "render_exists": has_render,
        "render_path": str(render.relative_to(REPO)) if has_render else None,
        "render_mtime": render.stat().st_mtime if has_render else None,
    }


def _q_summary(post: dict, qid: str) -> dict:
    return {
        "question": (post.get(f"{qid}_question") or "").strip(),
        "answer": (post.get(f"{qid}_answer") or "").strip(),
        "fact": (post.get(f"{qid}_fact") or "").strip(),
    }


def read_rows() -> list[dict]:
    """Read every Posts_Quiz row; enrich each with local file state."""
    sheets = quiz_sheets.build_sheets(write=False)
    posts = quiz_sheets.read_posts_bulk(sheets)
    out: list[dict] = []
    for p in posts:
        slug = (p.get("slug") or "").strip()
        try:
            order = int((p.get("order") or "").strip())
        except (ValueError, AttributeError):
            order = None
        out.append({
            "order": p.get("order") or "",
            "order_int": order,
            "post_date": p.get("post_date") or "",
            "slug": slug,
            "status": (p.get("final_status") or "").strip(),
            "drive_link": (p.get("final_video_link") or "").strip(),
            "caption": (p.get("caption") or "").strip(),
            "q1": _q_summary(p, "q1"),
            "q2": _q_summary(p, "q2"),
            "q3": _q_summary(p, "q3"),
            "files": _project_state(slug),
        })
    # Stable order: by daily ordinal ascending; rows without one fall to the end.
    out.sort(key=lambda d: (d["order_int"] is None, d["order_int"] or 0))
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
async def _run_build(job: Job) -> None:
    """Full build-to-render chain — build.py (artifacts + OpenArt backdrops +
    audio mix + stage into public/) → npx remotion render TriviaQuiz. Triggered
    by 'Build & Render'. Does NOT touch the sheet's Final Status; that's the
    human's call (Publish, or the status dropdown)."""
    if not job.slug:
        raise RuntimeError("build job missing 'slug'")
    py = sys.executable

    # Regen (Re-build & Render): clear the per-question backdrops so build.py
    # regenerates them fresh. Without this, --reuse-question-assets reuses the
    # JPGs already on disk.
    if job.extra.get("regen"):
        images_dir = PROJECTS_ROOT / job.slug / "assets" / "images"
        cleared = []
        for name in QUESTION_BACKDROPS:
            jpg = images_dir / name
            if jpg.exists():
                jpg.unlink()
                cleared.append(name)
        if cleared:
            _emit(job, f"regen: cleared {', '.join(cleared)} — will regenerate via OpenArt")
        else:
            _emit(job, "regen: no question backdrops on disk to clear")

    _emit(job, "=== Phase 1/2: build.py (artifacts + OpenArt + audio + stage) ===")
    cmd = [py, "-m", "scripts.trivia_quiz.build", "--slug", job.slug, *BUILD_FLAGS]
    rc = await _run_subprocess(job, cmd, REPO)
    if rc != 0:
        raise RuntimeError(f"build.py failed (exit {rc})")

    _emit(job, "=== Phase 2/2: remotion render (TriviaQuiz) ===")
    out_path = PROJECTS_ROOT / job.slug / "renders" / "final_quiz.mp4"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rc = await _run_subprocess(
        job,
        ["npx", "remotion", "render", REMOTION_ENTRY, REMOTION_COMP, str(out_path)],
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

    # Flip Final Status -> 'Ready to publish' (mirrors trivia-reaction's
    # generate-success flip). This is a status label only — it does NOT
    # publish; Publish stays a deliberate button per feedback_trivia_local_approval.
    # It's what makes Publish + Mark-as-Published gate consistently across pipelines.
    try:
        ws = await asyncio.to_thread(quiz_sheets.build_sheets, True)
        await asyncio.to_thread(
            quiz_sheets.write_post_field, ws, job.slug, "final_status",
            "Ready to publish",
        )
        _emit(job, f"  Posts_Quiz final_status for {job.slug} -> Ready to publish")
    except Exception as e:  # noqa: BLE001
        _emit(job, f"  ⚠ status flip skipped: {e}")


async def _run_publish(job: Job) -> None:
    """Upload final_quiz.mp4 to Drive and write Final Status + Link back.
    publish.py sets Final Status -> 'Ready to publish'; the row only flips to
    'Published' via the status dropdown once the post is actually live."""
    if not job.slug:
        raise RuntimeError("publish job missing 'slug'")
    py = sys.executable
    rc = await _run_subprocess(
        job, [py, "-m", "scripts.trivia_quiz.publish", "--slug", job.slug], REPO,
    )
    if rc != 0:
        raise RuntimeError(f"publish failed (exit {rc})")


async def _write_status(job: Job, status: str) -> None:
    """Status-only write to Posts_Quiz column AA (final_status)."""
    if not job.slug:
        raise RuntimeError(f"{job.kind} job missing 'slug'")
    if status not in VALID_STATUSES:
        raise RuntimeError(f"invalid status {status!r}")
    ws = await asyncio.to_thread(quiz_sheets.build_sheets, True)
    await asyncio.to_thread(
        quiz_sheets.write_post_field, ws, job.slug, "final_status", status,
    )
    _emit(job, f"Posts_Quiz final_status for {job.slug} -> {status}")


async def _run_mark_published(job: Job) -> None:
    """Flip 'Ready to publish' -> 'Published' (the human's signal the post is
    live), then best-effort capture the live TikTok video's ID by caption so the
    row gets a durable link for stats sync. The capture never blocks the flip."""
    await _write_status(job, "Published")

    # Best-effort: the post is live now, so find its TikTok video and store the
    # id (+ baseline snapshot). Requires TikTok creds/tokens in the environment;
    # if absent, we skip silently-but-loudly — the daily stats sync will still
    # match it by caption later.
    account = os.environ.get("TRIVIA_QUIZ_TIKTOK_ACCOUNT", "dailytrivia.tc")
    try:
        from scripts.social_stats.quiz_stats_sync import link_published_video
        res = await asyncio.to_thread(link_published_video, account, job.slug)
        if res.get("found"):
            _emit(job, f"  linked TikTok video {res['video_id']} "
                       f"(caption match {res['score']}) — stats will track it")
        else:
            _emit(job, f"  ⚠ no TikTok video linked yet ({res.get('reason')}); "
                       f"stats sync will retry by caption")
    except Exception as e:  # noqa: BLE001
        _emit(job, f"  ⚠ TikTok video-id capture skipped: {e}")


async def _worker(job: Job) -> None:
    async with worker_lock:
        job.status = "running"
        job.started_at = datetime.now(timezone.utc).isoformat()
        try:
            if job.kind == "build":
                await _run_build(job)
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
app = FastAPI(title="Trivia quiz runner")


@app.get("/")
async def index():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/health")
async def api_health():
    return {
        "ok": True,
        "repo": str(REPO),
        "quiz_sheet_id": quiz_sheets.QUIZ_SHEET_ID,
        "quiz_sheet_url": QUIZ_SHEET_URL,
        "posts_tab": quiz_sheets.POSTS_TAB,
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


@app.post("/api/run")
async def api_run(payload: dict):
    kind = payload.get("kind", "")
    if kind not in ("build", "publish", "mark_published"):
        raise HTTPException(400, f"bad kind: {kind!r}")
    slug = (payload.get("slug") or "").strip()
    if not slug:
        raise HTTPException(400, f"{kind} requires 'slug'")

    if kind == "publish":
        # Refuse if no render exists yet — saves a confusing publish.py crash.
        if not _project_state(slug)["render_exists"]:
            raise HTTPException(
                409, f"no render to publish for {slug} — run Build & Render first",
            )

    job = Job(id=uuid.uuid4().hex[:8], kind=kind, slug=slug)
    if kind == "build":
        # regen=True (Re-build & Render) clears Q1/Q2/Q3 backdrops before build
        # so they regenerate; default reuses the on-disk JPGs.
        job.extra["regen"] = bool(payload.get("regen"))
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
    for jid in recent_job_ids:
        job = jobs.get(jid)
        if job is None or job.status in ("success", "error"):
            continue
        if job.slug:
            by_slug.setdefault(job.slug, {})[job.kind] = job.status
    running = sum(1 for j in jobs.values() if j.status == "running")
    queued = sum(1 for j in jobs.values() if j.status == "queued")
    return {"by_slug": by_slug, "running_count": running, "queued_count": queued}


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
