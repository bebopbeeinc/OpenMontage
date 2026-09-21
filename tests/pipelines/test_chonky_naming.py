import pytest

from scripts.chonky.naming import build_filename, slug
from scripts.chonky.targeting import next_zone


def test_slug_survives_accented_place_names():
    assert slug("São Paulo") == "sao-paulo"
    assert slug("Zürich") == "zurich"
    assert slug("Côte d'Ivoire") == "cote-d-ivoire"
    assert slug("Gdańsk") == "gdansk"


def test_slug_writes_difficulty_5plus():
    assert slug("5+") == "5plus"


def test_filename_shape():
    assert build_filename("1", "Lisbon", "Portugal", ["arch", "calcada", "clock"]) == \
        "1_lisbon_portugal_arch_calcada_clock.jpg"


def test_filename_requires_exactly_three_clue_words():
    with pytest.raises(ValueError):
        build_filename("1", "Lisbon", "Portugal", ["arch", "calcada"])


def test_zone_targeting_follows_the_configured_majority_when_empty():
    assert next_zone([], 70)["target_zone"] == "viewframe"


def test_zone_targeting_corrects_an_over_represented_zone():
    rows = [{"chonky_zone": "viewframe"}] * 10
    assert next_zone(rows, 70)["target_zone"] == "margin"


def test_zone_targeting_reports_the_actual_split():
    rows = [{"chonky_zone": "viewframe"}] * 6 + [{"chonky_zone": "margin"}] * 4
    result = next_zone(rows, 70)
    assert result["stats"]["actual_viewframe_pct"] == 60
    assert result["target_zone"] == "viewframe"


def test_zone_targeting_ignores_unmeasured_rows():
    rows = [{"chonky_zone": ""}, {"chonky_zone": "viewframe"}]
    assert next_zone(rows, 70)["stats"]["total"] == 1
