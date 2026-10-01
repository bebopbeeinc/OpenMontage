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
    "viewpoint": "Charles Bridge, a third of the way across from the Old Town end, facing west",
    # A compliant prompt: depth stated as an ordering, height stated against
    # something his own size, and he is on the ground.
    "prompt": ("A photograph of Charles Bridge. Tourists walk ahead of the "
               "camera and Chonky sits far beyond all of them, on the "
               "cobblestones, no taller than the kerb stone beside him."),
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


def test_the_assigned_zone_is_not_passed_on_as_an_instruction():
    """Lateral targeting dropped 2026-10-01. The zone records, it does not ask.

    This test previously asserted the opposite — that "margin" reached the
    writer. It did, and the writer could only act on it by saying where he
    stood across the frame, which is measurably what broke his size.
    """
    caller = _client(VALID)
    prompts.draft(difficulty=2, target_zone="margin", used=[], caller=caller)
    assert "margin" not in caller.calls["user"]
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
            "those people is between the camera and him. He is no taller than "
            "the kerb stone beside him. ")


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
    assert draft["prompt"].startswith(good)


def test_standing_on_the_ground_is_not_furniture():
    """Ground, cobbles, pavement and sand are where he is supposed to be."""
    for surface in ["cobblestones", "ground", "pavement", "sand", "grass", "path"]:
        good = ORDERING + f"He sits on the {surface} at true cat scale."
        assert prompts.draft(difficulty=1, target_zone="viewframe", used=[],
                             caller=_reply(good))["prompt"].startswith(good)


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
    """Measured on what is removed, not on the total.

    The brief on top grows whenever a rule turns out to need repeating there,
    so a net-size assertion fails for the wrong reason — it would call a
    better brief a regression.
    """
    full = prompts._manual_text()
    kept = prompts.writer_manual()
    brief_size = kept.index("=" * 20, kept.index("YOUR JOB")) if "YOUR JOB" in kept else 0
    manual_part = len(kept) - len(prompts._WRITERS_BRIEF)
    assert manual_part < len(full) * 0.75, (
        f"only {len(full) - manual_part} characters of the other agent's job removed")


def test_the_writer_is_told_the_two_checks_its_answer_must_pass():
    """A check the writer was never told about is a trap, not a rule."""
    system = prompts.writer_manual()
    assert "between the camera and him" in system
    assert "on the ground" in system.lower()


# --------------------------------------------------------------------------
# A rejection has to teach the writer something, or the check is just a wall.
# --------------------------------------------------------------------------

GOOD = ("Tourists walk ahead of the camera and Chonky sits far beyond all of "
        "them, on the cobblestones beside an open violin case, no taller than "
        "the kerb stone next to him.")
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
    assert draft["prompt"].startswith(GOOD)
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
            "beyond all of them, standing on the cobblestones. He is no taller "
            "than the kerb stone beside him.")
    assert prompts.draft(difficulty=1, target_zone="viewframe", used=[],
                         caller=_reply(good))["prompt"].startswith(good)


def test_traffic_may_stand_at_the_kerb():
    good = ("Taxis stand on the kerb in the foreground. Every one of those "
            "people is between the camera and him; Chonky is on the gravel, no "
            "taller than the bollard base beside him.")
    assert prompts.draft(difficulty=1, target_zone="viewframe", used=[],
                         caller=_reply(good))["prompt"].startswith(good)


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
        good = phrasing + " He is on the cobblestones, no taller than the kerb."
        assert prompts.draft(difficulty=1, target_zone="viewframe", used=[],
                             caller=_reply(good))["prompt"].startswith(good)


def test_a_distance_in_metres_that_is_not_his_is_allowed():
    """A 200-metre bridge is a fact about the bridge, not a placement for him."""
    good = ("The camera looks down the 200-metre-long bridge. " + ORDERING +
            "He is on the cobblestones.")
    assert prompts.draft(difficulty=1, target_zone="viewframe", used=[],
                         caller=_reply(good))["prompt"].startswith(good)


# --------------------------------------------------------------------------
# JSON broken by the quotes inside it.
#
# A real draft failed after three attempts and eight minutes with "Expecting
# ',' delimiter at column 1194". The writer had put quoted sign text inside a
# JSON string value — which is exactly what asking it to name the lettering on
# a sign encourages it to do.
# --------------------------------------------------------------------------

def test_quoted_sign_text_does_not_break_the_reply():
    """The whole point of the verified-detail work is naming what signs say."""
    body = ('{"city": "Cusco", "country": "Peru", '
            '"viewpoint": "Cuesta de San Blas, facing downhill", '
            '"prompt": "A sign reads "TALLER DE ARTESANIA" above the door. '
            'Chonky sits far beyond all of them on the cobblestones, no taller '
            'than the kerb beside him.", '
            '"chonky_line": "Chonky sits on the cobbles.", '
            '"clues": ["a", "b", "c"], "clue_words": ["x", "y", "z"]}')
    draft = prompts.draft(difficulty=1, target_zone="viewframe", used=[],
                          caller=lambda s, u, model=None: body)
    assert "TALLER DE ARTESANIA" in draft["prompt"]


def test_the_writer_is_told_which_quotes_to_use():
    """Cheaper to prevent than to repair: say it in the request."""
    caller = _caller() if "_caller" in dir() else None
    seen = {}

    def spy(system, user, *, model=None):
        seen["user"] = user
        return json.dumps(VALID)

    prompts.draft(difficulty=1, target_zone="viewframe", used=[], caller=spy)
    assert "single quotes" in seen["user"].lower()


# --------------------------------------------------------------------------
# An API key in the environment must not select a path that cannot run.
#
# The server was restarted under a supervisor that inherited ANTHROPIC_API_KEY
# from the launching shell. _default_caller saw the key, chose the SDK, and
# every draft died on "No module named 'anthropic'" — while the `claude` CLI,
# which this pipeline is designed around and which needs no key at all, sat
# there working. The key is a preference; the CLI is the floor.
# --------------------------------------------------------------------------

def test_the_cli_is_used_when_the_sdk_is_not_installed(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-whatever")
    monkeypatch.setattr(prompts, "_sdk_available", lambda: False)
    used = {}

    def fake_cli(system, user, *, model=None):
        used["cli"] = True
        return "ok"

    monkeypatch.setattr(prompts, "_call_via_cli", fake_cli)
    assert prompts._default_caller("sys", "usr") == "ok"
    assert used.get("cli") is True


def test_the_sdk_is_still_preferred_when_it_is_installed(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-whatever")
    monkeypatch.setattr(prompts, "_sdk_available", lambda: True)
    monkeypatch.setattr(prompts, "_call_via_sdk",
                        lambda s, u, *, model=None: "sdk")
    assert prompts._default_caller("sys", "usr") == "sdk"


# --------------------------------------------------------------------------
# Size must be anchored to something cat-sized and nearby, not to a fraction
# of a person.
#
# Measured on one render (Reykjavik, 2026-10-01, image c5bf014d) — n=1, so
# this is a hypothesis with a mechanism, not a law:
#
#   prompt: jogger is "roughly one fifth of the ViewFrame's height"
#           Chonky is "roughly one sixth the height of the jogger"  -> 3.3%
#   render: jogger measured ~21% of the ViewFrame  -- obeyed
#           Chonky measured 6.4% of the ViewFrame  -- ~1.8x over
#
# The reference figure came out right and the ratio against it did not, with
# both at the same ground depth so perspective does not explain it. A 6:1
# ratio against a human is the kind of instruction these models execute badly;
# they bias a small subject upward until it reads clearly.
#
# This is the same shape as the metres-from-camera ban above ("fifteen metres
# returned three times the size band") and gets the same treatment: ban the
# phrasing that measurably fails, and require the one that states his height
# against something of roughly his own size standing near him.
# --------------------------------------------------------------------------

_SCENE = ("A quiet waterfront promenade in low sun, a sculpture on the left and "
          "a concert hall behind. People on the path are between the camera and "
          "him. ")


def test_sizing_him_as_a_fraction_of_a_person_is_refused():
    with pytest.raises(prompts.DraftError) as exc:
        prompts._check(
            _SCENE +
            "Chonky stands on the grass, his full visible height roughly one "
            "sixth the height of the jogger on the path.")
    assert "fraction" in str(exc.value).lower() or "person" in str(exc.value).lower()


def test_a_fraction_of_a_person_is_refused_in_figures_too():
    with pytest.raises(prompts.DraftError):
        prompts._check(
            _SCENE +
            "Chonky stands on the grass, about 1/6 of the height of the woman "
            "walking ahead of him.")


def test_a_cat_scale_anchor_beside_him_is_accepted():
    prompts._check(
        _SCENE +
        "Chonky stands on the grass, no taller than the kerb stone beside him.")


def test_a_prompt_that_never_states_his_height_is_refused():
    with pytest.raises(prompts.DraftError) as exc:
        prompts._check(
            _SCENE + "Chonky stands on the grass further back than the bicycle.")
    assert "height" in str(exc.value).lower() or "size" in str(exc.value).lower()


def test_the_anchor_is_read_only_in_sentences_about_him():
    """A size fact about the scene must not satisfy his own requirement."""
    with pytest.raises(prompts.DraftError):
        prompts._check(
            _SCENE +
            "The bollards are no taller than the kerb stones beside them. "
            "Chonky stands on the grass further back than the bicycle.")


# --------------------------------------------------------------------------
# Lateral targeting is dropped (Caglar's decision, 2026-10-01).
#
# The pipeline used to hand the writer an assigned zone — "Chonky's assigned
# zone for this image is: margin" — which can only be satisfied by telling the
# model where he stands across the frame. Manual 4.3c has the measurement for
# what that does, over five renders of one Prague scene:
#
#   "far beyond all of them"   (no lateral)   72 px   <- the only one in band
#   "off to the LEFT parapet"                164 px
#   "to the RIGHT, a third in from the..."   236 px
#
# and Kyoto 2026-10-01 ("off to the right, well clear of its centre") came
# back at 214 px, straddling. Asking for him over there reads as asking to SEE
# him over there, and the model obliges by bringing him closer.
#
# So the system no longer asks. Depth puts him where it puts him; the zone
# becomes a record of where he landed rather than an instruction. 4.3c already
# prescribed this — it was documented and enforced nowhere, which is how it
# kept happening.
# --------------------------------------------------------------------------

def _ask():
    """The system prompt and the per-image request, as the writer sees them."""
    return (prompts.writer_manual(),
            prompts._user_message(2, "viewframe", [], None, None, None))


def test_the_writer_is_not_handed_a_zone_to_place_him_in():
    system, user = _ask()
    assert "assigned zone" not in user.lower()
    assert "not yours to choose" not in user.lower()


def test_the_writer_is_told_not_to_place_him_across_the_frame():
    system, user = _ask()
    blob = (system + user).lower()
    assert "lateral" in blob or "across the frame" in blob


def test_a_lateral_placement_is_refused():
    for phrasing in [
        "Chonky stands on the paving off to the right of the frame.",
        "He sits near the left edge of the photograph.",
        "Chonky is well clear of its centre, over on the right.",
        "He is a third in from the right-hand parapet.",
    ]:
        with pytest.raises(prompts.DraftError):
            prompts._check(ORDERING + phrasing +
                           " He is no taller than the kerb beside him.")


def test_a_lateral_fact_about_the_scene_is_allowed():
    """The ban is on placing HIM, not on describing the street."""
    prompts._check(
        "The pagoda rises on the left side of the street and the shops run "
        "down the right. " + ORDERING +
        "Chonky is on the paving, no taller than the kerb beside him.")


# --------------------------------------------------------------------------
# The character reference block: appended, not asked for.
#
# Manual S6: "Every prompt must itself contain the sentence: use the attached
# character reference only for the cat's appearance, never as a layout,
# composition, or background reference." Section 6 is stripped from the
# writer's manual by _NOT_THE_WRITERS_JOB, so that sentence was in 0 of 29
# rendered prompts — the one rule stopping the model reading the model sheet
# as a layout reference was in the manual and in nothing the model ever saw.
#
# It is appended rather than required of the writer. This project has learned
# the same lesson three times over ("asking is not the same as getting" —
# depth, size, lateral position all needed compulsory phrasing): a fixed
# sentence that never varies should not be left to an LLM to retype. Appending
# it, the way the VERIFIED LOCAL DETAIL block is appended, makes it 100% and
# costs no draft rejections.
#
# The silhouette rides along for the same reason. The writer does currently
# describe it in every library prompt, so this is a floor under a thing that
# already works, not a replacement for it.
# --------------------------------------------------------------------------

def _flat(text):
    """One line, single spaces — the block is wrapped, the sentence is not."""
    return " ".join(text.split())


def test_the_reference_is_marked_appearance_only_in_every_prompt():
    out = prompts.draft(difficulty=1, target_zone="viewframe", used=[],
                        caller=_client(VALID))
    flat = _flat(out["prompt"])
    assert "use the attached character reference only for the cat's appearance, " \
           "never as a layout, composition, or background reference" in flat.lower()


def test_the_silhouette_reaches_every_prompt():
    out = prompts.draft(difficulty=1, target_zone="viewframe", used=[],
                        caller=_client(VALID))
    body = out["prompt"].lower()
    for marker in ("near-spherical", "belly bib", "white paws", "darker bands",
                   "short legs", "small head"):
        assert marker in body, marker


def test_the_block_comes_after_the_writers_own_words():
    """Appended like the verified detail, so a reviewer can see what was added."""
    out = prompts.draft(difficulty=1, target_zone="viewframe", used=[],
                        caller=_client(VALID))
    assert out["prompt"].startswith(VALID["prompt"])


def test_the_writer_is_told_the_reference_is_not_a_layout():
    """A rule the writer is never shown is a trap, per the brief's own reason."""
    system = prompts.writer_manual()
    assert "never as a layout" in system


def test_the_character_block_disclaims_any_say_over_his_size():
    """The block describes him; it must not be read as "make him prominent".

    Measured: four level-2 renders before the block averaged 129 px (83, 117,
    125, 189); the first four after it came back 195, 345, 384, 607 — against
    a band whose ceiling is 210. Describing the subject at length is a known
    way to make an image model enlarge it, and this block is the longest
    description of Chonky any prompt has ever carried. Two changes landed
    together (the block and five extra reference stills), so this line is a
    hypothesis being tested, not a diagnosis.
    """
    out = prompts.draft(difficulty=1, target_zone="viewframe", used=[],
                        caller=_client(VALID))
    flat = _flat(out["prompt"]).lower()
    assert "does not change his size" in flat
    assert "not make him more prominent" in flat
