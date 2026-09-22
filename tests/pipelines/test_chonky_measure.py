from PIL import Image

from scripts.chonky.geometry import IMG_W, IMG_H
from scripts.chonky.measure import _detect_by_colour, detect_chonky, verify


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
    box = _detect_by_colour(img)
    assert box is not None
    x0, y0, x1, y1 = box
    assert abs(x0 - 1232) <= 2 and abs(y0 - 1709) <= 2
    assert abs((y1 - y0) - 89) <= 2


def test_returns_none_when_there_is_no_cat():
    assert _detect_by_colour(_blank()) is None


def test_verify_passes_a_good_render():
    result = verify(_with_patch(1232, 1709, 86, 89), detector=_detect_by_colour)
    assert result["size"] == "ok"
    assert result["zone"] == "viewframe"
    assert result["ok"] is True


def test_verify_fails_an_oversized_cat():
    result = verify(_with_patch(1232, 1500, 250, 310), detector=_detect_by_colour)
    assert result["size"] == "too_big"
    assert result["ok"] is False


def test_verify_fails_centrestage():
    result = verify(_with_patch(900, 1709, 86, 89), detector=_detect_by_colour)
    assert result["zone"] == "centrestage"
    assert result["ok"] is False


def test_explicit_box_overrides_detection():
    img = _with_patch(1232, 1709, 86, 89)
    result = verify(img, box=(500, 300, 586, 389))
    assert result["box"] == (500, 300, 586, 389)
    assert result["height_px"] == 89
    assert result["zone"] == "viewframe"


def test_verify_handles_no_detection():
    result = verify(_blank(), detector=_detect_by_colour)
    assert result["box"] is None
    assert result["ok"] is False


def _with_warm_scene_noise(img, seed=3, fraction=0.06):
    """Scatter warm, ginger-passing pixels across the frame.

    This is the Lisbon failure in miniature: golden-hour stone, awnings and
    sunlit paving all satisfy any "is this ginger" test, so a detector that
    takes the bounding box of every matching pixel returns the whole frame.
    """
    import random
    rnd = random.Random(seed)
    w, h = img.size
    for _ in range(int(w * h * fraction / 100)):
        x, y = rnd.randrange(w), rnd.randrange(h)
        img.putpixel((x, y), (205, 125, 55))
    return img


def test_finds_the_cat_despite_a_warm_scene():
    # Regression: the real Rua Augusta render measured 2559 px (the whole
    # frame) before detection looked for a compact blob instead of a global bbox.
    img = _with_warm_scene_noise(_with_patch(1232, 1709, 86, 89))
    box = _detect_by_colour(img)
    assert box is not None
    x0, y0, x1, y1 = box
    assert abs((y1 - y0) - 89) <= 4, f"got {y1 - y0} px, expected ~89"
    assert abs(x0 - 1232) <= 8, f"box starts at x={x0}, expected ~1232"


def test_warm_scene_still_verifies_as_a_pass():
    img = _with_warm_scene_noise(_with_patch(1232, 1709, 86, 89))
    result = verify(img, detector=_detect_by_colour)
    assert result["size"] == "ok", result
    assert result["zone"] == "viewframe", result
    assert result["ok"] is True


def test_an_explicit_box_is_always_authoritative():
    """The UI's dragged box is the real measurement path.

    Detection is unreliable on real photographs (see the module docstring), so
    the contract that matters is this one: whatever box the operator supplies
    is measured and judged exactly, regardless of what detection thinks.
    """
    img = _with_warm_scene_noise(_with_patch(300, 300, 400, 400))  # detection will pick the big patch
    result = verify(img, box=(1232, 1709, 1318, 1798))
    assert result["box"] == (1232, 1709, 1318, 1798)
    assert result["height_px"] == 89
    assert result["size"] == "ok"
    assert result["zone"] == "viewframe"
    assert result["ok"] is True


def test_detector_is_injectable_and_wins():
    """The model path is swappable, so tests never download 22 MB of weights."""
    called = []

    def fake(img):
        called.append(img)
        return (1232, 1709, 1318, 1798)

    result = verify(_blank(), detector=fake)
    assert len(called) == 1
    assert result["height_px"] == 89
    assert result["zone"] == "viewframe"
    assert result["ok"] is True


def test_a_supplied_box_skips_detection_entirely():
    def exploding(img):
        raise AssertionError("detection must not run when a box is supplied")

    result = verify(_blank(), box=(1232, 1709, 1318, 1798), detector=exploding)
    assert result["height_px"] == 89


def test_detection_falls_back_to_colour_when_no_model(monkeypatch):
    import scripts.chonky.measure as m
    monkeypatch.setattr(m, "_detect_by_model", lambda img: None)
    img = _with_patch(1232, 1709, 86, 89)
    box = m.detect_chonky(img)
    assert box is not None, "must still find him via the colour fallback"
    assert abs((box[3] - box[1]) - 89) <= 4
