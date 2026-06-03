"""Sync corrected YAML fixtures INTO existing Posts_Quiz rows (update-in-place).

seed_sheet.py is append-only and skips slugs that already exist, so it can't
push edits to rows already on the sheet. This helper diffs each fixture's
question/backdrop fields against the live row and updates only what changed.

Read-only dry-run by default. Pass --apply to write.

Usage:
    .venv/bin/python -m scripts.trivia_quiz.sync_fixture_to_sheet --slug riddles-round-7 ...
    .venv/bin/python -m scripts.trivia_quiz.sync_fixture_to_sheet --all-riddles
    .venv/bin/python -m scripts.trivia_quiz.sync_fixture_to_sheet --all-riddles --apply
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
    build_sheets,
    read_posts_bulk,
    write_post_field,
)

# Only these fields can drift from a content edit; never touch publish state.
SYNC_FIELDS = [
    ("q1_question", ("q1", "question")),
    ("q1_backdrop", ("q1", "backdrop_hint")),
    ("q2_question", ("q2", "question")),
    ("q2_backdrop", ("q2", "backdrop_hint")),
    ("q3_question", ("q3", "question")),
    ("q3_backdrop", ("q3", "backdrop_hint")),
]


def _fixture(slug: str) -> dict:
    p = _REPO / "projects" / "trivia-quiz" / slug / "inputs" / "quiz_row.yaml"
    if not p.exists():
        sys.exit(f"✗ fixture not found: {p}")
    return yaml.safe_load(p.read_text())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", action="append", default=[], help="slug to sync (repeatable)")
    ap.add_argument("--all-riddles", action="store_true", help="all riddles-round-7..20")
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry-run)")
    args = ap.parse_args()

    slugs = list(args.slug)
    if args.all_riddles:
        slugs += [f"riddles-round-{n}" for n in range(7, 21)]
    if not slugs:
        sys.exit("✗ pass --slug or --all-riddles")

    sheets = build_sheets(write=args.apply)
    posts = {p["slug"].strip(): p for p in read_posts_bulk(sheets)}

    total_changes = 0
    missing = []
    for slug in slugs:
        if slug not in posts:
            missing.append(slug)
            print(f"  ⚠ {slug}: NOT in Posts_Quiz (would need seed_sheet append, not update)")
            continue
        live = posts[slug]
        row = _fixture(slug)
        changed = []
        for field, (qid, key) in SYNC_FIELDS:
            new_val = (row.get(qid, {}).get(key) or "").strip()
            old_val = (live.get(field) or "").strip()
            if new_val != old_val:
                changed.append((field, old_val, new_val))
        if not changed:
            print(f"  · {slug}: up to date")
            continue
        print(f"  → {slug}: {len(changed)} field(s) {'UPDATING' if args.apply else 'would update'}")
        for field, old_val, new_val in changed:
            print(f"      {field}:")
            print(f"        - {old_val}")
            print(f"        + {new_val}")
            if args.apply:
                write_post_field(sheets, slug, field, new_val)
            total_changes += 1

    print(f"\n{'APPLIED' if args.apply else 'DRY-RUN'}: {total_changes} field update(s)"
          + (f"; {len(missing)} slug(s) missing from sheet" if missing else ""))
    if not args.apply and total_changes:
        print("Re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
