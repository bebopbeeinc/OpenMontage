"""Write TikTok post stats back into the TriviaReactionQueue sheet + a history tab.

Sister of scripts/social_stats/quiz_stats_sync.py, for the trivia-reaction
pipeline (ellie.travelcrush account). Same contract — pull fresh stats via the
TikTok Display API, match each video to its Queue row by caption (exact once an
ID is stored, then ID-based), write the latest cumulative numbers next to the
row, and append a timestamped snapshot to a `Reaction_Stats` tab so growth /
deltas can be derived.

It reuses the validated matching core (`_norm`, `_match`, `_vstat`) and the
column-header / history-header constants from quiz_stats_sync — only the sheet
adapter (which sheet, which columns, where the header row lives) differs:

  - Target sheet: TriviaReactionQueue (queue_row.QUEUE_SHEET), tab `Queue`.
  - Layout: banner row 1, HEADER row 2, data from row 3 (NOT row 1 like
    Posts_Quiz). slug = col B, caption = col K.
  - Stats columns appended after the 12-col schema (A:L): cols M:R.
  - History tab: `Reaction_Stats` (append-only), same 7-col shape as Quiz_Stats.

Matching: videos with a stored `TikTok Video ID` (col M) are matched exactly by
id; the rest are matched by normalized-caption similarity (default threshold
0.80). Unmatched videos are reported, never silently dropped.

Usage (run via the SOPS wrapper so TikTok creds are present):
    # preview (no writes):
    secrets/with-secrets.sh .venv/bin/python -m scripts.social_stats.reaction_stats_sync --account ellie.travelcrush
    # apply:
    secrets/with-secrets.sh .venv/bin/python -m scripts.social_stats.reaction_stats_sync --account ellie.travelcrush --apply
"""
from __future__ import annotations

import argparse
import datetime
import difflib
import sys

from scripts.trivia_reaction.queue_row import (
    build_sheets,
    QUEUE_SHEET,
    QUEUE_TAB,
    QUEUE_HEADER_ROW,
    QUEUE_DATA_START_ROW,
    QUEUE_ROW_COLUMN_COUNT,
    ROW_KEYS,
    _index_to_column_letter,
)
# Reuse the validated matching core + shared header constants from the quiz sync
# so the two pipelines can't drift apart on how they normalize/match captions.
from scripts.social_stats.quiz_stats_sync import (
    _norm,
    _match,
    _vstat,
    _pubdate,
    STATS_HEADERS,
    HISTORY_HEADER,
    DEFAULT_THRESHOLD,
)
from scripts.social_stats.tiktok_api import fetch_stats

# Queue column indices (0-based) we read.
SLUG_IDX = ROW_KEYS.index("slug")          # B
CAPTION_IDX = ROW_KEYS.index("caption")    # K

# Stats columns appended after the existing 12-col schema (M onward).
STATS_BASE = QUEUE_ROW_COLUMN_COUNT                          # first free col index → M (12)
STATS_START_COL = _index_to_column_letter(STATS_BASE)        # M
STATS_END_COL = _index_to_column_letter(STATS_BASE + len(STATS_HEADERS) - 1)  # R

HISTORY_TAB = "Reaction_Stats"


def _read_grid(svc) -> list[dict]:
    """Return one dict per Queue data row, with its real sheet row number."""
    rng = f"'{QUEUE_TAB}'!A{QUEUE_DATA_START_ROW}:{STATS_END_COL}"
    rows = svc.spreadsheets().values().get(
        spreadsheetId=QUEUE_SHEET, range=rng,
    ).execute().get("values", [])
    out = []
    for i, row in enumerate(rows):
        rownum = QUEUE_DATA_START_ROW + i
        slug = row[SLUG_IDX] if len(row) > SLUG_IDX else ""
        caption = row[CAPTION_IDX] if len(row) > CAPTION_IDX else ""
        stored_id = row[STATS_BASE] if len(row) > STATS_BASE else ""
        if not (slug or caption):
            continue
        out.append({"rownum": rownum, "slug": slug, "caption": caption, "stored_id": stored_id})
    return out


def _ensure_history_tab(svc) -> None:
    titles = [s["properties"]["title"] for s in
              svc.spreadsheets().get(spreadsheetId=QUEUE_SHEET).execute()["sheets"]]
    if HISTORY_TAB in titles:
        return
    svc.spreadsheets().batchUpdate(
        spreadsheetId=QUEUE_SHEET,
        body={"requests": [{"addSheet": {"properties": {"title": HISTORY_TAB}}}]},
    ).execute()
    svc.spreadsheets().values().update(
        spreadsheetId=QUEUE_SHEET, range=f"'{HISTORY_TAB}'!A1",
        valueInputOption="RAW", body={"values": [HISTORY_HEADER]},
    ).execute()
    print(f"  · created tab '{HISTORY_TAB}'", file=sys.stderr)


def _ensure_grid_width(svc) -> None:
    """The Queue ships with 12 columns (A:L); our stats cols run to R (18).
    Sheets won't auto-expand on write, so append the missing columns first."""
    needed = STATS_BASE + len(STATS_HEADERS)
    meta = svc.spreadsheets().get(spreadsheetId=QUEUE_SHEET).execute()
    sheet = next(s for s in meta["sheets"] if s["properties"]["title"] == QUEUE_TAB)
    cols = sheet["properties"]["gridProperties"].get("columnCount", 0)
    if cols < needed:
        svc.spreadsheets().batchUpdate(
            spreadsheetId=QUEUE_SHEET,
            body={"requests": [{"appendDimension": {
                "sheetId": sheet["properties"]["sheetId"],
                "dimension": "COLUMNS", "length": needed - cols,
            }}]},
        ).execute()
        print(f"  · widened {QUEUE_TAB} from {cols} to {needed} columns", file=sys.stderr)


def _write_stats_rows(svc, items: list[tuple[int, dict]], iso: str) -> None:
    """Write the latest snapshot (id + stats + timestamp) into cols M:R for each
    (rownum, video). Widens the grid + ensures the header row (row 2) first."""
    _ensure_grid_width(svc)
    svc.spreadsheets().values().update(
        spreadsheetId=QUEUE_SHEET,
        range=f"'{QUEUE_TAB}'!{STATS_START_COL}{QUEUE_HEADER_ROW}:{STATS_END_COL}{QUEUE_HEADER_ROW}",
        valueInputOption="RAW", body={"values": [STATS_HEADERS]},
    ).execute()
    data = [{
        "range": f"'{QUEUE_TAB}'!{STATS_START_COL}{rn}:{STATS_END_COL}{rn}",
        "values": [[v.get("id"), _pubdate(v), *_vstat(v), iso]],
    } for rn, v in items]
    svc.spreadsheets().values().batchUpdate(
        spreadsheetId=QUEUE_SHEET, body={"valueInputOption": "RAW", "data": data},
    ).execute()


def _append_history(svc, items: list[tuple[str, dict]], iso: str) -> None:
    """Append one timestamped snapshot row per (slug, video) to Reaction_Stats."""
    _ensure_history_tab(svc)
    rows = [[iso, slug, v.get("id"), *_vstat(v)] for slug, v in items]
    svc.spreadsheets().values().append(
        spreadsheetId=QUEUE_SHEET, range=f"'{HISTORY_TAB}'!A1",
        valueInputOption="RAW", insertDataOption="INSERT_ROWS", body={"values": rows},
    ).execute()


def link_published_video(account: str, slug: str, threshold: float = DEFAULT_THRESHOLD) -> dict:
    """Find the just-published TikTok video for `slug` by caption and store its
    ID + a baseline snapshot. Mirrors quiz_stats_sync.link_published_video so the
    reaction publish step can give a row a durable, exact video ID at post time.

    Returns {found, video_id?, score?, reason?}. Raises only on hard failures
    (missing tokens/creds) — the caller treats those as non-fatal.
    """
    data = fetch_stats(account)
    videos = data["videos"]
    iso = datetime.datetime.fromtimestamp(data["fetched_at"]).isoformat(timespec="seconds")

    svc = build_sheets(write=True)
    row = next((p for p in _read_grid(svc) if p["slug"] == slug), None)
    if not row:
        return {"found": False, "reason": f"slug {slug!r} not found in {QUEUE_TAB}"}
    if not row["caption"]:
        return {"found": False, "reason": f"row {slug!r} has no caption to match on"}

    tgt = _norm(row["caption"])
    best_v, best_r = None, 0.0
    for v in videos:
        r = difflib.SequenceMatcher(None, tgt, _norm(v.get("title"))).ratio()
        if r > best_r:
            best_v, best_r = v, r
    if best_v is None or best_r < threshold:
        return {"found": False, "score": round(best_r, 2),
                "reason": "no video caption matched above threshold (not posted yet?)"}

    _write_stats_rows(svc, [(row["rownum"], best_v)], iso)
    _append_history(svc, [(slug, best_v)], iso)
    return {"found": True, "video_id": best_v.get("id"), "score": round(best_r, 2)}


def cmd_sync(account: str, threshold: float, apply: bool, max_videos: int) -> int:
    data = fetch_stats(account, max_videos)
    videos = data["videos"]
    iso = datetime.datetime.fromtimestamp(data["fetched_at"]).isoformat(timespec="seconds")

    svc = build_sheets(write=True)
    posts = _read_grid(svc)
    matched, unmatched = _match(posts, videos, threshold)

    # Report.
    print(f"\n=== {account}: {len(matched)}/{len(videos)} videos matched to Queue rows "
          f"({'APPLY' if apply else 'dry-run'}) ===", file=sys.stderr)
    print(f"{'row':>4} {'via':7} {'score':>5}  {'views':>6} {'likes':>5} {'cmt':>4} {'shr':>3}  slug")
    print("-" * 64)
    for m in sorted(matched, key=lambda m: m["rownum"]):
        vs = _vstat(m["video"])
        print(f"{m['rownum']:>4} {m['via']:7} {m['score']:5.2f}  "
              f"{vs[0]:>6} {vs[1]:>5} {vs[2]:>4} {vs[3]:>3}  {m['slug']}")
    if unmatched:
        print(f"\n  {len(unmatched)} video(s) NOT in the sheet (skipped):", file=sys.stderr)
        for v in unmatched:
            print(f"    · {(v.get('title') or '')[:50]!r}  ({v.get('view_count',0)} views)", file=sys.stderr)

    if not apply:
        print("\n(dry-run — nothing written. Re-run with --apply to write.)", file=sys.stderr)
        return 0
    if not matched:
        print("\n(no matches — nothing to write.)", file=sys.stderr)
        return 0

    _write_stats_rows(svc, [(m["rownum"], m["video"]) for m in matched], iso)
    _append_history(svc, [(m["slug"], m["video"]) for m in matched], iso)
    print(f"\n✓ wrote latest stats to {len(matched)} row(s) and appended "
          f"{len(matched)} snapshot(s) to '{HISTORY_TAB}'.", file=sys.stderr)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--account", default="ellie.travelcrush",
                    help="TikTok account tag (must have tokens; e.g. ellie.travelcrush)")
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD, help="caption match threshold (0-1)")
    ap.add_argument("--max-videos", type=int, default=20, help="recent videos to pull (API cap 20)")
    ap.add_argument("--apply", action="store_true", help="write to the sheet (default: dry-run preview)")
    args = ap.parse_args()
    return cmd_sync(args.account, args.threshold, args.apply, args.max_videos)


if __name__ == "__main__":
    raise SystemExit(main())
