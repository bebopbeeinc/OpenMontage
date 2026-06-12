"""Author a curated 2-truths-1-lie row into the Queue (the 'idea' stage).

Content is curated by hand (no DailyTriviaConfig). This appends a Draft row with
the destination, the three claims, which one is the lie (tracking only), the
overlay labels, the kicker demographic, and the overlay theme — and writes a
brief.json artifact.

Usage:
    python scripts/trivia_captain_2t1l/add_row.py \
        --slug bahamas-two-truths --place "The Bahamas" \
        --claim1 "Wild pigs swim out to your boat" \
        --claim2 "A blue hole drops over 600 feet" \
        --claim3 "One island has naturally blue sand" \
        --lie 3 --lie-model invented \
        --label1 "Swimming pigs" --label2 "600ft blue hole" --label3 "Blue sand" \
        --demographic "most men"
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.trivia_captain_2t1l import paths, queue_row  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True)
    ap.add_argument("--place", required=True)
    ap.add_argument("--claim1", required=True)
    ap.add_argument("--claim2", required=True)
    ap.add_argument("--claim3", required=True)
    ap.add_argument("--lie", required=True, choices=["1", "2", "3"],
                    help="which claim is the lie (tracking only — never rendered)")
    ap.add_argument("--lie-model", default="invented", choices=["myth", "invented"])
    ap.add_argument("--label1", default="")
    ap.add_argument("--label2", default="")
    ap.add_argument("--label3", default="")
    ap.add_argument("--demographic", default="most men")
    ap.add_argument("--kicker", default="",
                    help="full spoken taunt + CTA line (free-form; default composed from --demographic at script stage)")
    ap.add_argument("--theme", default="goldround")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    pdir = paths.project_dir(args.slug)
    brief = {
        "version": "1.0",
        "title": f"2 Truths, 1 Lie — {args.place}",
        "style": "trivia-captain-2t1l",
        "target_platform": "instagram",
        "target_duration_seconds": 15.0,
        "metadata": {
            "slug": args.slug,
            "place": args.place,
            "claims": [args.claim1, args.claim2, args.claim3],
            "lie_index": int(args.lie),
            "lie_model": args.lie_model,
            "labels": [args.label1, args.label2, args.label3],
            "demographic": args.demographic,
            "kicker": args.kicker,
            "theme": args.theme,
            "character_ref": "Captain Archibald",
            "playbook": "trivia-captain-2t1l",
        },
    }
    if args.dry_run:
        print(json.dumps(brief, indent=2))
        return 0

    (pdir / "artifacts").mkdir(parents=True, exist_ok=True)
    (pdir / "artifacts" / "brief.json").write_text(json.dumps(brief, indent=2))

    sheets = queue_row.build_sheets(write=True)
    existing = queue_row.find_row_by_slug(sheets, args.slug)
    values = [
        str(queue_row.next_index(sheets)), args.slug, queue_row.STATUS_DRAFT,
        args.place, args.claim1, args.claim2, args.claim3,
        args.lie, args.lie_model, args.label1, args.label2, args.label3,
        args.demographic, "", "", "", "", args.theme, args.kicker,
    ]
    if existing:
        # Re-add: update only the AUTHORED content cells in place — never touch
        # idx/status or the pipeline-output cells (prompt/caption/drive links),
        # so re-running on an existing slug doesn't wipe generated state.
        queue_row.update_cells(
            sheets, existing,
            place=args.place, claim_1=args.claim1, claim_2=args.claim2, claim_3=args.claim3,
            lie_index=args.lie, lie_model=args.lie_model,
            label_1=args.label1, label_2=args.label2, label_3=args.label3,
            demographic=args.demographic, theme=args.theme, kicker=args.kicker,
        )
        row = existing
    else:
        row = queue_row.append_row(sheets, values)
    print(f"✓ queued row {row}: {args.slug} ({args.place}) — Draft")
    print(f"  brief: {pdir / 'artifacts' / 'brief.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
