"""The tool writes the prompts, so the manual's rules have to reach the model.

Every rule in here was paid for with a failed render: metres do not control
distance, lateral placement drags him toward the camera, "no illustration
style" deletes the character, and a gag prop named as his surface brings both
forward.
"""

import json

import pytest

from scripts.chonky import prompts


def _client(payload):
    """A stand-in for Claude that records what it was asked."""
    calls = {}

    def call(system, user, *, model=None):
        calls["system"] = system
        calls["user"] = user
        return json.dumps(payload)

    call.calls = calls
    return call


VALID = {
    "city": "Prague",
    "country": "Czech Republic",
    "prompt": "A photograph of Charles Bridge ...",
    "clues": ["the bridge tower", "the castle on the hill", "baroque statues"],
    "clue_words": ["tower", "castle", "statues"],
}


def test_returns_a_location_prompt_and_three_clues():
    draft = prompts.draft(difficulty=1, target_zone="viewframe", used=[],
                          caller=_client(VALID))
    assert draft["city"] == "Prague"
    assert draft["country"] == "Czech Republic"
    assert draft["prompt"].startswith("A photograph")
    assert len(draft["clues"]) == 3


def test_the_manual_is_sent_as_the_system_prompt():
    """The rules are the whole value; a prompt written without them repeats today."""
    caller = _client(VALID)
    prompts.draft(difficulty=1, target_zone="viewframe", used=[], caller=caller)
    system = caller.calls["system"]
    assert "DEPTH IS INSTRUCTABLE" in system
    assert "no illustration style" in system


def test_already_used_locations_are_named_in_the_request():
    caller = _client(VALID)
    prompts.draft(difficulty=1, target_zone="viewframe",
                  used=["Lisbon, Portugal", "Sydney, Australia"], caller=caller)
    assert "Lisbon, Portugal" in caller.calls["user"]
    assert "Sydney, Australia" in caller.calls["user"]


def test_the_assigned_zone_is_named_in_the_request():
    """Zone is assigned per image by the ledger, never chosen by the writer."""
    caller = _client(VALID)
    prompts.draft(difficulty=2, target_zone="margin", used=[], caller=caller)
    assert "margin" in caller.calls["user"]
    assert "2" in caller.calls["user"]


def test_a_requested_city_is_honoured():
    caller = _client(VALID)
    prompts.draft(difficulty=1, target_zone="viewframe", used=[],
                  city="Prague", country="Czech Republic", caller=caller)
    assert "Prague" in caller.calls["user"]


def test_a_reply_that_is_not_json_is_rejected():
    """Better to fail loudly than to render something shaped wrong."""
    with pytest.raises(prompts.DraftError):
        prompts.draft(difficulty=1, target_zone="viewframe", used=[],
                      caller=lambda s, u, model=None: "Sure! Here you go:")


def test_a_reply_missing_the_prompt_is_rejected():
    bad = {"city": "Prague", "country": "Czech Republic", "clues": ["a", "b", "c"]}
    with pytest.raises(prompts.DraftError):
        prompts.draft(difficulty=1, target_zone="viewframe", used=[],
                      caller=lambda s, u, model=None: json.dumps(bad))


def test_json_wrapped_in_a_code_fence_is_accepted():
    """Models fence JSON by habit; that is not a malformed reply."""
    fenced = "```json\n" + json.dumps(VALID) + "\n```"
    draft = prompts.draft(difficulty=1, target_zone="viewframe", used=[],
                          caller=lambda s, u, model=None: fenced)
    assert draft["city"] == "Prague"


def test_a_prompt_that_says_no_illustration_style_is_rejected():
    """The exact phrasing that deleted the character last time."""
    bad = dict(VALID, prompt="A street scene, photoreal, no illustration style.")
    with pytest.raises(prompts.DraftError):
        prompts.draft(difficulty=1, target_zone="viewframe", used=[],
                      caller=lambda s, u, model=None: json.dumps(bad))


def test_a_prompt_that_places_him_in_metres_is_rejected():
    """"Fifteen metres" came back at three times the size band."""
    bad = dict(VALID, prompt="He sits about fifteen metres from the camera.")
    with pytest.raises(prompts.DraftError):
        prompts.draft(difficulty=1, target_zone="viewframe", used=[],
                      caller=lambda s, u, model=None: json.dumps(bad))
