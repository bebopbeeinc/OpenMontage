#!/usr/bin/env python
"""Shorten trivia VO text fields when assemble_modular reports they overshoot
their audio window. Reads assembly_warnings.json + sheet row, calls Claude
for length-bounded rewrites, and writes the result to text_overrides.json.

The web server runs this between Phase 1 attempts; on the next assemble run,
assemble_modular reads text_overrides.json and uses the shortened text for
this project only (the sheet is untouched).

Usage:
    python scripts/trivia/shorten_vo.py <row> <slug>

Auth:
    Tries the Anthropic SDK first (using ANTHROPIC_API_KEY).
    When that env var is missing, falls back to the local `claude` CLI
    (Claude Code), which uses your OAuth subscription — no API key required.

Exits:
    0  overrides written (one or more fields rewritten)
    2  no warnings to act on (caller should not retry)
    3  no Claude auth available (no ANTHROPIC_API_KEY and no `claude` CLI)
    4  sheet read failed
    5  Claude call failed (SDK or CLI)
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import anthropic

REPO = Path(__file__).resolve().parents[2]
_SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SCRIPTS / "common"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from post_row import (  # noqa: E402
    FIELD_TO_HEADER, POST_SHEET, build_sheets, cell_for, read_post_row,
)
from llm_shorten import claude_shorten  # noqa: E402

# Trivia VO is meant to read in the voice the rest of the row's copy is in.
TRIVIA_BRAND_TOKENS = ("Captain", "Travel Crush", "Fennec")
TRIVIA_TONE = "curious, punchy"

# Speech rate: roughly 2.5 words/sec at the Piper/ElevenLabs trivia voice.
# Used to size the rewrite target so the new VO fits with 1.0x tempo + headroom.
WORDS_PER_SECOND = 2.5

# Per-warning, which row fields to shorten. The script walks this list in
# order and shortens any field whose current length exceeds its proportional
# share of the total budget — so a multi-field entry like the choices-claim
# below can shrink the question when the options are already minimal.
WARNING_TO_FIELDS = {
    ("vo_line", "hook"):       ["hook"],
    ("vo_line", "resolution"): ["resolution"],
    ("vo_line", "cta"):        ["cta"],
    # Facts-mode claim VO is `f"{question}. {answer_prompt}"`. Shortening the
    # answer_prompt has the most leverage; question is the load-bearing claim.
    ("vo_line", "claim"):       ["answer_prompt", "question"],
    # Choices-mode claim VO is `question` + each lettered option (with pauses).
    # The options are structurally bounded (each must remain its full token —
    # "Beavers", "Oysters") so when they're already short, the only way to
    # claw back time is to tighten the question. List question first.
    ("choices_claim", "claim"): ["question", "answer_prompt"],
}


def _sheet_row(row_num: int) -> dict:
    return read_post_row(build_sheets(), row_num)


def _letter_to_index(letter: str) -> int:
    """Convert an A1-style column letter ('A'..'ZZ') to a 0-based index.

    Inverse of post_row._index_to_column_letter. Used to bridge from
    `cell_for`'s human-readable address back to the GridRange API which
    needs a numeric startColumnIndex.
    """
    n = 0
    for c in letter:
        n = n * 26 + (ord(c) - ord("A") + 1)
    return n - 1


_posts_sheet_id_cache: int | None = None


def _posts_sheet_id(sheets) -> int:
    """Fetch the numeric sheetId (gid) for the 'Posts' tab and cache it.

    The batchUpdate API needs the tab-level sheetId, not the spreadsheet ID.
    We only need it once per script run — every cell update on Posts reuses
    the same gid.
    """
    global _posts_sheet_id_cache
    if _posts_sheet_id_cache is not None:
        return _posts_sheet_id_cache
    meta = sheets.spreadsheets().get(
        spreadsheetId=POST_SHEET, fields="sheets.properties",
    ).execute()
    for s in meta.get("sheets", []):
        p = s.get("properties", {})
        if p.get("title") == "Posts":
            _posts_sheet_id_cache = p.get("sheetId")
            return _posts_sheet_id_cache  # type: ignore[return-value]
    raise RuntimeError("Could not find 'Posts' tab in spreadsheet")


def _commit_field_to_sheet(
    sheets,
    row_num: int,
    field: str,
    new_value: str,
    note: str,
) -> str | None:
    """Update the sheet cell for `field` on `row_num` and attach a tracking
    note. Returns the cell address (e.g. "F17") on success, None if skipped.

    Uses a single `batchUpdate` with an `updateCells` request so the value
    write and the note attach happen atomically. Cell position is resolved
    at runtime via `cell_for` so column reorderings don't require code
    changes here.
    """
    if field not in FIELD_TO_HEADER:
        return None
    addr = cell_for(sheets, row_num, field)        # "Posts!F17"
    cell = addr.split("!", 1)[1]                    # "F17"
    letter = "".join(c for c in cell if c.isalpha())
    col_idx = _letter_to_index(letter)
    sheet_id = _posts_sheet_id(sheets)
    sheets.spreadsheets().batchUpdate(
        spreadsheetId=POST_SHEET,
        body={
            "requests": [{
                "updateCells": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": row_num - 1,
                        "endRowIndex": row_num,
                        "startColumnIndex": col_idx,
                        "endColumnIndex": col_idx + 1,
                    },
                    "rows": [{
                        "values": [{
                            "userEnteredValue": {"stringValue": new_value},
                            "note": note,
                        }]
                    }],
                    "fields": "userEnteredValue,note",
                }
            }]
        },
    ).execute()
    return cell


def _build_shorten_note(
    field: str,
    warning: dict,
    before: str,
    after: str,
) -> str:
    """Build the tracking note attached to the cell after a rewrite.

    Includes timestamp, reason (which VO segment overshot and by how much),
    and the before/after with word counts so the editor can see the diff
    on hover without opening any other artifact.
    """
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    speech_s = warning.get("speech_s", 0)
    window_s = warning.get("window_s", 0)
    label = warning.get("label", "?")
    return (
        f"Auto-shortened {ts} by shorten_vo.py\n"
        f"Reason: '{label}' VO {speech_s:.2f}s overshot {window_s:.1f}s window\n"
        f"Before ({len(before.split())}w): {before}\n"
        f"After  ({len(after.split())}w): {after}"
    )


def _load_feedback(slug: str) -> str:
    """Read reviewer feedback for this project, empty if absent or malformed.

    Feedback is written by the web UI's /api/feedback endpoint to
    projects/<slug>/artifacts/feedback.json. When present, it's the human's
    note on what's wrong with the last render — exactly the context Claude
    needs to make smarter shortening decisions ("hook VO sped up too much"
    tells the editor to prefer brevity over keeping all the original beats).
    """
    path = REPO / "projects" / slug / "artifacts" / "feedback.json"
    if not path.exists():
        return ""
    try:
        return str(json.loads(path.read_text()).get("feedback", "") or "").strip()
    except (json.JSONDecodeError, OSError):
        return ""


def _claude_shorten(client: anthropic.Anthropic, row: dict, warning: dict,
                    field: str, current: str, target_words: int,
                    reviewer_feedback: str = "") -> str:
    """Trivia-shaped wrapper around the generic claude_shorten() helper."""
    label = warning.get("label", "?")
    speech_s = warning.get("speech_s", 0)
    window_s = warning.get("window_s", 0)
    situation = (
        f"VO segment '{label}' (row mode {row.get('mode')!r}, field {field}) "
        f"overshoots: {speech_s}s of speech into a {window_s}s window"
    )
    return claude_shorten(
        current=current,
        target_words=target_words,
        topic=(row.get("topic") or "").strip(),
        situation=situation,
        brand_tokens=TRIVIA_BRAND_TOKENS,
        reviewer_feedback=reviewer_feedback,
        tone_hints=TRIVIA_TONE,
        client=client,
    )


def main(row_num: int, slug: str) -> int:
    artifacts = REPO / "projects" / slug / "artifacts"
    warnings_path = artifacts / "assembly_warnings.json"
    overrides_path = artifacts / "text_overrides.json"

    if not warnings_path.exists():
        print(f"no assembly_warnings.json at {warnings_path}")
        return 2
    warnings = json.loads(warnings_path.read_text())
    if not warnings:
        print("warnings file is empty; nothing to do")
        return 2

    use_sdk = bool(os.environ.get("ANTHROPIC_API_KEY"))
    use_cli = bool(shutil.which("claude")) if not use_sdk else False
    if not (use_sdk or use_cli):
        print(
            "ERROR: no Claude auth available — set ANTHROPIC_API_KEY or install the `claude` CLI",
            file=sys.stderr,
        )
        return 3
    print(f"  (using Claude via {'SDK' if use_sdk else 'CLI'})")

    # Write-scoped client: we'll both READ the row and WRITE back the
    # shortened text + a tracking note on each rewritten cell.
    try:
        sheets_rw = build_sheets(write=True)
        row = read_post_row(sheets_rw, row_num)
    except Exception as e:
        print(f"ERROR: sheet read failed: {e}", file=sys.stderr)
        return 4

    feedback = _load_feedback(slug)
    if feedback:
        print(f"reviewer feedback in play: {feedback!r}")

    # Collect existing overrides (idempotent: don't re-shorten if already short).
    overrides: dict[str, str] = {}
    if overrides_path.exists():
        try:
            overrides = json.loads(overrides_path.read_text()) or {}
        except json.JSONDecodeError:
            overrides = {}

    client = anthropic.Anthropic() if use_sdk else None
    rewritten_count = 0
    for w in warnings:
        key = (w.get("kind", ""), w.get("label", ""))
        fields = WARNING_TO_FIELDS.get(key)
        if not fields:
            print(f"  skip: no field mapping for {key}")
            continue

        # How many words can fit? The original speech overshoots; aim for the
        # window MINUS a 10% safety buffer so the final result fits at 1.0x tempo.
        window_s = float(w.get("window_s", 0))
        target_speech = max(0.5, window_s * 0.9)
        # Total word budget for the entire warning's VO (e.g. for choices_claim
        # this is question + answer_prompt combined).
        total_target_words = max(3, int(target_speech * WORDS_PER_SECOND))

        # Walk fields in priority order. Each field gets a per-field share of
        # the total budget: max(3, total / N). We shorten the first field
        # whose current length exceeds its share and move on — one warning
        # produces at most one rewrite, then we re-assemble and re-warn if
        # the trim wasn't enough.
        per_field_target = max(3, total_target_words // max(1, len(fields)))

        shortened_this_warning = False
        for primary in fields:
            current = (overrides.get(primary) or row.get(primary) or "").strip()
            if not current:
                print(f"  skip: {primary} is empty in sheet")
                continue
            if len(current.split()) <= per_field_target:
                print(f"  skip: {primary} already at/under its share "
                      f"({len(current.split())} words ≤ {per_field_target})")
                continue
            print(f"  shortening {primary} for warning {key}: "
                  f"target={per_field_target} words (whole-VO budget={total_target_words})")
            print(f"    before: {current!r}")
            try:
                new = _claude_shorten(
                    client, row, w, primary, current, per_field_target,
                    reviewer_feedback=feedback,
                )
            except Exception as e:
                print(f"ERROR: Claude call failed: {e}", file=sys.stderr)
                return 5
            if not new or new == current:
                print(f"    (no change returned)")
                continue
            print(f"    after:  {new!r}")
            overrides[primary] = new
            rewritten_count += 1
            shortened_this_warning = True
            # Push to the sheet too so it stays in sync with the local
            # override and a future render from a fresh project dir starts
            # from the shortened copy. A note records what was changed and
            # why; on hover the editor sees the rewrite without leaving
            # the cell.
            try:
                note = _build_shorten_note(primary, w, current, new)
                cell = _commit_field_to_sheet(sheets_rw, row_num, primary, new, note)
                if cell:
                    print(f"    ✓ committed to sheet ({cell}) with tracking note")
                else:
                    print(f"    (no column mapping for {primary!r}; sheet not updated)")
            except Exception as e:
                # Non-fatal: the local override still drives the next assemble.
                print(f"    WARN: sheet commit failed (override still applies): {e}",
                      file=sys.stderr)
            break
        if not shortened_this_warning:
            print(f"  (warning {key} could not be addressed by shortening any of {fields})")

    if rewritten_count == 0:
        print("\nno fields were rewritten")
        return 2

    artifacts.mkdir(parents=True, exist_ok=True)
    overrides_path.write_text(json.dumps(overrides, indent=2))
    print(f"\n✓ wrote {len(overrides)} override(s) to {overrides_path.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: shorten_vo.py <row> <slug>")
    sys.exit(main(int(sys.argv[1]), sys.argv[2]))
