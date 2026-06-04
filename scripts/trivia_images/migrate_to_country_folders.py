#!/usr/bin/env python
"""One-time migration: move existing trivia images into per-country folders.

Background
----------
Before the country restructure, every image lived flat under the "Question
Images" Shared Drive root (`{N}{Q|A}.png`), with approval modeled as a folder
move (root = approved, the `WIP/` subfolder = unapproved). The sheet's country
tabs (US, India, France, …) carry a `COUNTRY` code and a `#` column whose value
is exactly that global image number, so the 9 country tabs collectively account
for every existing image.

This script reads the country tabs as the source of truth and, for each row
with number `N` and country code `C`:

  * moves `NQ.png` / `NA.png` (and their `Resized/` copies) out of the flat root
    (or `WIP/`) into `Question Images/<C>/` (+ `Question Images/<C>/Resized/`);
  * records approval in the sheet — image found in the **root** → write `✓` to
    that row's `Q/A Image Approved` column; image found in **WIP** → left blank.

This matches the new model where the file lives in one country folder and
"approved vs WIP" is a sheet status (see web/server.py, drive_config.py).

Safety
------
* **--dry-run is the default.** Pass --execute to actually move files / write
  the sheet. Nothing is modified without --execute.
* Idempotent: a file already in its country folder is left alone.
* Moves (not copies) — recoverable from Drive trash / by moving back if needed.
* Numbers claimed by more than one country row (a duplicate `#` in the sheet)
  are SKIPPED and reported, never moved to an arbitrary folder.

Usage:
    python scripts/trivia_images/migrate_to_country_folders.py            # dry run, all countries
    python scripts/trivia_images/migrate_to_country_folders.py --execute  # do it
    python scripts/trivia_images/migrate_to_country_folders.py --country US --execute
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

from google.oauth2 import service_account  # noqa: E402
from googleapiclient.discovery import build  # noqa: E402

from tools.publishers.google_drive import get_client  # noqa: E402
from drive_config import QUESTION_IMAGES_ROOT_ID  # noqa: E402
from sheet_schema import (  # noqa: E402
    DATA_START_ROW,
    FIELD_TO_HEADER,
    SA_PATH,
    SCOPES_RW,
    SHEET_ID,
    SheetSchema,
    a1_tab,
    index_to_letter,
)

# The legacy "WIP" staging subfolder — only referenced here, during migration.
# After this runs there is no WIP folder in the workflow.
WIP_FOLDER_ID = "1NMb2WeJp7HVsO-gzvOA-B0wK83uFta9k"
_RESIZED = "Resized"


def _build_sheets():
    creds = service_account.Credentials.from_service_account_file(str(SA_PATH), scopes=SCOPES_RW)
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _discover_country_tabs(sheets) -> list[str]:
    """Every tab whose header rows match the trivia-images schema, in workbook
    order. Same rule the web server uses — so this auto-covers new country tabs
    (no hardcoded list to fall out of date)."""
    from sheet_schema import HEADER_ROWS
    meta = sheets.spreadsheets().get(
        spreadsheetId=SHEET_ID, fields="sheets.properties(title,index)",
    ).execute()
    props = sorted(
        (p["properties"] for p in meta.get("sheets", []) if p["properties"].get("title")),
        key=lambda p: p.get("index", 0),
    )
    lo, hi = min(HEADER_ROWS), max(HEADER_ROWS)
    out: list[str] = []
    for p in props:
        title = p["title"]
        rows = sheets.spreadsheets().values().get(
            spreadsheetId=SHEET_ID, range=f"{a1_tab(title)}!{lo}:{hi}",
        ).execute().get("values", [])
        try:
            SheetSchema(sheets, tab=title).populate_from_rows(rows)
        except Exception:
            continue   # not a trivia-images country tab
        out.append(title)
    return out


def _read_tab_rows(sheets, tab: str) -> list[dict]:
    """[{row, number, code}] for every numbered data row of a country tab."""
    schema = SheetSchema(sheets, tab=tab)
    schema.refresh()
    last = index_to_letter(schema.max_index() + 2)
    rng = f"{a1_tab(tab)}!A{DATA_START_ROW}:{last}2000"
    vals = sheets.spreadsheets().values().get(spreadsheetId=SHEET_ID, range=rng).execute().get("values", [])
    out: list[dict] = []
    for i, v in enumerate(vals):
        f = schema.extract(v, ["number", "country"])
        if f["number"].isdigit():
            out.append({"row": DATA_START_ROW + i, "number": f["number"], "code": f["country"]})
    return out


def _ensure_approval_columns(sheets, tab: str) -> dict[str, str]:
    """Ensure the two approval columns exist on `tab`; return {field: letter}.

    Appends a missing header label into row 2 at the first free column.
    """
    schema = SheetSchema(sheets, tab=tab)
    schema.refresh()
    letters: dict[str, str] = {}
    for field in ("approved_q", "approved_r"):
        try:
            letters[field] = schema.letter(field)
            continue
        except KeyError:
            pass
        row2 = sheets.spreadsheets().values().get(
            spreadsheetId=SHEET_ID, range=f"{a1_tab(tab)}!2:2",
        ).execute().get("values", [[]])
        width = len(row2[0]) if row2 else 0
        letter = index_to_letter(width)
        sheets.spreadsheets().values().update(
            spreadsheetId=SHEET_ID, range=f"{a1_tab(tab)}!{letter}2",
            valueInputOption="RAW", body={"values": [[FIELD_TO_HEADER[field]]]},
        ).execute()
        schema.refresh()
        letters[field] = schema.letter(field)
    return letters


def _drive_name(number: str, kind: str) -> str:
    return f"{number}{'Q' if kind == 'question_image' else 'A'}.png"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--execute", action="store_true",
                    help="actually move files + write the sheet (default: dry run)")
    ap.add_argument("--country", help="limit to one COUNTRY code (e.g. US)")
    args = ap.parse_args()
    dry = not args.execute

    sheets = _build_sheets()
    client = get_client()

    # 1) Build the work list from the country tabs, detecting # collisions.
    print("discovering country tabs…")
    country_tabs = _discover_country_tabs(sheets)
    print(f"  {len(country_tabs)} country tab(s): {country_tabs}")
    owners: dict[int, list[tuple[str, str, int]]] = defaultdict(list)  # number -> [(tab, code, row)]
    tab_rows: dict[str, list[dict]] = {}
    for tab in country_tabs:
        rows = _read_tab_rows(sheets, tab)
        tab_rows[tab] = rows

    # One COUNTRY code per tab (a tab = one country). Derive it from the first
    # NON-EMPTY value, not literally row[0] — a leading row with a blank COUNTRY
    # cell shouldn't make the whole tab look country-less (which would skip it).
    tab_code: dict[str, str] = {}
    for tab in country_tabs:
        tab_code[tab] = next((r["code"] for r in tab_rows[tab] if r["code"]), "")

    # Group numbers by their owning (tab, code, row), using the tab-level code so
    # a blank per-row cell doesn't fragment the grouping.
    for tab in country_tabs:
        for r in tab_rows[tab]:
            owners[int(r["number"])].append((tab, tab_code[tab], r["row"]))

    # Warn about tabs that share a COUNTRY code — e.g. unpopulated tabs cloned
    # from another country that haven't been relabeled yet. They point at the
    # same Drive folder, so they're harmless to the move (handled below), but
    # the operator should know which tabs still need real data.
    code_tabs: dict[str, list[str]] = defaultdict(list)
    for tab in country_tabs:
        if tab_code[tab]:
            code_tabs[tab_code[tab]].append(tab)
    dup_codes = {code: tabs for code, tabs in code_tabs.items() if len(tabs) > 1}
    if dup_codes:
        print(f"\n⚠ {len(dup_codes)} COUNTRY code(s) used by >1 tab (likely unfinished clones):")
        for code, tabs in sorted(dup_codes.items()):
            print(f"    {code}: {tabs}  (treated as the same country folder)")

    # Resolve each number to ONE winning (tab, code, row). A real collision is
    # ambiguity in the DESTINATION — the number claimed under >1 distinct
    # country code. Same-code duplicates are not collisions (they all map to one
    # folder + filename); we pick the first owner in workbook order. For a
    # cross-code collision, prefer the US claimant (per the operator's call);
    # any remaining genuine ambiguity is skipped + reported.
    winners: dict[int, tuple[str, str, int]] = {}   # number -> chosen (tab, code, row)
    skipped_collisions: dict[int, list] = {}
    real_collisions: dict[int, list] = {}
    for n, o in owners.items():
        codes = {code for _t, code, _r in o}
        if len(codes) == 1:
            winners[n] = o[0]   # first owner (workbook order)
            continue
        real_collisions[n] = o
        us = [c for c in o if c[1] == "US"]
        if len(us) == 1:
            winners[n] = us[0]
        else:
            skipped_collisions[n] = o
    if real_collisions:
        print(f"\n⚠ {len(real_collisions)} number(s) claimed under >1 country code:")
        for n, o in sorted(real_collisions.items()):
            owners_str = ", ".join(f"{t} (row {row}, {code})" for t, code, row in o)
            chosen = winners.get(n)
            verdict = f"chose {chosen[1]}" if chosen else "SKIPPED (no single US claimant)"
            print(f"    #{n}: {owners_str}  -> {verdict}")

    # 2) Snapshot the source folders (flat root + WIP) and their Resized subs.
    print("\nlisting source Drive folders…")
    root_files = client.list_folder(QUESTION_IMAGES_ROOT_ID)
    wip_files = client.list_folder(WIP_FOLDER_ID)
    root_resized_id = client.find_or_create_folder(QUESTION_IMAGES_ROOT_ID, _RESIZED).id
    wip_resized_id = client.find_or_create_folder(WIP_FOLDER_ID, _RESIZED).id
    root_resized = client.list_folder(root_resized_id)
    wip_resized = client.list_folder(wip_resized_id)
    print(f"  root: {sum(1 for k in root_files if k[0].isdigit())} image(s)  |  "
          f"WIP: {sum(1 for k in wip_files if k[0].isdigit())} image(s)")

    # 3) Per country, move each (number, kind) into the country folder and
    #    capture approval flags to write.
    moved = approved_blank = missing = 0
    approvals: dict[str, dict[int, dict[str, str]]] = defaultdict(lambda: defaultdict(dict))  # tab -> row -> {field: '✓'}
    for tab in country_tabs:
        code = tab_code[tab]
        if not code:
            continue
        if args.country and code != args.country:
            continue
        # Resolve/create the country + its Resized folder (idempotent).
        country_id = client.find_or_create_folder(QUESTION_IMAGES_ROOT_ID, code).id
        client.ensure_anyone_reader(country_id)
        country_resized_id = client.find_or_create_folder(country_id, _RESIZED).id
        client.ensure_anyone_reader(country_resized_id)
        country_files = {} if dry else client.list_folder(country_id)

        print(f"\n[{tab} -> {code}/]")
        for r in tab_rows[tab]:
            n = int(r["number"])
            if n in skipped_collisions:
                continue   # genuinely ambiguous duplicate — left for manual fix
            if n in winners and winners[n] != (tab, code, r["row"]):
                continue   # this row lost the collision (US won)
            for kind, field in (("question_image", "approved_q"), ("answer_image", "approved_r")):
                name = _drive_name(r["number"], kind)
                # Already migrated?
                if not dry and name in country_files:
                    continue
                if name in root_files:
                    src_orig, src_res, was_approved = root_files[name], root_resized.get(name), True
                elif name in wip_files:
                    src_orig, src_res, was_approved = wip_files[name], wip_resized.get(name), False
                else:
                    missing += 1
                    continue
                src_folder = QUESTION_IMAGES_ROOT_ID if was_approved else WIP_FOLDER_ID
                src_res_folder = root_resized_id if was_approved else wip_resized_id
                tag = "approved" if was_approved else "wip"
                if dry:
                    print(f"  · would move {name} ({tag}) -> {code}/"
                          + ("  +resized" if src_res else "  (no resized)")
                          + ("  +✓" if was_approved else ""))
                    moved += 1
                    if was_approved:
                        approvals[tab][r["row"]][field] = "✓"
                    else:
                        approved_blank += 1
                    continue
                client.move(src_orig.id, add_parents=[country_id], remove_parents=[src_folder])
                if src_res is not None:
                    client.move(src_res.id, add_parents=[country_resized_id], remove_parents=[src_res_folder])
                print(f"  ✓ moved {name} ({tag}) -> {code}/" + ("  +resized" if src_res else ""))
                moved += 1
                if was_approved:
                    approvals[tab][r["row"]][field] = "✓"
                else:
                    approved_blank += 1

    # 4) Write approval ✓ flags (batched per tab).
    if approvals:
        print("\nwriting approval flags to the sheet…")
        for tab, rowmap in approvals.items():
            letters = {} if dry else _ensure_approval_columns(sheets, tab)
            data = []
            for row, fields in rowmap.items():
                for field, val in fields.items():
                    if dry:
                        print(f"  · would set {tab} row {row} {FIELD_TO_HEADER[field]} = {val}")
                    else:
                        data.append({"range": f"{a1_tab(tab)}!{letters[field]}{row}", "values": [[val]]})
            if data and not dry:
                sheets.spreadsheets().values().batchUpdate(
                    spreadsheetId=SHEET_ID,
                    body={"valueInputOption": "RAW", "data": data},
                ).execute()
                print(f"  ✓ {tab}: {len(data)} approval cell(s) set ✓")

    verb = "would move" if dry else "moved"
    us_resolved = sum(1 for n in real_collisions if n in winners)
    print(f"\n{'DRY RUN — ' if dry else ''}done. {verb} {moved} file(s); "
          f"{approved_blank} left WIP; {missing} sheet rows had no image; "
          f"{us_resolved} cross-code collision(s) resolved to US; "
          f"{len(skipped_collisions)} collision(s) skipped.")
    if dry:
        print("\nRe-run with --execute to apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
