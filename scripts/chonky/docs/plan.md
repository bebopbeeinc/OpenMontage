# Chonky Image Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `scripts/chonky/` — a pipeline that renders geography-puzzle images on OpenArt, verifies each one against hard pixel rules, and files approved results to Drive and a sheet, with a review UI at `/chonky`.

**Architecture:** A sibling of `scripts/trivia_images/`, reusing its OpenArt driver, image optimiser, Drive config and sheet schema unchanged. New code covers only what is Chonky-specific: the ViewFrame geometry, measuring the cat, zone targeting, and a review UI. A FastAPI sub-app is mounted at `/chonky` by the root `web/server.py`, exactly as the other pipelines are.

**Tech Stack:** Python 3, FastAPI, Pillow, pytest. OpenArt via `scripts/common/openart_api.py` (MCP, no browser).

**Spec:** `scripts/chonky/docs/design.md`

## Global Constraints

- Source renders are **2048 × 2560** (4:5). OpenArt ignores `customWidth`/`customHeight` — never pass them.
- OpenArt ignores `outputFormat` and returns ~9 MB PNG. JPEG encoding is always local.
- ViewFrame is **1200 × 2133 at x 424–1624, y 213–2346**.
- Chonky's height band is **65–105 px inclusive**.
- Central third of the ViewFrame, **x 824–1224**, is forbidden for Chonky.
- Delivered files target **700 KB – 1.2 MB**, hard ceiling 1.3 MB.
- Filenames: `{difficulty}_{city}_{country}_{c1}_{c2}_{c3}.jpg`, lowercase, hyphens inside multiword names, difficulty `5+` written `5plus`.
- Tests live in `tests/pipelines/`, run with `python -m pytest tests/ -v`.
- One render call per image. Never call a generate function twice for the same image without a stated verification failure.

---

### Task 1: Geometry and zone classification

**Files:**
- Create: `scripts/chonky/__init__.py` (empty)
- Create: `scripts/chonky/geometry.py`
- Test: `tests/pipelines/test_chonky_geometry.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `IMG_W, IMG_H, VF_W, VF_H, VF_X0, VF_Y0, VF_X1, VF_Y1, CHONKY_MIN_H, CHONKY_MAX_H` (ints); `classify_zone(x0: int, x1: int, y0: int, y1: int) -> str` returning one of `"viewframe" | "margin" | "centrestage" | "straddling"`; `size_verdict(height_px: int) -> str` returning `"ok" | "too_small" | "too_big"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/pipelines/test_chonky_geometry.py
from scripts.chonky.geometry import (
    IMG_W, IMG_H, VF_W, VF_H, VF_X0, VF_Y0, VF_X1, VF_Y1,
    classify_zone, size_verdict,
)


def test_frame_matches_what_openart_actually_returns():
    assert (IMG_W, IMG_H) == (2048, 2560)
    assert IMG_W / IMG_H == 0.8


def test_viewframe_is_centred_and_9x16():
    assert (VF_W, VF_H) == (1200, 2133)
    assert (VF_X0, VF_X1) == (424, 1624)
    assert (VF_Y0, VF_Y1) == (213, 2346)
    # the odd pixel goes to the bottom, per the playtest spec
    assert IMG_H - VF_Y1 == 214


def test_exploration_envelopes_match_the_playtest_spec():
    assert 170 <= 100 * IMG_W / VF_W <= 180
    assert 120 <= 100 * IMG_H / VF_H <= 125


def test_zone_boundaries():
    z = lambda x0, x1: classify_zone(x0, x1, 1000, 1100)
    assert z(300, 423) == "margin"
    assert z(424, 600) == "viewframe"
    assert z(1450, 1624) == "viewframe"
    assert z(1625, 1800) == "margin"
    assert z(414, 434) == "straddling"
    assert z(824, 1224) == "centrestage"
    assert z(823, 1224) == "viewframe"
    assert z(824, 1225) == "viewframe"


def test_zone_uses_vertical_bounds_too():
    assert classify_zone(800, 900, 50, 150) == "margin"
    assert classify_zone(800, 900, 2400, 2500) == "margin"


def test_the_measured_lisbon_render_is_a_viewframe_hit():
    # 2026-09-21, Rua Augusta: the first render that passed every check.
    assert classify_zone(1232, 1318, 1709, 1798) == "viewframe"


def test_size_verdict_band_edges():
    assert size_verdict(64) == "too_small"
    assert size_verdict(65) == "ok"
    assert size_verdict(89) == "ok"
    assert size_verdict(105) == "ok"
    assert size_verdict(106) == "too_big"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/OpenMontage && python -m pytest tests/pipelines/test_chonky_geometry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.chonky'`

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/chonky/geometry.py
"""Authored frame geometry for the Chonky pipeline.

The numbers are measured, not chosen. OpenArt ignores customWidth/customHeight
and returns 2048x2560 for aspectRatio 4:5, so that is the source size. The
ViewFrame is the 9:16 window the player opens on; everything outside it is
reached by panning.
"""
from __future__ import annotations

IMG_W, IMG_H = 2048, 2560       # what gpt-image-2-5-sunburst actually returns
VF_W, VF_H = 1200, 2133         # 9:16 ViewFrame

VF_X0 = (IMG_W - VF_W) // 2     # 424
VF_Y0 = (IMG_H - VF_H) // 2     # 213 — the odd pixel goes to the bottom
VF_X1 = VF_X0 + VF_W            # 1624
VF_Y1 = VF_Y0 + VF_H            # 2346

CHONKY_MIN_H, CHONKY_MAX_H = 65, 105

# Chonky must stay out of the ViewFrame's central third: present at a glance,
# never the subject of the photograph.
_CENTRE_X0 = VF_X0 + VF_W // 3          # 824
_CENTRE_X1 = VF_X1 - VF_W // 3          # 1224


def classify_zone(x0: int, x1: int, y0: int, y1: int) -> str:
    """Where a bounding box sits relative to the ViewFrame.

    "viewframe"    wholly inside, clear of the central third — the good case
    "margin"       wholly outside — a pan-reveal reward, also good
    "centrestage"  inside the central third — he is not the subject
    "straddling"   half in, half out — the player sees a fragment of cat
    """
    inside = VF_X0 <= x0 and x1 <= VF_X1 and VF_Y0 <= y0 and y1 <= VF_Y1
    if not inside:
        overlaps = x1 > VF_X0 and x0 < VF_X1 and y1 > VF_Y0 and y0 < VF_Y1
        return "straddling" if overlaps else "margin"
    if _CENTRE_X0 <= x0 and x1 <= _CENTRE_X1:
        return "centrestage"
    return "viewframe"


def size_verdict(height_px: int) -> str:
    """Too small is as much a failure as too big — he has to be findable."""
    if height_px < CHONKY_MIN_H:
        return "too_small"
    if height_px > CHONKY_MAX_H:
        return "too_big"
    return "ok"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/OpenMontage && python -m pytest tests/pipelines/test_chonky_geometry.py -v`
Expected: PASS, 7 tests

- [ ] **Step 5: Commit**

```bash
cd ~/OpenMontage
touch scripts/chonky/__init__.py
git add scripts/chonky/__init__.py scripts/chonky/geometry.py tests/pipelines/test_chonky_geometry.py
git commit -m "feat(chonky): frame geometry and zone classification"
```

---

### Task 2: Measuring Chonky in a render

**Files:**
- Create: `scripts/chonky/measure.py`
- Test: `tests/pipelines/test_chonky_measure.py`

**Interfaces:**
- Consumes: `scripts.chonky.geometry.classify_zone`, `size_verdict`.
- Produces: `detect_chonky(img: PIL.Image.Image) -> tuple[int, int, int, int] | None` returning `(x0, y0, x1, y1)`; `verify(img, box=None) -> dict` returning `{"box": (x0,y0,x1,y1) | None, "height_px": int | None, "size": str, "zone": str, "ok": bool}`.

Detection is a heuristic and will be wrong on warm-toned scenes — the Lisbon café render measured 556 px against a true 89 px because sunlit awnings matched the ginger threshold. `verify()` therefore accepts an explicit `box` so the UI can override it.

- [ ] **Step 1: Write the failing test**

```python
# tests/pipelines/test_chonky_measure.py
from PIL import Image

from scripts.chonky.geometry import IMG_W, IMG_H
from scripts.chonky.measure import detect_chonky, verify


def _blank():
    return Image.new("RGB", (IMG_W, IMG_H), (128, 128, 128))


def _with_patch(x0, y0, w, h, colour=(210, 120, 45)):
    img = _blank()
    for x in range(x0, x0 + w):
        for y in range(y0, y0 + h):
            img.putpixel((x, y), colour)
    return img


def test_detects_a_ginger_patch():
    img = _with_patch(1232, 1709, 86, 89)
    box = detect_chonky(img)
    assert box is not None
    x0, y0, x1, y1 = box
    assert abs(x0 - 1232) <= 2 and abs(y0 - 1709) <= 2
    assert abs((y1 - y0) - 89) <= 2


def test_returns_none_when_there_is_no_cat():
    assert detect_chonky(_blank()) is None


def test_verify_passes_a_good_render():
    result = verify(_with_patch(1232, 1709, 86, 89))
    assert result["size"] == "ok"
    assert result["zone"] == "viewframe"
    assert result["ok"] is True


def test_verify_fails_an_oversized_cat():
    result = verify(_with_patch(1232, 1500, 250, 310))
    assert result["size"] == "too_big"
    assert result["ok"] is False


def test_verify_fails_centrestage():
    result = verify(_with_patch(900, 1709, 86, 89))
    assert result["zone"] == "centrestage"
    assert result["ok"] is False


def test_explicit_box_overrides_detection():
    # Detection would find the patch; the caller insists on a different box.
    img = _with_patch(1232, 1709, 86, 89)
    result = verify(img, box=(500, 300, 586, 389))
    assert result["box"] == (500, 300, 586, 389)
    assert result["height_px"] == 89
    assert result["zone"] == "viewframe"


def test_verify_handles_no_detection():
    result = verify(_blank())
    assert result["box"] is None
    assert result["ok"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/OpenMontage && python -m pytest tests/pipelines/test_chonky_measure.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.chonky.measure'`

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/chonky/measure.py
"""Find Chonky in a render and judge him against the authored rules.

Detection is deliberately a hint, not an authority. Ginger fur and warm
late-afternoon stone occupy the same corner of RGB space, and a sunlit cafe
awning once measured 556 px against a true 89 px. The review UI therefore
draws this box as a draggable rectangle and passes a corrected one back.
"""
from __future__ import annotations

from typing import Optional

from PIL import Image

from scripts.chonky.geometry import classify_zone, size_verdict

Box = tuple[int, int, int, int]

# Saturated ginger: strongly red, with clear R>G>B separation and a dark blue
# channel. Pale warm stone fails the b < 120 test; shadowed brick fails r > 150.
_R_MIN, _RG_MIN, _GB_MIN, _B_MAX = 150, 55, 25, 120


def _is_ginger(px: tuple[int, int, int]) -> bool:
    r, g, b = px[0], px[1], px[2]
    return r > _R_MIN and (r - g) > _RG_MIN and (g - b) > _GB_MIN and b < _B_MAX


def detect_chonky(img: Image.Image) -> Optional[Box]:
    """Bounding box of the largest contiguous run of ginger pixels, or None."""
    rgb = img.convert("RGB")
    w, h = rgb.size
    px = rgb.load()

    xs: list[int] = []
    ys: list[int] = []
    # Step 2 px: a 65 px cat is ~32 samples tall, plenty, and it quarters the work.
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            if _is_ginger(px[x, y]):
                xs.append(x)
                ys.append(y)
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def verify(img: Image.Image, box: Optional[Box] = None) -> dict:
    """Measure and judge. Pass `box` to override detection with a human's."""
    if box is None:
        box = detect_chonky(img)
    if box is None:
        return {"box": None, "height_px": None, "size": "not_found",
                "zone": "not_found", "ok": False}

    x0, y0, x1, y1 = box
    height = y1 - y0
    size = size_verdict(height)
    zone = classify_zone(x0, x1, y0, y1)
    return {
        "box": (x0, y0, x1, y1),
        "height_px": height,
        "size": size,
        "zone": zone,
        "ok": size == "ok" and zone in ("viewframe", "margin"),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/OpenMontage && python -m pytest tests/pipelines/test_chonky_measure.py -v`
Expected: PASS, 7 tests

- [ ] **Step 5: Commit**

```bash
cd ~/OpenMontage
git add scripts/chonky/measure.py tests/pipelines/test_chonky_measure.py
git commit -m "feat(chonky): detect and verify Chonky against the size band and zones"
```

---

### Task 3: ViewFrame cropping and JPEG delivery encoding

**Files:**
- Create: `scripts/chonky/imaging.py`
- Test: `tests/pipelines/test_chonky_imaging.py`

**Interfaces:**
- Consumes: `scripts.chonky.geometry` constants; `scripts.trivia_images.image_optimize.optimize_image_bytes_jpg`.
- Produces: `viewframe_crop(img: Image.Image) -> Image.Image`; `encode_delivery(img: Image.Image, min_kb: int = 700, max_kb: int = 1200) -> tuple[bytes, int]` returning `(jpeg_bytes, quality_used)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/pipelines/test_chonky_imaging.py
import io

from PIL import Image

from scripts.chonky.geometry import IMG_W, IMG_H, VF_W, VF_H, VF_X0, VF_Y0
from scripts.chonky.imaging import encode_delivery, viewframe_crop


def _noisy_source():
    # Flat colour compresses to almost nothing, which would never exercise the
    # size search. Noise gives a realistically incompressible image.
    import random
    rnd = random.Random(1)
    img = Image.new("RGB", (IMG_W, IMG_H))
    px = img.load()
    for y in range(0, IMG_H, 1):
        for x in range(0, IMG_W, 1):
            px[x, y] = (rnd.randrange(256), rnd.randrange(256), rnd.randrange(256))
    return img


def test_viewframe_crop_is_exactly_the_authored_window():
    img = Image.new("RGB", (IMG_W, IMG_H), (10, 20, 30))
    # mark the top-left pixel of the ViewFrame so we can prove the origin
    img.putpixel((VF_X0, VF_Y0), (255, 0, 0))
    crop = viewframe_crop(img)
    assert crop.size == (VF_W, VF_H)
    assert crop.getpixel((0, 0)) == (255, 0, 0)


def test_encode_delivery_lands_in_the_size_envelope():
    data, quality = encode_delivery(_noisy_source())
    kb = len(data) // 1024
    assert 700 <= kb <= 1200, f"{kb} KB at q{quality}"
    assert Image.open(io.BytesIO(data)).format == "JPEG"


def test_encode_delivery_never_exceeds_the_hard_ceiling():
    data, _ = encode_delivery(_noisy_source())
    assert len(data) <= 1300 * 1024
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/OpenMontage && python -m pytest tests/pipelines/test_chonky_imaging.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.chonky.imaging'`

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/chonky/imaging.py
"""Cropping and delivery encoding.

OpenArt ignores outputFormat and hands back ~9 MB PNGs, so every delivered
asset is re-encoded here. This is the step Apps Script could not do at all,
and the reason the pipeline moved into Python.
"""
from __future__ import annotations

import io

from PIL import Image

from scripts.chonky.geometry import VF_H, VF_W, VF_X0, VF_Y0

# Descending quality ladder. 90 usually overshoots and 70 is visibly soft on
# fine clues like road markings, so the useful range sits between.
_QUALITY_LADDER = (92, 88, 85, 82, 78, 74, 70)


def viewframe_crop(img: Image.Image) -> Image.Image:
    """The 9:16 window the player opens on, before any panning."""
    return img.crop((VF_X0, VF_Y0, VF_X0 + VF_W, VF_Y0 + VF_H))


def _encode(img: Image.Image, quality: int) -> bytes:
    buf = io.BytesIO()
    rgb = img.convert("RGB")
    rgb.save(buf, format="JPEG", quality=quality, optimize=True, progressive=True)
    return buf.getvalue()


def encode_delivery(img: Image.Image, min_kb: int = 700, max_kb: int = 1200) -> tuple[bytes, int]:
    """Encode to JPEG inside the size envelope.

    Walks quality down until the file fits under `max_kb`, then stops — the
    first fit is the highest quality that fits. Prefers a slightly large file
    over crushing a clue, so if nothing fits it returns the lowest-quality
    attempt rather than raising.
    """
    best: tuple[bytes, int] | None = None
    for quality in _QUALITY_LADDER:
        data = _encode(img, quality)
        kb = len(data) // 1024
        best = (data, quality)
        if kb <= max_kb:
            return data, quality
    assert best is not None
    return best
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/OpenMontage && python -m pytest tests/pipelines/test_chonky_imaging.py -v`
Expected: PASS, 3 tests

- [ ] **Step 5: Commit**

```bash
cd ~/OpenMontage
git add scripts/chonky/imaging.py tests/pipelines/test_chonky_imaging.py
git commit -m "feat(chonky): ViewFrame crop and JPEG delivery encoding"
```

---

### Task 4: Filenames and zone targeting

**Files:**
- Create: `scripts/chonky/naming.py`
- Create: `scripts/chonky/targeting.py`
- Test: `tests/pipelines/test_chonky_naming.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `slug(text: str) -> str`; `build_filename(difficulty, city, country, clue_words: list[str]) -> str`; `next_zone(rows: list[dict], viewframe_pct: int = 70) -> dict` returning `{"target_zone": str, "stats": {...}, "reason": str}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/pipelines/test_chonky_naming.py
import pytest

from scripts.chonky.naming import build_filename, slug
from scripts.chonky.targeting import next_zone


def test_slug_survives_accented_place_names():
    assert slug("São Paulo") == "sao-paulo"
    assert slug("Zürich") == "zurich"
    assert slug("Côte d'Ivoire") == "cote-d-ivoire"
    assert slug("Gdańsk") == "gdansk"


def test_slug_writes_difficulty_5plus():
    assert slug("5+") == "5plus"


def test_filename_shape():
    assert build_filename("1", "Lisbon", "Portugal", ["arch", "calcada", "clock"]) == \
        "1_lisbon_portugal_arch_calcada_clock.jpg"


def test_filename_requires_exactly_three_clue_words():
    with pytest.raises(ValueError):
        build_filename("1", "Lisbon", "Portugal", ["arch", "calcada"])


def test_zone_targeting_follows_the_configured_majority_when_empty():
    assert next_zone([], 70)["target_zone"] == "viewframe"


def test_zone_targeting_corrects_an_over_represented_zone():
    rows = [{"chonky_zone": "viewframe"}] * 10
    assert next_zone(rows, 70)["target_zone"] == "margin"


def test_zone_targeting_reports_the_actual_split():
    rows = [{"chonky_zone": "viewframe"}] * 6 + [{"chonky_zone": "margin"}] * 4
    result = next_zone(rows, 70)
    assert result["stats"]["actual_viewframe_pct"] == 60
    assert result["target_zone"] == "viewframe"


def test_zone_targeting_ignores_unmeasured_rows():
    rows = [{"chonky_zone": ""}, {"chonky_zone": "viewframe"}]
    assert next_zone(rows, 70)["stats"]["total"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/OpenMontage && python -m pytest tests/pipelines/test_chonky_naming.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.chonky.naming'`

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/chonky/naming.py
"""Delivery filenames: {difficulty}_{city}_{country}_{c1}_{c2}_{c3}.jpg"""
from __future__ import annotations

import re
import unicodedata


def slug(text: str) -> str:
    """Lowercase, ASCII, hyphen-separated. Accents survive as their base letter."""
    t = unicodedata.normalize("NFD", str(text))
    t = "".join(ch for ch in t if unicodedata.category(ch) != "Mn")
    t = t.lower()
    t = t.replace("ß", "ss").replace("ø", "o").replace("đ", "d").replace("ł", "l")
    t = t.replace("+", "plus")
    t = re.sub(r"[^a-z0-9]+", "-", t)
    return t.strip("-")


def build_filename(difficulty: str, city: str, country: str, clue_words: list[str]) -> str:
    if len(clue_words) != 3:
        raise ValueError(f"clue_words must contain exactly 3 words, got {len(clue_words)}")
    parts = [slug(difficulty), slug(city), slug(country)] + [slug(w) for w in clue_words]
    return "_".join(parts) + ".jpg"
```

```python
# scripts/chonky/targeting.py
"""Decide which zone the next image's Chonky belongs in.

Compares the configured split against what the ledger actually holds, so a
run converges on the target instead of drifting whichever way the renders
happen to lean.
"""
from __future__ import annotations


def next_zone(rows: list[dict], viewframe_pct: int = 70) -> dict:
    vf = sum(1 for r in rows if r.get("chonky_zone") == "viewframe")
    margin = sum(1 for r in rows if r.get("chonky_zone") == "margin")
    total = vf + margin
    actual = round(100 * vf / total) if total else None

    if total == 0:
        target = "viewframe" if viewframe_pct >= 50 else "margin"
        reason = f"No history yet; following the configured majority of {viewframe_pct}% ViewFrame."
    else:
        target = "viewframe" if actual < viewframe_pct else "margin"
        reason = (f"Ledger is {actual}% ViewFrame against a target of "
                  f"{viewframe_pct}%, so this image goes in the {target} zone.")

    return {
        "target_zone": target,
        "stats": {"viewframe": vf, "margin": margin, "total": total,
                  "actual_viewframe_pct": actual},
        "reason": reason,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/OpenMontage && python -m pytest tests/pipelines/test_chonky_naming.py -v`
Expected: PASS, 8 tests

- [ ] **Step 5: Commit**

```bash
cd ~/OpenMontage
git add scripts/chonky/naming.py scripts/chonky/targeting.py tests/pipelines/test_chonky_naming.py
git commit -m "feat(chonky): delivery filenames and zone targeting"
```

---

### Task 5: Register the model and seed the character

**Files:**
- Modify: `scripts/common/openart_api.py` (the `MODEL_IDS` dict)
- Create: `character_library/chonky/01-model-sheet.png`
- Create: `character_library/chonky/README.md`
- Test: `tests/pipelines/test_chonky_model_route.py`

**Interfaces:**
- Consumes: `scripts.common.openart_api.model_id`.
- Produces: the display name `"GPT Image 2.5 Sunburst"` resolving to `gpt-image-2-5-sunburst`; the character name `"Chonky"` resolving to stills under `character_library/chonky/`.

- [ ] **Step 1: Write the failing test**

```python
# tests/pipelines/test_chonky_model_route.py
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "common"))

import openart_api as api  # noqa: E402
import openart_characters as characters  # noqa: E402


def test_sunburst_has_an_mcp_route():
    assert api.model_id("GPT Image 2.5 Sunburst") == "gpt-image-2-5-sunburst"


def test_flare_has_an_mcp_route():
    assert api.model_id("GPT Image 2.5 Flare") == "gpt-image-2-5-flare"


def test_chonky_character_has_stills():
    stills = characters.stills("Chonky")
    assert stills, "character_library/chonky/ must contain at least one still"
    assert all(p.exists() for p in stills)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/OpenMontage && python -m pytest tests/pipelines/test_chonky_model_route.py -v`
Expected: FAIL — `model_id` raises or returns the raw string, and `stills("Chonky")` is empty

- [ ] **Step 3: Write minimal implementation**

Add to `MODEL_IDS` in `scripts/common/openart_api.py`, immediately after the `"GPT Image 2"` line:

```python
    "GPT Image 2.5 Sunburst": "gpt-image-2-5-sunburst",
    "GPT Image 2.5 Flare":    "gpt-image-2-5-flare",
```

Seed the character from the model sheet already in the Chonky workspace:

```bash
cd ~/OpenMontage
mkdir -p character_library/chonky
cp ~/Claude/Chonky/assets/Main.png character_library/chonky/01-model-sheet.png
cat > character_library/chonky/README.md <<'MD'
# Chonky

The mascot of "Where in the World Is Chonky?" — a very round orange tabby.

`01-model-sheet.png` is the authored five-pose model sheet. It is the identity
reference: body shape, proportions, markings and colour come from it and are
not negotiable. A normal-bodied ginger cat is a failed render.

Two things it is NOT a reference for:
  * **Pose.** Three of its five poses stand upright on hind legs. That is model
    sheet presentation, not instruction. Chonky is always on four paws in a
    normal cat posture.
  * **Rendering style.** It is a studio render on a plain background. Fur,
    lighting and shadow must come from the scene he is placed in.
MD
```

- [ ] **Step 4: Copy the operating manual into the repo**

The prompt-writing rules are content, not code. Copy the authored manual —
which already carries the difficulty ladder, clue economics, the 65-105 px band,
the identity rules and the distance lesson — into the pipeline's docs:

```bash
cd ~/OpenMontage
sed -n '/^=\{10,\}$/,$p' ~/Claude/Chonky/apps-script/Manual.gs \
  | sed '$d' > scripts/chonky/docs/manual.md
head -5 scripts/chonky/docs/manual.md
```

Then correct the two things that changed when the pipeline moved out of Apps
Script: section 6's render settings become `model GPT Image 2.5 Sunburst`,
`aspect 4:5`, `resolution 4K`, `character Chonky` — with `customWidth`,
`customHeight`, `outputFormat` and `outputCompression` deleted, since OpenArt
ignores all four. Section 7's tool names (`inspect_render`, `review_image`)
are replaced by a single line: verification happens in the review UI.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd ~/OpenMontage && python -m pytest tests/pipelines/test_chonky_model_route.py -v`
Expected: PASS, 3 tests

- [ ] **Step 6: Commit**

```bash
cd ~/OpenMontage
git add scripts/common/openart_api.py character_library/chonky \
        scripts/chonky/docs/manual.md tests/pipelines/test_chonky_model_route.py
git commit -m "feat(chonky): register GPT Image 2.5 models, seed the character, carry over the manual"
```

---

### Task 6: Render one image

**Files:**
- Create: `scripts/chonky/render.py`
- Test: `tests/pipelines/test_chonky_render.py`

**Interfaces:**
- Consumes: `scripts.trivia_images.openart_image_driver.generate_image`; `scripts.chonky.geometry`.
- Produces: `render_once(prompt: str, out_path: Path, *, driver=None) -> Path`.

`driver` exists solely so the test can inject a fake — this module must never call OpenArt during a test run. It also enforces the one-call rule structurally: `render_once` calls the driver exactly once and has no retry path.

- [ ] **Step 1: Write the failing test**

```python
# tests/pipelines/test_chonky_render.py
from pathlib import Path

import pytest

from scripts.chonky.render import MODEL, render_once


def test_renders_once_with_the_authored_settings(tmp_path):
    calls = []

    def fake_driver(**kwargs):
        calls.append(kwargs)
        out = kwargs["output_paths"][0]
        out.write_bytes(b"fake")
        return [out]

    out = tmp_path / "r.png"
    result = render_once("a prompt", out, driver=fake_driver)

    assert result == out
    assert len(calls) == 1, "exactly one render call per image"
    assert calls[0]["model"] == MODEL == "GPT Image 2.5 Sunburst"
    assert calls[0]["aspect"] == "4:5"
    assert calls[0]["resolution"] == "4K"
    assert calls[0]["character"] == "Chonky"
    assert len(calls[0]["output_paths"]) == 1


def test_never_passes_custom_dimensions(tmp_path):
    # OpenArt ignores them; passing them invites the belief that they work.
    calls = []

    def fake_driver(**kwargs):
        calls.append(kwargs)
        kwargs["output_paths"][0].write_bytes(b"fake")
        return kwargs["output_paths"]

    render_once("p", tmp_path / "r.png", driver=fake_driver)
    assert "customWidth" not in calls[0]
    assert "customHeight" not in calls[0]


def test_propagates_driver_failure_without_retrying(tmp_path):
    calls = []

    def failing_driver(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("content policy")

    with pytest.raises(RuntimeError):
        render_once("p", tmp_path / "r.png", driver=failing_driver)
    assert len(calls) == 1, "a failure must not silently re-submit"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/OpenMontage && python -m pytest tests/pipelines/test_chonky_render.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.chonky.render'`

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/chonky/render.py
"""Submit exactly one OpenArt render for one image.

There is no retry and no loop in here on purpose. Duplicate submissions were
the single most expensive failure of the previous ChatGPT-driven build; here
one call to render_once is one image, and a reroll is an explicit new call by
a caller that has stated why.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, Optional

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

MODEL = "GPT Image 2.5 Sunburst"
ASPECT = "4:5"          # returns 2048x2560; customWidth/Height are ignored
RESOLUTION = "4K"
CHARACTER = "Chonky"


def render_once(prompt: str, out_path: Path, *, driver: Optional[Callable] = None) -> Path:
    """Render `prompt` to `out_path`. One call, no retries."""
    if driver is None:
        from scripts.trivia_images.openart_image_driver import generate_image
        driver = generate_image

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    saved = driver(
        prompt=prompt,
        model=MODEL,
        output_paths=[out_path],
        aspect=ASPECT,
        resolution=RESOLUTION,
        character=CHARACTER,
    )
    return Path(saved[0])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/OpenMontage && python -m pytest tests/pipelines/test_chonky_render.py -v`
Expected: PASS, 3 tests

- [ ] **Step 5: Commit**

```bash
cd ~/OpenMontage
git add scripts/chonky/render.py tests/pipelines/test_chonky_render.py
git commit -m "feat(chonky): single-submission OpenArt render"
```

---

### Task 7: Review UI

**Files:**
- Create: `scripts/chonky/web/__init__.py` (empty)
- Create: `scripts/chonky/web/server.py`
- Create: `scripts/chonky/web/index.html`
- Modify: `web/server.py` (add the import and mount)
- Test: `tests/pipelines/test_chonky_web.py`

**Interfaces:**
- Consumes: everything above.
- Produces: a FastAPI `app` mounted at `/chonky`.

Routes, mirroring `scripts/trivia_images/web/server.py`:

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | serves `index.html` |
| GET | `/api/health` | geometry + config echo, for a fast sanity check |
| POST | `/api/prompts` | accept pasted prompt TSV, return parsed rows with assigned zones |
| POST | `/api/run` | render one row; returns a job id |
| GET | `/api/jobs/{job_id}` | job status |
| GET | `/api/frame/{slug}` | full frame PNG |
| GET | `/api/viewframe/{slug}` | ViewFrame crop PNG |
| POST | `/api/measure` | re-measure with a caller-supplied box; returns the verdict |
| POST | `/api/approve` | encode, upload to Drive, append the sheet row |

- [ ] **Step 1: Write the failing test**

```python
# tests/pipelines/test_chonky_web.py
from fastapi.testclient import TestClient

from scripts.chonky.web.server import app

client = TestClient(app)


def test_health_reports_the_authored_geometry():
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["image"] == "2048x2560"
    assert body["viewframe"] == "1200x2133 at x 424-1624, y 213-2346"
    assert body["chonky_height_px"] == "65-105"


def test_prompts_endpoint_parses_tsv_and_assigns_zones():
    tsv = "Difficulty\tLocation\tprompt\n1\tLisbon, Portugal\tA photo of Rua Augusta\n"
    r = client.post("/api/prompts", json={"tsv": tsv})
    assert r.status_code == 200
    rows = r.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["difficulty"] == "1"
    assert rows[0]["location"] == "Lisbon, Portugal"
    assert rows[0]["target_zone"] in ("viewframe", "margin")


def test_measure_endpoint_accepts_a_corrected_box():
    r = client.post("/api/measure", json={"box": [1232, 1709, 1318, 1798]})
    assert r.status_code == 200
    body = r.json()
    assert body["height_px"] == 89
    assert body["zone"] == "viewframe"
    assert body["ok"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/OpenMontage && python -m pytest tests/pipelines/test_chonky_web.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.chonky.web'`

- [ ] **Step 3: Write minimal implementation**

Create `scripts/chonky/web/server.py` with the mount contract copied from `scripts/trivia_images/web/server.py` — the module docstring must state that `index.html` sets `<base href="/chonky/">` and that the app must be reached through the mount. Implement the three routes the test exercises first (`/api/health`, `/api/prompts`, `/api/measure`), each delegating to the modules from Tasks 1–4; add the render and approve routes after the test passes.

`index.html` sets `<base href="/chonky/">`, uses relative `fetch("api/…")` URLs, and lays out one card per image: full frame with an SVG rectangle overlay bound to the measured box and draggable via pointer events, the ViewFrame crop beside it, height and zone badges that recolour on the verdict, three editable clue fields with a live word counter capped at 12, and Reroll / Approve buttons.

Add to `web/server.py`, in the existing import block and mount block:

```python
from scripts.chonky.web import server as chonky
...
app.mount("/chonky", chonky.app)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/OpenMontage && python -m pytest tests/pipelines/test_chonky_web.py -v`
Expected: PASS, 3 tests

- [ ] **Step 5: Commit**

```bash
cd ~/OpenMontage
git add scripts/chonky/web web/server.py tests/pipelines/test_chonky_web.py
git commit -m "feat(chonky): review UI mounted at /chonky"
```

---

---

### Task 8: Delivery — Drive and the sheet

**Files:**
- Create: `scripts/chonky/deliver.py`
- Test: `tests/pipelines/test_chonky_deliver.py`

**Interfaces:**
- Consumes: `scripts.chonky.imaging.encode_delivery`, `scripts.chonky.naming.build_filename`, `scripts.trivia_images.drive_config`.
- Produces: `deliver(img, *, difficulty, city, country, clue_words, measurement, uploader=None, sheet_writer=None) -> dict` returning `{"filename": str, "kb": int, "quality": int, "drive_link": str, "row": dict}`.

`uploader` and `sheet_writer` are injectable so the test never touches Drive.
The row carries the measured values verbatim — guessing them corrupts the zone
targeting that Task 4 computes from them.

- [ ] **Step 1: Write the failing test**

```python
# tests/pipelines/test_chonky_deliver.py
from PIL import Image

from scripts.chonky.deliver import deliver
from scripts.chonky.geometry import IMG_W, IMG_H


def _img():
    import random
    rnd = random.Random(7)
    im = Image.new("RGB", (IMG_W, IMG_H))
    px = im.load()
    for y in range(IMG_H):
        for x in range(IMG_W):
            px[x, y] = (rnd.randrange(256), rnd.randrange(256), rnd.randrange(256))
    return im


def test_deliver_names_encodes_and_records():
    uploaded = {}
    rows = []

    def fake_uploader(data, filename):
        uploaded["filename"] = filename
        uploaded["bytes"] = len(data)
        return "https://drive.google.com/file/d/FAKE/view"

    result = deliver(
        _img(),
        difficulty="1", city="Lisbon", country="Portugal",
        clue_words=["arch", "calcada", "clock"],
        measurement={"height_px": 89, "zone": "viewframe", "box": (1232, 1709, 1318, 1798)},
        uploader=fake_uploader,
        sheet_writer=rows.append,
    )

    assert result["filename"] == "1_lisbon_portugal_arch_calcada_clock.jpg"
    assert 700 <= result["kb"] <= 1200
    assert result["drive_link"].startswith("https://drive.google.com/")
    assert uploaded["filename"] == result["filename"]


def test_row_carries_the_measured_values_verbatim():
    rows = []
    deliver(
        _img(),
        difficulty="1", city="Lisbon", country="Portugal",
        clue_words=["arch", "calcada", "clock"],
        measurement={"height_px": 89, "zone": "viewframe", "box": (1232, 1709, 1318, 1798)},
        uploader=lambda d, f: "https://drive.google.com/file/d/FAKE/view",
        sheet_writer=rows.append,
    )
    row = rows[0]
    assert row["chonky_px_h"] == 89
    assert row["chonky_zone"] == "viewframe"
    assert row["chonky_x"] == 1232
    assert row["chonky_y"] == 1709
    assert row["status"] == "verified"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/OpenMontage && python -m pytest tests/pipelines/test_chonky_deliver.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.chonky.deliver'`

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/chonky/deliver.py
"""Encode, upload and record one approved image.

Only called from the UI's Approve action. Nothing reaches Drive or the sheet
without a human having looked at the render and its ViewFrame.
"""
from __future__ import annotations

import datetime as _dt
from typing import Callable, Optional

from PIL import Image

from scripts.chonky.imaging import encode_delivery
from scripts.chonky.naming import build_filename


def _default_uploader(data: bytes, filename: str) -> str:
    from scripts.trivia_images import drive_config
    return drive_config.upload_bytes(data, filename)


def deliver(
    img: Image.Image,
    *,
    difficulty: str,
    city: str,
    country: str,
    clue_words: list[str],
    measurement: dict,
    clues: Optional[list[str]] = None,
    prompt: str = "",
    viewpoint: str = "",
    uploader: Optional[Callable[[bytes, str], str]] = None,
    sheet_writer: Optional[Callable[[dict], None]] = None,
) -> dict:
    filename = build_filename(difficulty, city, country, clue_words)
    data, quality = encode_delivery(img)

    upload = uploader or _default_uploader
    drive_link = upload(data, filename)

    box = measurement.get("box") or (None, None, None, None)
    clues = clues or ["", "", ""]
    row = {
        "date": _dt.date.today().isoformat(),
        "difficulty": difficulty,
        "city": city,
        "country": country,
        "viewpoint": viewpoint,
        "prompt": prompt,
        "clue1": clues[0],
        "clue2": clues[1],
        "clue3": clues[2],
        "filename": filename,
        "drive_link": drive_link,
        "status": "verified",
        "chonky_zone": measurement.get("zone"),
        "chonky_px_h": measurement.get("height_px"),
        "chonky_x": box[0],
        "chonky_y": box[1],
    }
    if sheet_writer is not None:
        sheet_writer(row)

    return {"filename": filename, "kb": len(data) // 1024, "quality": quality,
            "drive_link": drive_link, "row": row}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/OpenMontage && python -m pytest tests/pipelines/test_chonky_deliver.py -v`
Expected: PASS, 2 tests

- [ ] **Step 5: Wire it to the real Drive helper**

Open `scripts/trivia_images/drive_config.py` and find the function that uploads
bytes to a folder. If its name is not `upload_bytes`, correct `_default_uploader`
above to call the real one — do not add a wrapper in `drive_config.py`.

- [ ] **Step 6: Commit**

```bash
cd ~/OpenMontage
git add scripts/chonky/deliver.py tests/pipelines/test_chonky_deliver.py
git commit -m "feat(chonky): approved-image delivery to Drive and the sheet"
```

## Verification

- `cd ~/OpenMontage && python -m pytest tests/ -v` — the whole suite, including the pipelines already in the repo, must stay green.
- `uvicorn web.server:app --port 8765 --reload`, then open `http://127.0.0.1:8765/chonky/`.
- End-to-end smoke: paste one difficulty-1 prompt, render it, and confirm from the UI that Chonky's measured height is 65–105 px, his zone matches the assignment, the delivered JPEG is 700 KB–1.2 MB, and the Drive file plus sheet row both appear.
- Reference point from the loop run on 2026-09-21: a Rua Augusta render measured 89 px at x 1232–1318, y 1709–1798, classified `viewframe`, delivered at 1116 KB. Any regression in the geometry or measurement code should still reproduce those numbers from that image.
