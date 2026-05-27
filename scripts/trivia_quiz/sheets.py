"""Sheets reader for trivia-quiz — single Posts_Quiz tab.

Modeled on scripts/trivia/post_row.py. Replaces the YAML fixture path
(projects/trivia-quiz/<slug>/inputs/quiz_row.yaml) for v0.2 authoring.

Single-tab schema: each Posts_Quiz row is fully self-contained —
identity, hook variant, all 3 questions inline, post metadata, publish
state. No separate Questions bank, no UID resolution. One row = one post.

The reader produces a dict in the SAME shape as the YAML fixture.
Downstream code (build_brief, build_quiz_meta, audio) is unchanged.

Service account auth — same as trivia-short (~/.google/claude-sheets-sa.json
or $OPENMONTAGE_SA_PATH).
"""
from __future__ import annotations

import datetime as _dt
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from google.oauth2 import service_account
from googleapiclient.discovery import build

# ---------------------------------------------------------------------------
# Auth — reused from trivia-short patterns
# ---------------------------------------------------------------------------

SA_PATH = Path(os.environ.get(
    "OPENMONTAGE_SA_PATH",
    str(Path.home() / ".google" / "claude-sheets-sa.json"),
))

# Same spreadsheet as trivia-short by default — operationally tidy. Override
# with $TRIVIA_QUIZ_SHEET if you want a dedicated spreadsheet.
DEFAULT_SHEET_ID = "1EzucrS6yUPfodtt7WVuvW3PjZ1yhWUgfWUowPkMP6Eg"
QUIZ_SHEET_ID = os.environ.get("TRIVIA_QUIZ_SHEET", DEFAULT_SHEET_ID)

READ_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
READWRITE_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

POSTS_TAB = "Posts_Quiz"
HEADER_ROW = 1   # row 1 = header labels
DATA_START = 2   # row 2 = first data row


def build_sheets(write: bool = False):
    """Sheets v4 client. Pass write=True for stages that update cells."""
    scopes = READWRITE_SCOPES if write else READ_SCOPES
    creds = service_account.Credentials.from_service_account_file(
        str(SA_PATH), scopes=scopes,
    )
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


# ---------------------------------------------------------------------------
# Posts_Quiz tab schema — single source of truth for column order
# ---------------------------------------------------------------------------
#
# Each row is fully self-contained: identity, hook variant, all 3 questions
# inline, post metadata, captions, publish state. Wide (29 cols) but no joins
# needed — one row = one post = everything you'd write for that post.

POST_FIELDS: tuple[str, ...] = (
    # Identity
    "order",              # A: daily ordinal
    "post_date",          # B: yyyy-mm-dd
    "slug",               # C: kebab-case
    "hook_variant",       # D: key into styles/trivia-quiz.yaml::hook_closer_variants ("" → default)
    # Q1 (Easy)
    "q1_question",        # E
    "q1_choices",         # F: pipe-separated "A) X | B) Y | C) Z" (blank = T/F)
    "q1_answer",          # G: exact choice label
    "q1_fact",            # H: surprise fact shown after reveal
    "q1_backdrop",        # I: OpenArt prompt for the Q1 backdrop
    # Q2 (Medium)
    "q2_question",        # J
    "q2_choices",         # K
    "q2_answer",          # L
    "q2_fact",            # M
    "q2_backdrop",        # N
    # Q3 (Hard)
    "q3_question",        # O
    "q3_choices",         # P
    "q3_answer",          # Q
    "q3_fact",            # R
    "q3_backdrop",        # S
    "q3_game_themed",     # T: YES/NO
    # Post metadata
    "game_hook_line",     # U: VO line on score card when Q3 is game-themed
    "bottom_cta",         # V: row-level CTA override ("" → style default)
    "music_track",        # W: filename in music_library/ ("" → style default)
    "reward",             # X: optional reward sweetener
    "caption",            # Y: post caption used for BOTH TikTok and Instagram
    "pinned_comment",     # Z: pinned reply template
    # Publish state
    "final_status",       # AA: Draft / Ready to publish / Approved / Published
    "final_video_link",   # AB: Drive URL — written by publish stage
    "final_feedback",     # AC: free-form notes for revisions
)
POST_HEADERS: dict[str, str] = {
    "order":             "Order",
    "post_date":         "Post Date",
    "slug":              "Slug",
    "hook_variant":      "Hook Variant",
    "q1_question":       "Q1 Question",
    "q1_choices":        "Q1 Choices",
    "q1_answer":         "Q1 Answer",
    "q1_fact":           "Q1 Surprise Fact",
    "q1_backdrop":       "Q1 Backdrop Hint",
    "q2_question":       "Q2 Question",
    "q2_choices":        "Q2 Choices",
    "q2_answer":         "Q2 Answer",
    "q2_fact":           "Q2 Surprise Fact",
    "q2_backdrop":       "Q2 Backdrop Hint",
    "q3_question":       "Q3 Question",
    "q3_choices":        "Q3 Choices",
    "q3_answer":         "Q3 Answer",
    "q3_fact":           "Q3 Surprise Fact",
    "q3_backdrop":       "Q3 Backdrop Hint",
    "q3_game_themed":    "Q3 Game Themed",
    "game_hook_line":    "Game Hook Line",
    "bottom_cta":        "Bottom CTA",
    "music_track":       "Music Track",
    "reward":            "Reward",
    "caption":           "Caption (TikTok + Instagram)",
    "pinned_comment":    "Pinned Comment",
    "final_status":      "Final Status",
    "final_video_link":  "Final Video Link",
    "final_feedback":    "Final Feedback",
}


def _col_letter(idx: int) -> str:
    """Convert 0-indexed column to A1 letter(s). Handles past Z (AA, AB, …)."""
    s = ""
    n = idx
    while True:
        s = chr(ord("A") + (n % 26)) + s
        n = n // 26 - 1
        if n < 0:
            break
    return s


def _pad(values: List[Any], count: int) -> List[Any]:
    return (list(values) + [""] * count)[:count]


# ---------------------------------------------------------------------------
# Bulk readers — load each tab into Python dicts
# ---------------------------------------------------------------------------

def read_posts_bulk(sheets) -> List[Dict[str, Any]]:
    """Read every Posts_Quiz row in order."""
    n = len(POST_FIELDS)
    end_col = _col_letter(n - 1)
    rng = f"{POSTS_TAB}!A{DATA_START}:{end_col}"
    resp = sheets.spreadsheets().values().get(
        spreadsheetId=QUIZ_SHEET_ID, range=rng,
    ).execute()
    rows = resp.get("values", [])
    out: List[Dict[str, Any]] = []
    for row in rows:
        padded = _pad(row, n)
        rec = dict(zip(POST_FIELDS, padded))
        if not (rec["slug"] or rec["order"]):
            continue
        out.append(rec)
    return out


# ---------------------------------------------------------------------------
# Resolve one post row to the YAML-fixture-equivalent dict
# ---------------------------------------------------------------------------

def fail(msg: str) -> "None":
    raise RuntimeError(msg)


def _q_block_from_post(post: Dict[str, Any], qid: str, difficulty: str) -> Dict[str, Any]:
    """Extract the {question, difficulty, choices, answer, surprise_fact,
    backdrop_hint, game_themed} block for qid in {q1, q2, q3} from the row
    fields that live inline (q1_question, q1_choices, q1_answer, etc)."""
    choices_raw = (post.get(f"{qid}_choices") or "").strip()
    choices = [c.strip() for c in choices_raw.split("|")] if choices_raw else []
    block: Dict[str, Any] = {
        "question":      (post.get(f"{qid}_question") or "").strip(),
        "difficulty":    difficulty,
        "choices":       choices,
        "answer":        (post.get(f"{qid}_answer") or "").strip(),
        "surprise_fact": (post.get(f"{qid}_fact") or "").strip(),
        "backdrop_hint": (post.get(f"{qid}_backdrop") or "").strip(),
    }
    if qid == "q3":
        block["game_themed"] = (post.get("q3_game_themed") or "").strip().upper() == "YES"
    return block


def resolve_post_row_to_fixture(
    sheets, *, slug: Optional[str] = None, row: Optional[int] = None,
) -> Dict[str, Any]:
    """Read one Posts_Quiz row and produce a dict matching the YAML fixture
    shape (so build_brief works unchanged). All question content is inline
    on the row — no UID lookups, no separate tab.

    Lookup: pass `slug` OR `row` (1-indexed in data, ignoring header).
    """
    posts = read_posts_bulk(sheets)

    post: Optional[Dict[str, Any]] = None
    if slug:
        matches = [p for p in posts if p["slug"].strip() == slug.strip()]
        if not matches:
            fail(f"slug {slug!r} not found in {POSTS_TAB}")
        if len(matches) > 1:
            fail(f"slug {slug!r} matches {len(matches)} rows in {POSTS_TAB} — must be unique")
        post = matches[0]
    elif row is not None:
        if row < 1 or row > len(posts):
            fail(f"row {row} out of range (have {len(posts)} data rows)")
        post = posts[row - 1]
    else:
        fail("must pass slug or row")

    q1 = _q_block_from_post(post, "q1", "Easy")
    q2 = _q_block_from_post(post, "q2", "Medium")
    q3 = _q_block_from_post(post, "q3", "Hard")

    return {
        "slug":            post["slug"].strip(),
        "post_date":       post.get("post_date") or None,
        "topic_mix":       f"{q1['difficulty']} → {q2['difficulty']} → {q3['difficulty']}",
        "q1":              q1,
        "q2":              q2,
        "q3":              q3,
        "hook_variant":    (post.get("hook_variant") or "").strip(),
        "game_hook_line":  (post.get("game_hook_line") or "").strip(),
        "bottom_cta":      (post.get("bottom_cta") or "").strip(),
        "music_track":     (post.get("music_track") or "").strip(),
        "reward":          (post.get("reward") or "").strip(),
        "captions": {
            # Single shared caption — used for both TikTok and Instagram so
            # the two platforms never drift. Pinned comment stays separate
            # since it's a different artifact (auto-posted reply).
            "caption":        (post.get("caption") or "").strip(),
            "pinned_comment": (post.get("pinned_comment") or "").strip(),
        },
    }


# ---------------------------------------------------------------------------
# Writeback — used by publish stage (Phase 4) to write Final Status + Link
# ---------------------------------------------------------------------------

def write_post_field(sheets, slug: str, field: str, value: str) -> None:
    """Write a single field of a Posts_Quiz row by slug. Used by the publish
    stage to set final_status and final_video_link."""
    if field not in POST_HEADERS:
        fail(f"unknown POST field: {field!r}")
    col_letter = _col_letter(POST_FIELDS.index(field))

    posts = read_posts_bulk(sheets)
    for i, p in enumerate(posts):
        if p["slug"].strip() == slug.strip():
            row = DATA_START + i  # account for header
            rng = f"{POSTS_TAB}!{col_letter}{row}"
            sheets.spreadsheets().values().update(
                spreadsheetId=QUIZ_SHEET_ID,
                range=rng,
                valueInputOption="RAW",
                body={"values": [[value]]},
            ).execute()
            return
    fail(f"slug {slug!r} not found in {POSTS_TAB} (write_post_field {field})")


# ---------------------------------------------------------------------------
# One-shot tab initializer — creates Questions + Posts_Quiz with header rows
# ---------------------------------------------------------------------------

def ensure_tabs_exist(sheets, *, delete_orphan_questions: bool = False) -> dict:
    """Create the Posts_Quiz tab if missing + write/refresh the header row.
    Idempotent — safe to re-run after schema changes.

    Pass delete_orphan_questions=True to also remove a legacy `Questions`
    tab from an earlier two-tab schema.
    """
    meta = sheets.spreadsheets().get(spreadsheetId=QUIZ_SHEET_ID).execute()
    existing_tabs = {s["properties"]["title"]: s["properties"]["sheetId"]
                      for s in meta.get("sheets", [])}

    requests = []
    if POSTS_TAB not in existing_tabs:
        requests.append({"addSheet": {"properties": {
            "title": POSTS_TAB,
            "gridProperties": {"frozenRowCount": 1},
        }}})
    if delete_orphan_questions and "Questions" in existing_tabs:
        requests.append({"deleteSheet": {"sheetId": existing_tabs["Questions"]}})

    if requests:
        sheets.spreadsheets().batchUpdate(
            spreadsheetId=QUIZ_SHEET_ID, body={"requests": requests},
        ).execute()

    # Write/refresh the header row
    posts_headers = [POST_HEADERS[k] for k in POST_FIELDS]
    sheets.spreadsheets().values().update(
        spreadsheetId=QUIZ_SHEET_ID,
        range=f"{POSTS_TAB}!A1",
        valueInputOption="RAW",
        body={"values": [posts_headers]},
    ).execute()

    created = [POSTS_TAB] if POSTS_TAB not in existing_tabs else []
    return {
        "created": created,
        "ensured": [POSTS_TAB],
        "deleted_orphan_questions": delete_orphan_questions and "Questions" in existing_tabs,
        "spreadsheet_id": QUIZ_SHEET_ID,
    }
