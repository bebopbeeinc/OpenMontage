"""The viewpoint has to be stated, not buried.

Section 2 of the manual already demands one exact, verified, photographable
viewpoint, and already forbids a generic scene inspired by the city. The
renders came back with cube-clipped laurel trees described as "a formal
geometric plaza-landscaping style" — a motif, not a place — so the rule was
present and ignored, the same way the placement rules were.

Making the writer name the viewpoint as its own field is what turns a vague
one into something a reviewer can see without reading three thousand
characters of prompt.
"""

import json

import pytest

from scripts.chonky import prompts

VALID = {
    "city": "Prague",
    "country": "Czech Republic",
    "viewpoint": ("Charles Bridge, a third of the way across from the Old Town "
                  "end, camera facing west toward the Mala Strana towers"),
    "prompt": ("Tourists walk ahead of the camera and Chonky sits far beyond "
               "all of them, on the cobblestones, no taller than the kerb beside him."),
    "clues": ["a", "b", "c"],
    "clue_words": ["x", "y", "z"],
}


def _reply(payload):
    return lambda s, u, model=None: json.dumps(payload)


def test_the_viewpoint_comes_back_with_the_prompt():
    draft = prompts.draft(difficulty=1, target_zone="viewframe", used=[],
                          caller=_reply(VALID))
    assert draft["viewpoint"].startswith("Charles Bridge")


def test_a_draft_with_no_viewpoint_is_rejected():
    without = {k: v for k, v in VALID.items() if k != "viewpoint"}
    with pytest.raises(prompts.DraftError):
        prompts.draft(difficulty=1, target_zone="viewframe", used=[],
                      caller=_reply(without))


def test_a_viewpoint_that_names_no_place_is_rejected():
    """"A plaza in Guadalajara" is the failure, stated out loud."""
    for vague in ["a plaza", "a busy street", "the old town", "downtown",
                  "a square in the city centre"]:
        with pytest.raises(prompts.DraftError):
            prompts.draft(difficulty=1, target_zone="viewframe", used=[],
                          caller=_reply(dict(VALID, viewpoint=vague)))


def test_the_brief_carries_the_geography_lock():
    """The rule was in section 2 and ignored; the brief is what gets read."""
    system = prompts.writer_manual()
    assert "ONE REAL PLACE" in system


def test_the_brief_forbids_the_place_naming_itself():
    """A sign reading "Seattle" solves the puzzle by reading, not by knowing."""
    system = prompts.writer_manual()
    assert "NOTHING IN THE PICTURE SPELLS THE ANSWER" in system
