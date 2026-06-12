"""TriviaCaptain2T1LQueue schema, reader, and writer (workflow-state SoT).

Sister of scripts/trivia_captain/queue_row.py. Same auth/helper contract, but a
2-truths-1-lie content model: the row carries the destination, the three claims,
which one is the lie (tracking only — never rendered), the per-row overlay labels,
the kicker demographic, the assembled Seedance prompt, the caption, drive links,
and the overlay theme.

Content is CURATED directly in this sheet (no DailyTriviaConfig dependency).

Layout: row 1 = banner, row 2 = header, data starts at row 3.
"""
from __future__ import annotations

import os
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build

SA_PATH = Path(os.environ.get(
    "OPENMONTAGE_SA_PATH",
    str(Path.home() / ".google" / "claude-sheets-sa.json"),
))

# Pinned IDs. A dedicated tab on the @dailytrivia.tc "Post Calendar" sheet
# (alongside Posts / Posts_Quiz), shared with the service account.
QUEUE_SHEET = "1EzucrS6yUPfodtt7WVuvW3PjZ1yhWUgfWUowPkMP6Eg"
QUEUE_TAB = "Posts_2T1L"
QUEUE_HEADER_ROW = 2
QUEUE_DATA_START_ROW = 3

READ_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
READWRITE_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# ROW_KEYS is the ordered tuple of Python field names, in left-to-right column
# order. read_queue_row zips this against the row's values in source-order.
ROW_KEYS: tuple[str, ...] = (
    "idx",            # A — free running index (sheet header "#")
    "slug",           # B — kebab-case identifier, used as project dir name
    "status",         # C — Draft / Ready to review / Ready to publish / Published
    "place",          # D — destination, e.g. "The Bahamas" (rendered on the place banner)
    "claim_1",        # E — full text of claim 1 (spoken; human review)
    "claim_2",        # F — full text of claim 2
    "claim_3",        # G — full text of claim 3
    "lie_index",      # H — which claim is the lie: 1 / 2 / 3 (TRACKING ONLY — never rendered)
    "lie_model",      # I — myth / invented
    "label_1",        # J — 2-3 word overlay label for claim 1 (e.g. "Swimming pigs")
    "label_2",        # K — overlay label for claim 2
    "label_3",        # L — overlay label for claim 3
    "demographic",    # M — kicker taunt group, e.g. "most men" / "Americans"
    "openart_prompt", # N — assembled Seedance prompt (script-director authors)
    "caption",        # O — IG-ready post description + hashtags
    "drive_link",     # P — final rendered mp4 webViewLink (publish writes)
    "drive_clip_link",# Q — raw Seedance clip webViewLink (secondary deliverable)
    "theme",          # R — overlay theme name (goldround / neon / candy / gold). default goldround
    "kicker",         # S — the spoken demographic taunt + CTA line (authored per row; free-form, NOT a fixed enum). build_prompt fills a default from `demographic` if blank.
)
QUEUE_ROW_COLUMN_COUNT = len(ROW_KEYS)  # 19
LAST_COL = "S"
QUEUE_ROW_RANGE = f"'{QUEUE_TAB}'!A{{row}}:{LAST_COL}{{row}}"
QUEUE_ROW_BULK_RANGE = f"'{QUEUE_TAB}'!A{{min_row}}:{LAST_COL}{{max_row}}"

FIELD_TO_HEADER: dict[str, str] = {
    "idx":            "#",
    "slug":           "Slug",
    "status":         "Status",
    "place":          "Place",
    "claim_1":        "Claim 1",
    "claim_2":        "Claim 2",
    "claim_3":        "Claim 3",
    "lie_index":      "Lie #",
    "lie_model":      "Lie Model",
    "label_1":        "Label 1",
    "label_2":        "Label 2",
    "label_3":        "Label 3",
    "demographic":    "Demographic",
    "openart_prompt": "OpenArt Prompt",
    "caption":        "Caption",
    "drive_link":     "Drive Link",
    "drive_clip_link":"Drive Clip",
    "theme":          "Theme",
    "kicker":         "Kicker",
}
# Header labels in column order — used by seed_sheet.py to lay out row 2.
HEADER_LABELS: list[str] = [FIELD_TO_HEADER[k] for k in ROW_KEYS]

STATUS_DRAFT            = "Draft"
STATUS_READY_TO_REVIEW  = "Ready to review"
STATUS_READY_TO_PUBLISH = "Ready to publish"
STATUS_PUBLISHED        = "Published"

_header_to_letter_cache: dict[str, str] | None = None


def build_sheets(write: bool = False):
    scopes = READWRITE_SCOPES if write else READ_SCOPES
    creds = service_account.Credentials.from_service_account_file(
        str(SA_PATH), scopes=scopes,
    )
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _pad(values: list, count: int) -> list:
    return (list(values) + [""] * count)[:count]


def _index_to_column_letter(idx: int) -> str:
    if idx < 26:
        return chr(ord("A") + idx)
    return chr(ord("A") + idx // 26 - 1) + chr(ord("A") + idx % 26)


def _refresh_header_cache(sheets) -> dict[str, str]:
    global _header_to_letter_cache
    r = sheets.spreadsheets().values().get(
        spreadsheetId=QUEUE_SHEET,
        range=f"'{QUEUE_TAB}'!{QUEUE_HEADER_ROW}:{QUEUE_HEADER_ROW}",
    ).execute()
    headers = r.get("values", [[]])[0]
    label_to_letter: dict[str, str] = {}
    for i, label in enumerate(headers):
        label = (label or "").strip()
        if label and label not in label_to_letter:
            label_to_letter[label] = _index_to_column_letter(i)
    _header_to_letter_cache = label_to_letter
    return label_to_letter


def cell_for(sheets, row: int, field: str) -> str:
    try:
        header_label = FIELD_TO_HEADER[field]
    except KeyError as e:
        raise KeyError(
            f"unknown queue field {field!r}; known: {sorted(FIELD_TO_HEADER)}"
        ) from e
    cache = _header_to_letter_cache or _refresh_header_cache(sheets)
    if header_label not in cache:
        cache = _refresh_header_cache(sheets)
        if header_label not in cache:
            raise RuntimeError(
                f"Queue header row missing label {header_label!r} for field {field!r}; "
                f"known labels: {sorted(cache.keys())}"
            )
    return f"'{QUEUE_TAB}'!{cache[header_label]}{row}"


def read_queue_row(sheets, row: int) -> dict:
    r = sheets.spreadsheets().values().get(
        spreadsheetId=QUEUE_SHEET, range=QUEUE_ROW_RANGE.format(row=row),
    ).execute()
    values = r.get("values", [[]])[0]
    d = dict(zip(ROW_KEYS, _pad(values, QUEUE_ROW_COLUMN_COUNT)))
    d["row"] = row
    return d


def read_queue_bulk(sheets, min_row: int = None, max_row: int = 500) -> list[dict]:
    if min_row is None:
        min_row = QUEUE_DATA_START_ROW
    r = sheets.spreadsheets().values().get(
        spreadsheetId=QUEUE_SHEET,
        range=QUEUE_ROW_BULK_RANGE.format(min_row=min_row, max_row=max_row),
    ).execute()
    out: list[dict] = []
    for i, vals in enumerate(r.get("values", []), start=min_row):
        if not vals or not any((v or "").strip() for v in vals):
            continue
        d = dict(zip(ROW_KEYS, _pad(vals, QUEUE_ROW_COLUMN_COUNT)))
        d["row"] = i
        out.append(d)
    return out


def find_row_by_slug(sheets, slug: str) -> int | None:
    for r in read_queue_bulk(sheets):
        if (r.get("slug") or "").strip() == slug:
            return r["row"]
    return None


def next_index(sheets) -> int:
    rows = read_queue_bulk(sheets)
    mx = 0
    for r in rows:
        try:
            mx = max(mx, int(r.get("idx") or 0))
        except ValueError:
            continue
    return mx + 1


def append_row(sheets, values: list) -> int:
    if len(values) != QUEUE_ROW_COLUMN_COUNT:
        values = (list(values) + [""] * QUEUE_ROW_COLUMN_COUNT)[:QUEUE_ROW_COLUMN_COUNT]
    resp = sheets.spreadsheets().values().append(
        spreadsheetId=QUEUE_SHEET,
        range=f"'{QUEUE_TAB}'!A{QUEUE_DATA_START_ROW}",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": [values]},
    ).execute()
    updated_range = resp.get("updates", {}).get("updatedRange", "")
    if "!" in updated_range:
        first = updated_range.split("!", 1)[1].split(":", 1)[0]
        digits = "".join(c for c in first if c.isdigit())
        if digits:
            return int(digits)
    return -1


def update_cells(sheets, row: int, **fields: str) -> None:
    if not fields:
        return
    data = []
    for field, value in fields.items():
        data.append({"range": cell_for(sheets, row, field), "values": [[value]]})
    sheets.spreadsheets().values().batchUpdate(
        spreadsheetId=QUEUE_SHEET,
        body={"valueInputOption": "USER_ENTERED", "data": data},
    ).execute()
