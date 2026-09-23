"""Per-family clue emphasis, and a scene that is not a crowd.

The clue families come from the manual's own ranked list. A family left alone
must behave exactly as the manual already says — the dropdown's default is
"no opinion", not "weight zero".
"""

import json

import pytest

from scripts.chonky import prompts

VALID = {
    "city": "Prague",
    "country": "Czech Republic",
    "prompt": ("Tourists walk ahead of the camera and Chonky sits far beyond "
               "all of them, on the cobblestones."),
    "clues": ["a", "b", "c"],
    "clue_words": ["x", "y", "z"],
}


def _caller():
    calls = {}

    def call(system, user, *, model=None):
        calls["system"], calls["user"] = system, user
        return json.dumps(VALID)

    call.calls = calls
    return call


def test_the_families_are_the_manuals_own_list():
    assert "road_signs" in prompts.CLUE_FAMILIES
    assert "flags" in prompts.CLUE_FAMILIES
    assert "landmark" in prompts.CLUE_FAMILIES
    assert len(prompts.CLUE_FAMILIES) == 14


def test_no_weights_means_the_request_says_nothing_about_emphasis():
    """The default must leave the manual's own ranking untouched."""
    caller = _caller()
    prompts.draft(difficulty=1, target_zone="viewframe", used=[], caller=caller)
    assert "emphasis" not in caller.calls["user"].lower()


def test_a_weighted_family_is_named_with_its_weight():
    caller = _caller()
    prompts.draft(difficulty=1, target_zone="viewframe", used=[],
                  weights={"road_signs": 5, "flags": 2}, caller=caller)
    user = caller.calls["user"]
    assert "Road signs" in user and "5" in user
    assert "Flags" in user and "2" in user


def test_unweighted_families_are_not_suppressed():
    """A weight on one family must not read as an instruction to drop the rest."""
    caller = _caller()
    prompts.draft(difficulty=1, target_zone="viewframe", used=[],
                  weights={"road_signs": 5}, caller=caller)
    user = caller.calls["user"]
    assert "does not reduce" in user or "not at the expense" in user
    assert "Flags" not in user, "an unweighted family should not be listed at all"


def test_a_weight_outside_one_to_five_is_refused():
    for bad in (0, 6, -1, "high"):
        with pytest.raises(ValueError):
            prompts.draft(difficulty=1, target_zone="viewframe", used=[],
                          weights={"flags": bad}, caller=_caller())


def test_an_unknown_family_is_refused():
    with pytest.raises(ValueError):
        prompts.draft(difficulty=1, target_zone="viewframe", used=[],
                      weights={"seagulls": 4}, caller=_caller())


def test_the_brief_says_the_scene_is_not_crowded():
    """Every render so far came back packed with tourists."""
    system = prompts.writer_manual()
    assert "NOT A CROWD SCENE" in system


def test_the_brief_says_depth_can_be_measured_against_something_other_than_people():
    """The ordering rule reaches for a crowd unless told it has alternatives.

    "Every one of those people is between the camera and him" is the easiest
    way to satisfy the depth rule, and it is also how the frame fills up.
    """
    system = prompts.writer_manual()
    assert "lamppost" in system or "stall" in system
