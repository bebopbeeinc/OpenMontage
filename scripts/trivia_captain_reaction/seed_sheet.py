"""Add + populate the Posts_Reaction tab on the dailytrivia.tc Post Calendar.

The Post Calendar spreadsheet already exists and is shared with the service
account (it hosts Posts / Posts_Quiz / Posts_2T1L). This script ADDS a new
`Posts_Reaction` tab (never renames existing tabs), then lays out row 1
(banner), row 2 (header from queue_row.HEADER_LABELS), bold/frozen header,
and column widths.

Setup:
  1. Confirm the SA (claude-sheets-config@travel-crush.iam.gserviceaccount.com)
     has Editor on the Post Calendar.
  2. Run:  python scripts/trivia_captain_reaction/seed_sheet.py --sheet-id 1EzucrS6yUPfodtt7WVuvW3PjZ1yhWUgfWUowPkMP6Eg
     (the --sheet-id default already points at the Post Calendar; queue_row.QUEUE_SHEET
      is the source of truth.)

The Drive renders folder is the existing dailytrivia.tc folder
(publish.DRIVE_FOLDER_ID) — no new folder needed.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.trivia_captain_reaction import queue_row  # noqa: E402

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
BANNER = (
    "I Just Found Out — Captain Archibald reaction reels (dailytrivia.tc). "
    "ellie.travelcrush's proven reaction format, Captain character A/B test. "
    "Workflow-state SoT; trivia content resolved from DailyTriviaConfig."
)


def _sheets():
    creds = service_account.Credentials.from_service_account_file(
        str(queue_row.SA_PATH), scopes=SCOPES,
    )
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet-id", default=queue_row.QUEUE_SHEET,
                    help="Post Calendar spreadsheet ID (default: queue_row.QUEUE_SHEET)")
    args = ap.parse_args()

    sheets = _sheets()
    sheet_id = args.sheet_id
    ncols = queue_row.QUEUE_ROW_COLUMN_COUNT

    # Resolve the Posts_Reaction tab id; ADD it if missing (never rename existing
    # tabs — this lives alongside Posts / Posts_Quiz / Posts_2T1L on the shared
    # account sheet).
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
    else:
        print(f"  · tab {queue_row.QUEUE_TAB!r} already present (id={tab_id})")

    # Banner (row 1) + header (row 2) values.
    sheets.spreadsheets().values().batchUpdate(
        spreadsheetId=sheet_id,
        body={"valueInputOption": "RAW", "data": [
            {"range": f"'{queue_row.QUEUE_TAB}'!A1", "values": [[BANNER]]},
            {"range": f"'{queue_row.QUEUE_TAB}'!A2", "values": [queue_row.HEADER_LABELS]},
        ]},
    ).execute()

    # Formatting: merge banner, bold/format both top rows, widen text columns.
    wide = {"Question (EN)", "Correct Answer (EN)", "Hook VO", "Fact VO",
            "Kicker VO", "OpenArt Prompt", "Caption", "Drive Link", "Drive Clip"}
    reqs = [
        {"updateSheetProperties": {"properties": {"sheetId": tab_id,
            "gridProperties": {"frozenRowCount": 2}}, "fields": "gridProperties.frozenRowCount"}},
        {"mergeCells": {"range": {"sheetId": tab_id, "startRowIndex": 0, "endRowIndex": 1,
                                  "startColumnIndex": 0, "endColumnIndex": ncols}, "mergeType": "MERGE_ALL"}},
        {"repeatCell": {
            "range": {"sheetId": tab_id, "startRowIndex": 0, "endRowIndex": 1},
            "cell": {"userEnteredFormat": {
                "textFormat": {"bold": True, "fontSize": 11},
                "backgroundColor": {"red": 0.44, "green": 0.31, "blue": 0.88}}},
            "fields": "userEnteredFormat(textFormat,backgroundColor)"}},
        {"repeatCell": {
            "range": {"sheetId": tab_id, "startRowIndex": 0, "endRowIndex": 1},
            "cell": {"userEnteredFormat": {"textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1}}}},
            "fields": "userEnteredFormat.textFormat.foregroundColor"}},
        {"repeatCell": {
            "range": {"sheetId": tab_id, "startRowIndex": 1, "endRowIndex": 2},
            "cell": {"userEnteredFormat": {
                "textFormat": {"bold": True},
                "backgroundColor": {"red": 0.93, "green": 0.9, "blue": 0.98}}},
            "fields": "userEnteredFormat(textFormat,backgroundColor)"}},
    ]
    for i, key in enumerate(queue_row.ROW_KEYS):
        label = queue_row.FIELD_TO_HEADER[key]
        reqs.append({"updateDimensionProperties": {
            "range": {"sheetId": tab_id, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1},
            "properties": {"pixelSize": 320 if label in wide else 110},
            "fields": "pixelSize"}})
    sheets.spreadsheets().batchUpdate(spreadsheetId=sheet_id, body={"requests": reqs}).execute()

    print("✓ populated Posts_Reaction tab (banner + header + formatting)")
    print(f"  QUEUE_SHEET = {sheet_id!r}")
    print(f"  url         = https://docs.google.com/spreadsheets/d/{sheet_id}/edit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
