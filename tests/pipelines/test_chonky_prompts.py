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
    # A compliant prompt: depth stated as an ordering, and he is on the ground.
    "prompt": ("A photograph of Charles Bridge. Tourists walk ahead of the "
               "camera and Chonky sits far beyond all of them, on the "
               "cobblestones."),
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


# --------------------------------------------------------------------------
# The rules the manual states, checked on the way back.
#
# The writer had all of these in its system prompt and wrote "sitting on the
# stone parapet of the terrace balustrade" anyway. That render came back at
# 115 px against a 43-69 px band. Asking is not the same as getting.
# --------------------------------------------------------------------------

def _reply(prompt):
    return lambda s, u, model=None: json.dumps(dict(VALID, prompt=prompt))


ORDERING = ("A dozen tourists walk ahead of the camera. Chonky sits far beyond "
            "all of them, behind the furthest walking tourist, so every one of "
            "those people is between the camera and him. ")


def test_a_prompt_that_seats_him_on_furniture_is_rejected():
    """A prop named as his surface makes the prop the subject and brings both forward."""
    with pytest.raises(prompts.DraftError) as exc:
        prompts.draft(difficulty=1, target_zone="viewframe", used=[],
                      caller=_reply(ORDERING + "He is sitting on the stone parapet."))
    assert "parapet" in str(exc.value)


def test_the_furniture_rule_covers_the_usual_suspects():
    for surface in ["bench", "balustrade", "ledge", "crate", "step",
                    "windowsill", "railing", "wall"]:
        with pytest.raises(prompts.DraftError):
            prompts.draft(difficulty=1, target_zone="viewframe", used=[],
                          caller=_reply(ORDERING + f"He sits on the {surface}."))


def test_a_prompt_with_no_depth_ordering_is_rejected():
    """Metres do not work, so an ordering against the scene is the only control."""
    with pytest.raises(prompts.DraftError) as exc:
        prompts.draft(difficulty=1, target_zone="viewframe", used=[],
                      caller=_reply("A bridge. Chonky sits on the cobblestones."))
    assert "depth" in str(exc.value).lower() or "ordering" in str(exc.value).lower()


def test_a_compliant_prompt_passes():
    """The check must not reject the shape the manual actually asks for."""
    good = ORDERING + ("He sits on all fours on the open cobblestones beside an "
                       "open violin case, at true cat scale.")
    draft = prompts.draft(difficulty=1, target_zone="viewframe", used=[],
                          caller=_reply(good))
    assert draft["prompt"] == good


def test_standing_on_the_ground_is_not_furniture():
    """Ground, cobbles, pavement and sand are where he is supposed to be."""
    for surface in ["cobblestones", "ground", "pavement", "sand", "grass", "path"]:
        good = ORDERING + f"He sits on the {surface} at true cat scale."
        assert prompts.draft(difficulty=1, target_zone="viewframe", used=[],
                             caller=_reply(good))["prompt"] == good


# --------------------------------------------------------------------------
# What the writer is actually sent.
#
# Three drafts followed the placement rules zero times. The manual they were
# sent is written for a different agent doing a different job: it says to
# write six prompts, to run a whole batch without stopping, to call tools the
# writer does not have, and to answer as TSV — while the request asks for one
# prompt as JSON.
# --------------------------------------------------------------------------

def test_the_writer_is_not_told_to_do_a_job_it_cannot_do():
    system = prompts.writer_manual()
    for instruction in ["write_rows", "inspect_render", "save_image",
                        "openart_creation_wait", "RUN THE WHOLE BATCH",
                        "six prompts", "as TSV"]:
        assert instruction not in system, f"still telling the writer to {instruction!r}"


def test_the_craft_rules_survive_the_trim():
    """Everything the writer needs to write one good prompt stays."""
    system = prompts.writer_manual()
    for rule in ["DEPTH IS INSTRUCTABLE",
                 "HE MUST NOT SIT ON OR IN THE GAG PROP",
                 "no illustration style",
                 "WHO HE IS",
                 "REFERENCE-LOCKED GEOGRAPHY",
                 "EVERY IMAGE NEEDS AT LEAST TWO REAL CLUES"]:
        assert rule in system, f"trimmed away a rule the writer needs: {rule!r}"


def test_the_trim_actually_removes_a_large_share_of_the_manual():
    full = prompts._manual_text()
    assert len(prompts.writer_manual()) < len(full) * 0.8


def test_the_writer_is_told_the_two_checks_its_answer_must_pass():
    """A check the writer was never told about is a trap, not a rule."""
    system = prompts.writer_manual()
    assert "between the camera and him" in system
    assert "on the ground" in system.lower()


# --------------------------------------------------------------------------
# A rejection has to teach the writer something, or the check is just a wall.
# --------------------------------------------------------------------------

GOOD = ("Tourists walk ahead of the camera and Chonky sits far beyond all of "
        "them, on the cobblestones beside an open violin case.")
BAD = "Chonky sits on the stone parapet, looking out over the river."


def test_a_rejected_draft_is_retried_with_the_reason():
    replies = [json.dumps(dict(VALID, prompt=BAD)),
               json.dumps(dict(VALID, prompt=GOOD))]
    asks = []

    def caller(system, user, *, model=None):
        asks.append(user)
        return replies.pop(0)

    draft = prompts.draft(difficulty=1, target_zone="viewframe", used=[],
                          caller=caller)
    assert draft["prompt"] == GOOD
    assert len(asks) == 2, "a rejected draft must be asked again"
    assert "parapet" in asks[1], "the retry must say what was wrong"
    assert BAD in asks[1], "the retry must show the prompt that was rejected"


def test_it_gives_up_rather_than_retry_forever():
    calls = []

    def caller(system, user, *, model=None):
        calls.append(user)
        return json.dumps(dict(VALID, prompt=BAD))

    with pytest.raises(prompts.DraftError):
        prompts.draft(difficulty=1, target_zone="viewframe", used=[], caller=caller)
    assert 2 <= len(calls) <= 4, f"retried {len(calls)} times"


def test_a_first_draft_that_passes_is_not_retried():
    calls = []

    def caller(system, user, *, model=None):
        calls.append(user)
        return json.dumps(dict(VALID, prompt=GOOD))

    prompts.draft(difficulty=1, target_zone="viewframe", used=[], caller=caller)
    assert len(calls) == 1


# --------------------------------------------------------------------------
# False positives cost three draft cycles and a failed job, and the retry then
# tells the writer to fix something it never wrote. These are the phrasings a
# review found being rejected wrongly, and the two that walked straight through.
# --------------------------------------------------------------------------

def test_other_people_may_sit_on_things():
    """The rule is about Chonky's surface, not about the furniture in the scene."""
    good = ("A dozen tourists sit on the steps of the cathedral. Chonky is far "
            "beyond all of them, standing on the cobblestones.")
    assert prompts.draft(difficulty=1, target_zone="viewframe", used=[],
                         caller=_reply(good))["prompt"] == good


def test_traffic_may_stand_at_the_kerb():
    good = ("Taxis stand on the kerb in the foreground. Every one of those "
            "people is between the camera and him; Chonky is on the gravel.")
    assert prompts.draft(difficulty=1, target_zone="viewframe", used=[],
                         caller=_reply(good))["prompt"] == good


def test_upon_and_atop_are_the_same_rule():
    for phrasing in ["Chonky sits upon the parapet.",
                     "Chonky sits atop the stone bench.",
                     "Chonky is perched on top of the crate."]:
        with pytest.raises(prompts.DraftError):
            prompts.draft(difficulty=1, target_zone="viewframe", used=[],
                          caller=_reply(ORDERING + phrasing))


def test_a_comparative_counts_as_an_ordering():
    """"Further from the camera than the tourists" is the rule, stated plainly."""
    for phrasing in ["Chonky is much further from the camera than the tourists.",
                     "He sits deeper in the scene than the market stalls.",
                     "Chonky is farther back than the furthest walking tourist."]:
        good = phrasing + " He is on the cobblestones."
        assert prompts.draft(difficulty=1, target_zone="viewframe", used=[],
                             caller=_reply(good))["prompt"] == good


def test_a_distance_in_metres_that_is_not_his_is_allowed():
    """A 200-metre bridge is a fact about the bridge, not a placement for him."""
    good = ("The camera looks down the 200-metre-long bridge. " + ORDERING +
            "He is on the cobblestones.")
    assert prompts.draft(difficulty=1, target_zone="viewframe", used=[],
                         caller=_reply(good))["prompt"] == good
