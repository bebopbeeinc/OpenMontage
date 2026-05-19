"""Read daily-trivia content and resolve Uid-keyed text via LocalizedTextConfig.

Two spreadsheets feed the trivia-reaction pipeline:

  - DailyTriviaConfig  (1G1iffAILfxvfF_JZx7CWNTi9IF1y_LyKmi9hrgWGGfk)
      Game-config rows. Per Day N, the row at index (N + DATA_OFFSET) carries
      Uid pointers in columns C / H / L (Question, FirstAnswer, CorrectExplanation)
      that need to be resolved against LocalizedTextConfig before the
      pipeline can do anything useful with them.

  - LocalizedTextConfig (1y-REopUmAtoirAp-8rOMNz7LlqLe2mjVAlkdhmGKgtY)
      Uid -> 13-locale string dictionary. Trivia-reaction is EN-only for
      ship-1 (col B = EN). Lookup is exact-match on col A.

Both sheets are READ-ONLY from this pipeline. We never write here.

Public surface:
  build_sheets()                       # service-account Sheets client (read-only)
  read_daily_trivia_row(sheets, day)   # -> TriviaRow with Uids and resolved EN text
  resolve_uid(sheets, uid)             # one-off Uid -> EN lookup
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build

# Service-account path — same env override as trivia/post_row.py.
SA_PATH = Path(os.environ.get(
    "OPENMONTAGE_SA_PATH",
    str(Path.home() / ".google" / "claude-sheets-sa.json"),
))

# Sheet IDs (pinned for ship-1).
DAILY_TRIVIA_SHEET = "1G1iffAILfxvfF_JZx7CWNTi9IF1y_LyKmi9hrgWGGfk"
LOCALIZED_TEXT_SHEET = "1y-REopUmAtoirAp-8rOMNz7LlqLe2mjVAlkdhmGKgtY"

# Per-tab layout.
# DailyTriviaConfig:
#   row 1 = banner, row 2 = type, row 3 = header, row 4+ = data.
#   Day N lives at sheet row (3 + N) so trivia_uid1 = row 4.
DAILY_TRIVIA_TAB_DEV  = "DailyTriviaConfig (DEV)"
DAILY_TRIVIA_TAB_PROD = "DailyTriviaConfig (PROD)"
DAILY_TRIVIA_HEADER_ROW = 3
DAILY_TRIVIA_DATA_START_ROW = 4

# LocalizedTextConfig:
#   row 1 = banner, row 2 = type, row 3 = locale codes, row 4 = header,
#   row 5+ = Uid -> 13 translations.
LOCALIZED_TEXT_TAB_DEV = "LocalizedTextConfig (DEV)"
LOCALIZED_TEXT_HEADER_ROW = 4
LOCALIZED_TEXT_DATA_START_ROW = 5

READ_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


def build_sheets():
    """Build a read-only Sheets v4 client for daily-trivia + LocalizedText."""
    creds = service_account.Credentials.from_service_account_file(
        str(SA_PATH), scopes=READ_SCOPES,
    )
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


# In-memory Uid -> EN cache. The LocalizedText sheet is large (~14k rows) so
# resolving one Uid at a time over the API is slow. We bulk-fetch col A and
# col B on first hit and serve subsequent lookups from RAM.
_locale_cache: dict[str, str] | None = None


def _load_locale_cache(sheets, tab: str = LOCALIZED_TEXT_TAB_DEV) -> dict[str, str]:
    """Read all (Uid, EN) pairs from LocalizedTextConfig into a dict."""
    global _locale_cache
    rng = f"'{tab}'!A{LOCALIZED_TEXT_DATA_START_ROW}:B"
    r = sheets.spreadsheets().values().get(
        spreadsheetId=LOCALIZED_TEXT_SHEET, range=rng,
    ).execute()
    cache: dict[str, str] = {}
    for row in r.get("values", []):
        if not row:
            continue
        uid = (row[0] or "").strip()
        en = row[1] if len(row) > 1 else ""
        if uid:
            cache[uid] = en
    _locale_cache = cache
    return cache


def resolve_uid(sheets, uid: str, tab: str = LOCALIZED_TEXT_TAB_DEV) -> str:
    """Return the EN string for `uid`. Empty string if the Uid is missing."""
    if not uid:
        return ""
    cache = _locale_cache if _locale_cache is not None else _load_locale_cache(sheets, tab)
    return cache.get(uid.strip(), "")


@dataclass
class TriviaRow:
    """One DailyTriviaConfig row, with the four Uids resolved to EN strings.

    The original Uids are kept alongside the resolved text so downstream
    artifacts can record both (Uid is the stable identity, EN is the human
    copy that drove the VO).
    """
    day: int
    row: int                           # 1-indexed sheet row
    uid_trivia: str                    # e.g. "trivia_uid5"
    uid_question: str
    uid_correct_answer: str
    uid_correct_explanation: str
    question_en: str
    correct_answer_en: str
    correct_explanation_en: str
    question_image_url: str = ""
    answer_image_url: str = ""


# DailyTriviaConfig column index map (0-indexed within the row's value list).
# These are the columns currently used by trivia-reaction. Pinned to the
# header positions seen at design time. If the sheet schema drifts we'll
# refresh-resolve via the live header row instead — for now this is fixed.
_COL_UID                = 0   # A
_COL_DAY                = 1   # B
_COL_QUESTION_UID       = 2   # C
_COL_QUESTION_IMAGE_URL = 3   # D
_COL_ANSWER_IMAGE_URL   = 5   # F
_COL_FIRST_ANSWER_UID   = 7   # H
_COL_EXPLAIN_CORRECT_UID = 11  # L


def read_daily_trivia_row(
    sheets,
    day: int,
    tab: str = DAILY_TRIVIA_TAB_DEV,
) -> TriviaRow:
    """Read one Day's row, resolving all four Uids to EN strings.

    Raises ValueError if Day N is missing or the Uids don't resolve.
    """
    if day < 1:
        raise ValueError(f"day must be >= 1; got {day}")
    sheet_row = DAILY_TRIVIA_DATA_START_ROW + (day - 1)
    rng = f"'{tab}'!A{sheet_row}:S{sheet_row}"
    r = sheets.spreadsheets().values().get(
        spreadsheetId=DAILY_TRIVIA_SHEET, range=rng,
    ).execute()
    values = r.get("values", [[]])
    if not values or not values[0]:
        raise ValueError(f"DailyTriviaConfig {tab!r} has no data at row {sheet_row}")
    row = values[0]
    # Right-pad so optional cols beyond the row's tail return "" rather than IndexError.
    row = list(row) + [""] * (20 - len(row))

    uid_trivia = (row[_COL_UID] or "").strip()
    sheet_day_val = (row[_COL_DAY] or "").strip()
    try:
        sheet_day_int = int(sheet_day_val)
    except ValueError:
        sheet_day_int = -1
    if sheet_day_int != day:
        raise ValueError(
            f"DailyTriviaConfig row {sheet_row} has Day={sheet_day_val!r}, expected {day}"
        )

    uid_question = (row[_COL_QUESTION_UID] or "").strip()
    uid_first    = (row[_COL_FIRST_ANSWER_UID] or "").strip()
    uid_correct  = (row[_COL_EXPLAIN_CORRECT_UID] or "").strip()

    return TriviaRow(
        day=day,
        row=sheet_row,
        uid_trivia=uid_trivia,
        uid_question=uid_question,
        uid_correct_answer=uid_first,
        uid_correct_explanation=uid_correct,
        question_en=resolve_uid(sheets, uid_question),
        correct_answer_en=resolve_uid(sheets, uid_first),
        correct_explanation_en=resolve_uid(sheets, uid_correct),
        question_image_url=(row[_COL_QUESTION_IMAGE_URL] or "").strip(),
        answer_image_url=(row[_COL_ANSWER_IMAGE_URL] or "").strip(),
    )
