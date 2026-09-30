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
    "clues": ["The blue road sign carries Hangul lettering",
              "A Taegukgi flies beside the gate",
              "The kerb is painted in yellow and white stripes"],
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


def test_the_twelve_word_limit_is_stated(tmp_path):
    asked = _asked(tmp_path).lower()
    assert "12 words" in asked or "twelve words" in asked


def test_the_bracket_convention_is_stated(tmp_path):
    asked = _asked(tmp_path)
    assert "[" in asked and "square bracket" in asked.lower()


def test_the_tone_is_stated(tmp_path):
    asked = _asked(tmp_path).lower()
    assert "10 year old" in asked or "ten year old" in asked
    assert "fun-fact" in asked or "fun fact" in asked


def test_the_three_clues_have_their_assigned_jobs(tmp_path):
    """7.4: most definitive, second and distinct, then one semi-hidden."""
    asked = _asked(tmp_path).lower()
    assert "most definitive" in asked
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
