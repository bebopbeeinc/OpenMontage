"""Real signage and plates for the real place.

The Cusco render put a European plate with a blue EU strip on a car in Peru,
and a Spanish-style street plaque with no lettering at all. Both were invented
by the image model filling in a gap the prompt left open.

The verification pass is already on the web looking the viewpoint up. It can
come back with what is actually there — the street's real name, the country's
real plate design, the script on the signage — and those facts go into the
prompt as facts, overriding whatever the writer assumed.
"""

import json

import pytest

from scripts.chonky import verify_place as V

verify_viewpoint = V.verify_viewpoint

FOUND = {
    "real": True,
    "confidence": "high",
    "note": "documented street in the San Blas quarter",
    "details": {
        "street_name": "CUESTA DE SAN BLAS",
        "plate_format": ("white rectangular plate, black characters, three "
                         "letters then three digits, no EU band"),
        "script": "Latin alphabet, Spanish, some Quechua street names",
        "signage": "blue and white ceramic tile street plaques",
    },
}


def _reply(payload):
    return lambda s, u, model=None: json.dumps(payload)


def test_the_verified_details_come_back():
    out = verify_viewpoint(city="Cusco", country="Peru",
                           viewpoint="Cuesta de San Blas", caller=_reply(FOUND))
    assert out["details"]["street_name"] == "CUESTA DE SAN BLAS"
    assert "no EU band" in out["details"]["plate_format"]


def test_details_are_asked_for():
    seen = {}

    def caller(system, user, *, model=None):
        seen["user"] = user
        return json.dumps(FOUND)

    verify_viewpoint(city="Cusco", country="Peru", viewpoint="x", caller=caller)
    assert "plate" in seen["user"].lower()
    assert "street" in seen["user"].lower()


def test_a_reply_without_details_still_verifies():
    """Details are a bonus on top of the answer, not a new way to fail."""
    out = verify_viewpoint(city="Cusco", country="Peru", viewpoint="x",
                           caller=_reply({"real": True, "confidence": "high",
                                          "note": "found it"}))
    assert out["verified"] is True
    assert out["details"] == {}


def test_the_details_become_a_block_the_render_can_use():
    block = V.detail_block(FOUND["details"])
    assert "CUESTA DE SAN BLAS" in block
    assert "no EU band" in block
    # Stated as fact, because it is: it was looked up, not imagined.
    assert "verified" in block.lower()


def test_no_details_means_no_block():
    """Silence beats inventing a heading with nothing under it."""
    assert V.detail_block({}) == ""


def test_the_block_is_appended_to_the_prompt_not_woven_into_it():
    """It has to be visibly separate so a reviewer can see what was imposed."""
    block = V.detail_block(FOUND["details"])
    assert block.startswith("\n")


# --------------------------------------------------------------------------
# It has to reach the render, or it is a fact nobody used.
# --------------------------------------------------------------------------

from scripts.chonky import prompts  # noqa: E402

DRAFTED = {
    "city": "Cusco", "country": "Peru",
    "viewpoint": "Cuesta de San Blas, facing downhill",
    "prompt": ("The crowd walks ahead and Chonky sits far beyond all of them "
               "on the cobblestones."),
    "clues": ["a", "b", "c"], "clue_words": ["x", "y", "z"],
}


def test_the_verified_detail_is_appended_to_the_prompt():
    out = prompts.draft(difficulty=1, target_zone="viewframe", used=[],
                        caller=lambda s, u, model=None: json.dumps(DRAFTED),
                        verifier=lambda **kw: FOUND | {"verified": True})
    assert "CUESTA DE SAN BLAS" in out["prompt"]
    assert "no EU band" in out["prompt"]
    # And the writer's own prompt survives underneath it.
    assert "Chonky sits far beyond all of them" in out["prompt"]


def test_no_details_leaves_the_prompt_alone():
    plain = {"real": True, "confidence": "high", "note": "found", "verified": True,
             "details": {}}
    out = prompts.draft(difficulty=1, target_zone="viewframe", used=[],
                        caller=lambda s, u, model=None: json.dumps(DRAFTED),
                        verifier=lambda **kw: plain)
    assert out["prompt"] == DRAFTED["prompt"]


def test_the_appended_block_does_not_break_the_prompt_checks():
    """The block mentions plates and streets; it must not trip the rules."""
    out = prompts.draft(difficulty=1, target_zone="viewframe", used=[],
                        caller=lambda s, u, model=None: json.dumps(DRAFTED),
                        verifier=lambda **kw: FOUND | {"verified": True})
    assert out["prompt"]


# --------------------------------------------------------------------------
# A photograph of the actual viewpoint, so the render has something to match.
# --------------------------------------------------------------------------

WITH_PHOTO = FOUND | {
    "photo_url": "https://upload.example.org/choquechaka.jpg",
    "photo_note": "street-level photograph looking uphill",
}


def test_the_verifier_returns_a_photograph_of_the_viewpoint():
    out = verify_viewpoint(city="Cusco", country="Peru", viewpoint="x",
                           caller=_reply(WITH_PHOTO))
    assert out["photo_url"].endswith("choquechaka.jpg")


def test_a_photograph_is_asked_for():
    seen = {}

    def caller(system, user, *, model=None):
        seen["user"] = user
        return json.dumps(WITH_PHOTO)

    verify_viewpoint(city="Cusco", country="Peru", viewpoint="x", caller=caller)
    assert "photograph" in seen["user"].lower()


def test_no_photograph_is_not_a_failure():
    """An unanchored render is worse than an anchored one, not a broken one."""
    out = verify_viewpoint(city="Cusco", country="Peru", viewpoint="x",
                           caller=_reply(FOUND))
    assert out["verified"] is True
    assert out["photo_url"] == ""


def test_the_photo_url_travels_with_the_draft():
    out = prompts.draft(difficulty=1, target_zone="viewframe", used=[],
                        caller=lambda s, u, model=None: json.dumps(DRAFTED),
                        verifier=lambda **kw: WITH_PHOTO | {"verified": True})
    assert out["photo_url"].endswith("choquechaka.jpg")
