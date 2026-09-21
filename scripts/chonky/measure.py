"""Measure Chonky in a render and judge him against the authored rules.

HOW THIS IS MEANT TO BE USED
----------------------------
`verify(img, box=...)` with an explicit box is the real interface. The review
UI draws a rectangle over the render, the operator drags it onto the cat, and
the numbers that reach the sheet come from that. One drag, exact every time.

`detect_chonky()` is a BEST-EFFORT HINT to pre-position that rectangle. Do not
trust it unattended, and do not gate delivery on it.

WHY DETECTION IS ONLY A HINT
----------------------------
Finding a ginger cat by colour in a photograph of a sunlit street does not
work, and this was established by measurement rather than assumed. Against the
real Rua Augusta render (Chonky truly at 89 px, x 1232-1318):

  * bounding box of every ginger pixel      -> 2559 px, the whole frame
  * largest compact connected blob          -> 115 px, a sunlit facade
  * same, requiring a white bib inside it   -> 115 px, the same facade

Golden-hour stone is warm, dense, and flecked with bright neutral highlights,
so it satisfies every cheap test a small ginger cat does. Separating them
reliably needs a real detector, not a threshold — which is a bigger piece of
work than the one drag it would save.
"""
from __future__ import annotations

from typing import Optional

from PIL import Image

from scripts.chonky.geometry import classify_zone, size_verdict

Box = tuple[int, int, int, int]

# Saturated ginger: strongly red, with clear R>G>B separation and a dark blue
# channel. Pale warm stone fails the b < 120 test; shadowed brick fails r > 150.
_R_MIN, _RG_MIN, _GB_MIN, _B_MAX = 150, 55, 25, 120


def _is_ginger(px) -> bool:
    r, g, b = px[0], px[1], px[2]
    return r > _R_MIN and (r - g) > _RG_MIN and (g - b) > _GB_MIN and b < _B_MAX


def _is_bib(px) -> bool:
    """Chonky's white belly bib and socks: bright and close to neutral.

    This is the signal that separates him from a sunlit facade. Warm stone is
    bright but never neutral; his bib is.
    """
    r, g, b = px[0], px[1], px[2]
    return min(r, g, b) > 165 and (max(r, g, b) - min(r, g, b)) < 45


def _mask(img: Image.Image, scale: int) -> tuple[list[list[bool]], int, int]:
    """Binary ginger mask of the image downsampled by `scale`."""
    w, h = img.size
    small = img.convert("RGB").resize((w // scale, h // scale), Image.NEAREST)
    sw, sh = small.size
    px = small.load()
    return [[_is_ginger(px[x, y]) for x in range(sw)] for y in range(sh)], sw, sh


def _blobs(mask: list[list[bool]], sw: int, sh: int) -> list[dict]:
    """Connected components of the mask, 4-connected, with shape stats."""
    seen = [[False] * sw for _ in range(sh)]
    out: list[dict] = []
    for sy in range(sh):
        for sx in range(sw):
            if not mask[sy][sx] or seen[sy][sx]:
                continue
            stack = [(sx, sy)]
            seen[sy][sx] = True
            xs, ys = [], []
            while stack:
                x, y = stack.pop()
                xs.append(x)
                ys.append(y)
                for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                    if 0 <= nx < sw and 0 <= ny < sh and mask[ny][nx] and not seen[ny][nx]:
                        seen[ny][nx] = True
                        stack.append((nx, ny))
            x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
            bw, bh = x1 - x0 + 1, y1 - y0 + 1
            out.append({
                "area": len(xs),
                "box": (x0, y0, x1, y1),
                "fill": len(xs) / (bw * bh),
                "aspect": bw / bh if bh else 0.0,
            })
    return out


def detect_chonky(img: Image.Image) -> Optional[Box]:
    """BEST-EFFORT hint at where Chonky is. Frequently wrong on real photos.

    Returns the largest compact ginger blob carrying some white. Good enough to
    pre-position the UI rectangle on a clean scene; see the module docstring
    for why it should never be trusted unattended.

    Taking the bounding box of every ginger pixel in the frame does not work:
    a golden-hour street is full of warm stone, and the real Rua Augusta render
    returned the entire 2048x2560 frame that way. Chonky is instead found as a
    COMPACT CONNECTED BLOB — scattered scene warmth breaks into many small,
    sparse components, while he is one dense one.
    """
    rgb = img.convert("RGB")
    w, h = rgb.size
    scale = 4 if min(w, h) >= 400 else 1

    mask, sw, sh = _mask(rgb, scale)
    blobs = _blobs(mask, sw, sh)
    if not blobs:
        return None

    # A cat is solid and roughly as wide as tall. Sunlit paving is neither.
    candidates = [b for b in blobs if b["fill"] >= 0.35 and 0.35 <= b["aspect"] <= 3.0]
    if not candidates:
        return None

    # Shape and colour alone still pick sunlit facades out of a golden-hour
    # street. What no building has is Chonky's white bib sitting INSIDE the
    # ginger. Score each candidate on how much neutral-bright pixel sits in
    # its box, and prefer a blob that has some.
    px_full = rgb.load()
    for b in candidates:
        bx0_, by0_, bx1_, by1_ = b["box"]
        fx0, fy0 = bx0_ * scale, by0_ * scale
        fx1 = min(w - 1, (bx1_ + 1) * scale)
        fy1 = min(h - 1, (by1_ + 1) * scale)
        bib = total = 0
        for yy in range(fy0, fy1 + 1, 2):
            for xx in range(fx0, fx1 + 1, 2):
                total += 1
                if _is_bib(px_full[xx, yy]):
                    bib += 1
        b["bib"] = bib / total if total else 0.0

    with_bib = [b for b in candidates if b["bib"] >= 0.04]
    best = max(with_bib or candidates, key=lambda b: b["area"])

    bx0, by0, bx1, by1 = best["box"]
    # Back to full resolution, padded, then refine the edges at 1 px so the
    # measurement is exact at the boundaries of the 65-105 band.
    pad = 2 * scale
    rx0 = max(0, bx0 * scale - pad)
    ry0 = max(0, by0 * scale - pad)
    rx1 = min(w - 1, (bx1 + 1) * scale + pad)
    ry1 = min(h - 1, (by1 + 1) * scale + pad)

    px = rgb.load()
    rows = [0] * (ry1 - ry0 + 1)
    cols = [0] * (rx1 - rx0 + 1)
    for y in range(ry0, ry1 + 1):
        for x in range(rx0, rx1 + 1):
            if _is_ginger(px[x, y]):
                rows[y - ry0] += 1
                cols[x - rx0] += 1

    def _extent(counts: list[int], origin: int) -> Optional[tuple[int, int]]:
        """First and last index carrying real substance, not a stray pixel.

        A single warm speck in the padding would otherwise stretch the box —
        that is what put the measurement 9 px out. Requiring a fifth of the
        densest line keeps the cat's own soft edges and drops the noise.
        """
        peak = max(counts) if counts else 0
        if peak == 0:
            return None
        floor = max(2, peak // 5)
        hits = [i for i, c in enumerate(counts) if c >= floor]
        return (origin + hits[0], origin + hits[-1]) if hits else None

    ext_y = _extent(rows, ry0)
    ext_x = _extent(cols, rx0)
    if ext_x is None or ext_y is None:
        return (bx0 * scale, by0 * scale, bx1 * scale, by1 * scale)
    return (ext_x[0], ext_y[0], ext_x[1], ext_y[1])


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
