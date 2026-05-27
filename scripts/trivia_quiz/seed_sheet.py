"""Seed the Questions + Posts_Quiz tabs with content from an existing YAML
fixture. One-shot migration helper — useful for v0.2 onboarding so the user
has at least one working row in the sheet to model off.

Usage:
    python -m scripts.trivia_quiz.seed_sheet --slug scotland-turkey-bahamas

Idempotent: if the slug already has rows on either tab, the script skips
that tab (won't duplicate). Q UIDs are auto-generated based on the highest
existing UID + 1.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parents[2]
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(_REPO / ".env")
except ImportError:
    pass

from scripts.trivia_quiz.sheets import (
    QUIZ_SHEET_ID,
    POSTS_TAB,
    POST_FIELDS,
    DATA_START,
    build_sheets,
    read_posts_bulk,
)


def _fmt_choices(choices: list[str]) -> str:
    return " | ".join(choices) if choices else ""


def seed_from_fixture(slug: str) -> None:
    fixture_path = _REPO / "projects" / "trivia-quiz" / slug / "inputs" / "quiz_row.yaml"
    if not fixture_path.exists():
        sys.exit(f"✗ fixture not found: {fixture_path}")
    row = yaml.safe_load(fixture_path.read_text())

    sheets = build_sheets(write=True)
    existing_posts = read_posts_bulk(sheets)

    if any(p["slug"].strip() == slug for p in existing_posts):
        print(f"  ⚠ Posts_Quiz already has slug={slug!r}; skipping")
        return

    # Build the one wide row with all 3 questions inline
    post_record = {
        "order":             str(len(existing_posts) + 1),
        "post_date":         str(row.get("post_date") or ""),
        "slug":              row["slug"],
        "hook_variant":      row.get("hook_variant", ""),
        # Q1
        "q1_question":       row["q1"].get("question", ""),
        "q1_choices":        _fmt_choices(row["q1"].get("choices") or []),
        "q1_answer":         row["q1"].get("answer", ""),
        "q1_fact":           row["q1"].get("surprise_fact", ""),
        "q1_backdrop":       row["q1"].get("backdrop_hint", ""),
        # Q2
        "q2_question":       row["q2"].get("question", ""),
        "q2_choices":        _fmt_choices(row["q2"].get("choices") or []),
        "q2_answer":         row["q2"].get("answer", ""),
        "q2_fact":           row["q2"].get("surprise_fact", ""),
        "q2_backdrop":       row["q2"].get("backdrop_hint", ""),
        # Q3
        "q3_question":       row["q3"].get("question", ""),
        "q3_choices":        _fmt_choices(row["q3"].get("choices") or []),
        "q3_answer":         row["q3"].get("answer", ""),
        "q3_fact":           row["q3"].get("surprise_fact", ""),
        "q3_backdrop":       row["q3"].get("backdrop_hint", ""),
        "q3_game_themed":    "YES" if row["q3"].get("game_themed") else "NO",
        # Post metadata
        "game_hook_line":    row.get("game_hook_line", ""),
        "bottom_cta":        row.get("bottom_cta", ""),
        "music_track":       row.get("music_track", ""),
        "reward":            row.get("reward", ""),
        # Single shared caption — prefer `caption`, fall back to legacy `tiktok`
        "caption":           (row.get("captions") or {}).get("caption")
                              or (row.get("captions") or {}).get("tiktok", ""),
        "pinned_comment":    (row.get("captions") or {}).get("pinned_comment", ""),
        "final_status":      "Draft",
        "final_video_link":  "",
        "final_feedback":    "",
    }
    sheets.spreadsheets().values().append(
        spreadsheetId=QUIZ_SHEET_ID,
        range=f"{POSTS_TAB}!A{DATA_START}",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": [[post_record[k] for k in POST_FIELDS]]},
    ).execute()
    print(f"  ✓ added Posts_Quiz row for slug={slug!r} ({len(POST_FIELDS)} columns)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True, help="slug of the YAML fixture to seed from")
    args = ap.parse_args()
    print(f"→ Seeding sheet from fixture: {args.slug}")
    seed_from_fixture(args.slug)
    print(f"→ Done. Verify by running:")
    print(f"    python -m scripts.trivia_quiz.build --slug {args.slug} --from-sheet --with-openart --reuse-question-assets --with-vo --with-music --with-sfx")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
