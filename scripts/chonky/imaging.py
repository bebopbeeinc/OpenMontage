"""Cropping and delivery encoding.

OpenArt ignores outputFormat and hands back ~9 MB PNGs, so every delivered
asset is re-encoded here. This is the step Apps Script could not do at all,
and a large part of why the pipeline moved into Python.
"""
from __future__ import annotations

import io

from PIL import Image

from scripts.chonky.geometry import VF_H, VF_W, VF_X0, VF_Y0

# Descending quality ladder. 92 usually overshoots the size target and 70 is
# visibly soft on fine clues like road markings, so the useful range is between.
_QUALITY_LADDER = (92, 88, 85, 82, 78, 74, 70)


def viewframe_crop(img: Image.Image) -> Image.Image:
    """The 9:16 window the player opens on, before any panning."""
    return img.crop((VF_X0, VF_Y0, VF_X0 + VF_W, VF_Y0 + VF_H))


def _encode(img: Image.Image, quality: int) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(
        buf, format="JPEG", quality=quality, optimize=True, progressive=True
    )
    return buf.getvalue()


def encode_delivery(
    img: Image.Image, min_kb: int = 700, max_kb: int = 1200
) -> tuple[bytes, int]:
    """Encode to JPEG inside the size envelope.

    Walks quality down and returns the first result that fits under `max_kb`,
    which is the highest quality that fits. If nothing fits, returns the
    lowest-quality attempt rather than raising: the spec prefers a slightly
    large file over a crushed clue.
    """
    best: tuple[bytes, int] | None = None
    for quality in _QUALITY_LADDER:
        data = _encode(img, quality)
        best = (data, quality)
        if len(data) // 1024 <= max_kb:
            return data, quality
    assert best is not None
    return best
