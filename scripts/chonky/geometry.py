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
