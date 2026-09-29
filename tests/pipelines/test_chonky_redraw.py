"""Redrawing the photograph, rather than composing a scene beside it.

A render anchored to a photograph of Cuesta de San Blas came back a different
street. The photograph was attached, but the prompt still described a scene
from scratch in three thousand characters — so the model had a full
composition to build and a picture to take style from, and style is what it
took.

Asking it to REDRAW the photograph makes the picture the subject rather than
the mood. The differences it is allowed are named and small: light, weather,
the incidental people and traffic. The street itself is not one of them.
"""

import json

import pytest

from scripts.chonky import prompts

DRAFTED = {
    "city": "Cusco", "country": "Peru",
    "viewpoint": "Cuesta de San Blas, facing downhill toward the old town",
    "prompt": ("A long invented description of a Cusco street with a church "
               "tower, market stalls and a moto-taxi. Chonky sits far beyond "
               "all of them on the cobblestones."),
    "chonky_line": ("Chonky sits on the cobblestones beside the left-hand "
                    "steps, far beyond the walking figures."),
    "clues": ["a", "b", "c"], "clue_words": ["x", "y", "z"],
}

FOUND = {"real": True, "confidence": "high", "note": "documented street",
         "verified": True, "photo_url": "https://x/street.jpg",
         "photo_note": "looking downhill", "details": {}}


def _reply(payload):
    return lambda s, u, model=None: json.dumps(payload)


def test_an_anchored_prompt_asks_for_the_photograph_to_be_redrawn():
    out = prompts.draft(difficulty=1, target_zone="viewframe", used=[],
                        caller=_reply(DRAFTED), verifier=lambda **kw: FOUND)
    assert "REDRAW THE ATTACHED PHOTOGRAPH" in out["prompt"]


def test_the_invented_scene_is_dropped_when_there_is_a_photograph():
    """Its composition is what competed with the picture and won."""
    out = prompts.draft(difficulty=1, target_zone="viewframe", used=[],
                        caller=_reply(DRAFTED), verifier=lambda **kw: FOUND)
    assert "market stalls" not in out["prompt"]
    assert "church tower" not in out["prompt"]


def test_chonky_still_gets_into_the_anchored_prompt():
    out = prompts.draft(difficulty=1, target_zone="viewframe", used=[],
                        caller=_reply(DRAFTED), verifier=lambda **kw: FOUND)
    assert "beside the left-hand steps" in out["prompt"]


def test_the_allowed_differences_are_named_and_small():
    out = prompts.draft(difficulty=1, target_zone="viewframe", used=[],
                        caller=_reply(DRAFTED), verifier=lambda **kw: FOUND)
    prompt = out["prompt"]
    assert "light" in prompt.lower()
    assert "do not" in prompt.lower()


def test_without_a_photograph_the_written_scene_is_used():
    """Unanchored still has to render something, so the description stands."""
    plain = dict(FOUND, photo_url="")
    out = prompts.draft(difficulty=1, target_zone="viewframe", used=[],
                        caller=_reply(DRAFTED), verifier=lambda **kw: plain)
    assert "market stalls" in out["prompt"]
    assert "REDRAW" not in out["prompt"]


def test_a_draft_with_no_chonky_line_is_rejected():
    """Without it an anchored prompt has no cat in it at all."""
    without = {k: v for k, v in DRAFTED.items() if k != "chonky_line"}
    with pytest.raises(prompts.DraftError):
        prompts.draft(difficulty=1, target_zone="viewframe", used=[],
                      caller=_reply(without), verifier=lambda **kw: FOUND)


# --------------------------------------------------------------------------
# The photograph is the scene now, so it has to satisfy the scene's rules.
#
# Picking "any photograph of this viewpoint" throws away every condition this
# project built: the difficulty definitions, the landmark ban above level 3,
# the clue families, the crowd rule and the rule that nothing may spell the
# answer. A photograph that breaks them is a level that breaks them.
# --------------------------------------------------------------------------

from scripts.chonky import verify_place as V  # noqa: E402

# Bound at import, before conftest's offline guard replaces the attribute:
# these tests are about what the real verifier asks for.
verify_viewpoint = V.verify_viewpoint


def _seen_user(payload):
    seen = {}

    def caller(system, user, *, model=None):
        seen["user"] = user
        return json.dumps(payload)

    return seen, caller


VERIFIED = {"real": True, "confidence": "high", "note": "n",
            "photo_url": "https://x/a.jpg", "details": {}}


def test_the_difficulty_governs_which_photograph_is_wanted():
    seen, caller = _seen_user(VERIFIED)
    verify_viewpoint(city="Cusco", country="Peru", viewpoint="x",
                       difficulty=4, caller=caller)
    assert "difficulty 4" in seen["user"]


def test_a_landmark_photograph_is_refused_above_level_three():
    """The manual: never an unmistakable landmark at 4, 5 or 5+."""
    seen, caller = _seen_user(VERIFIED)
    verify_viewpoint(city="Cusco", country="Peru", viewpoint="x",
                       difficulty=5, caller=caller)
    assert "landmark" in seen["user"].lower()


def test_the_photograph_must_not_spell_the_answer():
    seen, caller = _seen_user(VERIFIED)
    verify_viewpoint(city="Cusco", country="Peru", viewpoint="x",
                       difficulty=3, caller=caller)
    user = seen["user"].lower()
    assert "name of the city" in user or "spell" in user


def test_a_crowd_photograph_is_not_wanted():
    seen, caller = _seen_user(VERIFIED)
    verify_viewpoint(city="Cusco", country="Peru", viewpoint="x",
                       difficulty=2, caller=caller)
    assert "crowd" in seen["user"].lower()


def test_the_photograph_must_carry_readable_evidence():
    """A view with nothing to read is not a geography puzzle."""
    seen, caller = _seen_user(VERIFIED)
    verify_viewpoint(city="Cusco", country="Peru", viewpoint="x",
                       difficulty=4, caller=caller)
    user = seen["user"].lower()
    assert "signage" in user or "legible" in user


def test_a_portrait_capable_photograph_is_preferred():
    """The frame is 4:5 with a 9:16 window inside it; a letterbox loses the sides."""
    seen, caller = _seen_user(VERIFIED)
    verify_viewpoint(city="Cusco", country="Peru", viewpoint="x",
                       difficulty=1, caller=caller)
    assert "portrait" in seen["user"].lower()


def test_the_difficulty_reaches_the_photograph_search():
    """Otherwise the level's rules are written down and never applied."""
    seen = {}

    def verifier(**kw):
        seen.update(kw)
        return FOUND

    prompts.draft(difficulty=5, target_zone="viewframe", used=[],
                  caller=_reply(DRAFTED), verifier=verifier)
    assert seen["difficulty"] == 5
