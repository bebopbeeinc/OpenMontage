import pytest

from scripts.chonky.render import ASPECT, CHARACTER, MODEL, RESOLUTION, render_once


def _fake(calls):
    def driver(**kwargs):
        calls.append(kwargs)
        kwargs["output_paths"][0].write_bytes(b"fake")
        return kwargs["output_paths"]
    return driver


def test_renders_once_with_the_authored_settings(tmp_path):
    calls = []
    out = tmp_path / "r.png"
    result = render_once("a prompt", out, driver=_fake(calls))

    assert result == out
    assert len(calls) == 1, "exactly one render call per image"
    assert calls[0]["model"] == MODEL == "GPT Image 2.5 Sunburst"
    assert calls[0]["aspect"] == ASPECT == "4:5"
    assert calls[0]["resolution"] == RESOLUTION == "4K"
    assert calls[0]["character"] == CHARACTER == "Chonky"
    assert len(calls[0]["output_paths"]) == 1


def test_never_passes_custom_dimensions(tmp_path):
    # OpenArt ignores them; passing them invites the belief that they work.
    calls = []
    render_once("p", tmp_path / "r.png", driver=_fake(calls))
    assert "customWidth" not in calls[0]
    assert "customHeight" not in calls[0]


def test_never_passes_output_format(tmp_path):
    # Also ignored — conversion to JPEG happens locally in imaging.py.
    calls = []
    render_once("p", tmp_path / "r.png", driver=_fake(calls))
    assert "outputFormat" not in calls[0]


def test_propagates_failure_without_retrying(tmp_path):
    calls = []

    def failing(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("content policy")

    with pytest.raises(RuntimeError):
        render_once("p", tmp_path / "r.png", driver=failing)
    assert len(calls) == 1, "a failure must not silently re-submit"


def test_creates_the_output_directory(tmp_path):
    calls = []
    out = tmp_path / "nested" / "deeper" / "r.png"
    render_once("p", out, driver=_fake(calls))
    assert out.exists()
