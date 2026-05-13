"""Brian-tab schema and header-row resolver for trivia_images.

The Brian tab has two header rows: row 1 holds section labels (e.g.
"image drive", "image complete") and row 2 holds the per-column names
the team types ("Number", "Question text", "Answer 1 (correct)", …).
Hardcoded column indices (C, D, Q, R, …) broke whenever someone
inserted a column to the left, so this module resolves field → live
column letter at runtime by reading **both header rows** once per
process. Row 2 wins when a label appears in both.

Single source of truth for both `scripts/trivia_images/generate.py` and
`scripts/trivia_images/web/server.py`. Adding a column in the sheet is
safe as long as the **header label** of the columns this module cares
about is unchanged. Renaming a tracked header label requires updating
`FIELD_TO_HEADER` in one place.

Local to the trivia_images pipeline — does not share code with the
unrelated trivia/ pipeline.
"""
from __future__ import annotations

from pathlib import Path

SHEET_ID = "1Kh9Ai9-sKyyK1q24jVkQqeIz-Y-0rdNVIjPc2EF8hPk"
SHEET_TAB = "Brian"
HEADER_ROWS = (1, 2)    # row 1 = section banners, row 2 = per-column names
DATA_START_ROW = 3
SA_PATH = Path.home() / ".google" / "claude-sheets-sa.json"
SCOPES_RW = ["https://www.googleapis.com/auth/spreadsheets"]
SCOPES_RO = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

# Python field name → header label as it appears in the sheet (either
# row 1 or row 2 — resolver checks both). Keep field names stable
# across the codebase; the labels are what users see, and the only
# thing that needs to match the sheet. Renaming a tracked label
# requires updating this map in one place.
FIELD_TO_HEADER: dict[str, str] = {
    "number":            "Number",
    "complete":          "image complete",     # lives in row 1
    "category":          "Category",
    "mode":              "MODE",
    "question":          "Question text",
    "answer_correct":    "Answer 1 (correct)",
    "answer_2":          "Answer  2",          # the sheet has two spaces in these labels
    "answer_3":          "Answer  3",
    "answer_4":          "Answer  4",
    "response_correct":  "Response Text - CORRECT",
    "response_incorrect": "Response Text - INCORRECT answer",
    "hint":              "HINT",
    "prompt_q":          "Question IMAGE",
    "prompt_r":          "Answer IMAGE (CORRECT)",
}

# Optional fields — resolver returns None for these if the header label
# isn't present, instead of raising. Useful for forward compatibility
# (e.g. adding a new column that older code doesn't require).
OPTIONAL_FIELDS: frozenset[str] = frozenset({
    "response_correct", "response_incorrect", "hint",
    "answer_2", "answer_3", "answer_4",
})


def index_to_letter(idx: int) -> str:
    """0 → 'A', 25 → 'Z', 26 → 'AA'."""
    if idx < 26:
        return chr(ord("A") + idx)
    return chr(ord("A") + idx // 26 - 1) + chr(ord("A") + idx % 26)


def letter_to_index(letter: str) -> int:
    """'A' → 0, 'Z' → 25, 'AA' → 26."""
    letter = letter.upper()
    if len(letter) == 1:
        return ord(letter) - ord("A")
    return (ord(letter[0]) - ord("A") + 1) * 26 + (ord(letter[1]) - ord("A"))


class SheetSchema:
    """Resolves field name → live column index/letter via row 2.

    Caches the resolution per instance; call `refresh()` to re-read the
    header row if the sheet may have changed within the same process.
    """

    def __init__(self, sheets) -> None:
        self._sheets = sheets
        self._field_to_index: dict[str, int] | None = None

    def _resolve(self) -> dict[str, int]:
        lo, hi = min(HEADER_ROWS), max(HEADER_ROWS)
        r = self._sheets.spreadsheets().values().get(
            spreadsheetId=SHEET_ID,
            range=f"{SHEET_TAB}!{lo}:{hi}",
        ).execute()
        rows = r.get("values") or []
        # Per-row label maps. Row 2 wins over row 1 when both have the
        # same label (rare but possible — row 2 is the authoritative
        # per-column row).
        row1 = rows[0] if len(rows) >= 1 else []
        row2 = rows[1] if len(rows) >= 2 else []
        label_to_index: dict[str, int] = {}
        for source in (row1, row2):
            for i, label in enumerate(source):
                label = (label or "").strip()
                if label:
                    label_to_index[label] = i   # row 2 overwrites row 1

        out: dict[str, int] = {}
        unresolved_required: list[str] = []
        for field, header in FIELD_TO_HEADER.items():
            idx = label_to_index.get(header)
            if idx is not None:
                out[field] = idx
            elif field not in OPTIONAL_FIELDS:
                unresolved_required.append(f"{field} (label={header!r})")
        if unresolved_required:
            raise RuntimeError(
                f"Brian header rows missing required labels: "
                f"{', '.join(unresolved_required)}. "
                f"Resolved labels: {sorted(label_to_index)}"
            )
        self._field_to_index = out
        return out

    def refresh(self) -> dict[str, int]:
        return self._resolve()

    def index(self, field: str) -> int:
        """0-based column index for `field`. Re-reads the header rows
        once and retries before giving up — handles the case where the
        sheet drifted within this process's lifetime.

        KeyError if `field` is optional and absent. RuntimeError if a
        required field is missing (surfaces during `_resolve`).
        """
        cache = self._field_to_index or self._resolve()
        if field in cache:
            return cache[field]
        cache = self._resolve()
        return cache[field]

    def letter(self, field: str) -> str:
        return index_to_letter(self.index(field))

    def max_index(self) -> int:
        """Largest column index across all resolved fields (for read range sizing)."""
        cache = self._field_to_index or self._resolve()
        return max(cache.values()) if cache else 0

    def extract(self, row: list[str], fields: list[str]) -> dict[str, str]:
        """Pull `fields` out of a raw row, padding short rows and stripping cells."""
        cache = self._field_to_index or self._resolve()
        max_idx = max((cache.get(f, -1) for f in fields), default=-1)
        padded = list(row) + [""] * (max(0, max_idx + 1 - len(row)))
        out: dict[str, str] = {}
        for f in fields:
            idx = cache.get(f)
            out[f] = "" if idx is None else (padded[idx] or "").strip()
        return out
