#!/usr/bin/env python
"""Backfill: move mis-filed images into each row's OWN country folder.

Background
----------
Before the per-row country fix, the web UI never sent a per-row COUNTRY on
generate, so `/api/run` wrote EVERY image in a tab to that tab's *default*
country folder — `_tab_country_code(tab)`, i.e. the first data row's code. On a
single-country tab that's correct. On a MIXED-country tab (e.g. "Generated
Questions", whose rows span IE/PT/ZA/US/…), every image landed in the first
row's country folder instead of the row's own, so the row rendered "none" even
though generation succeeded.

The display/S3 paths were always row-aware (they resolve the folder from the
row's own COUNTRY), so the fix was to make the write/serve paths agree. This
script repairs the images that were already mis-filed: for each row whose
COUNTRY differs from its tab's default, it moves

    <default>/<N><Q|A>.png            -> <rowCountry>/<N><Q|A>.png
    <default>/Resized/<N><Q|A>.png    -> <rowCountry>/Resized/<N><Q|A>.png

Safety
------
* **Dry run is the default.** Pass --execute to actually move files.
* Moves (not copies) via Drive parent reassignment — recoverable by moving back.
* NEVER overwrites: if the destination already has that file, the move is
  SKIPPED and reported (nothing is clobbered).
* A source file that doesn't exist is simply skipped (not every row has an image).
* Only rows where COUNTRY != the tab default are touched; single-country tabs
  are no-ops.

Usage:
    python scripts/trivia_images/backfill_row_country_folders.py                 # dry run, all tabs
    python scripts/trivia_images/backfill_row_country_folders.py --execute       # do it
    python scripts/trivia_images/backfill_row_country_folders.py --tab "Generated Questions" --execute
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PKG_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(PKG_DIR))
sys.path.insert(0, str(PKG_DIR / "web"))

import server as S  # noqa: E402  (web/server.py — trivia-images helpers)

KINDS = (("question_image", "Q"), ("answer_image", "A"))


def _plan_moves(tab: str) -> tuple[str, list[dict]]:
    """Return (default_code, [move-plan]) for one tab. Read-only.

    Each plan entry: {number, code (dest), kind, tier ('orig'|'resized'),
    file_id, src_folder, dst_folder, dest_exists (bool)}.
    """
    default_code = S._tab_country_code(tab)
    rows = S.read_rows(tab=tab)
    plans: list[dict] = []
    for r in rows:
        dest = (r.get("country") or "").strip()
        if not dest or dest == default_code:
            continue  # already in the right (default) folder
        for kind, _short in KINDS:
            name = S.drive_name(r["number"], kind)
            # Original (country folder).
            src = S.find_original(default_code, name)
            if src is not None:
                dst = S.find_original(dest, name)
                plans.append({
                    "number": r["number"], "code": dest, "kind": kind, "tier": "orig",
                    "name": name, "file_id": src.id,
                    "src_folder": S.country_folder_id(default_code),
                    "dst_folder": S.country_folder_id(dest),
                    "dest_exists": dst is not None,
                })
            # Resized copy (Resized subfolder).
            rsrc = S.find_resized(default_code, name)
            if rsrc is not None:
                rdst = S.find_resized(dest, name)
                plans.append({
                    "number": r["number"], "code": dest, "kind": kind, "tier": "resized",
                    "name": name, "file_id": rsrc.id,
                    "src_folder": S.country_resized_folder_id(default_code),
                    "dst_folder": S.country_resized_folder_id(dest),
                    "dest_exists": rdst is not None,
                })
    return default_code, plans


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--execute", action="store_true",
                    help="actually move files (default: dry run)")
    ap.add_argument("--tab", help="limit to one tab name (default: every mixed-country tab)")
    args = ap.parse_args()
    dry = not args.execute

    client = S.get_client()
    tabs = [args.tab] if args.tab else [t["name"] for t in S._discover_tabs()]

    grand = {"move": 0, "skip_dest": 0}
    per_country: dict[str, int] = defaultdict(int)

    for tab in tabs:
        try:
            default_code, plans = _plan_moves(tab)
        except Exception as e:
            print(f"== {tab}: read failed ({e}) — skip")
            continue
        if not plans:
            continue
        movable = [p for p in plans if not p["dest_exists"]]
        conflicts = [p for p in plans if p["dest_exists"]]
        print(f"\n== {tab}: default folder = {default_code}")
        print(f"   {len(movable)} file(s) to move, {len(conflicts)} skipped (dest exists)")
        for p in conflicts:
            print(f"   ⚠ SKIP {default_code}/{p['name']} ({p['tier']}): "
                  f"{p['code']}/{p['name']} already exists — not overwriting")

        for p in movable:
            arrow = f"{default_code}/{p['name']} -> {p['code']}/{p['name']} ({p['tier']})"
            if dry:
                print(f"   would move  {arrow}")
            else:
                client.move(p["file_id"],
                            add_parents=[p["dst_folder"]],
                            remove_parents=[p["src_folder"]])
                print(f"   moved       {arrow}")
            grand["move"] += 1
            per_country[p["code"]] += 1
        grand["skip_dest"] += len(conflicts)

    verb = "would move" if dry else "moved"
    print(f"\n{'DRY RUN — nothing changed. ' if dry else ''}"
          f"{verb} {grand['move']} file(s); skipped {grand['skip_dest']} (dest exists).")
    if per_country:
        print("  by destination country: "
              + ", ".join(f"{c}:{n}" for c, n in sorted(per_country.items())))
    if dry and grand["move"]:
        print("  re-run with --execute to apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
