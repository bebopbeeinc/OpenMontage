"""Resize + lossless-optimize trivia images to the game's 512×384 (4:3) target.

The game consumes 512×384 PNGs (true 4:3 — 512/384 = 1.333…). OpenArt's 4:3
"2K" renders come back at ~2400×1792 (≈1.339), so resizing straight to 512×384
is essentially a clean downscale with a sub-half-percent aspect nudge — no crop,
no visible distortion. Output is a **truly lossless** optimized PNG: Pillow's
LANCZOS resample for the downscale + `optimize=True, compress_level=9` for the
encode. No palette quantization, so nothing is thrown away beyond the
resolution the game doesn't use.

Shared by the CLI (generate.py), the web server (server.py), and the one-time
Drive batch (optimize_drive.py).
"""
from __future__ import annotations

import io
from pathlib import Path

# The game's expected asset size. 512×384 is true 4:3 (the feedback's "512×360"
# was a typo — 512×360 is 64:45, which would distort a 4:3 render).
GAME_WIDTH = 512
GAME_HEIGHT = 384


def optimize_image_bytes(
    data: bytes, *, width: int = GAME_WIDTH, height: int = GAME_HEIGHT
) -> bytes:
    """Downscale `data` to width×height and return optimized lossless PNG bytes.

    The renders are 4:3 and the target is 4:3, so a direct resize is a clean
    scale (no crop/letterbox needed). Alpha is preserved when present;
    everything else is flattened to RGB to keep PNGs lean.
    """
    from PIL import Image

    src = Image.open(io.BytesIO(data))
    if src.mode == "P":
        # Palette images may carry transparency; promote to RGBA so the
        # resample is correct, then it stays RGBA below.
        src = src.convert("RGBA")
    elif src.mode not in ("RGB", "RGBA"):
        src = src.convert("RGBA" if "A" in src.getbands() else "RGB")

    if src.size != (width, height):
        src = src.resize((width, height), Image.LANCZOS)

    out = io.BytesIO()
    src.save(out, format="PNG", optimize=True, compress_level=9)
    return out.getvalue()


def optimize_file(
    src: Path, *, dest: Path | None = None,
    width: int = GAME_WIDTH, height: int = GAME_HEIGHT,
) -> Path:
    """Optimize the image at `src` to a 512×384 lossless PNG.

    Writes to `dest` if given, else to `src` with a `.png` suffix. When the
    source had a non-`.png` extension (`.jpg`/`.webp`), the original file is
    removed after the `.png` is written so the on-disk library holds exactly
    one file per slug. Returns the path written.
    """
    src = Path(src)
    png = optimize_image_bytes(src.read_bytes(), width=width, height=height)
    target = Path(dest) if dest is not None else src.with_suffix(".png")
    target.write_bytes(png)
    if src.suffix.lower() != ".png" and src.resolve() != target.resolve() and src.exists():
        src.unlink()
    return target
