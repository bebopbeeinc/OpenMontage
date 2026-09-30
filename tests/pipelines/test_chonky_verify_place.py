"""Checking that a viewpoint is a real place before paying to render it.

The writer now has to NAME a viewpoint, which stops "a plaza" — but a
confidently worded invented street sails straight through. The manual has
always demanded the opposite: "Verify it against reliable maps, street-level
imagery, official imagery, or multiple photographs."

A render of a beautiful street that does not exist fails the game completely,
and no amount of looking at the finished image can catch it.
"""

import json

import pytest

from scripts.chonky import verify_place as V

# Bound at import, before any conftest guard replaces the attribute.
verify_viewpoint = V.verify_viewpoint


def _reply(payload):
    return lambda system, user, *, model=None: json.dumps(payload)


REAL = {"real": True, "confidence": "high",
        "note": "Cuesta San Blas is a documented cobbled street in Cusco"}


def test_a_verified_place_comes_back_real():
    out = verify_viewpoint(city="Cusco", country="Peru",
                           viewpoint="Cuesta San Blas, facing downhill",
                           caller=_reply(REAL))
    assert out["real"] is True
    assert out["confidence"] == "high"
    assert "Cuesta San Blas" in out["note"]


def test_an_invented_place_comes_back_not_real():
    out = verify_viewpoint(city="Cusco", country="Peru",
                           viewpoint="Calle Mirador de los Tres Condores",
                           caller=_reply({"real": False, "confidence": "high",
                                          "note": "no such street is documented"}))
    assert out["real"] is False


def test_the_place_and_the_viewpoint_both_reach_the_search():
    seen = {}

    def caller(system, user, *, model=None):
        seen["user"] = user
        return json.dumps(REAL)

    verify_viewpoint(city="Cusco", country="Peru",
                     viewpoint="Cuesta San Blas, facing downhill", caller=caller)
    assert "Cusco" in seen["user"] and "Peru" in seen["user"]
    assert "Cuesta San Blas" in seen["user"]


def test_low_confidence_is_not_treated_as_verified():
    """The manual says omit what is uncertain; a maybe is a no."""
    out = verify_viewpoint(city="Cusco", country="Peru", viewpoint="somewhere",
                           caller=_reply({"real": True, "confidence": "low",
                                          "note": "could not find it"}))
    assert out["verified"] is False, "low confidence must not pass as verified"


def test_high_confidence_and_real_is_verified():
    out = verify_viewpoint(city="Cusco", country="Peru", viewpoint="Cuesta San Blas",
                           caller=_reply(REAL))
    assert out["verified"] is True


def test_an_unreadable_reply_counts_as_not_verified():
    """It used to raise. Raising was wrong.

    A correct rejection often arrives as prose, and raising turned the right
    answer into a crashed job instead of a retry somewhere findable. An
    unreadable reply now fails closed: nothing was verified, so nothing is
    claimed.
    """
    out = verify_viewpoint(city="Cusco", country="Peru", viewpoint="x",
                           caller=lambda s, u, model=None: "I had a look and yes")
    assert out["verified"] is False


def test_a_transport_failure_still_raises():
    """Fail-closed is for an answer that cannot be read, not for no answer.

    A CLI that fell over is a broken pipeline, and swallowing that as "the
    place is not real" would blame the writer for an outage.
    """
    def boom(system, user, *, model=None):
        raise V.PlaceError("`claude` CLI failed (exit 1)")

    with pytest.raises(V.PlaceError):
        verify_viewpoint(city="Cusco", country="Peru", viewpoint="x", caller=boom)


# --------------------------------------------------------------------------
# In the draft loop: an unverified place must not reach OpenArt.
# --------------------------------------------------------------------------

from scripts.chonky import prompts  # noqa: E402

DRAFTED = {
    "city": "Cusco", "country": "Peru",
    "viewpoint": "Cuesta San Blas, facing downhill toward the old town",
    "prompt": ("The crowd walks ahead and Chonky sits far beyond all of them "
               "on the cobblestones."),
    "clues": ["a", "b", "c"], "clue_words": ["x", "y", "z"],
}


def test_a_verified_viewpoint_is_accepted():
    out = prompts.draft(difficulty=1, target_zone="viewframe", used=[],
                        caller=lambda s, u, model=None: json.dumps(DRAFTED),
                        verifier=lambda **kw: {"verified": True, "real": True,
                                               "confidence": "high", "note": "found it"})
    assert out["viewpoint"].startswith("Cuesta San Blas")
    assert out["place_note"] == "found it"


def test_an_unverified_viewpoint_is_rejected_and_retried():
    """Rejecting it feeds the reason back, so the next attempt picks elsewhere."""
    asks = []

    def caller(system, user, *, model=None):
        asks.append(user)
        return json.dumps(DRAFTED)

    findings = [{"verified": False, "real": False, "confidence": "high",
                 "note": "no such street is documented"},
                {"verified": True, "real": True, "confidence": "high",
                 "note": "found it"}]

    out = prompts.draft(difficulty=1, target_zone="viewframe", used=[],
                        caller=caller, verifier=lambda **kw: findings.pop(0))
    assert out["place_note"] == "found it"
    assert len(asks) == 2
    assert "no such street is documented" in asks[1]


def test_without_a_verifier_nothing_is_claimed_about_the_place():
    """Absence of a check must not read as a passed check."""
    out = prompts.draft(difficulty=1, target_zone="viewframe", used=[],
                        caller=lambda s, u, model=None: json.dumps(DRAFTED))
    assert out.get("place_note") is None


# --------------------------------------------------------------------------
# A nuanced rejection arrives as prose, not JSON.
#
# Asked about an invented Cusco street, the verifier answered correctly — the
# real Mirador de los Cóndores is a condor-viewing trail 92 km away, not a
# street — but it explained rather than emitting JSON, and the parser threw.
# A correct rejection surfaced as a parse error, so the job died instead of
# retrying somewhere findable.
# --------------------------------------------------------------------------

PROSE = ('The real "Mirador de los Cóndores" is a remote condor-viewing trail '
         'in the Apurímac canyon near Chonta, about 92 km from Cusco — a '
         'hiking viewpoint, not a street.')


def test_a_prose_reply_is_retried_asking_for_json():
    replies = [PROSE, json.dumps({"real": False, "confidence": "high",
                                  "note": "not a street"})]
    asks = []

    def caller(system, user, *, model=None):
        asks.append(user)
        return replies.pop(0)

    out = verify_viewpoint(city="Cusco", country="Peru", viewpoint="x", caller=caller)
    assert out["verified"] is False
    assert len(asks) == 2, "a prose reply must be asked again"


def test_prose_that_stays_prose_counts_as_not_verified():
    """Fail closed. An answer nobody could read is not a place anybody found."""
    out = verify_viewpoint(city="Cusco", country="Peru", viewpoint="x",
                           caller=lambda s, u, model=None: PROSE)
    assert out["verified"] is False
    assert out["real"] is False


def test_the_prose_is_kept_as_the_reason():
    """It is the most useful thing in the reply: it says what is actually there."""
    out = verify_viewpoint(city="Cusco", country="Peru", viewpoint="x",
                           caller=lambda s, u, model=None: PROSE)
    assert "condor" in out["note"].lower()


def test_json_buried_in_prose_is_still_read():
    """Models preface JSON with a sentence; that is not a failure to answer."""
    mixed = ('Here is what I found:\n\n'
             '{"real": true, "confidence": "high", "note": "documented street"}')
    out = verify_viewpoint(city="Cusco", country="Peru", viewpoint="x",
                           caller=lambda s, u, model=None: mixed)
    assert out["verified"] is True
