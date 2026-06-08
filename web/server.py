"""OpenMontage pipeline launcher.

A thin FastAPI app that lives at the project root and mounts per-pipeline web
apps as sub-applications. The launcher itself only serves the home page (a
list of pipelines); everything else is delegated to the mounted pipeline.

Adding a new pipeline:
  1. Build a FastAPI app in scripts/<pipeline>/web/server.py (the trivia app
     is the reference shape — see scripts/trivia/web/server.py).
  2. Mount it here:
       from scripts.<pipeline>.web import server as pipeline
       app.mount("/<pipeline>", pipeline.app)
  3. Add an entry to PIPELINES so the launcher home page lists it.
  4. In that pipeline's index.html, set `<base href="/<pipeline>/">` and use
     relative API paths (`fetch("api/...")` not `fetch("/api/...")`) so
     URLs resolve correctly under the mount prefix.

Run:
  # Dev (auto-reload on file save, no Deploy-button restart):
  uvicorn web.server:app --port 8765 --reload

  # Managed (Deploy button fully restarts the process via the supervisor):
  scripts/run_launcher.sh
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse

# Pipeline sub-apps. Each must be a self-contained FastAPI app.
from scripts.trivia.web import server as trivia
from scripts.trivia_captain.web import server as trivia_captain
from scripts.trivia_images.web import server as trivia_images
from scripts.trivia_quiz.web import server as trivia_quiz
from scripts.trivia_reaction.web import server as trivia_reaction

REPO = Path(__file__).resolve().parents[1]
WEB_DIR = Path(__file__).resolve().parent


# Pipeline catalog — drives the launcher home page. Keep `path` in sync with
# the mount call below + the `<base href>` in the pipeline's index.html.
PIPELINES = [
    {
        "id": "trivia",
        "path": "/trivia/",
        "name": "Trivia Short",
        "description": (
            "Daily-trivia vertical video. Driven by the Post Calendar sheet — "
            "human writes copy, OpenArt generates 3 clips, this pipeline "
            "stitches them, transcribes, renders TikTok-style captions, "
            "publishes to Drive."
        ),
        "stability": "beta",
    },
    {
        "id": "trivia-images",
        "path": "/trivia-images/",
        "name": "Trivia Images",
        "description": (
            "Per-row question + answer images for trivia rows. Reads the "
            "Brian tab of the trivia-questions sheet; generates the question "
            "image from col Q with OpenArt, then the answer image from col R "
            "using the question image as a same-scene reference."
        ),
        "stability": "beta",
    },
    {
        "id": "trivia-quiz",
        "path": "/trivia-quiz/",
        "name": "Trivia Quiz",
        "description": (
            "~30s vertical quiz short driven by the Posts_Quiz sheet tab. Each "
            "row is one post (3 questions: easy → medium → hard). Build & "
            "Render runs OpenArt backdrops + full audio (VO/music/SFX) and the "
            "Remotion TriviaQuiz composition; Publish uploads to Drive."
        ),
        "stability": "alpha",
    },
    {
        "id": "trivia-reaction",
        "path": "/trivia-reaction/",
        "name": "Trivia Reaction",
        "description": (
            "\"So I just found out…\" 15s vertical reaction reels driven by "
            "the TriviaReactionQueue sheet. Per-Day pipeline: select from "
            "daily-trivia, draft script, drive OpenArt/Seedance for the avatar "
            "clip, assemble + Remotion-render captions, publish to Drive."
        ),
        "stability": "alpha",
    },
    {
        "id": "trivia-captain",
        "path": "/trivia-captain/",
        "name": "Trivia Captain",
        "description": (
            "\"And you're telling me this NOW?\" 15s vertical reaction reels "
            "fronted by \"Captain\" Archibald, driven by the TriviaCaptainQueue "
            "sheet. Sister of Trivia Reaction: same daily-trivia source and "
            "Seedance/Remotion path, but a rotating hook library and the game "
            "splash rendered in-camera on his tablet via an OpenArt reference "
            "image."
        ),
        "stability": "alpha",
    },
]


app = FastAPI(title="OpenMontage pipeline launcher")

# Pipeline sub-app registry — drives both mounting and the deploy-time
# "any jobs running?" guard. Each sub-app module is expected to expose
# `jobs: dict[str, Job]` where Job has at least `.id`, `.kind`, `.slug`,
# `.status` ∈ {"queued","running","success","error"} (see scripts/trivia*/web).
PIPELINE_MODULES: dict[str, object] = {
    "trivia": trivia,
    "trivia-images": trivia_images,
    "trivia-quiz": trivia_quiz,
    "trivia-reaction": trivia_reaction,
    "trivia-captain": trivia_captain,
}

# Mount each sub-app at "/<id>". Sub-apps have their own routes rooted at "/",
# so mounting at "/<id>" prefixes everything correctly.
for _pipeline_id, _module in PIPELINE_MODULES.items():
    app.mount(f"/{_pipeline_id}", _module.app)


@app.get("/")
async def home():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/pipelines")
async def list_pipelines():
    """Used by the home page JS to render the pipeline cards."""
    return {"pipelines": PIPELINES}


@app.get("/api/health")
async def health():
    return {"ok": True, "pipelines": [p["id"] for p in PIPELINES]}


# Restart strategy is picked at request time, but we cache the mode at boot so
# the UI can show "supervised" / "reload" / "manual" before any deploy runs.
SUPERVISED = bool(os.environ.get("OPENMONTAGE_LAUNCHER_SUPERVISED"))
# Exit code 75 (EX_TEMPFAIL) is the sentinel scripts/run_launcher.sh watches
# for — anything else stops the supervisor. We don't use 0 because a clean exit
# should let the operator actually stop the server with Ctrl+C.
RESTART_EXIT_CODE = 75

ACTIVE_JOB_STATUSES = ("queued", "running")


def _scan_active_jobs() -> list[dict]:
    """Walk every mounted pipeline's job registry and collect active jobs.

    Returns a flat list of dicts the UI can render. We use small per-pipeline
    try/except so one misbehaving sub-app can't block deploys for the whole
    launcher (e.g., if a refactor temporarily changes the Job shape).
    """
    active: list[dict] = []
    for pipeline_id, module in PIPELINE_MODULES.items():
        jobs = getattr(module, "jobs", None)
        if not isinstance(jobs, dict):
            continue
        try:
            for job in jobs.values():
                status = getattr(job, "status", None)
                if status not in ACTIVE_JOB_STATUSES:
                    continue
                active.append({
                    "pipeline": pipeline_id,
                    "id": getattr(job, "id", ""),
                    "kind": getattr(job, "kind", ""),
                    "slug": getattr(job, "slug", ""),
                    "status": status,
                    "started_at": getattr(job, "started_at", None),
                })
        except Exception:  # noqa: BLE001 — never let an introspection crash block deploy UI
            continue
    return active


@app.get("/api/jobs/active")
async def jobs_active():
    """Lets the UI disable the Deploy button while pipeline runs are in flight."""
    active = _scan_active_jobs()
    return {"count": len(active), "jobs": active}


@app.post("/api/deploy")
async def deploy(
    background_tasks: BackgroundTasks,
    force: bool = Query(
        False,
        description="If true, deploy even when pipeline jobs are queued/running. "
                    "The restart will kill those child processes.",
    ),
):
    """Pull from origin and restart the server when there's anything new.

    Restart strategy, in order of preference:

      1. **Supervised** (`OPENMONTAGE_LAUNCHER_SUPERVISED=1`, set by
         `scripts/run_launcher.sh`): after the response flushes, exit the
         process with code 75. The supervisor respawns a fresh uvicorn.
         Works without `--reload`.

      2. **--reload fallback**: bump this file's mtime so uvicorn's watcher
         restarts the worker. Only fires if the operator started uvicorn with
         `--reload`; otherwise it's a no-op and the UI surfaces a manual-
         restart warning.

    We don't `os.execv` from a bare process because pipeline runs may have
    spawned child processes attached to this one; the supervisor path is the
    clean way to do a full restart.

    Active-job guard: if any pipeline sub-app has a queued/running job, this
    returns 409 with the conflict list. Pass `?force=true` to deploy anyway —
    the restart will kill those child processes, so the operator has to opt in.
    """
    if not force:
        active = _scan_active_jobs()
        if active:
            return JSONResponse(
                status_code=409,
                content={
                    "ok": False,
                    "reason": "active_jobs",
                    "active_jobs": active,
                    "message": (
                        f"{len(active)} pipeline job(s) still running. "
                        "Wait for them to finish, or deploy with force=true to "
                        "kill them as part of the restart."
                    ),
                },
            )

    head_before = _git_head()
    pull = subprocess.run(
        ["git", "pull", "--ff-only", "origin"],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=120,
    )
    head_after = _git_head()
    changed = head_before != head_after

    result = {
        "ok": pull.returncode == 0,
        "changed": changed,
        "head_before": head_before,
        "head_after": head_after,
        "stdout": pull.stdout,
        "stderr": pull.stderr,
        "supervised": SUPERVISED,
        "restart_method": "none",
    }

    if pull.returncode != 0 or not changed:
        return result

    if SUPERVISED:
        background_tasks.add_task(_schedule_supervised_restart)
        result["restart_method"] = "supervisor"
    else:
        # Best-effort uvicorn --reload trigger.
        try:
            os.utime(__file__, None)
            result["restart_method"] = "reload"
        except OSError as exc:
            result["reload_error"] = str(exc)

    return result


def _schedule_supervised_restart() -> None:
    """Exit with the supervisor's restart sentinel after a short grace period.

    Runs on FastAPI's background-task thread *after* the HTTP response has been
    flushed to the client, so the Deploy button sees its result before the
    socket closes. We use a daemon thread + delayed `os._exit` instead of
    `sys.exit` so that uvicorn's lifespan/shutdown hooks don't swallow or
    re-raise the signal and prevent the supervisor from seeing exit 75.
    """
    def _exit_after_delay() -> None:
        time.sleep(0.5)
        os._exit(RESTART_EXIT_CODE)

    threading.Thread(target=_exit_after_delay, daemon=True).start()


def _git_head() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(REPO),
            capture_output=True,
            text=True,
            timeout=10,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


# Touch-mtime detection of reload mode: record server start time so the client
# can tell if /api/health came back from a fresh process.
_BOOT_TIME = time.time()


@app.get("/api/boot")
async def boot():
    return {"boot_time": _BOOT_TIME, "supervised": SUPERVISED}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8765)
