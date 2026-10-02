"""Per-level default clue weights, shown in the UI before generating.

These numbers are AUTHORED. The manual gives a ranking (5.3), a landmark gate
(3.2) and a statement about what carries a level 5 (3.2a) — it does not give
weights. Caglar asked for the dropdowns pre-filled per level anyway, having
been told that; what follows derives as closely from those three passages as
numbers can, and says so in the UI.
"""
import pytest

from scripts.chonky import emphasis
from scripts.chonky.prompts import CLUE_FAMILIES


def test_every_level_is_covered():
    for level in (1, 2, 3, 4, 5):
        assert emphasis.default_weights(level)


def test_only_real_families_at_legal_weights():
    for level in (1, 2, 3, 4, 5):
        for key, weight in emphasis.default_weights(level).items():
            assert key in CLUE_FAMILIES, key
            assert isinstance(weight, int) and 1 <= weight <= 5, (key, weight)


def test_landmarks_are_generous_at_one_and_two():
    """3.2: "Use famous landmarks and monuments GENEROUSLY at difficulty 1 and 2."""
    assert emphasis.default_weights(1)["landmark"] == 5
    assert emphasis.default_weights(2)["landmark"] == 5


def test_landmarks_are_absent_from_four_upward():
    """3.2 bans them outright there, so no weight at all — not a low one.

    A weight of 1 still asks for a landmark. The manual says never, and the
    honest encoding of never is to leave the family out of the request.
    """
    for level in (4, 5):
        assert "landmark" not in emphasis.default_weights(level)


def test_the_everyday_systems_lead_at_every_level():
    """5.3 ranks them first and they never stop being the most functional."""
    for level in (1, 2, 3, 4, 5):
        w = emphasis.default_weights(level)
        assert w["road_markings"] == 5
        assert w["road_signs"] == 5
        assert w["language"] == 5


def test_infrastructure_and_architecture_carry_level_five():
    """3.2a: "Subtle infrastructure and architecture carry it."""
    five = emphasis.default_weights(5)
    base = emphasis.default_weights(1)
    for key in ("infrastructure", "architecture"):
        assert five[key] > base[key], key


def test_an_unknown_level_is_refused():
    with pytest.raises(ValueError):
        emphasis.default_weights(9)


def test_the_weights_pass_the_prompt_writers_own_validator():
    """Whatever the UI pre-fills must survive _weights_section untouched."""
    from scripts.chonky.prompts import _weights_section
    for level in (1, 2, 3, 4, 5):
        lines = _weights_section(emphasis.default_weights(level))
        assert lines and any("1 to 5" in ln for ln in lines)
