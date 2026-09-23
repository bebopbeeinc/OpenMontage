"""Real pixel sizes in the resolution dropdown.

OpenArt's tiers are named 1k/2k/4k and produce sizes that are not round
numbers — 4:5 at 4k comes back 2048x2560 — so a tier tells an operator
nothing about what they will get. customWidth/customHeight are honoured
(verified: 2048x2048 requested on a 4:5 aspect, 2048x2048 returned), so the
sizes can be named exactly.
"""

import pytest

from scripts.chonky import render as R


@pytest.mark.parametrize("aspect", R.ASPECT_RATIOS)
def test_every_offered_ratio_has_sizes(aspect):
    assert R.sizes_for(aspect), f"no sizes offered for {aspect}"


@pytest.mark.parametrize("aspect", R.ASPECT_RATIOS)
def test_the_sizes_hold_the_ratio_they_are_for(aspect):
    w_part, h_part = (int(p) for p in aspect.split(":"))
    want = w_part / h_part
    for w, h in R.sizes_for(aspect):
        assert abs(w / h - want) / want < 0.01, f"{w}x{h} is not {aspect}"


@pytest.mark.parametrize("aspect", R.ASPECT_RATIOS)
def test_the_sizes_are_on_an_eight_pixel_grid(aspect):
    """A grid keeps the sizes tidy; 8 rather than 16 so extreme ratios stay true.

    At 16 a 21:9 frame came out 1.6% off, which reads as a letterbox.
    """
    for w, h in R.sizes_for(aspect):
        assert w % 8 == 0 and h % 8 == 0, f"{w}x{h} is off the grid"


def test_a_square_ratio_offers_the_square_sizes():
    assert (4096, 4096) in R.sizes_for("1:1")
    assert (1024, 1024) in R.sizes_for("1:1")


def test_the_authored_frame_is_offered_for_four_by_five():
    """2048x2560 is the frame every constant in geometry.py describes."""
    assert (2048, 2560) in R.sizes_for("4:5")


def test_sizes_run_smallest_to_largest():
    sizes = R.sizes_for("4:5")
    assert sizes == sorted(sizes, key=lambda s: s[0] * s[1])


def test_an_unknown_ratio_has_no_sizes():
    with pytest.raises(ValueError):
        R.sizes_for("7:11")
