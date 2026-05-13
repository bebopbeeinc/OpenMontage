#!/usr/bin/env python
"""Trivia pipeline web UI (FastAPI sub-app, mounted by web/server.py at /trivia).

Canonical entry point — the launcher in web/server.py:
    uvicorn web.server:app --port 8765 --reload
    # then open http://127.0.0.1:8765/trivia/

The trivia app's index.html sets <base href="/trivia/"> and uses relative
fetch URLs (e.g. fetch("api/rows")), so it MUST be served under a /trivia
mount or every API call will 404. Do NOT run `uvicorn scripts.trivia.web.server:app`
directly — that exposes the routes at the root and breaks the base-href contract.

The `python scripts/trivia/web/server.py` form below is supported: the __main__
block wraps this sub-app in a launcher that mounts it at /trivia, so the URL
shape matches the canonical launcher.

The server only binds 127.0.0.1, so it is unreachable from other machines
unless you tunnel it (Tailscale / cloudflared / ssh -L).

Surfaces the Post Calendar as a table and exposes per-row actions:
  - Run pipeline:  feedback_router -> assemble_modular -> transcribe ->
                   reconcile -> apply_feedback_patches -> remotion render -> verify
                   (stops here; does NOT auto-publish — local review required)
  - Publish:       publish.py (uploads to Drive, flips L=Ready to publish)
  - Approve:       flips L=Approved
  - Feedback:      writes projects/<slug>/artifacts/feedback.json, picked up
                   by the next render via feedback_router.py
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
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

# Sibling import — post_row lives one directory up (scripts/trivia/).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from post_row import (  # noqa: E402
    POST_SHEET, ROW_KEYS, SA_PATH, build_sheets, read_posts_bulk,
)

REPO = Path(__file__).resolve().parents[3]
WEB_DIR = Path(__file__).resolve().parent

# SEGMENT_LIBRARY mirrors assemble_modular.py (filename-only post-2026-05).
# Pipeline-local — clips live under scripts/trivia/library/.
LIBRARY_BASE = REPO / "scripts" / "trivia" / "library"
SEGMENT_LIBRARY = {
    "hook":   LIBRARY_BASE / "reactions",
    "body":   LIBRARY_BASE / "bodies",
    "closer": LIBRARY_BASE / "closers",
}
SEGMENT_SOURCES = [
    ("hook",   "reaction_filename"),
    ("body",   "body_filename"),
    ("closer", "closer_filename"),
]


def _segment_status(row: dict, segment: str, fname_col: str) -> dict:
    """Return {status, detail} for one segment.

    status:
      'ok-local'     canonical file exists in library
      'needs-pick'   canonical missing but variant files exist on disk
                     (caller has to choose one before the pipeline can run)
      'missing-file' canonical name given but no canonical / variants on disk
      'empty'        no filename in the sheet
    """
    fname = (row.get(fname_col) or "").strip()
    if fname:
        local = SEGMENT_LIBRARY[segment] / fname
        if local.exists():
            return {"status": "ok-local", "detail": str(local)}
        variants = list_variants(segment, fname)
        if any(v["kind"] == "variant" for v in variants):
            n = sum(1 for v in variants if v["kind"] == "variant")
            return {
                "status": "needs-pick",
                "detail": f"{n} variant(s) on disk — pick one to use",
            }
        return {
            "status": "missing-file",
            "detail": f"{fname_col}={fname} not found in {SEGMENT_LIBRARY[segment]}",
        }
    return {
        "status": "empty",
        "detail": f"{fname_col} is empty",
    }

JobKind = Literal["render", "publish", "generate"]
JobStatus = Literal["queued", "running", "success", "error"]

# UI pipeline-segment names → openart_generate.py CLI segment names.
# Hook sources from the reactions library (per SEGMENT_SOURCES in
# assemble_modular.py); body and closer map 1:1.
SEGMENT_TO_GENERATE = {"hook": "reaction", "body": "body", "closer": "closer"}
CANONICAL_FILENAME_KEY = {
    "hook": "reaction_filename",
    "body": "body_filename",
    "closer": "closer_filename",
}


def list_variants(segment: str, canonical_filename: str) -> list[dict]:
    """Scan the segment's library for variant files matching the canonical stem.

    Returns a list of {filename, kind, mtime} sorted by mtime desc. `kind` is
    'canonical' for the no-suffix file, 'variant' for `<stem>_v<N><ext>` files.
    """
    if not canonical_filename or segment not in SEGMENT_LIBRARY:
        return []
    library = SEGMENT_LIBRARY[segment]
    if not library.exists():
        return []
    p = Path(canonical_filename)
    stem, ext = p.stem, p.suffix
    out: list[dict] = []
    canonical = library / canonical_filename
    if canonical.exists():
        out.append({
            "filename": canonical.name,
            "kind": "canonical",
            "mtime": canonical.stat().st_mtime,
        })
    for v in library.glob(f"{stem}_v*{ext}"):
        out.append({
            "filename": v.name,
            "kind": "variant",
            "mtime": v.stat().st_mtime,
        })
    # Canonical always first; variants follow newest-first.
    out.sort(key=lambda x: (0 if x["kind"] == "canonical" else 1, -x["mtime"]))
    return out


def pick_variant(segment: str, canonical_filename: str, variant_filename: str) -> Path:
    """Copy variant -> canonical, backing up any existing canonical."""
    if segment not in SEGMENT_LIBRARY:
        raise ValueError(f"unknown segment {segment!r}")
    if "/" in variant_filename or ".." in variant_filename:
        raise ValueError("bad variant filename")
    if "/" in canonical_filename or ".." in canonical_filename:
        raise ValueError("bad canonical filename")
    library = SEGMENT_LIBRARY[segment]
    src = library / variant_filename
    dst = library / canonical_filename
    if not src.exists():
        raise FileNotFoundError(f"variant not found: {src}")
    if src.resolve().parent != library.resolve():
        raise ValueError("variant path escaped library dir")
    if dst.exists():
        backup = library / f"{Path(canonical_filename).stem}.prev{Path(canonical_filename).suffix}"
        dst.replace(backup)
    import shutil as _sh
    _sh.copy2(src, dst)
    return dst


@dataclass
class Job:
    id: str
    kind: JobKind
    row: int
    slug: str
    status: JobStatus = "queued"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    started_at: str | None = None
    finished_at: str | None = None
    log: list[str] = field(default_factory=list)
    error: str | None = None
    render_path: str | None = None
    extra: dict = field(default_factory=dict)

    def summary(self) -> dict:
        verdict = None
        report = self.extra.get("verify_report") if hasattr(self, "extra") else None
        if report:
            verdict = report.get("verdict")
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
            "render_path": self.render_path,
            "log_lines": len(self.log),
            "verify_verdict": verdict,
        }


jobs: dict[str, Job] = {}
recent_job_ids: deque[str] = deque(maxlen=50)
worker_lock = asyncio.Lock()
log_subscribers: dict[str, list[asyncio.Queue[str]]] = {}


def _sheets():
    return build_sheets(write=True)   # server writes Posts!L on approve


def read_rows(min_row: int = 5, max_row: int = 200) -> list[dict]:
    out = read_posts_bulk(_sheets(), min_row=min_row, max_row=max_row)
    for d in out:
        # Per-segment status + any locally-cached variants.
        segments = {}
        for seg, fcol in SEGMENT_SOURCES:
            st = _segment_status(d, seg, fcol)
            st["variants"] = list_variants(seg, (d.get(fcol) or "").strip())
            segments[seg] = st
        d["segments"] = segments
        d["sources_ready"] = all(
            s["status"] == "ok-local"
            for s in d["segments"].values()
        )
        slug = (d.get("slug") or "").strip()
        d["render_exists"] = bool(slug) and (
            REPO / "projects" / slug / "renders" / "final_with_bg.mp4"
        ).exists()
        d["feedback"] = _load_feedback(slug) if slug else ""
    return out


def _load_feedback(slug: str) -> str:
    """Read reviewer feedback for a project. Empty string if absent or malformed."""
    path = REPO / "projects" / slug / "artifacts" / "feedback.json"
    if not path.exists():
        return ""
    try:
        return str(json.loads(path.read_text()).get("feedback", "") or "")
    except (json.JSONDecodeError, OSError):
        return ""


def mark_approved(row: int) -> None:
    s = _sheets()
    s.spreadsheets().values().update(
        spreadsheetId=POST_SHEET,
        range=f"Posts!L{row}",
        valueInputOption="USER_ENTERED",
        body={"values": [["Approved"]]},
    ).execute()


def _emit(job: Job, line: str) -> None:
    line = line.rstrip("\n")
    job.log.append(line)
    for q in log_subscribers.get(job.id, []):
        try:
            q.put_nowait(line)
        except asyncio.QueueFull:
            pass


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


RENDER_MAX_ATTEMPTS = 2

# Per-strategy assemble_modular CLI overrides for an auto-recovery retry.
RECOVERY_OVERRIDES: dict[str, dict[str, str]] = {
    "audio_clipping":  {"music_volume_db": "-20"},
    "audio_too_loud":  {"music_volume_db": "-20"},
    "audio_too_quiet": {"music_volume_db": "-8"},
    # VO non-determinism (ElevenLabs / Piper warm-up) — blind retry.
    "low_word_count":  {},
}


async def _run_render_attempt(
    job: Job, overrides: dict[str, str], attempt: int,
) -> None:
    py = sys.executable
    artifacts = REPO / "projects" / job.slug / "artifacts"
    warnings_path = artifacts / "assembly_warnings.json"
    overrides_path = artifacts / "text_overrides.json"
    feedback_path = artifacts / "feedback.json"
    feedback_plan_path = artifacts / "feedback_plan.json"
    assemble_overrides_path = artifacts / "assemble_overrides.json"

    # Phase 0a: classify reviewer feedback into a structured plan, if present.
    # Empty feedback => router exits 2 and clears the stale plan file. Re-runs
    # on each retry attempt by design: transcribe regenerates words.json on
    # every attempt, and the plan's target indices are resolved against that
    # fresh transcript, so re-routing keeps the plan aligned.
    if feedback_path.exists():
        _emit(job, "=== Phase 0/5: classify reviewer feedback (Claude) ===")
        rc = await _run_subprocess(
            job, [py, "scripts/trivia/feedback_router.py", str(job.row), job.slug],
            REPO,
        )
        if rc == 0 and feedback_plan_path.exists():
            try:
                plan = json.loads(feedback_plan_path.read_text())
                _emit(job, f"feedback summary: {plan.get('summary', '(no summary)')}")
                if plan.get("unresolved"):
                    _emit(job, "unresolved feedback (will not be auto-fixed):")
                    for u in plan["unresolved"]:
                        _emit(job, f"  - {u}")
            except json.JSONDecodeError:
                pass
        elif rc not in (0, 2):
            _emit(job, f"feedback_router failed (exit {rc}); continuing without it")

        # Phase 0b: apply pre-assemble side effects (brand tokens, music vol,
        # shorten_vo gate, blocker surfacing).
        if feedback_plan_path.exists():
            _emit(job, "=== applying pre-assemble feedback patches ===")
            await _run_subprocess(
                job,
                [py, "scripts/trivia/apply_feedback_patches.py", job.slug, "--phase", "pre"],
                REPO,
            )

    # Effective overrides: call-level (from UI) take precedence over router-emitted.
    effective: dict[str, str] = {}
    if assemble_overrides_path.exists():
        try:
            effective.update(json.loads(assemble_overrides_path.read_text()) or {})
        except json.JSONDecodeError:
            pass
    effective.update(overrides or {})

    flags = ["--with-vo", "--with-music", "--with-sfx", "--silent-hook"]
    if "music_volume_db" in effective:
        flags.extend(["--music-volume-db", str(effective["music_volume_db"])])

    for assemble_attempt in range(1, 3):
        label = f"Phase 1/5: assemble (clips -> bg.mp4)"
        if assemble_attempt > 1:
            label += f" — retry with shortened VO"
        _emit(job, f"=== {label} ===")
        rc = await _run_subprocess(
            job,
            [py, "scripts/trivia/assemble_modular.py", str(job.row), job.slug, *flags],
            REPO,
        )
        if rc != 0:
            raise RuntimeError(f"assemble_modular failed (exit {rc})")

        # Did the run emit any structured "VO too long" warnings?
        if not warnings_path.exists() or assemble_attempt >= 2:
            break

        _emit(job, "")
        _emit(job, "VO doesn't fit window — calling auto-shortener (Claude) to rewrite offending lines")
        rc = await _run_subprocess(
            job,
            [py, "scripts/trivia/shorten_vo.py", str(job.row), job.slug],
            REPO,
        )
        if rc != 0:
            _emit(job, "auto-shortener didn't produce overrides — keeping original text.")
            break
        _emit(job, f"applied overrides from {overrides_path.relative_to(REPO)}; re-assembling.")

    _emit(job, "=== Phase 2/5: transcribe (bg.mp4 -> words.json) ===")
    rc = await _run_subprocess(
        job, [py, "scripts/common/transcribe.py", job.slug], REPO,
    )
    if rc != 0:
        raise RuntimeError(f"transcribe failed (exit {rc})")

    _emit(job, "=== Phase 3/5: reconcile (align transcript to sheet VO script) ===")
    # Non-blocking: if sheet read fails or no fixes are found, the existing
    # words.json stays in place and the pipeline continues.
    await _run_subprocess(
        job, [py, "scripts/trivia/reconcile_captions.py", str(job.row), job.slug],
        REPO,
    )

    # Phase 3.5: apply reviewer-feedback word/timing patches (after reconcile so
    # we have the final transcript to edit). Patches are matched by word+time
    # so they survive transcribe re-runs.
    if feedback_plan_path.exists():
        _emit(job, "=== applying post-reconcile feedback patches (word/timing) ===")
        await _run_subprocess(
            job,
            [py, "scripts/trivia/apply_feedback_patches.py", job.slug, "--phase", "post"],
            REPO,
        )

    _emit(job, "=== Phase 4/5: remotion render (TriviaWithBg) ===")
    out_path = REPO / "projects" / job.slug / "renders" / "final_with_bg.mp4"
    rc = await _run_subprocess(
        job,
        ["npx", "remotion", "render", "src/index-trivia.tsx", "TriviaWithBg", str(out_path)],
        REPO / "remotion-composer",
    )
    if rc != 0:
        raise RuntimeError(f"remotion render failed (exit {rc})")
    if not out_path.exists():
        raise RuntimeError(f"render reported success but {out_path} is missing")
    job.render_path = str(out_path)

    _emit(job, "=== Phase 5/5: verify (duration / frames / captions / audio) ===")
    # rc here is informational — non-zero means verdict=fail, but render itself
    # ran fine. We read the JSON report to decide whether to retry.
    await _run_subprocess(
        job, [py, "scripts/trivia/verify_render.py", job.slug], REPO,
    )
    report_path = REPO / "projects" / job.slug / "artifacts" / "verify_report.json"
    if report_path.exists():
        try:
            job.extra["verify_report"] = json.loads(report_path.read_text())
        except json.JSONDecodeError:
            pass


async def _run_render_pipeline(job: Job) -> None:
    artifacts = REPO / "projects" / job.slug / "artifacts"
    feedback_path = artifacts / "feedback.json"
    feedback_plan_path = artifacts / "feedback_plan.json"

    # Snapshot feedback's saved_at BEFORE any retries so we can race-safely
    # clear it once at the end of the pipeline. Clearing inside each attempt
    # used to drop feedback before the verify-retry path could re-apply the
    # plan against the retry's fresh transcript.
    initial_feedback_saved_at: str | None = None
    if feedback_path.exists():
        try:
            initial_feedback_saved_at = str(
                json.loads(feedback_path.read_text()).get("saved_at", "")
            ) or None
        except json.JSONDecodeError:
            pass

    overrides: dict[str, str] = {}
    for attempt in range(1, RENDER_MAX_ATTEMPTS + 1):
        if attempt > 1:
            _emit(job, "")
            _emit(job, f"=== Retry {attempt}/{RENDER_MAX_ATTEMPTS} (recovery overrides: {overrides or 'blind retry'}) ===")
        job.extra["attempt"] = attempt
        await _run_render_attempt(job, overrides, attempt)

        report = job.extra.get("verify_report") or {}
        verdict = report.get("verdict")
        if verdict != "fail":
            break

        recovery = report.get("recovery")
        if not recovery:
            _emit(job, "")
            _emit(job, "verify reported fail but no auto-recovery strategy is available — stopping.")
            _emit(job, "review the report + frames, then re-run manually after adjusting.")
            break
        if attempt >= RENDER_MAX_ATTEMPTS:
            _emit(job, "")
            _emit(job, f"verify still failing after {attempt} attempt(s) — giving up.")
            break

        strategy = recovery["strategy"]
        overrides = RECOVERY_OVERRIDES.get(strategy, {})
        _emit(job, "")
        _emit(job, f"verify failed: {recovery['reason']}")
        _emit(job, f"applying recovery strategy '{strategy}' -> overrides: {overrides or '(blind retry; non-deterministic step may produce different output)'}")

    # Clear feedback artifacts now that the pipeline has processed them across
    # all retry attempts. Race-safe: only clear if saved_at hasn't changed
    # since we started — if the reviewer added newer feedback during the
    # render, leave it for the next pipeline run. Brand tokens / assemble
    # overrides / blockers are project state and stay.
    if initial_feedback_saved_at and feedback_path.exists():
        try:
            current_saved_at = str(
                json.loads(feedback_path.read_text()).get("saved_at", "")
            )
        except json.JSONDecodeError:
            current_saved_at = ""
        if current_saved_at == initial_feedback_saved_at:
            feedback_path.unlink(missing_ok=True)
            feedback_plan_path.unlink(missing_ok=True)
            _emit(job, "cleared feedback.json + feedback_plan.json (processed)")
        else:
            _emit(job, "feedback.json was updated during the render — leaving it for the next pass")

    out_path = REPO / "projects" / job.slug / "renders" / "final_with_bg.mp4"
    _emit(job, "")
    _emit(job, f"render ready for local review: {out_path}")
    _emit(job, "play locally, then click 'Publish' when satisfied.")


async def _run_publish(job: Job) -> None:
    py = sys.executable
    rc = await _run_subprocess(
        job,
        [py, "scripts/trivia/publish.py", job.slug, str(job.row)],
        REPO,
    )
    if rc != 0:
        raise RuntimeError(f"publish failed (exit {rc})")


async def _run_generate(
    job: Job, segments_cli: list[str], variants: int, force: bool,
) -> None:
    py = sys.executable
    cmd = [
        py, "scripts/trivia/openart_generate.py", str(job.row),
        "--segments", ",".join(segments_cli),
        "--variants", str(variants),
        "--headless",
    ]
    if force:
        cmd.append("--force")
    rc = await _run_subprocess(job, cmd, REPO)
    if rc != 0:
        raise RuntimeError(f"openart_generate failed (exit {rc})")


async def _worker(job: Job) -> None:
    async with worker_lock:
        job.status = "running"
        job.started_at = datetime.now(timezone.utc).isoformat()
        try:
            if job.kind == "render":
                await _run_render_pipeline(job)
            elif job.kind == "publish":
                await _run_publish(job)
            elif job.kind == "generate":
                segs = job.extra.get("segments_cli", [])
                variants = int(job.extra.get("variants", 2))
                force = bool(job.extra.get("force", False))
                await _run_generate(job, segs, variants, force)
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


app = FastAPI(title="Trivia pipeline runner")


@app.get("/")
async def index():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/health")
async def api_health():
    return {
        "ok": True,
        "repo": str(REPO),
        "sa_path": str(SA_PATH),
        "sa_present": SA_PATH.exists(),
        "python": sys.executable,
    }


@app.get("/api/rows")
async def api_rows():
    try:
        return read_rows()
    except FileNotFoundError as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        raise HTTPException(500, f"sheet read failed: {e}")


@app.post("/api/run")
async def api_run(payload: dict):
    kind = payload.get("kind", "render")
    if kind not in ("render", "publish", "generate"):
        raise HTTPException(400, "kind must be 'render', 'publish', or 'generate'")
    try:
        row = int(payload["row"])
    except (KeyError, ValueError, TypeError):
        raise HTTPException(400, "row must be an integer")
    slug = str(payload.get("slug", "")).strip()
    if not slug and kind != "generate":
        raise HTTPException(400, "slug required")

    extra: dict = {}
    if kind == "publish":
        out_path = REPO / "projects" / slug / "renders" / "final_with_bg.mp4"
        if not out_path.exists():
            raise HTTPException(
                409, f"no render to publish at {out_path} — run pipeline first",
            )
    elif kind == "generate":
        raw_segs = payload.get("segments") or []
        if not isinstance(raw_segs, list) or not raw_segs:
            raise HTTPException(400, "segments must be a non-empty list")
        bad = [s for s in raw_segs if s not in SEGMENT_TO_GENERATE]
        if bad:
            raise HTTPException(400, f"unknown segments: {bad}")
        extra["segments_cli"] = [SEGMENT_TO_GENERATE[s] for s in raw_segs]
        extra["segments_ui"] = list(raw_segs)
        extra["variants"] = int(payload.get("variants", 2))
        extra["force"] = bool(payload.get("force", False))

    job = Job(id=uuid.uuid4().hex[:8], kind=kind, row=row, slug=slug, extra=extra)
    jobs[job.id] = job
    recent_job_ids.append(job.id)
    log_subscribers.setdefault(job.id, [])
    asyncio.create_task(_worker(job))
    return job.summary()


@app.post("/api/pick")
async def api_pick(payload: dict):
    segment = payload.get("segment", "")
    canonical = (payload.get("canonical_filename") or "").strip()
    variant = (payload.get("variant_filename") or "").strip()
    if segment not in SEGMENT_LIBRARY:
        raise HTTPException(400, f"unknown segment {segment!r}")
    if not canonical or not variant:
        raise HTTPException(400, "canonical_filename and variant_filename required")
    try:
        dst = pick_variant(segment, canonical, variant)
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "path": str(dst)}


@app.get("/api/library/{segment}/{filename}")
async def api_library_file(segment: str, filename: str):
    if segment not in SEGMENT_LIBRARY:
        raise HTTPException(404)
    if "/" in filename or ".." in filename:
        raise HTTPException(400, "bad filename")
    p = SEGMENT_LIBRARY[segment] / filename
    if not p.exists() or p.resolve().parent != SEGMENT_LIBRARY[segment].resolve():
        raise HTTPException(404)
    return FileResponse(p, media_type="video/mp4")


@app.post("/api/approve")
async def api_approve(payload: dict):
    try:
        row = int(payload["row"])
    except (KeyError, ValueError, TypeError):
        raise HTTPException(400, "row must be an integer")
    try:
        mark_approved(row)
    except Exception as e:
        raise HTTPException(500, str(e))
    return {"ok": True, "row": row}


@app.post("/api/feedback")
async def api_feedback(payload: dict):
    """Persist reviewer feedback for one row to the project's artifacts dir.

    Feedback used to live in Posts!N; that column was deleted in the 2026-05
    cleanup. Feedback is now project-scoped — it sits alongside
    assembly_warnings.json + text_overrides.json so the regen subprocesses
    that already read from artifacts/ can pick it up.

    Latest feedback wins (overwrite). For history, look at git/Drive versions.
    """
    try:
        row = int(payload["row"])
    except (KeyError, ValueError, TypeError):
        raise HTTPException(400, "row must be an integer")
    slug = str(payload.get("slug", "")).strip()
    if not slug:
        raise HTTPException(400, "slug is required (row must have a value in the Slug column)")
    text = str(payload.get("feedback", ""))

    artifacts = REPO / "projects" / slug / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    path = artifacts / "feedback.json"
    path.write_text(json.dumps({
        "row": row,
        "slug": slug,
        "feedback": text,
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }, indent=2) + "\n")
    return {"ok": True, "row": row, "slug": slug, "path": str(path.relative_to(REPO))}


@app.get("/api/jobs")
async def api_jobs():
    return [jobs[jid].summary() for jid in reversed(recent_job_ids) if jid in jobs]


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
            # If the job is already done, push terminator immediately after
            # the client connects so the stream closes cleanly.
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


@app.get("/api/render/{slug}")
async def api_render_file(slug: str):
    p = REPO / "projects" / slug / "renders" / "final_with_bg.mp4"
    if not p.exists():
        raise HTTPException(404, f"no render at {p}")
    return FileResponse(p, media_type="video/mp4")


@app.get("/api/verify/{slug}")
async def api_verify(slug: str):
    if "/" in slug or ".." in slug:
        raise HTTPException(400, "bad slug")
    p = REPO / "projects" / slug / "artifacts" / "verify_report.json"
    if not p.exists():
        raise HTTPException(404, f"no verify report for {slug}")
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError as e:
        raise HTTPException(500, f"report malformed: {e}")


@app.get("/api/frame/{slug}/{filename}")
async def api_frame(slug: str, filename: str):
    if "/" in slug or ".." in slug or "/" in filename or ".." in filename:
        raise HTTPException(400, "bad path")
    p = REPO / "projects" / slug / "renders" / "frames" / filename
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="image/jpeg")


if __name__ == "__main__":
    # Wrap in a /trivia mount so the standalone form matches the launcher's
    # URL shape (index.html's <base href="/trivia/"> requires the prefix).
    import uvicorn
    _wrapper = FastAPI(title="Trivia pipeline runner (standalone)")
    _wrapper.mount("/trivia", app)
    print(f"  repo:    {REPO}")
    print(f"  sa file: {SA_PATH} ({'ok' if SA_PATH.exists() else 'MISSING'})")
    print("  open:    http://127.0.0.1:8765/trivia/")
    uvicorn.run(_wrapper, host="127.0.0.1", port=8765, log_level="info")
