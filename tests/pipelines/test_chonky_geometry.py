from scripts.chonky.geometry import (
    IMG_W, IMG_H, VF_W, VF_H, VF_X0, VF_Y0, VF_X1, VF_Y1,
    classify_zone, size_verdict,
)
from scripts.chonky import geometry


def test_frame_matches_what_openart_actually_returns():
    assert (IMG_W, IMG_H) == (2048, 2560)
    assert IMG_W / IMG_H == 0.8


def test_viewframe_is_centred_and_9x16():
    assert (VF_W, VF_H) == (1200, 2133)
    assert (VF_X0, VF_X1) == (424, 1624)
    assert (VF_Y0, VF_Y1) == (213, 2346)
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


# --------------------------------------------------------------------------
# Renders do not always arrive at the reference size.
#
# The server's driver returned a 1344x1680 Prague render while every constant
# here describes 2048x2560. Judged against the raw constants, the ViewFrame's
# bottom edge sat 666 px below the bottom of the picture.
# --------------------------------------------------------------------------

SMALL = (1344, 1680)


def test_the_viewframe_fits_inside_a_smaller_render():
    x0, y0, x1, y1 = geometry.viewframe_box(SMALL)
    assert 0 <= x0 < x1 <= SMALL[0]
    assert 0 <= y0 < y1 <= SMALL[1], "ViewFrame must not fall outside the picture"


def test_the_viewframe_stays_centred_at_any_size():
    for size in [(2048, 2560), SMALL, (4096, 5120)]:
        x0, y0, x1, y1 = geometry.viewframe_box(size)
        # The reference frame is one pixel off-centre vertically by design —
        # the odd pixel goes to the bottom — so at scale s that becomes s.
        tol = geometry.scale_for(size) + 1
        assert abs(x0 - (size[0] - x1)) <= tol, f"not horizontally centred at {size}"
        assert abs(y0 - (size[1] - y1)) <= tol, f"not vertically centred at {size}"


def test_the_size_band_scales_with_the_frame():
    lo, hi = geometry.size_band(SMALL)
    ref_lo, ref_hi = geometry.CHONKY_MIN_H, geometry.CHONKY_MAX_H
    assert lo < ref_lo and hi < ref_hi
    # Same fraction of the frame, which is what the rule actually says.
    assert abs(lo / SMALL[1] - ref_lo / geometry.IMG_H) < 0.001
    assert abs(hi / SMALL[1] - ref_hi / geometry.IMG_H) < 0.001


def test_a_cat_in_band_for_a_small_render_is_not_called_too_small():
    """The same cat, the same picture, judged twice: only the ruler differs."""
    lo, hi = geometry.size_band(SMALL)
    good = (lo + hi) // 2
    assert geometry.size_verdict(good, SMALL) == "ok"
    # Against the unscaled constants that very cat reads as a failure.
    assert geometry.size_verdict(good) == "too_small"


def test_the_prague_cat_is_too_big_at_its_own_scale():
    """278 px in a 1680-tall frame is a sixth of the picture, not a near miss."""
    assert geometry.size_verdict(278, SMALL) == "too_big"


def test_zone_is_judged_in_the_frame_the_box_came_from():
    """The Prague box sits inside the ViewFrame of the render it was measured in."""
    assert geometry.classify_zone(465, 729, 1014, 1292, SMALL) == "viewframe"


def test_omitting_the_size_keeps_the_reference_behaviour():
    assert geometry.viewframe_box() == (geometry.VF_X0, geometry.VF_Y0,
                                        geometry.VF_X1, geometry.VF_Y1)
    assert geometry.size_band() == (geometry.CHONKY_MIN_H, geometry.CHONKY_MAX_H)
