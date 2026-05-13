#!/usr/bin/env python
"""Generate question images from the Trivia Spreadsheet (Brian tab).

Reads each row from the `Brian` tab of the trivia-questions sheet, uses the
prompt in column Q to drive OpenArt's Nano Banana Pro image generator, saves
the resulting image to a local library, and marks column D = ✓.

Sheet layout (Brian tab):
    row 1: section-header labels (decorative)
    row 2: column names (Number, Question text, ..., Question IMAGE, ...)
    row 3+: data rows

    col C  Number                  (used as the image slug: q{number}.<ext>)
    col D  image complete          (we write "✓" on success)
    col Q  Question IMAGE          (the prompt for OpenArt — index 16)
    col R  Answer IMAGE (CORRECT)  (a sibling prompt — not generated here)

Usage:
    # Single row (smoke test)
    python scripts/trivia_images/generate.py --row 3

    # Range of rows
    python scripts/trivia_images/generate.py --rows 3-20

    # All remaining (col Q filled, col D empty)
    python scripts/trivia_images/generate.py --all

    # Force regenerate even if the local file exists / D is ✓
    python scripts/trivia_images/generate.py --row 3 --force

    # Skip writing back to the sheet (dry-run-ish)
    python scripts/trivia_images/generate.py --row 3 --no-mark
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build

REPO = Path(__file__).resolve().parents[2]

# Route generation through the registered tool so this CLI benefits from any
# future tool-level cost tracking / retry / telemetry. The tool itself wraps
# scripts/trivia_images/openart_image_driver.generate_image.
sys.path.insert(0, str(REPO))
from tools.tool_registry import registry  # noqa: E402

registry.discover()
_openart_image = registry._tools["openart_image"]
SA_PATH = Path.home() / ".google" / "claude-sheets-sa.json"
SHEET_ID = "1Kh9Ai9-sKyyK1q24jVkQqeIz-Y-0rdNVIjPc2EF8hPk"
SHEET_TAB = "Brian"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Pipeline-local image library. Gitignored under scripts/trivia_images/library/
# alongside the code that produces it.
LIBRARY_DIR = Path(__file__).resolve().parent / "library"

MODEL = "Nano Banana Pro"
ASPECT = "4:3"          # matches the "4:3 aspect ratio" trailer in every prompt
RESOLUTION = "2K"       # 1K / 2K / 4K available; 2K is the quality/cost sweet spot

# Column indices (0-based) in the Brian tab
COL_NUMBER = 2          # C
COL_COMPLETE = 3        # D
COL_PROMPT = 16         # Q
DATA_START_ROW = 3      # rows 1-2 are headers


def _build_sheets():
    creds = service_account.Credentials.from_service_account_file(str(SA_PATH), scopes=SCOPES)
    return build("sheets", "v4", credentials=creds)


def _read_range(sheets, a1: str) -> list[list[str]]:
    r = sheets.spreadsheets().values().get(spreadsheetId=SHEET_ID, range=a1).execute()
    return r.get("values", [])


def _row_fields(row: list[str]) -> dict[str, str]:
    """Pad short rows and extract the cells we care about."""
    padded = row + [""] * (max(0, COL_PROMPT + 1 - len(row)))
    return {
        "number": (padded[COL_NUMBER] or "").strip(),
        "complete": (padded[COL_COMPLETE] or "").strip(),
        "prompt": (padded[COL_PROMPT] or "").strip(),
    }


def _resolve_rows(sheets, args) -> list[int]:
    """Decide which 1-based sheet rows to process based on CLI args."""
    if args.row:
        return [args.row]
    if args.rows:
        lo, hi = args.rows.split("-", 1)
        return list(range(int(lo), int(hi) + 1))
    if args.all:
        # Pull every data row so we can filter by "Q filled, D empty"
        values = _read_range(sheets, f"{SHEET_TAB}!A{DATA_START_ROW}:Q1000")
        rows = []
        for i, v in enumerate(values):
            row_num = DATA_START_ROW + i
            f = _row_fields(v)
            if f["number"] and f["prompt"] and not f["complete"]:
                rows.append(row_num)
        return rows
    sys.exit("specify one of --row N | --rows N-M | --all")


def _mark_complete(sheets, row: int) -> None:
    """Write '✓' into column D of the given row."""
    sheets.spreadsheets().values().update(
        spreadsheetId=SHEET_ID,
        range=f"{SHEET_TAB}!D{row}",
        valueInputOption="USER_ENTERED",
        body={"values": [["✓"]]},
    ).execute()


def _output_path(number: str, variants: int) -> list[Path]:
    """Build output paths for a question. variants=1 → no suffix."""
    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    base = LIBRARY_DIR / f"q{number}.jpg"      # extension may be rewritten by the driver
    if variants == 1:
        return [base]
    stem, suf = base.stem, base.suffix
    return [base.with_name(f"{stem}_v{i+1}{suf}") for i in range(variants)]


def _existing_variants(number: str) -> list[Path]:
    """Find any already-saved images for this question, any extension."""
    if not LIBRARY_DIR.exists():
        return []
    found: list[Path] = []
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        found.extend(LIBRARY_DIR.glob(f"q{number}{ext}"))
        found.extend(LIBRARY_DIR.glob(f"q{number}_v*{ext}"))
    return found


def main() -> int:
    ap = argparse.ArgumentParser()
    grp = ap.add_mutually_exclusive_group(required=False)
    grp.add_argument("--row", type=int, help="single 1-based sheet row")
    grp.add_argument("--rows", help="inclusive range, e.g. '3-20'")
    grp.add_argument("--all", action="store_true",
                     help="every row where Q is filled and D is empty")
    ap.add_argument("--variants", type=int, default=1,
                    help="variants per row (default 1)")
    ap.add_argument("--force", action="store_true",
                    help="regenerate even if a local file exists or D=✓")
    ap.add_argument("--no-mark", action="store_true",
                    help="skip writing ✓ back to column D after success")
    ap.add_argument("--headless", action="store_true",
                    help="run the browser headless (after first login)")
    ap.add_argument("--sleep-between", type=float, default=1.5,
                    help="seconds to wait between rows (default 1.5)")
    args = ap.parse_args()

    if args.variants < 1:
        sys.exit("--variants must be ≥ 1")

    sheets = _build_sheets()
    rows = _resolve_rows(sheets, args)
    if not rows:
        print("no rows to process.")
        return 0

    # Single fetch to get the fields for every target row
    range_a1 = f"{SHEET_TAB}!A{min(rows)}:Q{max(rows)}"
    block = _read_range(sheets, range_a1)
    row_to_fields: dict[int, dict[str, str]] = {}
    for offset, v in enumerate(block):
        row_to_fields[min(rows) + offset] = _row_fields(v)

    print(f"Brian tab — {len(rows)} target row(s):")
    plan: list[tuple[int, dict[str, str], list[Path]]] = []
    for r in rows:
        f = row_to_fields.get(r, {"number": "", "complete": "", "prompt": ""})
        if not f["number"]:
            print(f"  row {r}: skip (no number)")
            continue
        if not f["prompt"]:
            print(f"  row {r} (#{f['number']}): skip (no Q prompt)")
            continue
        existing = _existing_variants(f["number"])
        if (existing or f["complete"] == "✓") and not args.force:
            why = []
            if existing:
                why.append(f"{len(existing)} file(s) exist: {', '.join(p.name for p in existing)}")
            if f["complete"] == "✓":
                why.append("D=✓")
            print(f"  row {r} (#{f['number']}): skip ({'; '.join(why)})")
            continue
        paths = _output_path(f["number"], args.variants)
        plan.append((r, f, paths))
        print(f"  row {r} (#{f['number']}): generate → {', '.join(p.name for p in paths)}")

    if not plan:
        print("nothing to generate.")
        return 0

    print(f"\nproceeding with {len(plan)} row(s) (model={MODEL}, aspect={ASPECT}, res={RESOLUTION})\n")
    headless = args.headless
    failures = 0
    for r, f, paths in plan:
        print(f"=== row {r} (q{f['number']}) ===")
        prompt_preview = f["prompt"][:120].replace("\n", " ")
        print(f"  prompt: {prompt_preview}…")
        # Variants -> output_paths; pass a single output_path when n==1 so the
        # tool's path-resolution matches the script's existing convention
        # (`q{N}.<ext>` rather than `q{N}_v1.<ext>`).
        tool_inputs = {
            "prompt": f["prompt"],
            "model": MODEL,
            "aspect": ASPECT,
            "resolution": RESOLUTION,
            "headless": headless,
        }
        if len(paths) == 1:
            tool_inputs["output_path"] = str(paths[0])
        else:
            tool_inputs["output_paths"] = [str(p) for p in paths]
        result = _openart_image.execute(tool_inputs)
        if not result.success:
            failures += 1
            print(f"  ✗ row {r}: {result.error}", file=sys.stderr)
        else:
            for s in result.data.get("saved_paths", []):
                print(f"  ✓ {s}")
            if not args.no_mark:
                _mark_complete(sheets, r)
                print(f"  ✓ marked D{r} = ✓")
        if args.sleep_between and r != plan[-1][0]:
            time.sleep(args.sleep_between)

    print(f"\ndone. {len(plan) - failures}/{len(plan)} succeeded.")
    return 1 if failures and len(plan) == failures else 0


if __name__ == "__main__":
    sys.exit(main())
