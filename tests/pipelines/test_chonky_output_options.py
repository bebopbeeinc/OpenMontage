"""Aspect ratio and resolution, as OpenArt actually names them.

The driver has been sending {"resolution": "4K"} to a schema whose field is
`resolutionTier` with values 1k/2k/4k and which forbids unknown keys. Every
render came back at the 2k default — 1344x1680 for 4:5 — while the geometry
constants described the 2048x2560 that 4k produces.
"""

import pytest

from scripts.chonky import render as R


def test_the_resolution_reaches_openart_under_the_name_it_expects():
    sent = {}
    R.render_once("p", "/tmp/chonky-opt.png",
                  driver=lambda **kw: sent.update(kw) or [kw["output_paths"][0]],
                  resolution="4k")
    assert sent["resolution"] == "4k"


def test_the_tier_is_normalised_to_openarts_spelling():
    """"4K" is what a human writes; the schema enum is lowercase."""
    assert R.normalise_tier("4K") == "4k"
    assert R.normalise_tier("2k") == "2k"
    assert R.normalise_tier(None) == R.RESOLUTION


def test_an_unknown_tier_is_refused_rather_than_silently_ignored():
    """Silently ignoring one is exactly how this went unnoticed for a day."""
    with pytest.raises(ValueError):
        R.normalise_tier("8k")


def test_the_offered_ratios_are_the_ones_the_model_accepts():
    assert R.ASPECT_RATIOS == ("1:1", "3:2", "2:3", "16:9", "9:16", "4:3",
                               "3:4", "5:4", "4:5", "21:9", "9:21")


def test_an_unknown_ratio_is_refused():
    with pytest.raises(ValueError):
        R.normalise_aspect("7:11")


def test_the_aspect_reaches_the_driver():
    sent = {}
    R.render_once("p", "/tmp/chonky-opt2.png",
                  driver=lambda **kw: sent.update(kw) or [kw["output_paths"][0]],
                  aspect="9:16")
    assert sent["aspect"] == "9:16"


def test_the_driver_sends_the_field_openart_defined():
    """`resolution` is not a field on this schema, and the schema forbids extras.

    Sent under the wrong name it was dropped in silence — which is how a whole
    day's renders came back one tier smaller than the pipeline asked for.
    """
    import sys
    from pathlib import Path as _P

    sys.path.insert(0, str(_P(__file__).resolve().parents[2] / "scripts" / "common"))
    from scripts.trivia_images import openart_image_driver as drv

    captured = {}

    def fake_generate(*, media, model_display, mode, params, output_paths, **kw):
        captured.update(params())
        return output_paths

    drv.api.generate = fake_generate
    drv.generate_image(prompt="p", model="GPT Image 2.5 Sunburst",
                       output_paths=[_P("/tmp/x.png")], resolution="4k")

    assert "resolutionTier" in captured, f"sent {sorted(captured)}"
    assert captured["resolutionTier"] == "4k"
    assert "resolution" not in captured, "the unknown key must not be sent"


def test_an_exact_size_reaches_openart_as_custom_dimensions():
    """OpenArt's schema has customWidth/customHeight and lockAspectRatio.

    Whether it honours them is a question about OpenArt, not about us; this
    only asserts that we ask, and ask in the field names the schema defines.
    """
    from pathlib import Path as _P

    from scripts.trivia_images import openart_image_driver as drv

    captured = {}

    def fake_generate(*, media, model_display, mode, params, output_paths, **kw):
        captured.update(params())
        return output_paths

    drv.api.generate = fake_generate
    drv.generate_image(prompt="p", model="GPT Image 2.5 Sunburst",
                       output_paths=[_P("/tmp/x.png")],
                       width=2048, height=2048)

    assert captured["customWidth"] == 2048
    assert captured["customHeight"] == 2048
    assert captured["lockAspectRatio"] is False


def test_no_exact_size_means_no_custom_fields_are_sent():
    """The tier path must stay exactly as it is when nobody asked for a size."""
    from pathlib import Path as _P

    from scripts.trivia_images import openart_image_driver as drv

    captured = {}

    def fake_generate(*, media, model_display, mode, params, output_paths, **kw):
        captured.update(params())
        return output_paths

    drv.api.generate = fake_generate
    drv.generate_image(prompt="p", model="GPT Image 2.5 Sunburst",
                       output_paths=[_P("/tmp/x.png")])

    assert "customWidth" not in captured
    assert "lockAspectRatio" not in captured
