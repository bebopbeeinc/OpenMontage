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
