#!/usr/bin/env python
"""Read one daily-trivia Day and write a brief artifact for the trivia-captain pipeline.

Inputs:
    --day N        the DailyTriviaConfig Day number (also the row index minus offset)
    --slug NAME    optional kebab-case slug override (default: derived from question text)
    --tab TAB      DailyTriviaConfig tab name (default: 'DailyTriviaConfig (DEV)')

What it does:
    1. Read DailyTriviaConfig row for Day N.
    2. Resolve question / correct-answer / correct-explanation Uids via LocalizedTextConfig.
    3. Compute a stable sheet-revision hash over the cells the pipeline reads.
    4. Write projects/trivia-captain/<slug>/artifacts/brief.json (schema-flexible; see metadata.trivia_captain).
    5. Upsert the TriviaCaptainQueue row: Day, Slug, Status=Draft, Question, Correct Answer.

Fact-fit classification was removed on 2026-05-19 — humans curate which Days
make good reaction-reel candidates; the heuristic was redundant.

Usage:
    python scripts/trivia_captain/select_row.py --day 2
    python scripts/trivia_captain/select_row.py --day 2 --slug guinea-pigs-switzerland
    python scripts/trivia_captain/select_row.py --day 2 --dry-run
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from scripts.trivia_captain import daily_trivia, queue_row  # noqa: E402
from scripts.trivia_captain.paths import project_dir  # noqa: E402


def slugify(text: str, fallback: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    # Trim to 5 words for a usable directory name.
    parts = [p for p in s.split("-") if p][:5]
    out = "-".join(parts) or fallback
    return out[:60]


def compute_revision_hash(trivia: daily_trivia.TriviaRow) -> str:
    """SHA-1 over the cells the pipeline reads. Detects sheet edits between runs."""
    payload = [
        trivia.uid_trivia, str(trivia.day),
        trivia.uid_question, trivia.uid_correct_answer, trivia.uid_correct_explanation,
        trivia.question_en, trivia.correct_answer_en, trivia.correct_explanation_en,
    ]
    return hashlib.sha1(json.dumps(payload).encode()).hexdigest()[:12]


def write_brief(slug: str, brief: dict) -> Path:
    artifacts_dir = project_dir(slug) / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    out = artifacts_dir / "brief.json"
    out.write_text(json.dumps(brief, indent=2) + "\n")
    return out


def upsert_queue_row(
    write_sheets, day: int, slug: str,
    question_en: str, correct_answer_en: str,
    status: str = queue_row.STATUS_DRAFT,
) -> int:
    """Find Queue!Day=N or append a new row; return the 1-indexed sheet row."""
    existing = queue_row.find_row_by_day(write_sheets, day)
    if existing:
        queue_row.update_cells(
            write_sheets, existing,
            slug=slug, status=status,
            question_en=question_en, correct_answer_en=correct_answer_en,
        )
        return existing
    # Append a fresh row. ROW_KEYS order = day, slug, status, question_en, ...
    row_values = [
        str(day),                # A day
        slug,                    # B slug
        status,                  # C status
        question_en,             # D question
        correct_answer_en,       # E correct answer
        "",                      # F hook VO (script-director fills later)
        "",                      # G fact VO
        "",                      # H kicker VO
        "",                      # I drive link
        "",                      # J openart_prompt (script-director fills later)
        "",                      # K caption (script-director fills later)
        "",                      # L drive_clip_link (publish fills later)
    ]
    return queue_row.append_row(write_sheets, row_values)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", type=int, required=True)
    ap.add_argument("--slug", type=str, default=None,
                    help="kebab-case slug (default: derived from question text)")
    ap.add_argument("--tab", type=str, default=daily_trivia.DAILY_TRIVIA_TAB_DEV)
    ap.add_argument("--dry-run", action="store_true",
                    help="read + print; no brief written, no sheet write")
    args = ap.parse_args()

    sheets_ro = daily_trivia.build_sheets()
    trivia = daily_trivia.read_daily_trivia_row(sheets_ro, args.day, tab=args.tab)
    slug = args.slug or slugify(trivia.question_en, fallback=f"day-{args.day}")

    print(f"=== Day {args.day} → {slug} ===")
    print(f"  Question:    {trivia.question_en}")
    print(f"  Correct:     {trivia.correct_answer_en}")
    print(f"  Explanation: {trivia.correct_explanation_en}")

    # Brief shape matches schemas/artifacts/brief.schema.json (v1.0).
    # The "I just found out…" hook is the canonical opener for this format;
    # key_points are the three beats lifted from the trivia content.
    brief = {
        "version": "1.0",
        "title": f"I just found out — Day {args.day}: {trivia.correct_answer_en}",
        "hook": "So I just found out…",
        "key_points": [
            trivia.question_en,
            trivia.correct_answer_en,
            trivia.correct_explanation_en,
        ],
        "core_message": trivia.correct_explanation_en,
        "tone": "amused, intimate, single-take selfie reaction",
        "style": "trivia-captain",
        "target_platform": "instagram",
        "target_duration_seconds": 15.0,
        "metadata": {
            "sheet_revision": compute_revision_hash(trivia),
            "trivia_captain": {
                "day": args.day,
                "slug": slug,
                "trivia_uid": trivia.uid_trivia,
                "uid_question": trivia.uid_question,
                "uid_correct_answer": trivia.uid_correct_answer,
                "uid_correct_explanation": trivia.uid_correct_explanation,
                "question_en": trivia.question_en,
                "correct_answer_en": trivia.correct_answer_en,
                "correct_explanation_en": trivia.correct_explanation_en,
                "question_image_url": trivia.question_image_url,
                "answer_image_url": trivia.answer_image_url,
                "character_ref": "Captain Archibald",
                "playbook": "trivia-captain",
                "source_sheet_row": trivia.row,
                "source_tab": args.tab,
            },
        },
    }

    if args.dry_run:
        print()
        print(json.dumps(brief, indent=2))
        return 0

    brief_path = write_brief(slug, brief)
    print(f"  brief: {brief_path.relative_to(REPO)}")

    ws = queue_row.build_sheets(write=True)
    qrow = upsert_queue_row(
        ws, args.day, slug,
        trivia.question_en, trivia.correct_answer_en,
        status=queue_row.STATUS_DRAFT,
    )
    print(f"  queue: Queue!row {qrow} → Status={queue_row.STATUS_DRAFT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
