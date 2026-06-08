"""TriviaCaptainQueue schema, reader, and writer (workflow-state SoT).

Sister of scripts/trivia/post_row.py. Same contract:
  - Single source of truth for Queue spreadsheet ID and scopes
  - ORDERED schema (ROW_KEYS) — must match left-to-right column order
  - Field -> header-label map (FIELD_TO_HEADER) — drives runtime cell_for
  - Read/write helpers used by select / asset / edit / compose / publish

The TriviaCaptainQueue sheet is THIS pipeline's source of truth for workflow
state. Per-row Status, Drive Link, and OpenArt Prompt live here. The trivia
CONTENT (Question / CorrectAnswer / CorrectExplanation) is owned by the
daily-trivia + LocalizedTextConfig sheets — see daily_trivia.py — and is
resolved fresh on every run.

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

# Pinned IDs.
QUEUE_SHEET = "1bUvGLcSJr-YCwBxanCQ_7QT5YK8ZFgjgZbT32u2550w"
QUEUE_TAB = "Queue"
QUEUE_HEADER_ROW = 2
QUEUE_DATA_START_ROW = 3

READ_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
READWRITE_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# ROW_KEYS is the ordered tuple of Python field names. read_queue_row zips
# this against the row's values in source-order, so the order MUST match
# the sheet's left-to-right column order.
ROW_KEYS: tuple[str, ...] = (
    "day",                  # A — int, matches DailyTriviaConfig.B
    "slug",                 # B — kebab-case identifier, used as project dir name
    "status",               # C — Draft / Ready to review / Ready to publish / Published
    "question_en",          # D — resolved EN question (denormalized for human review)
    "correct_answer_en",    # E — resolved EN correct answer
    "hook_vo",              # F — VO line 1: "So I just found out…"
    "fact_vo",              # G — VO line 2: the surprising fact
    "kicker_vo",            # H — VO line 3: the punchline / number reveal
    "drive_link",           # I — final captioned mp4 webViewLink, written by publish (the posted version)
    "openart_prompt",       # J — assembled Seedance prompt, written by script director; human-readable copy-paste source
    "caption",              # K — IG-ready post description + hashtags, written by script director
    "drive_clip_link",      # L — raw avatar-clip webViewLink (Seedance output, no captions); secondary deliverable
    "reference_image",      # M — repo-relative path (or Drive link) of the image uploaded to OpenArt as the tablet-screen reference
    "fact_image_prompt",    # N — prompt that generates the per-row fact image (the full-bleed tablet screen). Generate Phase 0 reads it; script-director authors it.
)
QUEUE_ROW_COLUMN_COUNT = len(ROW_KEYS)  # 13
QUEUE_ROW_RANGE = f"'{QUEUE_TAB}'!A{{row}}:N{{row}}"
QUEUE_ROW_BULK_RANGE = f"'{QUEUE_TAB}'!A{{min_row}}:N{{max_row}}"

FIELD_TO_HEADER: dict[str, str] = {
    "day":               "Day",
    "slug":              "Slug",
    "status":            "Status",
    "question_en":       "Question (EN)",
    "correct_answer_en": "Correct Answer (EN)",
    "hook_vo":           "Hook VO",
    "fact_vo":           "Fact VO",
    "kicker_vo":         "Kicker VO",
    "drive_link":        "Drive Link",
    "openart_prompt":    "OpenArt Prompt",
    "caption":           "Caption",
    "drive_clip_link":   "Drive Clip",
    "reference_image":   "Reference Image",
    "fact_image_prompt": "Fact Image Prompt",
}

# Status enum — 4 states, simplified from the old 7-state model on 2026-05-19.
# Workflow:
#   Draft (row exists, prompt being authored)
#     -> Ready to review (script-director locks the prompt; awaiting human OK to generate)
#     -> Ready to publish (the full generate+render chain finished; awaiting drive upload)
#     -> Published (human flipped after Instagram post is live)
STATUS_DRAFT            = "Draft"
STATUS_READY_TO_REVIEW  = "Ready to review"
STATUS_READY_TO_PUBLISH = "Ready to publish"
STATUS_PUBLISHED        = "Published"

_header_to_letter_cache: dict[str, str] | None = None


def build_sheets(write: bool = False):
    """Sheets v4 client. Pass write=True for stages that mutate the Queue (select / publish)."""
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
    """Return the A1-style address for `field` on `row` (e.g. 'Queue!C5')."""
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
    """Read one Queue row and return a dict keyed by ROW_KEYS, plus `row`."""
    r = sheets.spreadsheets().values().get(
        spreadsheetId=QUEUE_SHEET, range=QUEUE_ROW_RANGE.format(row=row),
    ).execute()
    values = r.get("values", [[]])[0]
    d = dict(zip(ROW_KEYS, _pad(values, QUEUE_ROW_COLUMN_COUNT)))
    d["row"] = row
    return d


def read_queue_bulk(sheets, min_row: int = None, max_row: int = 500) -> list[dict]:
    """Read a contiguous range of Queue rows; skips fully-empty rows."""
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


def find_row_by_day(sheets, day: int) -> int | None:
    """Return the 1-indexed sheet row for `day`, or None if not yet enqueued."""
    rows = read_queue_bulk(sheets)
    for r in rows:
        try:
            if int(r.get("day") or 0) == day:
                return r["row"]
        except ValueError:
            continue
    return None


def append_row(sheets, values: list) -> int:
    """Append a row at the end of the Queue tab. `values` is a 1xN list of cells.

    Returns the 1-indexed sheet row of the appended row.
    """
    if len(values) != QUEUE_ROW_COLUMN_COUNT:
        values = list(values) + [""] * (QUEUE_ROW_COLUMN_COUNT - len(values))
        values = values[:QUEUE_ROW_COLUMN_COUNT]
    resp = sheets.spreadsheets().values().append(
        spreadsheetId=QUEUE_SHEET,
        range=f"'{QUEUE_TAB}'!A{QUEUE_DATA_START_ROW}",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": [values]},
    ).execute()
    # appended range looks like 'Queue'!A14:K14 — pull the row number out.
    updated_range = resp.get("updates", {}).get("updatedRange", "")
    if "!" in updated_range:
        cell = updated_range.split("!", 1)[1]
        # e.g. "A14:K14" -> "14"
        first = cell.split(":", 1)[0]
        digits = "".join(c for c in first if c.isdigit())
        if digits:
            return int(digits)
    return -1


def update_cells(sheets, row: int, **fields: str) -> None:
    """batchUpdate one or more cells on `row`. Each kwarg is field_name=value."""
    if not fields:
        return
    data = []
    for field, value in fields.items():
        rng = cell_for(sheets, row, field)
        data.append({"range": rng, "values": [[value]]})
    sheets.spreadsheets().values().batchUpdate(
        spreadsheetId=QUEUE_SHEET,
        body={"valueInputOption": "USER_ENTERED", "data": data},
    ).execute()
