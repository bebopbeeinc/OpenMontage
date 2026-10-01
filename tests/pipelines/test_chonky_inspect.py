"""STEP C: look at the render before writing the clues.

The manual has always said it — "Clue messages must never be written from the
prompt alone: the prompt says what SHOULD be there; STEP C catches when the
render did not deliver it" — and the pipeline never did it. Clues came back in
the same reply as the prompt, before any image existed, and nothing ever
compared them to the pixels.

That is why Tallinn's clue described an EU blue strip reading EST on a plate
that shows nothing of the kind, and why Guadalajara's cited a Jalisco plate
too blurry to read.
"""

import json

import pytest
from PIL import Image

from scripts.chonky import inspect as I

# Bound at import, before conftest's offline guard replaces the attribute.
inspect_render = I.inspect_render


def _reply(payload):
    return lambda system, user, *, model=None, image=None: json.dumps(payload)


GOOD = {
    "clues": ["The blue road sign carries Hangul lettering, the alphabet "
              "invented in the 1440s and used nowhere outside the Korean "
              "peninsula.",
              "A Taegukgi flies beside the gate — that red-and-blue circle "
              "with its four black trigrams belongs to one country's flag "
              "and no other.",
              "Those yellow-and-white striped kerbs are a Korean road "
              "marking, and they mean no stopping here at any hour."],
    "clue_words": ["sign", "flag", "kerb"],
    "names_the_place": False,
}


def test_it_returns_clues_taken_from_the_image(tmp_path):
    img = tmp_path / "r.png"
    Image.new("RGB", (64, 80), (120, 120, 120)).save(img)
    out = inspect_render(img, city="Seoul", country="South Korea",
                           difficulty=2, caller=_reply(GOOD))
    assert out["clues"][0].startswith("The blue road sign")
    assert out["clue_words"] == ["sign", "flag", "kerb"]


def test_the_image_is_given_to_the_caller(tmp_path):
    """A clue written without looking at the render is the bug, not the fix."""
    img = tmp_path / "r.png"
    Image.new("RGB", (64, 80), (120, 120, 120)).save(img)
    seen = {}

    def caller(system, user, *, model=None, image=None):
        seen["image"] = image
        return json.dumps(GOOD)

    inspect_render(img, city="Seoul", country="South Korea", difficulty=2,
                     caller=caller)
    assert str(seen["image"]) == str(img)


def test_it_reports_when_the_picture_names_the_place(tmp_path):
    img = tmp_path / "r.png"
    Image.new("RGB", (64, 80), (120, 120, 120)).save(img)
    out = inspect_render(img, city="Seattle", country="United States",
                           difficulty=2,
                           caller=_reply(dict(GOOD, names_the_place=True,
                                              names_the_place_detail="a sign reads Seattle")))
    assert out["names_the_place"] is True
    assert "Seattle" in out["names_the_place_detail"]


def test_a_reply_with_the_wrong_number_of_clues_is_rejected(tmp_path):
    img = tmp_path / "r.png"
    Image.new("RGB", (64, 80), (120, 120, 120)).save(img)
    with pytest.raises(I.InspectError):
        inspect_render(img, city="Seoul", country="South Korea", difficulty=2,
                         caller=_reply(dict(GOOD, clues=["only", "two"])))


def test_an_unreadable_reply_is_rejected(tmp_path):
    img = tmp_path / "r.png"
    Image.new("RGB", (64, 80), (120, 120, 120)).save(img)
    with pytest.raises(I.InspectError):
        inspect_render(img, city="Seoul", country="South Korea", difficulty=2,
                         caller=lambda s, u, model=None, image=None: "sure thing!")


def test_the_instruction_demands_legibility(tmp_path):
    """"A Jalisco plate" is worthless if the plate cannot be read."""
    img = tmp_path / "r.png"
    Image.new("RGB", (64, 80), (120, 120, 120)).save(img)
    seen = {}

    def caller(system, user, *, model=None, image=None):
        seen["user"] = user
        return json.dumps(GOOD)

    inspect_render(img, city="Seoul", country="South Korea", difficulty=2,
                     caller=caller)
    assert "legible" in seen["user"].lower() or "readable" in seen["user"].lower()


def test_it_refuses_to_describe_an_image_that_is_not_there(tmp_path):
    with pytest.raises(FileNotFoundError):
        inspect_render(tmp_path / "missing.png", city="Seoul",
                         country="South Korea", difficulty=2, caller=_reply(GOOD))


# --------------------------------------------------------------------------
# A sign the model declined to letter.
#
# The Cusco render produced a proper blue-and-white ceramic street plaque with
# an empty white centre — the frame without the name. Nothing caught it,
# because the clue pass only writes clues and a blank sign simply became a
# clue nobody wrote.
# --------------------------------------------------------------------------

def test_it_reports_lettering_that_did_not_come_out(tmp_path):
    img = tmp_path / "r.png"
    Image.new("RGB", (64, 80), (120, 120, 120)).save(img)
    out = inspect_render(img, city="Cusco", country="Peru", difficulty=4,
                         caller=_reply(dict(GOOD, broken_text=True,
                                            broken_text_detail="the tiled street plaque is blank")))
    assert out["broken_text"] is True
    assert "blank" in out["broken_text_detail"]


def test_clean_lettering_is_not_reported(tmp_path):
    img = tmp_path / "r.png"
    Image.new("RGB", (64, 80), (120, 120, 120)).save(img)
    out = inspect_render(img, city="Cusco", country="Peru", difficulty=4,
                         caller=_reply(GOOD))
    assert out["broken_text"] is False


def test_the_instruction_asks_about_blank_and_garbled_signs(tmp_path):
    img = tmp_path / "r.png"
    Image.new("RGB", (64, 80), (120, 120, 120)).save(img)
    seen = {}

    def caller(system, user, *, model=None, image=None):
        seen["user"] = user
        return json.dumps(GOOD)

    inspect_render(img, city="Cusco", country="Peru", difficulty=4, caller=caller)
    assert "blank" in seen["user"].lower()
    assert "garbled" in seen["user"].lower()


# --------------------------------------------------------------------------
# The manual already specifies the clue wording, and the clue pass ignored it.
#
# Section 7.5: "Each message is ONE simple factual sentence, MAXIMUM 12 WORDS
# ... Simple wording, fun-fact tone, internationally readable, understandable
# by a 10 year old." Section 7.6 brackets the important nouns. What came back
# was eighteen words of description with neither.
#
# I wrote fresh instructions for this pass instead of carrying 7.4-7.7 across.
# --------------------------------------------------------------------------

def _asked(tmp_path):
    img = tmp_path / "r.png"
    Image.new("RGB", (64, 80), (120, 120, 120)).save(img)
    seen = {}

    def caller(system, user, *, model=None, image=None):
        seen["user"] = user
        return json.dumps(GOOD)

    inspect_render(img, city="Cusco", country="Peru", difficulty=4, caller=caller)
    return seen["user"]



def test_the_bracket_convention_is_stated(tmp_path):
    asked = _asked(tmp_path)
    assert "[" in asked and "square bracket" in asked.lower()



def test_the_three_clues_have_their_assigned_jobs(tmp_path):
    """Strongest first, something different second, a subtle one third."""
    asked = _asked(tmp_path).lower()
    assert "strongest" in asked
    assert "semi-hidden" in asked or "less obvious" in asked


def test_an_overlong_clue_is_rejected():
    """Asking is not getting — this pipeline has learned that twice."""
    long_clue = ("A narrow street paved with rounded cobblestones and a smooth "
                 "flagstone walkway running straight down the middle")
    with pytest.raises(I.InspectError):
        I.check_clues([long_clue, "Flag: A [Peruvian flag] flies.", "c"])


def test_clues_within_the_limit_pass():
    I.check_clues([
        "Landmark: The [Eiffel Tower] rises behind the fountains.",
        "Flag: A blue-white-red [French flag] flies from the pole.",
        "Street furniture: Green cast-iron [Wallace fountains] mark [Paris].",
    ])


# --------------------------------------------------------------------------
# Hints that help the player guess.
#
# The 12-word bracketed format produced captions: "Bay: A curved [bay] is
# lined with white high-rises." That describes the picture the player is
# already looking at and narrows nothing down.
#
# Caglar's example of a good one, for a Guadalajara render:
#
#   "Those teal rental bikes on the sidewalk belong to the 'MiBici' network,
#    which is only found in Mexico's second-largest city."
#
# Conversational. Points at a specific visible object. Names the thing —
# MiBici. Gives the inferential step without handing over the answer: "Mexico's
# second-largest city", not "Guadalajara". Teaches something. Twenty-five
# words, no brackets, no category label.
# --------------------------------------------------------------------------

def test_the_hint_must_narrow_the_place_down(tmp_path):
    asked = _asked(tmp_path).lower()
    assert "narrow" in asked or "where" in asked


def test_the_hint_must_not_hand_over_the_answer(tmp_path):
    """"Mexico's second-largest city", never "Guadalajara"."""
    asked = _asked(tmp_path)
    assert "second-largest" in asked or "without naming" in asked.lower()


def test_the_worked_example_is_shown(tmp_path):
    """One good example teaches the shape better than any list of rules."""
    asked = _asked(tmp_path)
    assert "MiBici" in asked


def test_the_tone_is_conversational(tmp_path):
    asked = _asked(tmp_path).lower()
    assert "conversational" in asked or "like a person" in asked


def test_brackets_and_category_labels_are_forbidden(tmp_path):
    """The prompt must ban the old format, not demand it."""
    asked = _asked(tmp_path).lower()
    assert "no square brackets" in asked
    assert "no category prefixes" in asked


def test_a_caption_is_rejected():
    """Describing the picture is not a hint. This is the failure, by name."""
    with pytest.raises(I.InspectError):
        I.check_clues([
            "A curved bay is lined with white high-rises and moored boats.",
            "Those teal rental bikes belong to the MiBici network, found only "
            "in Mexico's second-largest city.",
            "The pavement lettering is Hangul, which means you are in Korea.",
        ])


def test_a_hint_that_names_something_identifiable_passes():
    I.check_clues([
        "Those teal rental bikes on the sidewalk belong to the MiBici network, "
        "which is only found in Mexico's second-largest city.",
        "The blue road signs use Hangul, the alphabet invented in the 1440s and "
        "used nowhere but the Korean peninsula.",
        "Those orange flexible bollards with reflective bands are a fixture of "
        "South Korean streets.",
    ])


def test_room_to_actually_say_something():
    """The 12-word cap is what forced the captions. Caglar's own example is 25."""
    assert I.MAX_CLUE_WORDS >= 25
