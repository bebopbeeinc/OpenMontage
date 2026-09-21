import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "common"))

import openart_api as api  # noqa: E402
import openart_characters as characters  # noqa: E402


def test_sunburst_has_an_mcp_route():
    assert api.model_id("GPT Image 2.5 Sunburst") == "gpt-image-2-5-sunburst"


def test_flare_has_an_mcp_route():
    assert api.model_id("GPT Image 2.5 Flare") == "gpt-image-2-5-flare"


def test_chonky_character_has_stills():
    stills = characters.stills("Chonky")
    assert stills, "character_library/chonky/ must contain at least one still"
    assert all(p.exists() for p in stills)
