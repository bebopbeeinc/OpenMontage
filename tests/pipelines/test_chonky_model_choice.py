"""Choosing the generator, and editing a photograph rather than describing one.

A render anchored to a photograph of Cuesta de San Blas came back a different
street: wider, with a church tower the photograph does not contain. GPT Image
treats a visual reference as style and identity — it is what keeps Chonky
looking like Chonky — and nothing in its form schema constrains layout.

Grok Imagine Image 2.0 is built for the opposite job: editing an existing
picture rather than rendering a new one. Reaching it means the pipeline has to
be able to pick a model at all, which it never could.
"""

import pytest

from scripts.chonky import render as R


def test_the_model_can_be_chosen_per_render():
    sent = {}
    R.render_once("p", "/tmp/chonky-model.png",
                  driver=lambda **kw: sent.update(kw) or [kw["output_paths"][0]],
                  model="Grok Imagine Image 2.0")
    assert sent["model"] == "Grok Imagine Image 2.0"


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


def test_the_editing_model_is_reachable():
    """It has to be in the driver's map or the pipeline cannot address it."""
    assert "Grok Imagine Image 2.0" in R.MODELS
