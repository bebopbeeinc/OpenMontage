"""Shared guards for the Chonky pipeline tests.

Two steps in the render path now reach out to Claude: choosing the locations
and, after the render, reading the image to write the clues. A test that
forgets to stub one does not fail — it quietly makes a real call, which is
slow, costs money, and makes the suite depend on the network. These fixtures
make forgetting impossible; a test that cares about either step overrides the
fixture for itself.
"""

import pytest


@pytest.fixture(autouse=True)
def _offline_inspector(monkeypatch):
    from scripts.chonky import inspect as inspect_mod

    def refuse(path, **kw):
        raise inspect_mod.InspectError(
            "inspect_render was not stubbed in this test; stub it rather than "
            "calling Claude")

    monkeypatch.setattr(inspect_mod, "inspect_render", refuse)


@pytest.fixture(autouse=True)
def _offline_picker(monkeypatch):
    from scripts.chonky import prompts as prompts_mod

    monkeypatch.setattr(
        prompts_mod, "pick_locations",
        lambda **kw: [{"city": f"City{i}", "country": "Country"}
                      for i in range(len(kw["slots"]))])
