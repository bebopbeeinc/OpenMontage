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


# Fifty characters each, in the voice of someone telling a ten-year-old.
GOOD = {
    "clues": ["Those blue signs are written in Hangul",
              "That flag has a red and blue circle",
              "The kerb is painted yellow and white"],
    "clue_words": ["sign", "flag", "kerb"],
    "names_the_place": False,
}


def test_it_returns_clues_taken_from_the_image(tmp_path):
    img = tmp_path / "r.png"
    Image.new("RGB", (64, 80), (120, 120, 120)).save(img)
    out = inspect_render(img, city="Seoul", country="South Korea",
                           difficulty=2, caller=_reply(GOOD))
    assert out["clues"][0].startswith("Those blue signs")
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




def test_the_three_clues_have_their_assigned_jobs(tmp_path):
    """Strongest first, something different second, a subtle one third."""
    asked = _asked(tmp_path).lower()
    assert "strongest" in asked
    assert "semi-hidden" in asked or "less obvious" in asked



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








# --------------------------------------------------------------------------
# Two things this pass has never reported, and both are the things that decide
# whether a render is usable at all.
#
# IDENTITY. The manual calls an ordinary ginger cat "a FAILED image" and "the
# single worst outcome", and nothing in the pipeline has ever checked for it.
# `measurement` records a box, a height and a zone; YOLO says "cat", which
# cannot tell Chonky from any tabby in the world. Every render on 2026-10-01
# came back as a normal cat and the pipeline reported them all as fine.
#
# CLUES. S3.2b sets a floor of two independent real clues at EVERY difficulty
# — "a deserted beach ... is NOT an acceptable level 5 image" — and S5.2 wants
# at least one definitive clue inside the ViewFrame. Neither is enforced
# anywhere. A beautiful render carrying nothing to reason from is a failed
# geography puzzle, and until now only a person noticing caught it.
#
# Both are reports, not gates. They sit beside the pixel verdict and the
# operator decides, exactly as the size verdict works today.
# --------------------------------------------------------------------------

def test_it_asks_whether_the_cat_is_actually_chonky(tmp_path):
    asked = _asked(tmp_path).lower()
    assert "chonky" in asked
    assert "bib" in asked or "near-spherical" in asked


def test_it_asks_how_many_clues_are_really_visible(tmp_path):
    asked = _asked(tmp_path).lower()
    assert "clue" in asked
    assert "two" in asked or "at least 2" in asked


def test_it_asks_whether_a_clue_sits_inside_the_viewframe(tmp_path):
    asked = _asked(tmp_path).lower()
    assert "viewframe" in asked


def test_the_identity_verdict_is_returned(tmp_path):
    img = tmp_path / "r.png"
    Image.new("RGB", (64, 80), (120, 120, 120)).save(img)
    out = inspect_render(img, city="Seoul", country="South Korea", difficulty=2,
                         caller=_reply(dict(GOOD, is_chonky=False,
                                            identity_note="ordinary tabby, no bib")))
    assert out["is_chonky"] is False
    assert out["identity_note"] == "ordinary tabby, no bib"


def test_the_clue_verdict_is_returned(tmp_path):
    img = tmp_path / "r.png"
    Image.new("RGB", (64, 80), (120, 120, 120)).save(img)
    out = inspect_render(img, city="Seoul", country="South Korea", difficulty=2,
                         caller=_reply(dict(GOOD, clue_count=3,
                                            clue_families=["language", "flags"],
                                            clue_in_viewframe=True)))
    assert out["clue_count"] == 3
    assert out["clue_families"] == ["language", "flags"]
    assert out["clue_in_viewframe"] is True


def test_an_unknown_clue_family_is_dropped_rather_than_trusted(tmp_path):
    """The families are a fixed vocabulary; an invented one means nothing."""
    img = tmp_path / "r.png"
    Image.new("RGB", (64, 80), (120, 120, 120)).save(img)
    out = inspect_render(img, city="Seoul", country="South Korea", difficulty=2,
                         caller=_reply(dict(GOOD, clue_families=["language", "vibes"])))
    assert out["clue_families"] == ["language"]


def test_a_reply_without_the_new_fields_still_works(tmp_path):
    """An older reply shape must not crash the pass; it reports nothing known."""
    img = tmp_path / "r.png"
    Image.new("RGB", (64, 80), (120, 120, 120)).save(img)
    out = inspect_render(img, city="Seoul", country="South Korea", difficulty=2,
                         caller=_reply(GOOD))
    assert out["is_chonky"] is None
    assert out["clue_count"] is None


# --------------------------------------------------------------------------
# Three ways the clue pass threw away a good answer, all measured on the
# verification batch of 2026-10-01. Each one silently dropped the render back
# to prompt-written clues — the exact failure STEP C exists to prevent — and
# took the identity and clue verdicts down with it.
# --------------------------------------------------------------------------



def test_a_paragraph_is_still_refused():
    with pytest.raises(I.InspectError):
        I.check_clues(["word " * 60, "b", "c"])


def test_json_after_a_line_of_prose_is_still_read(tmp_path):
    """Venice: the CLI prefixed "Apologies — that tool call was a mistake".

    verify_place.py already recovers from this; this pass did not, and threw
    away a complete, correct reply over a sentence in front of it.
    """
    img = tmp_path / "r.png"
    Image.new("RGB", (64, 80), (120, 120, 120)).save(img)
    noisy = ("Apologies — that tool call was a mistake. Here is the requested "
             "JSON:\n\n```json\n" + json.dumps(GOOD) + "\n```")
    out = inspect_render(img, city="Venice", country="Italy", difficulty=2,
                         caller=lambda s, u, *, model=None, image=None: noisy)
    assert out["clues"][0].startswith("Those blue signs")


# --------------------------------------------------------------------------
# The hint format, third revision (Caglar, 2026-10-02): max 50 CHARACTERS,
# and the voice of a person telling a ten-year-old something.
#
#     "that church was baroque architecture"        -- 36 characters
#
# The MiBici shape this replaces ran to about 130 characters and carried two
# halves: the thing you can see, and what it narrows the place down to. Fifty
# characters only fits the first half. The hint now points at the thing and
# names it, and the player does the rest.
# --------------------------------------------------------------------------

def test_a_fifty_character_hint_passes():
    I.check_clues(["That church was baroque architecture",
                   "Those blue signs are written in Hangul",
                   "The plates are yellow, that means Dutch"])


def test_a_hint_over_fifty_characters_is_refused():
    with pytest.raises(I.InspectError) as exc:
        I.check_clues([
            "Those teal rental bikes belong to the MiBici network, which is "
            "only found in Mexico's second-largest city.",
            "short one", "another short one"])
    assert "50" in str(exc.value)


def test_exactly_fifty_is_allowed_and_fifty_one_is_not():
    I.check_clues(["x" * 50, "a", "b"])
    with pytest.raises(I.InspectError):
        I.check_clues(["x" * 51, "a", "b"])


def test_the_limit_is_stated_in_characters_not_words(tmp_path):
    asked = _asked(tmp_path).lower()
    assert "50 characters" in asked
    assert "thirty words" not in asked


def test_the_ten_year_old_voice_and_example_are_shown(tmp_path):
    asked = _asked(tmp_path)
    assert "baroque architecture" in asked
    assert "10-year-old" in asked or "ten-year-old" in asked


# --------------------------------------------------------------------------
# Twelve tests were removed here on 2026-10-02, when the hint format changed
# for the third time. They asserted the two formats that came before:
#
#   1. Twelve words with [brackets] round the key nouns. It produced captions
#      — "Bay: A curved [bay] is lined with white high-rises" — because twelve
#      words has no room to name a thing AND say anything about it, so it kept
#      the describing half and dropped the helping half.
#   2. Up to about thirty words, conversational, naming a diagnostic feature
#      and what it narrowed the place down to. Roughly 130 characters.
#
# The current rule is fifty characters in the voice of someone telling a
# ten-year-old, which only fits the first half of (2). What those tests
# guarded is now guarded by the character cap and the worked example; keeping
# them would have meant asserting three incompatible formats at once.
#
# Two checks beyond length were tried and are gone for good: a word cap, which
# produced the captions above, and a "must contain a proper noun" rule, which
# rejected a Sydney hint describing the Harbour Bridge precisely and
# deliberately without naming it. Avoiding the answer and naming something
# identifiable pull in opposite directions, and no regex tells them apart.
# --------------------------------------------------------------------------
