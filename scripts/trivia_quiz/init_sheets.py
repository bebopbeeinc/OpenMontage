"""One-shot helper to initialize the Questions + Posts_Quiz tabs.

Usage:
    python -m scripts.trivia_quiz.init_sheets

What it does:
    1. Authenticates with the service account at ~/.google/claude-sheets-sa.json
       (override via $OPENMONTAGE_SA_PATH).
    2. Connects to the spreadsheet specified by $TRIVIA_QUIZ_SHEET, or the
       default (the same one trivia-short uses).
    3. Creates a `Questions` tab and a `Posts_Quiz` tab if they don't exist,
       with header rows pre-populated and the header row frozen.
    4. Re-writes the header row even if the tabs exist (idempotent — safe to
       re-run after schema changes).

After running this, the user can start authoring directly in the spreadsheet
and use `--from-sheet --slug X` on the build script.

Idempotent — safe to run repeatedly. The script does not touch data rows.
"""
from __future__ import annotations

import sys

# Auto-load .env so SA_PATH / TRIVIA_QUIZ_SHEET env overrides work
from pathlib import Path
_REPO = Path(__file__).resolve().parents[2]
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(_REPO / ".env")
except ImportError:
    pass

import argparse

from scripts.trivia_quiz.sheets import (
    QUIZ_SHEET_ID,
    POSTS_TAB,
    POST_FIELDS,
    POST_HEADERS,
    build_sheets,
    ensure_tabs_exist,
    _col_letter,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--delete-orphan-questions", action="store_true",
                    help="Delete the legacy Questions tab from the earlier two-tab schema")
    args = ap.parse_args()

    print(f"→ Initializing trivia-quiz sheet")
    print(f"  spreadsheet: {QUIZ_SHEET_ID}")
    print(f"  tab to ensure: {POSTS_TAB!r}")
    print()

    sheets = build_sheets(write=True)
    result = ensure_tabs_exist(sheets, delete_orphan_questions=args.delete_orphan_questions)

    if result["created"]:
        print(f"  ✓ created tab(s): {', '.join(result['created'])}")
    else:
        print(f"  ✓ tab already exists")
    if result.get("deleted_orphan_questions"):
        print(f"  ✓ deleted legacy Questions tab")
    print(f"  ✓ header row written/refreshed")
    print()

    print(f"=== Posts_Quiz tab — column layout ({len(POST_FIELDS)} cols) ===")
    for i, key in enumerate(POST_FIELDS):
        col = _col_letter(i)
        print(f"  {col:>3}: {POST_HEADERS[key]:<35}  → {key}")
    print()
    print("→ Done. Author directly in the sheet and run:")
    print("    python -m scripts.trivia_quiz.build --slug <slug> --from-sheet ...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
