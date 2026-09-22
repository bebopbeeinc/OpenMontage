#!/usr/bin/env python
"""Chonky pipeline web UI (FastAPI sub-app, mounted by web/server.py at /chonky).

Canonical entry point — the launcher in web/server.py:
    uvicorn web.server:app --port 8765 --reload
    # then open http://127.0.0.1:8765/chonky/

index.html sets <base href="/chonky/"> and uses relative fetch URLs, so this
app MUST be served under a /chonky mount or every API call 404s. Do NOT run
`uvicorn scripts.chonky.web.server:app` directly; the __main__ block below
wraps it in a launcher that mounts it correctly.

What it surfaces:
  - Paste the prompt TSV a model produced, get one card per image with its
    assigned Chonky zone.
  - Render each image ONCE. Duplicate submission is impossible here: the
    button disables itself and render_once has no retry path.
  - Inspect each result: full frame beside its ViewFrame, Chonky's measured
    height and zone with pass/fail badges, and the measurement box as a
    draggable rectangle because the detector can be wrong.
  - Edit three clue messages, then Approve — the only path that writes to
    Drive and the sheet.
"""
from __future__ import annotations

import sys
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from PIL import Image

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.chonky import geometry as geo  # noqa: E402
from scripts.chonky.deliver import deliver  # noqa: E402
from scripts.chonky.imaging import viewframe_crop  # noqa: E402
from scripts.chonky.measure import verify  # noqa: E402
from scripts.chonky.render import render_once  # noqa: E402
from scripts.chonky.targeting import next_zone  # noqa: E402

HERE = Path(__file__).resolve().parent
LIBRARY = HERE.parent / "library"          # gitignored; large renders
LIBRARY.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Chonky pipeline runner")

# In-memory only: a batch is a sitting, not a record. The sheet is the record.
#
# `jobs` is public and shaped the way web/server.py's deploy guard expects
# (.id / .kind / .slug / .status), so a deploy cannot land mid-render.
@dataclass
class Job:
    id: str
    slug: str
    status: str = "running"          # queued | running | success | error
    kind: str = "chonky-render"
    started_at: float = field(default_factory=time.time)
    image_id: str = ""
    measurement: Optional[dict] = None
    error: Optional[str] = None
    trace: Optional[str] = None


jobs: dict[str, Job] = {}
_images: dict[str, Path] = {}
_lock = threading.Lock()


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

def _split_location(text: str) -> tuple[str, str]:
    """"Lisbon, Portugal" -> ("Lisbon", "Portugal"). Country-only is tolerated."""
    parts = [p.strip() for p in text.split(",")]
    if len(parts) >= 2:
        return parts[0], parts[-1]
    return parts[0], ""


def _parse_tsv(tsv: str) -> list[dict]:
    rows: list[dict] = []
    for line in tsv.splitlines():
        if not line.strip():
            continue
        cells = [c.strip() for c in line.split("\t")]
        if len(cells) < 3:
            continue
        if cells[0].lower().startswith("difficulty"):      # header
            continue
        difficulty, location, prompt = cells[0], cells[1], "\t".join(cells[2:])
        city, country = _split_location(location)
        rows.append({"difficulty": difficulty, "location": location,
                     "city": city, "country": country, "prompt": prompt})
    return rows


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((HERE / "index.html").read_text())


def _mask(value: str) -> str:
    """Enough of an id to recognise, not enough to be a leak."""
    if not value:
        return ""
    return value[:6] + "…" + value[-4:] if len(value) > 12 else "set"


def _readiness() -> dict:
    """Preflight for every external dependency.

    Each of these fails at a different and inconvenient moment — missing
    config kills Approve at the END of a batch, a missing OpenArt token kills
    Render at the start, a missing service account 403s the upload, and a
    missing detector degrades measurement silently. Checking them together is
    how an operator finds out before spending credits.
    """
    import importlib.util
    import os

    checks: dict[str, dict] = {}

    folder = os.environ.get("CHONKY_DRIVE_FOLDER_ID", "")
    checks["drive_folder"] = {
        "ok": bool(folder),
        "detail": f"CHONKY_DRIVE_FOLDER_ID={_mask(folder)}" if folder
                  else "CHONKY_DRIVE_FOLDER_ID is unset — Approve will refuse",
    }

    sheet = os.environ.get("CHONKY_SHEET_ID", "")
    checks["sheet"] = {
        "ok": bool(sheet),
        "detail": f"CHONKY_SHEET_ID={_mask(sheet)}" if sheet
                  else "CHONKY_SHEET_ID is unset — Approve will refuse",
    }

    token = REPO / ".openart" / "mcp-token.json"
    account = ""
    if token.exists():
        try:
            import sys as _sys
            _common = str(REPO / "scripts" / "common")
            if _common not in _sys.path:
                _sys.path.insert(0, _common)
            import openart_api as _api
            acct = _api.call_tool("openart_account_get", {})
            ws = (acct.get("workspace") or {}).get("name", "?")
            account = f"{acct.get('user', {}).get('email', '?')} · workspace {ws} · {acct.get('credits', '?')} credits"
        except Exception as exc:
            account = f"token present but unusable: {type(exc).__name__}"
    checks["openart_token"] = {
        "ok": bool(token.exists() and account and "unusable" not in account),
        "detail": account or f"no token at {token} — run scripts/common/openart_mcp.py --login",
    }

    sa = Path.home() / ".google" / "claude-sheets-sa.json"
    checks["service_account"] = {
        "ok": sa.exists(),
        "detail": f"present at {sa}" if sa.exists()
                  else f"missing at {sa} — Drive and Sheets writes will fail",
    }

    has_yolo = importlib.util.find_spec("ultralytics") is not None
    checks["detector"] = {
        "ok": has_yolo,
        "detail": "ultralytics installed" if has_yolo
                  else "ultralytics missing — measurement falls back to a colour "
                       "heuristic that is unreliable on sunlit scenes "
                       "(pip install -r requirements-detect.txt)",
    }

    checks["workspace"] = {
        "ok": True,
        "detail": os.environ.get("CHONKY_OPENART_WORKSPACE", "R N (default)"),
    }
    return checks


@app.get("/api/health")
def health() -> dict:
    ready = _readiness()
    return {
        "image": f"{geo.IMG_W}x{geo.IMG_H}",
        "viewframe": (f"{geo.VF_W}x{geo.VF_H} at x {geo.VF_X0}-{geo.VF_X1}, "
                      f"y {geo.VF_Y0}-{geo.VF_Y1}"),
        "chonky_height_px": f"{geo.CHONKY_MIN_H}-{geo.CHONKY_MAX_H}",
        "viewframe_box": [geo.VF_X0, geo.VF_Y0, geo.VF_X1, geo.VF_Y1],
        "all_ready": all(c["ok"] for c in ready.values()),
        "ready": ready,
    }


@app.post("/api/prompts")
def prompts(payload: dict) -> dict:
    """Parse a pasted TSV and assign each image a Chonky zone.

    Zones are assigned as if each row were appended to the ledger in turn, so
    a six-image batch converges on the configured split instead of every image
    landing in the majority zone.
    """
    rows = _parse_tsv(payload.get("tsv", ""))
    pct = int(payload.get("viewframe_pct", 70))

    out: list[dict] = []
    history: list[dict] = []
    for row in rows:
        decision = next_zone(history, pct)
        row = dict(
            row,
            id=uuid.uuid4().hex[:8],
            target_zone=decision["target_zone"],
            zone_reason=decision["reason"],
        )
        history.append({"chonky_zone": row["target_zone"]})
        out.append(row)

    return {"rows": out,
            "viewframe_box": [geo.VF_X0, geo.VF_Y0, geo.VF_X1, geo.VF_Y1]}


@app.post("/api/measure")
def measure(payload: dict) -> dict:
    """Judge a box without needing the image — the maths is pure geometry."""
    box = payload.get("box")
    if not box or len(box) != 4:
        return JSONResponse({"error": "box must be [x0, y0, x1, y1]"}, status_code=400)
    x0, y0, x1, y1 = (int(v) for v in box)
    height = y1 - y0
    size = geo.size_verdict(height)
    zone = geo.classify_zone(x0, x1, y0, y1)
    return {"box": [x0, y0, x1, y1], "height_px": height, "size": size,
            "zone": zone, "ok": size == "ok" and zone in ("viewframe", "margin")}


@app.post("/api/run")
def run(payload: dict) -> dict:
    """Render ONE image. Returns a job id; poll /api/jobs/{id}.

    One click is one submission. The UI disables the button while a job is
    running, and render_once itself has no retry path, so a duplicate render
    cannot happen by accident the way it did under ChatGPT.
    """
    image_id = payload.get("id") or uuid.uuid4().hex[:8]
    prompt = payload.get("prompt", "")
    if not prompt.strip():
        return JSONResponse({"error": "prompt is empty"}, status_code=400)

    job_id = uuid.uuid4().hex[:8]
    with _lock:
        jobs[job_id] = Job(id=job_id, slug=image_id, image_id=image_id)

    def _work() -> None:
        try:
            out = LIBRARY / f"{image_id}.png"
            render_once(prompt, out)
            with Image.open(out) as im:
                result = verify(im)
            with _lock:
                _images[image_id] = out
                job = jobs[job_id]
                job.status = "success"
                job.measurement = result
        except Exception as exc:                      # surfaced to the UI as-is
            with _lock:
                job = jobs[job_id]
                job.status = "error"
                job.error = f"{type(exc).__name__}: {exc}"
                job.trace = traceback.format_exc()[-2000:]

    threading.Thread(target=_work, daemon=True).start()
    return {"job_id": job_id, "image_id": image_id}


@app.get("/api/jobs/{job_id}")
def job(job_id: str) -> dict:
    with _lock:
        j = jobs.get(job_id)
    if j is None:
        return {"state": "unknown"}
    return {"state": {"success": "done", "error": "failed"}.get(j.status, j.status),
            "image_id": j.image_id, "measurement": j.measurement,
            "error": j.error, "trace": j.trace}


def _open(image_id: str) -> Optional[Image.Image]:
    path = _images.get(image_id)
    if path is None or not path.exists():
        return None
    return Image.open(path)


@app.get("/api/frame/{image_id}")
def frame(image_id: str):
    """The full render, downscaled for the browser."""
    img = _open(image_id)
    if img is None:
        return JSONResponse({"error": "unknown image"}, status_code=404)
    small = img.copy()
    small.thumbnail((900, 900))
    buf = __import__("io").BytesIO()
    small.convert("RGB").save(buf, format="JPEG", quality=85)
    return Response(buf.getvalue(), media_type="image/jpeg")


@app.get("/api/viewframe/{image_id}")
def viewframe(image_id: str):
    """Exactly what the player opens on."""
    img = _open(image_id)
    if img is None:
        return JSONResponse({"error": "unknown image"}, status_code=404)
    crop = viewframe_crop(img)
    crop.thumbnail((600, 1200))
    buf = __import__("io").BytesIO()
    crop.convert("RGB").save(buf, format="JPEG", quality=85)
    return Response(buf.getvalue(), media_type="image/jpeg")


@app.post("/api/approve")
def approve(payload: dict) -> dict:
    """Encode, upload to Drive, append the sheet row. The only write path."""
    image_id = payload.get("image_id", "")
    img = _open(image_id)
    if img is None:
        return JSONResponse({"error": "unknown image"}, status_code=404)
    try:
        result = deliver(
            img,
            difficulty=payload["difficulty"],
            city=payload["city"],
            country=payload["country"],
            clue_words=payload["clue_words"],
            measurement=payload["measurement"],
            clues=payload.get("clues"),
            prompt=payload.get("prompt", ""),
            viewpoint=payload.get("viewpoint", ""),
            scene_type=payload.get("scene_type", ""),
            gag=payload.get("gag", ""),
            batch_no=int(payload.get("batch_no", 1)),
        )
    except Exception as exc:
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=400)
    return result


if __name__ == "__main__":  # pragma: no cover
    import uvicorn
    _wrapper = FastAPI(title="Chonky pipeline runner (standalone)")
    _wrapper.mount("/chonky", app)
    print("  open:    http://127.0.0.1:8765/chonky/")
    uvicorn.run(_wrapper, host="127.0.0.1", port=8765, log_level="info")
