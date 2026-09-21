"""Delivery filenames: {difficulty}_{city}_{country}_{c1}_{c2}_{c3}.jpg"""
from __future__ import annotations

import re
import unicodedata


def slug(text: str) -> str:
    """Lowercase, ASCII, hyphen-separated. Accents survive as their base letter.

    Place names carry diacritics constantly, and collapsing them to hyphens
    turns "Cote d'Ivoire" into "c-te-d-ivoire".
    """
    t = unicodedata.normalize("NFD", str(text))
    t = "".join(ch for ch in t if unicodedata.category(ch) != "Mn")
    t = t.lower()
    t = t.replace("ß", "ss").replace("ø", "o").replace("đ", "d").replace("ł", "l")
    t = t.replace("+", "plus")
    t = re.sub(r"[^a-z0-9]+", "-", t)
    return t.strip("-")


def build_filename(difficulty: str, city: str, country: str, clue_words: list[str]) -> str:
    if len(clue_words) != 3:
        raise ValueError(f"clue_words must contain exactly 3 words, got {len(clue_words)}")
    parts = [slug(difficulty), slug(city), slug(country)] + [slug(w) for w in clue_words]
    return "_".join(parts) + ".jpg"
