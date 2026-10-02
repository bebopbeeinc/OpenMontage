"""Per-level default clue weights, offered to the operator before generating.

THESE NUMBERS ARE AUTHORED HERE. The manual does not contain them. What it
contains is three things, and each one is used below:

  - 5.3 ranks the fourteen clue families "in order of how functional they are
    for this game". CLUE_FAMILIES is written in that order, so a family's
    position in it is the manual's own statement of its value.
  - 3.2 gates landmarks: "Use famous landmarks and monuments GENEROUSLY at
    difficulty 1 and 2. Never use an unmistakable landmark at difficulty 4,
    5, or 5+."
  - 3.2a says what carries the hardest levels: "Subtle infrastructure and
    architecture carry it. No dependence on a famous landmark at all."

Turning a ranking into numbers is a judgement, and it was made knowing the
cost: before this, a family left at Default was omitted from the prompt
entirely and kept whatever standing the manual gave it, so the absence of an
opinion did not arrive as an opinion. Pre-filling means every generation now
states fourteen opinions. The operator can set any family back to Default,
and the UI says where the numbers came from.
"""
from __future__ import annotations

from scripts.chonky.prompts import CLUE_FAMILIES

LEVELS = (1, 2, 3, 4, 5)

# Rank buckets. Fourteen ranked families into the 1-5 scale the prompt uses:
# the three most functional lead, and the three least carry the least.
_BUCKETS = ((3, 5), (6, 4), (9, 3), (12, 2), (14, 1))


def _base() -> dict[str, int]:
    """Each family's weight from its position in 5.3's ranked list."""
    out: dict[str, int] = {}
    for rank, key in enumerate(CLUE_FAMILIES, start=1):
        for limit, weight in _BUCKETS:
            if rank <= limit:
                out[key] = weight
                break
    return out


# What each level changes about that baseline, and why. A key mapped to None
# is dropped from the request rather than weighted low: a weight of 1 still
# asks for the thing, and 3.2 says never.
_BY_LEVEL: dict[int, dict[str, int | None]] = {
    # 3.2: landmarks generous. 3.2a: the opening ViewFrame nearly solves it.
    1: {"landmark": 5},
    2: {"landmark": 5},
    # 3 is the hinge — 3.2a wants several plausible places at first glance, so
    # the monument stops doing the work without being banned yet.
    3: {"landmark": 2},
    # 3.2: never an unmistakable landmark from 4 upward.
    4: {"landmark": None},
    # 3.2a: "Subtle infrastructure and architecture carry it."
    5: {"landmark": None, "infrastructure": 4, "architecture": 4,
        "bollards": 4, "utility_poles": 4},
}


def default_weights(difficulty: int) -> dict[str, int]:
    """The weights to pre-fill for this level. Not a rule — a starting point."""
    try:
        level = int(difficulty)
    except (TypeError, ValueError):
        raise ValueError(f"difficulty must be a level, got {difficulty!r}") from None
    if level not in LEVELS:
        raise ValueError(f"difficulty must be one of {LEVELS}, got {level}")

    weights = _base()
    for key, value in _BY_LEVEL[level].items():
        if value is None:
            weights.pop(key, None)
        else:
            weights[key] = value
    return weights


def all_levels() -> dict[int, dict[str, int]]:
    """Every level's defaults, for the UI to pre-fill from without a round trip."""
    return {level: default_weights(level) for level in LEVELS}
