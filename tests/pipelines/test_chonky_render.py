import tempfile
from pathlib import Path

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
    # Lowercase: OpenArt's resolutionTier enum is 1k/2k/4k, and "4K" sent
    # under the wrong field name was silently dropped for a day.
    assert calls[0]["resolution"] == RESOLUTION == "4k"
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


def test_bills_one_named_workspace_with_no_fallback(tmp_path):
    """Chonky spends the workspace it is told to, or fails.

    The shared driver falls back to another workspace when the primary is out
    of credits. That is right for the trivia pipelines and wrong here: a
    shortfall must be visible, not quietly billed somewhere nobody chose.
    """
    from scripts.chonky.render import FALLBACK_WORKSPACES, WORKSPACE

    calls = []
    render_once("p", tmp_path / "r.png", driver=_fake(calls))
    assert WORKSPACE, "a workspace must always be named explicitly"
    assert calls[0]["workspace"] == WORKSPACE
    assert calls[0]["fallback_workspaces"] == FALLBACK_WORKSPACES == ()


def test_workspace_is_configurable(monkeypatch, tmp_path):
    """So the funded workspace can move without a code change."""
    import importlib

    monkeypatch.setenv("CHONKY_OPENART_WORKSPACE", "Some Other Workspace")
    import scripts.chonky.render as r
    importlib.reload(r)
    try:
        calls = []
        r.render_once("p", tmp_path / "r.png", driver=_fake(calls))
        assert calls[0]["workspace"] == "Some Other Workspace"
    finally:
        monkeypatch.delenv("CHONKY_OPENART_WORKSPACE", raising=False)
        importlib.reload(r)


def test_render_records_what_it_submitted():
    """"Did the model sheet reach OpenArt?" must be answerable after the fact.

    A render that comes back with the wrong character is otherwise a dead end:
    nothing raised, so the only evidence that references were attached is that
    nothing said they weren't.
    """
    def driver(**kwargs):
        # The real driver reports through the `log` sink it is handed, rather
        # than by writing to a stderr the caller has swapped out from under it.
        sink = kwargs.get("log")
        if sink is not None:
            sink.append("  → character stills: Chonky")
            sink.append("  → reference image: 01-model-sheet.png")
        out = kwargs["output_paths"][0]
        Path(out).write_bytes(b"x")
        return [out]

    log: list[str] = []
    with tempfile.TemporaryDirectory() as td:
        render_once("a prompt", Path(td) / "out.png", driver=driver, log=log)

    joined = "\n".join(log)
    assert "character stills: Chonky" in joined
    assert "01-model-sheet.png" in joined


def test_render_works_without_a_log():
    def driver(**kwargs):
        out = kwargs["output_paths"][0]
        Path(out).write_bytes(b"x")
        return [out]

    with tempfile.TemporaryDirectory() as td:
        assert render_once("p", Path(td) / "o.png", driver=driver).exists()
