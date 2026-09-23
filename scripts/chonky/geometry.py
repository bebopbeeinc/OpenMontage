"""Authored frame geometry for the Chonky pipeline.

The authored rules are proportional: the ViewFrame is a centred 9:16 window on
a 4:5 source, and Chonky stands 3-5% of the ViewFrame's height. The pixel
constants below are those rules written out at the reference size, kept
because the manual, the UI and the sheet all quote them.

They are not the rule itself. A render that comes back at 1344x1680 — which
the server's driver does return, whatever resolution is requested — is still a
4:5 frame with the same ViewFrame and the same size band in proportion, but
every one of these constants is wrong for it by a third. Measuring such a
render against them put the ViewFrame's bottom edge 666 px below the picture
and judged a cat filling a sixth of the frame as merely "too big", the right
verdict reached with the wrong arithmetic.

So the functions take the frame they are judging. The constants remain as the
reference, and `scale_for()` maps them onto whatever actually arrived.
"""
from __future__ import annotations

IMG_W, IMG_H = 2048, 2560       # the reference frame the constants describe
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


def scale_for(size: tuple[int, int] | None) -> float:
    """How much bigger the given frame is than the reference frame.

    Keyed on height, because the size band and the ViewFrame are both vertical
    measurements and the aspect is fixed at 4:5.
    """
    if not size:
        return 1.0
    return size[1] / IMG_H


def viewframe_box(size: tuple[int, int] | None = None) -> tuple[int, int, int, int]:
    """The ViewFrame rectangle in the coordinates of the frame given."""
    s = scale_for(size)
    return (round(VF_X0 * s), round(VF_Y0 * s), round(VF_X1 * s), round(VF_Y1 * s))


def size_band(size: tuple[int, int] | None = None) -> tuple[int, int]:
    """The 65-105 px band expressed in the coordinates of the frame given."""
    s = scale_for(size)
    return (round(CHONKY_MIN_H * s), round(CHONKY_MAX_H * s))


def classify_zone(x0: int, x1: int, y0: int, y1: int,
                  size: tuple[int, int] | None = None) -> str:
    """Where a bounding box sits relative to the ViewFrame.

    "viewframe"    wholly inside, clear of the central third — the good case
    "margin"       wholly outside — a pan-reveal reward, also good
    "centrestage"  inside the central third — he is not the subject
    "straddling"   half in, half out — the player sees a fragment of cat

    `size` is the frame the box was measured in. Omit it only when the box is
    already in reference coordinates.
    """
    s = scale_for(size)
    vf_x0, vf_y0, vf_x1, vf_y1 = (VF_X0 * s, VF_Y0 * s, VF_X1 * s, VF_Y1 * s)
    c_x0, c_x1 = _CENTRE_X0 * s, _CENTRE_X1 * s

    inside = vf_x0 <= x0 and x1 <= vf_x1 and vf_y0 <= y0 and y1 <= vf_y1
    if not inside:
        overlaps = x1 > vf_x0 and x0 < vf_x1 and y1 > vf_y0 and y0 < vf_y1
        return "straddling" if overlaps else "margin"
    if c_x0 <= x0 and x1 <= c_x1:
        return "centrestage"
    return "viewframe"


def size_verdict(height_px: int, size: tuple[int, int] | None = None) -> str:
    """Too small is as much a failure as too big — he has to be findable."""
    lo, hi = size_band(size)
    if height_px < lo:
        return "too_small"
    if height_px > hi:
        return "too_big"
    return "ok"
