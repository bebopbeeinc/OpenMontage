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

import json
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
from scripts.chonky.measure import detector_status, verify  # noqa: E402
from scripts.chonky.render import CHARACTER, render_once  # noqa: E402
# Imported under another name: the TSV route below is also called
# `prompts`, and being defined later it silently replaced the module.
from scripts.chonky import prompts as prompt_writer  # noqa: E402
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
    # What the driver said it submitted — workspace and attached references.
    submission: list[str] = field(default_factory=list)


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

    # Read the RESOLVED values, not the environment: the company addresses are
    # committed as defaults, so an unset env var is normal rather than broken.
    from scripts.chonky import deliver as _deliver

    folder = _deliver.DRIVE_FOLDER_ID
    overridden = bool(os.environ.get("CHONKY_DRIVE_FOLDER_ID"))
    checks["drive_folder"] = {
        "ok": bool(folder),
        "detail": (f"{_mask(folder)}"
                   + (" (env override)" if overridden else " (committed default)")) if folder
                  else "no Drive folder resolved — Approve will refuse",
    }

    sheet = _deliver.SHEET_ID
    sheet_overridden = bool(os.environ.get("CHONKY_SHEET_ID"))
    checks["sheet"] = {
        "ok": bool(sheet),
        "detail": (f"{_mask(sheet)} · tab {_deliver.BATCHES_TAB}"
                   + (" (env override)" if sheet_overridden else " (committed default)")) if sheet
                  else "no sheet resolved — Approve will refuse",
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

    # Invalidate first: a package installed while this process was already
    # running is invisible until the path cache is refreshed, which makes a
    # successful install look like a failed one.
    importlib.invalidate_caches()
    # Then actually load it. "The name resolves on disk" and "this imports and
    # a model can be constructed" are different claims, and the gap between
    # them is invisible from outside: the pipeline keeps running, quietly
    # measuring with the colour heuristic instead.
    det = detector_status()
    checks["detector"] = {
        "ok": det["ok"],
        "detail": f"{det['model']} ready" if det["ok"]
                  else f"detector unusable, falling back to the colour "
                       f"heuristic (unreliable on sunlit scenes) — {det['error']}",
    }

    checks["workspace"] = {
        "ok": True,
        "detail": os.environ.get("CHONKY_OPENART_WORKSPACE", "R N (default)"),
    }

    # Which interpreter is actually serving this. Without it, "dependency
    # missing" is ambiguous between a failed install and an install that
    # landed in a different Python — the usual cause on a machine with
    # several.
    import sys as _sys
    checks["python"] = {
        "ok": True,
        "detail": f"{_sys.executable} ({_sys.version.split()[0]})",
    }
    return checks


@app.get("/api/health")
def health() -> dict:
    ready = _readiness()
    return {
        # Labelled as the reference, because renders do not arrive at this
        # size — the header read as a claim about the last render and was
        # wrong about every one of them.
        "image": f"reference {geo.IMG_W}x{geo.IMG_H}",
        "viewframe": (f"{geo.VF_W}x{geo.VF_H} at x {geo.VF_X0}-{geo.VF_X1}, "
                      f"y {geo.VF_Y0}-{geo.VF_Y1}"),
        "chonky_height_px": f"{geo.CHONKY_MIN_H}-{geo.CHONKY_MAX_H} at that size; "
                            f"each render is measured in its own",
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
    """Judge a box the operator dragged, in the frame they dragged it on.

    `image_id` is how the frame size is known. Without it the box is assumed
    to be in reference coordinates, which is only true when the render came
    back at the reference size — and the correction a human makes is supposed
    to be the authoritative one, so it must not be the one judged by the
    wrong ruler.
    """
    box = payload.get("box")
    if not box or len(box) != 4:
        return JSONResponse({"error": "box must be [x0, y0, x1, y1]"}, status_code=400)
    x0, y0, x1, y1 = (int(v) for v in box)

    frame = None
    image_id = payload.get("image_id")
    if image_id:
        img = _open(image_id)
        if img is not None:
            frame = img.size

    height = y1 - y0
    size = geo.size_verdict(height, frame)
    zone = geo.classify_zone(x0, x1, y0, y1, frame)
    return {"box": [x0, y0, x1, y1], "height_px": height, "size": size,
            "zone": zone, "ok": size == "ok" and zone in ("viewframe", "margin"),
            "frame_size": list(frame) if frame else None,
            "band_px": list(geo.size_band(frame)),
            "viewframe_box": list(geo.viewframe_box(frame))}


@app.post("/api/run")
def run(payload: dict) -> dict:
    """Render ONE image. Returns a job id; poll /api/jobs/{id}.

    One click is one submission. The UI disables the button while a job is
    running, and render_once itself has no retry path, so a duplicate render
    cannot happen by accident the way it did under ChatGPT.
    """
    image_id = payload.get("id") or uuid.uuid4().hex[:8]
    prompt = payload.get("prompt", "")
    location = payload.get("location")
    difficulty = payload.get("difficulty")
    target_zone = payload.get("target_zone")
    clues = payload.get("clues")
    if not prompt.strip():
        return JSONResponse({"error": "prompt is empty"}, status_code=400)

    job_id = uuid.uuid4().hex[:8]
    with _lock:
        jobs[job_id] = Job(id=job_id, slug=image_id, image_id=image_id)

    def _work() -> None:
        try:
            out = LIBRARY / f"{image_id}.png"
            submission: list[str] = []
            render_once(prompt, out, log=submission)
            with Image.open(out) as im:
                result = verify(im)
            with _lock:
                _images[image_id] = out
                job = jobs[job_id]
                job.status = "success"
                job.measurement = result
                job.submission = submission
            _write_sidecar(image_id, prompt=prompt, location=location,
                           difficulty=difficulty, target_zone=target_zone,
                           clues=clues, measurement=result)
        except Exception as exc:                      # surfaced to the UI as-is
            with _lock:
                job = jobs[job_id]
                job.status = "error"
                job.error = f"{type(exc).__name__}: {exc}"
                job.trace = traceback.format_exc()[-2000:]

    threading.Thread(target=_work, daemon=True).start()
    return {"job_id": job_id, "image_id": image_id}


def _used_locations() -> list[str]:
    """Locations already rendered, from the library's own record.

    Reuse is what the manual spends its first section on, and the writer can
    only avoid it if it is told. Read from disk rather than from this process
    so a restart does not wipe the memory of what has been made.
    """
    seen: list[str] = []
    for side in LIBRARY.glob("*.json"):
        loc = _read_sidecar(side.stem).get("location")
        if loc and loc not in seen:
            seen.append(loc)
    return seen


@app.post("/api/draft")
def draft_only(payload: dict) -> dict:
    """Write a prompt and stop, without rendering it.

    Prompt quality is what decides whether a render passes its checks, so
    iterating on it must not cost a render. This is also the only way to see
    what the writer does across several attempts without paying for each one.
    """
    used = _used_locations()
    zone = payload.get("target_zone") or next_zone([], int(
        payload.get("viewframe_pct", 70)))["target_zone"]
    spec = payload.get("city")
    city, country = _split_location(spec) if spec else (None, None)
    try:
        d = prompt_writer.draft(difficulty=payload.get("difficulty", 1),
                                target_zone=zone, used=used,
                                city=city, country=country)
    except Exception as exc:                          # noqa: BLE001 - shown in the UI
        return JSONResponse({"error": f"{exc}"}, status_code=400)
    return {**d, "target_zone": zone}


@app.post("/api/generate")
def generate(payload: dict) -> dict:
    """Write the prompts and render them. The operator supplies no prompt.

    Each image is drafted in its own thread and rendered as soon as its draft
    is ready, so a slow draft does not hold up the others. A draft that fails
    the manual's checks fails that job and never reaches OpenArt — a bad
    prompt is cheap, a render is not.
    """
    count = max(1, min(int(payload.get("count", 1)), 12))
    difficulty = payload.get("difficulty", 1)
    cities = payload.get("cities") or []
    pct = int(payload.get("viewframe_pct", 70))

    used = _used_locations()
    history: list[dict] = []
    out = []
    for i in range(count):
        decision = next_zone(history, pct)
        history.append({"chonky_zone": decision["target_zone"]})
        image_id = uuid.uuid4().hex[:8]
        job_id = uuid.uuid4().hex[:8]
        with _lock:
            jobs[job_id] = Job(id=job_id, slug=image_id, image_id=image_id,
                               status="drafting")
        out.append({"job_id": job_id, "image_id": image_id,
                    "target_zone": decision["target_zone"]})

        spec = (cities[i] if i < len(cities) else None) or None
        city, country = _split_location(spec) if spec else (None, None)

        def _draft_then_render(job_id=job_id, image_id=image_id,
                               zone=decision["target_zone"],
                               city=city, country=country) -> None:
            try:
                d = prompt_writer.draft(difficulty=difficulty, target_zone=zone,
                                  used=used, city=city, country=country)
            except Exception as exc:                  # noqa: BLE001 - shown in the UI
                with _lock:
                    job = jobs[job_id]
                    job.status = "error"
                    job.error = f"{type(exc).__name__}: {exc}"
                return
            location = f"{d['city']}, {d['country']}"
            with _lock:
                jobs[job_id].status = "running"
            # Render on this thread: the job already exists and is being
            # polled, so handing off to another one buys nothing.
            _run_render_inline(job_id, image_id, d["prompt"], location=location,
                               difficulty=difficulty, target_zone=zone,
                               clues=d["clues"])

        threading.Thread(target=_draft_then_render, daemon=True).start()

    return {"jobs": out}


def _run_render_inline(job_id: str, image_id: str, prompt: str, *, location=None,
                       difficulty=None, target_zone=None, clues=None) -> None:
    try:
        out = LIBRARY / f"{image_id}.png"
        submission: list[str] = []
        render_once(prompt, out, log=submission)
        with Image.open(out) as im:
            result = verify(im)
        with _lock:
            _images[image_id] = out
            job = jobs[job_id]
            job.status = "success"
            job.measurement = result
            job.submission = submission
        _write_sidecar(image_id, prompt=prompt, location=location,
                       difficulty=difficulty, target_zone=target_zone,
                       clues=clues, measurement=result)
    except Exception as exc:                          # surfaced to the UI as-is
        with _lock:
            job = jobs[job_id]
            job.status = "error"
            job.error = f"{type(exc).__name__}: {exc}"
            job.trace = traceback.format_exc()[-2000:]


@app.get("/api/jobs/{job_id}")
def job(job_id: str) -> dict:
    with _lock:
        j = jobs.get(job_id)
    if j is None:
        return {"state": "unknown"}
    return {"state": {"success": "done", "error": "failed"}.get(j.status, j.status),
            "image_id": j.image_id, "measurement": j.measurement,
            "error": j.error, "trace": j.trace, "submission": j.submission}


def _open(image_id: str) -> Optional[Image.Image]:
    """The render for this id, from memory or from disk.

    `_images` is only a per-process index, so on its own it loses every render
    the moment the server restarts — a deploy would make images that cost real
    credits unreviewable while the files sat on disk untouched. The library is
    the actual record; the dict is just a cache of it.
    """
    path = _images.get(image_id)
    if path is None:
        candidate = LIBRARY / f"{image_id}.png"
        if candidate.exists():
            path = candidate
            _images[image_id] = path
    if path is None or not path.exists():
        return None
    return Image.open(path)


@app.get("/api/redetect/{image_id}")
def redetect(image_id: str, imgsz: int = 0):
    """Re-run detection on a stored render and show its working.

    Exists because "the box is wrong" is not a diagnosis. A box can be wrong
    because the detector was never consulted, because it found nothing, or
    because it found the wrong thing confidently — and those need different
    fixes. Listing the candidates it considered tells them apart without
    spending another render.
    """
    img = _open(image_id)
    if img is None:
        return JSONResponse({"error": "unknown image"}, status_code=404)

    from scripts.chonky import measure as _m

    result = verify(img)
    candidates = []
    model = _m._load_model()
    if model is not None:
        raw = model.predict(img.convert("RGB"), verbose=False, conf=0.05,
                            imgsz=imgsz or _m._INFER_SIZE)[0]
        for b in raw.boxes:
            candidates.append({
                "cls": int(b.cls),
                "name": raw.names.get(int(b.cls), "?"),
                "conf": round(float(b.conf), 3),
                "box": [round(v) for v in b.xyxy[0].tolist()],
            })
        candidates.sort(key=lambda c: -c["conf"])
    return {"measurement": result,
            # The band and the ViewFrame are both expressed in 2048x2560
            # pixels. If the render is not that size, every number downstream
            # is being read against the wrong ruler.
            "image_size": list(img.size),
            "expected_size": [geo.IMG_W, geo.IMG_H],
            "imgsz": imgsz or _m._INFER_SIZE,
            "detector_status": _m.detector_status(),
            "candidates": candidates[:25]}


@app.get("/api/reference")
def reference():
    """The Chonky model sheet, for comparison beside a render.

    Size and zone are measured; likeness is not, and cannot be — it is the
    reviewer's call. Making them hold the character in their head while
    judging is how a plausible ginger cat gets approved.
    """
    # openart_characters lives in scripts/common, which the OpenArt driver
    # puts on sys.path when it loads. Do it here too rather than depend on
    # that having happened: this route can be the first thing a fresh process
    # serves.
    _common = str(REPO / "scripts" / "common")
    if _common not in sys.path:
        sys.path.insert(0, _common)
    import openart_characters as _chars

    try:
        stills = _chars.stills(CHARACTER)
    except Exception:                              # noqa: BLE001
        stills = []
    if not stills:
        return JSONResponse({"error": "no model sheet in character_library"},
                            status_code=404)
    img = Image.open(stills[0])
    img.thumbnail((1200, 1200))
    buf = __import__("io").BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=88)
    return Response(buf.getvalue(), media_type="image/jpeg")


def _sidecar_path(image_id: str) -> Path:
    return LIBRARY / f"{image_id}.json"


def _read_sidecar(image_id: str) -> dict:
    try:
        return json.loads(_sidecar_path(image_id).read_text())
    except (OSError, ValueError):
        return {}


def _write_sidecar(image_id: str, **fields) -> None:
    """Record a render's own account of itself next to the file.

    The library is what survives a restart, so what a render was and how it
    measured has to live there too — held only in the process, it disappears
    on the next deploy and the image becomes an anonymous PNG.
    """
    data = _read_sidecar(image_id)
    data.update(image_id=image_id, **fields)
    _sidecar_path(image_id).write_text(json.dumps(data, indent=2))


@app.get("/api/renders")
def renders():
    """Every render on disk, newest first.

    Reads the library rather than a dict of this process's own work: the page
    must show renders made before the last restart, and by other people.
    """
    out = []
    for png in LIBRARY.glob("*.png"):
        side = _read_sidecar(png.stem)
        out.append({
            "image_id": png.stem,
            "measurement": side.get("measurement"),
            "prompt": side.get("prompt"),
            "location": side.get("location"),
            "difficulty": side.get("difficulty"),
            "target_zone": side.get("target_zone"),
            "clues": side.get("clues"),
            "clue_words": side.get("clue_words"),
            "approved": side.get("approved", False),
            "rejected": side.get("rejected", False),
            "reject_reason": side.get("reject_reason"),
            "drive_url": side.get("drive_url"),
            "created_at": png.stat().st_mtime,
        })
    out.sort(key=lambda r: r["created_at"], reverse=True)
    return {"renders": out}


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


def _split_city_country(location: str) -> tuple[str, str]:
    city, _, country = (location or "").partition(",")
    return city.strip(), country.strip()


@app.post("/api/approve")
def approve(payload: dict) -> dict:
    """Encode, upload to Drive, append the sheet row. The only write path.

    Everything it needs was recorded beside the render when it was made, so a
    reviewer clicking Approve on a tile supplies nothing but which tile. Fields
    passed in the payload still win, for the case where someone corrected one.
    """
    image_id = payload.get("image_id", "")
    img = _open(image_id)
    if img is None:
        return JSONResponse({"error": "unknown image"}, status_code=404)

    side = _read_sidecar(image_id)
    city, country = _split_city_country(side.get("location", ""))
    measurement = payload.get("measurement") or side.get("measurement")

    if not measurement:
        return JSONResponse({"error": "this render has not been measured"},
                            status_code=400)
    if not measurement.get("ok"):
        # The band and the zone are hard constraints. One click is exactly
        # where they would quietly stop being hard.
        return JSONResponse({"error": (
            f"render failed its checks: {measurement.get('height_px')} px "
            f"{measurement.get('size')}, zone {measurement.get('zone')}")},
            status_code=400)

    clue_words = payload.get("clue_words") or side.get("clue_words")
    if not clue_words or len(clue_words) != 3:
        return JSONResponse(
            {"error": "this render has no three clue words, which the filename needs"},
            status_code=400)

    try:
        result = deliver(
            img,
            difficulty=payload.get("difficulty") or side.get("difficulty"),
            city=payload.get("city") or city,
            country=payload.get("country") or country,
            clue_words=clue_words,
            measurement=measurement,
            clues=payload.get("clues") or side.get("clues"),
            prompt=payload.get("prompt") or side.get("prompt", ""),
            viewpoint=payload.get("viewpoint", ""),
            scene_type=payload.get("scene_type", ""),
            gag=payload.get("gag", ""),
            batch_no=int(payload.get("batch_no", 1)),
        )
    except Exception as exc:
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=400)

    _write_sidecar(image_id, approved=True, rejected=False,
                   drive_url=result.get("drive_url"),
                   filename=result.get("filename"))
    return result


@app.post("/api/reject")
def reject(payload: dict) -> dict:
    """Record that a render was rejected, and why.

    The file stays. A rejected render is the evidence for what the prompt did
    wrong, and the reason is the only record of a judgement no measurement
    could make.
    """
    image_id = payload.get("image_id", "")
    if not (LIBRARY / f"{image_id}.png").exists():
        return JSONResponse({"error": "unknown image"}, status_code=404)
    _write_sidecar(image_id, rejected=True, approved=False,
                   reject_reason=payload.get("reason", ""))
    return {"ok": True, "image_id": image_id}


@app.post("/api/regenerate")
def regenerate(payload: dict) -> dict:
    """Render an edited prompt as a NEW image, keeping the original.

    A reroll is a second attempt, not a correction of the first: keeping both
    is what makes it possible to see which edit changed what.
    """
    source_id = payload.get("image_id", "")
    prompt = (payload.get("prompt") or "").strip()
    if not prompt:
        return JSONResponse({"error": "prompt is empty"}, status_code=400)

    # A hand-edited prompt gets the same check a written one gets. Both cost
    # the same render.
    try:
        prompt_writer._check(prompt)
    except prompt_writer.DraftError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    side = _read_sidecar(source_id)
    image_id = uuid.uuid4().hex[:8]
    job_id = uuid.uuid4().hex[:8]
    with _lock:
        jobs[job_id] = Job(id=job_id, slug=image_id, image_id=image_id)

    threading.Thread(
        target=_run_render_inline,
        args=(job_id, image_id, prompt),
        kwargs=dict(location=side.get("location"), difficulty=side.get("difficulty"),
                    target_zone=side.get("target_zone"), clues=side.get("clues")),
        daemon=True,
    ).start()
    return {"job_id": job_id, "image_id": image_id, "from": source_id}


if __name__ == "__main__":  # pragma: no cover
    import uvicorn
    _wrapper = FastAPI(title="Chonky pipeline runner (standalone)")
    _wrapper.mount("/chonky", app)
    print("  open:    http://127.0.0.1:8765/chonky/")
    uvicorn.run(_wrapper, host="127.0.0.1", port=8765, log_level="info")
