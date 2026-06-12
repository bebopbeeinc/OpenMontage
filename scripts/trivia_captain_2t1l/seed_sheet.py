"""Populate an EXISTING TriviaCaptain2T1LQueue spreadsheet (banner + header + format).

The service account cannot create Drive files (no storage quota), so the sheet
must be created by a human and shared with the service account
(claude-sheets-config@travel-crush.iam.gserviceaccount.com) as Editor first.
Then this script lays out row 1 (banner), row 2 (header from
queue_row.HEADER_LABELS), bold/frozen header, and column widths.

Setup:
  1. Create a blank Google Sheet; rename a tab to "Queue".
  2. Share it (Editor) with the service account email.
  3. Run:  python scripts/trivia_captain_2t1l/seed_sheet.py --sheet-id <ID>
  4. Paste the ID into queue_row.QUEUE_SHEET.
  Also create a Drive folder for renders, share it with the SA, and paste its
  ID into publish.DRIVE_FOLDER_ID.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.trivia_captain_2t1l import queue_row  # noqa: E402

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
BANNER = "Captain's Two Truths & a Lie — production queue (curated 2T1L sets; content authored here)"


def _sheets():
    creds = service_account.Credentials.from_service_account_file(
        str(queue_row.SA_PATH), scopes=SCOPES,
    )
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet-id", required=True,
                    help="existing spreadsheet ID (created by a human, shared with the SA as Editor)")
    args = ap.parse_args()

    sheets = _sheets()
    sheet_id = args.sheet_id
    ncols = queue_row.QUEUE_ROW_COLUMN_COUNT

    # Resolve the Queue tab id; ADD it if missing (never rename existing tabs —
    # this lives alongside Posts / Posts_Quiz on a shared account sheet).
    meta = sheets.spreadsheets().get(spreadsheetId=sheet_id).execute()
    tab_id = None
    for s in meta.get("sheets", []):
        if s["properties"]["title"] == queue_row.QUEUE_TAB:
            tab_id = s["properties"]["sheetId"]
            break
    if tab_id is None:
        resp = sheets.spreadsheets().batchUpdate(spreadsheetId=sheet_id, body={"requests": [
            {"addSheet": {"properties": {"title": queue_row.QUEUE_TAB,
                                         "gridProperties": {"frozenRowCount": 2, "columnCount": ncols}}}}]}).execute()
        tab_id = resp["replies"][0]["addSheet"]["properties"]["sheetId"]
        print(f"  + added tab {queue_row.QUEUE_TAB!r}")

    # Banner (row 1) + header (row 2) values.
    sheets.spreadsheets().values().batchUpdate(
        spreadsheetId=sheet_id,
        body={"valueInputOption": "RAW", "data": [
            {"range": f"'{queue_row.QUEUE_TAB}'!A1", "values": [[BANNER]]},
            {"range": f"'{queue_row.QUEUE_TAB}'!A2", "values": [queue_row.HEADER_LABELS]},
        ]},
    ).execute()

    # 3) Formatting: merge banner, bold/format both top rows, widen text columns.
    wide = {"Place", "Claim 1", "Claim 2", "Claim 3", "OpenArt Prompt", "Caption"}
    reqs = [
        {"updateSheetProperties": {"properties": {"sheetId": tab_id,
            "gridProperties": {"frozenRowCount": 2}}, "fields": "gridProperties.frozenRowCount"}},
        {"mergeCells": {"range": {"sheetId": tab_id, "startRowIndex": 0, "endRowIndex": 1,
                                  "startColumnIndex": 0, "endColumnIndex": ncols}, "mergeType": "MERGE_ALL"}},
        {"repeatCell": {
            "range": {"sheetId": tab_id, "startRowIndex": 0, "endRowIndex": 1},
            "cell": {"userEnteredFormat": {
                "textFormat": {"bold": True, "fontSize": 11},
                "backgroundColor": {"red": 0.07, "green": 0.16, "blue": 0.36}}},
            "fields": "userEnteredFormat(textFormat,backgroundColor)"}},
        {"repeatCell": {
            "range": {"sheetId": tab_id, "startRowIndex": 0, "endRowIndex": 1},
            "cell": {"userEnteredFormat": {"textFormat": {"foregroundColor": {"red": 1, "green": 0.88, "blue": 0.55}}}},
            "fields": "userEnteredFormat.textFormat.foregroundColor"}},
        {"repeatCell": {
            "range": {"sheetId": tab_id, "startRowIndex": 1, "endRowIndex": 2},
            "cell": {"userEnteredFormat": {
                "textFormat": {"bold": True},
                "backgroundColor": {"red": 0.9, "green": 0.93, "blue": 0.98}}},
            "fields": "userEnteredFormat(textFormat,backgroundColor)"}},
    ]
    for i, key in enumerate(queue_row.ROW_KEYS):
        label = queue_row.FIELD_TO_HEADER[key]
        reqs.append({"updateDimensionProperties": {
            "range": {"sheetId": tab_id, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1},
            "properties": {"pixelSize": 320 if label in wide else 120},
            "fields": "pixelSize"}})
    sheets.spreadsheets().batchUpdate(spreadsheetId=sheet_id, body={"requests": reqs}).execute()

    print("✓ populated Queue tab (banner + header + formatting)")
    print(f"  QUEUE_SHEET = {sheet_id!r}")
    print(f"  url         = https://docs.google.com/spreadsheets/d/{sheet_id}/edit")
    print("\nPaste QUEUE_SHEET into queue_row.py. Create + share a Drive renders folder")
    print("for publish.DRIVE_FOLDER_ID separately (the SA cannot create Drive files).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
