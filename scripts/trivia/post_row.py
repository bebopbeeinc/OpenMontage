"""Canonical Posts-sheet schema and reader for the trivia-short pipeline.

Single source of truth for:
  - Posts spreadsheet ID and service-account scopes
  - Row schema (ROW_KEYS) — the ordered list of Python field names
  - Field → header-label map (FIELD_TO_HEADER) — drives runtime column lookup
  - Sheets client factory (build_sheets)
  - Row reader (read_post_row) and bulk reader (read_posts_bulk)
  - `cell_for(sheets, row, field)` — resolves a field name to the live
    A1-style cell address (e.g. "Posts!P17") by consulting the sheet's
    header row. Use this for all writes so column re-orderings in the
    sheet don't require hunting hardcoded letters across the codebase.

Consumers (assemble_modular, shorten_vo, openart_generate, web/server,
reconcile_captions, pick_reactions_llm) MUST import the schema from here
rather than redefining it. When a column is added, removed, or renamed
in the sheet, update FIELD_TO_HEADER (and ROW_KEYS if Python field names
change) — that's the only place the change needs to land for every
consumer to pick it up.
"""
from __future__ import annotations

import os
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build

# Service-account path. Honors $OPENMONTAGE_SA_PATH for non-default locations.
SA_PATH = Path(os.environ.get(
    "OPENMONTAGE_SA_PATH",
    str(Path.home() / ".google" / "claude-sheets-sa.json"),
))

# Sheet IDs (pinned). The Posts sheet is this pipeline's source of truth.
POST_SHEET = "1EzucrS6yUPfodtt7WVuvW3PjZ1yhWUgfWUowPkMP6Eg"
CLIPS_SHEET = "1E19Pv9ur0KsgHxny65rX_CXsT-yHPkbyhqjZTEvJG_E"
HOOK_SHEET = "1lwnBldh_fMAKHWMxQbzRxJ7GQ9m6wf35GR9bRCann8I"

# Scopes. Use READ_SCOPES for stages that never write to the sheet; use
# READWRITE_SCOPES for idea (picker), edit (emphasis), publish (L/M).
READ_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
READWRITE_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Posts column layout (26 cols, A:Z) — post-Style-removal. Style (column N)
# was retired because every row renders photorealistic.
#
# ROW_KEYS is the ordered tuple of Python field names. `read_post_row` zips
# this against the row's values in source-order, so the order must match
# the sheet's left-to-right column order. If the order drifts the bulk
# reader returns mis-keyed data and downstream stages silently corrupt.
ROW_KEYS: tuple[str, ...] = (
    "order", "post", "mode", "topic", "hook", "question", "answer_prompt",   # A-G
    "ending", "resolution", "cta", "trivia_uid",                              # H-K
    "final_status", "final_video_link",                                       # L-M
    "reaction_archetype", "reaction_prompt", "reaction_filename",             # N-P
    "emphasis_override",                                                       # Q
    "body_prompt", "body_filename",                                            # R-S
    "closer_prompt", "closer_filename",                                        # T-U
    "tiktok_description", "pinned_comment",                                   # V-W
    "hero_visuals", "slug", "music_track",                                    # X-Z
)
POST_ROW_COLUMN_COUNT = len(ROW_KEYS)  # 26
POST_ROW_RANGE = f"Posts!A{{row}}:Z{{row}}"
POST_ROW_BULK_RANGE = f"Posts!A{{min_row}}:Z{{max_row}}"
POST_HEADER_ROW = 4  # rows 1-3 are decorative banner; row 4 is column labels

# Python field name → sheet header-row label. The header label is what
# users see in the spreadsheet; the field name is what code uses. Keeping
# these decoupled means cosmetic header renames in the sheet only require
# updating this map (one place), and column reorderings don't require any
# code change at all (cell_for resolves at runtime).
FIELD_TO_HEADER: dict[str, str] = {
    "order":              "Order",
    "post":               "Post",
    "mode":               "Mode",
    "topic":              "Topic",
    "hook":               "Hook",
    "question":           "Question",
    "answer_prompt":      "Answer Prompt",
    "ending":             "Ending",
    "resolution":         "Resolution",
    "cta":                "CTA",
    "trivia_uid":         "Trivia UID",
    "final_status":       "Final Status",
    "final_video_link":   "Final Video Link",
    "reaction_archetype": "Reaction Archetype",
    "reaction_prompt":    "Reaction Prompt",
    "reaction_filename":  "Reaction Filename",
    "emphasis_override":  "Emphasis Override",
    "body_prompt":        "Body Prompt",
    "body_filename":      "Body Filename",
    "closer_prompt":      "Closer Prompt",
    "closer_filename":    "Closer Filename",
    "tiktok_description": "TikTok Description",
    "pinned_comment":     "Pinned Comment",
    "hero_visuals":       "Hero Visuals",
    "slug":               "Slug",
    "music_track":        "Music Track",
}

_header_to_letter_cache: dict[str, str] | None = None


def build_sheets(write: bool = False):
    """Build a Sheets v4 client. Pass write=True for stages that write."""
    scopes = READWRITE_SCOPES if write else READ_SCOPES
    creds = service_account.Credentials.from_service_account_file(
        str(SA_PATH), scopes=scopes,
    )
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _pad(values: list, count: int) -> list:
    return (list(values) + [""] * count)[:count]


def _index_to_column_letter(idx: int) -> str:
    """0 → 'A', 25 → 'Z', 26 → 'AA', 27 → 'AB', …"""
    if idx < 26:
        return chr(ord("A") + idx)
    return chr(ord("A") + idx // 26 - 1) + chr(ord("A") + idx % 26)


def _refresh_header_cache(sheets) -> dict[str, str]:
    """Read the live header row and rebuild the `header_label → letter`
    cache. Called lazily by `cell_for`; can be invoked manually after a
    column add/remove if the same Python process needs to see the change
    without restarting.
    """
    global _header_to_letter_cache
    r = sheets.spreadsheets().values().get(
        spreadsheetId=POST_SHEET, range=f"Posts!{POST_HEADER_ROW}:{POST_HEADER_ROW}",
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
    """Return the A1-style cell address for `field` on `row`.

    Looks up the sheet's live header row (row 4) once per process to
    resolve which column letter currently holds the given field. Callers
    use the result for writes:

        sheets.spreadsheets().values().update(
            spreadsheetId=POST_SHEET,
            range=cell_for(sheets, row, "question"),
            valueInputOption="USER_ENTERED",
            body={"values": [[new_text]]},
        ).execute()

    Raises if `field` isn't in `FIELD_TO_HEADER` (schema/code mismatch)
    or if the header label isn't present in the sheet (sheet drifted
    from the schema — surface fast instead of writing to the wrong cell).
    """
    try:
        header_label = FIELD_TO_HEADER[field]
    except KeyError as e:
        raise KeyError(
            f"unknown Posts field {field!r}; known fields: {sorted(FIELD_TO_HEADER)}"
        ) from e
    cache = _header_to_letter_cache or _refresh_header_cache(sheets)
    try:
        letter = cache[header_label]
    except KeyError as e:
        # Cache may be stale (header row changed since we cached). Refresh once.
        cache = _refresh_header_cache(sheets)
        try:
            letter = cache[header_label]
        except KeyError:
            raise RuntimeError(
                f"Posts header row missing label {header_label!r} "
                f"(expected for field {field!r}); known labels: "
                f"{sorted(cache.keys())}",
            ) from e
    return f"Posts!{letter}{row}"


def column_letter_for(sheets, field: str) -> str:
    """Return just the column letter for `field` (e.g. 'F' for 'question').

    Useful when you need to construct a multi-row range like
    `Posts!{letter}5:{letter}26` instead of a single cell.
    """
    return cell_for(sheets, 1, field).split("!", 1)[1].rstrip("0123456789")


def read_post_row(sheets, row: int) -> dict:
    """Read one Posts row and return a dict keyed by ROW_KEYS."""
    r = sheets.spreadsheets().values().get(
        spreadsheetId=POST_SHEET,
        range=POST_ROW_RANGE.format(row=row),
    ).execute()
    values = r.get("values", [[]])[0]
    return dict(zip(ROW_KEYS, _pad(values, POST_ROW_COLUMN_COUNT)))


def read_posts_bulk(sheets, min_row: int = 5, max_row: int = 200) -> list[dict]:
    """Read a contiguous range of Posts rows. Each dict carries an extra
    `row` field (1-indexed sheet row) for cross-referencing the source."""
    r = sheets.spreadsheets().values().get(
        spreadsheetId=POST_SHEET,
        range=POST_ROW_BULK_RANGE.format(min_row=min_row, max_row=max_row),
    ).execute()
    out: list[dict] = []
    for i, vals in enumerate(r.get("values", []), start=min_row):
        if not vals or not any((v or "").strip() for v in vals):
            continue
        d = dict(zip(ROW_KEYS, _pad(vals, POST_ROW_COLUMN_COUNT)))
        d["row"] = i
        out.append(d)
    return out
