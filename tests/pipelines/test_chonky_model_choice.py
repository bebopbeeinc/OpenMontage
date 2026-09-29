"""Choosing the generator, and editing a photograph rather than describing one.

A render anchored to a photograph of Cuesta de San Blas came back a different
street: wider, with a church tower the photograph does not contain. GPT Image
treats a visual reference as style and identity — it is what keeps Chonky
looking like Chonky — and nothing in its form schema constrains layout.

The model was decided at the start of this project: gpt-image-2-5-sunburst in
image2image, because that is how Chonky's model sheet is attached at all and
because the frame geometry was measured against its output. Trying another one
mid-project was a mistake — it reopened a settled decision, and it did not
help anyway, since nothing in this API exposes structural control whichever
model is asked.

These tests hold the decision in place.
"""

import pytest

from scripts.chonky import render as R


def test_another_model_is_refused():
    """Not a menu. Chonky's identity and the measured geometry both depend on it."""
    with pytest.raises(ValueError):
        R.render_once("p", "/tmp/chonky-model.png",
                      driver=lambda **kw: [kw["output_paths"][0]],
                      model="Nano Banana Pro")


def test_the_default_model_is_unchanged():
    sent = {}
    R.render_once("p", "/tmp/chonky-model2.png",
                  driver=lambda **kw: sent.update(kw) or [kw["output_paths"][0]])
    assert sent["model"] == R.MODEL == "GPT Image 2.5 Sunburst"


def test_an_unknown_model_is_refused_before_it_costs_anything():
    with pytest.raises(ValueError):
        R.render_once("p", "/tmp/chonky-model3.png",
                      driver=lambda **kw: [kw["output_paths"][0]],
                      model="Definitely Not A Model")


def test_the_decided_model_is_addressable():
    """The one model this pipeline uses must have a route, or nothing renders."""
    import sys
    from pathlib import Path as _P

    sys.path.insert(0, str(_P(__file__).resolve().parents[2] / "scripts" / "common"))
    import openart_api

    assert R.MODEL in openart_api.MODEL_IDS
