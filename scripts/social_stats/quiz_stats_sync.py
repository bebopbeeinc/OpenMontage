"""Write TikTok post stats back into the Posts_Quiz sheet + a Quiz_Stats history tab.

Pulls fresh stats via the TikTok Display API (scripts.social_stats.tiktok_api),
matches each video to its Posts_Quiz row by caption (exact once an ID is stored,
then ID-based), writes the latest cumulative numbers next to the row, and appends
a timestamped snapshot to a `Quiz_Stats` tab so growth/deltas can be derived.

Matching: videos with a stored `TikTok Video ID` (col AD) are matched exactly by
id; the rest are matched by normalized-caption similarity (default threshold 0.80
— validated to cleanly separate exact matches from noise). Unmatched videos
(e.g. older posts not in the sheet) are reported, never silently dropped.

Storage:
  - Per Posts_Quiz row (cols AD-AI): TikTok Video ID, Views, Likes, Comments,
    Shares, Stats Updated — the LATEST cumulative snapshot (overwritten).
  - Quiz_Stats tab (append-only): timestamp, slug, video_id, views, likes,
    comments, shares — the cumulative SERIES (for trends/deltas).

Usage (run via the SOPS wrapper so TikTok creds are present):
    # preview (no writes):
    secrets/with-secrets.sh .venv/bin/python -m scripts.social_stats.quiz_stats_sync --account dailytrivia.tc
    # apply:
    secrets/with-secrets.sh .venv/bin/python -m scripts.social_stats.quiz_stats_sync --account dailytrivia.tc --apply
"""
from __future__ import annotations

import argparse
import datetime
import difflib
import re
import sys

from scripts.trivia_quiz.sheets import (
    build_sheets,
    POSTS_TAB,
    QUIZ_SHEET_ID,
    DATA_START,
    POST_FIELDS,
    _col_letter,
)
from scripts.social_stats.tiktok_api import fetch_stats

# Posts_Quiz column indices (0-based) we read.
SLUG_IDX = POST_FIELDS.index("slug")          # C
CAPTION_IDX = POST_FIELDS.index("caption")    # Y

# Stats columns appended after the existing schema (AD onward).
STATS_BASE = len(POST_FIELDS)                 # first free column index → AD
STATS_HEADERS = [
    "TikTok Video ID", "TikTok Published", "TikTok Views", "TikTok Likes",
    "TikTok Comments", "TikTok Shares", "Stats Updated",
]
STATS_END_COL = _col_letter(STATS_BASE + len(STATS_HEADERS) - 1)   # AI
STATS_START_COL = _col_letter(STATS_BASE)                          # AD

HISTORY_TAB = "Quiz_Stats"
HISTORY_HEADER = ["timestamp", "slug", "video_id", "views", "likes", "comments", "shares"]

DEFAULT_THRESHOLD = 0.80


def _norm(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"#\w+", "", s)        # drop hashtags
    s = re.sub(r"[^\w\s]", "", s)     # drop punctuation/emoji
    return re.sub(r"\s+", " ", s).strip()


def _read_grid(svc) -> list[dict]:
    """Return one dict per Posts_Quiz data row, with its real sheet row number."""
    rng = f"{POSTS_TAB}!A{DATA_START}:{STATS_END_COL}"
    rows = svc.spreadsheets().values().get(
        spreadsheetId=QUIZ_SHEET_ID, range=rng,
    ).execute().get("values", [])
    out = []
    for i, row in enumerate(rows):
        rownum = DATA_START + i
        slug = row[SLUG_IDX] if len(row) > SLUG_IDX else ""
        caption = row[CAPTION_IDX] if len(row) > CAPTION_IDX else ""
        stored_id = row[STATS_BASE] if len(row) > STATS_BASE else ""
        if not (slug or caption):
            continue
        out.append({"rownum": rownum, "slug": slug, "caption": caption, "stored_id": stored_id})
    return out


def _match(posts: list[dict], videos: list[dict], threshold: float) -> tuple[list[dict], list[dict]]:
    """Match posts to videos ONE-TO-ONE. Returns (matched, unmatched_videos).

    Builds every candidate (post, video) pair scoring >= threshold (plus exact
    id pairs at 1.0), then assigns greedily highest-score-first so each post and
    each video is used at most once — two near-identical captions can't both
    claim the same video.
    """
    by_id = {v.get("id"): v for v in videos if v.get("id")}
    norm_titles = [(_norm(v.get("title")), v) for v in videos]

    cands = []  # (score, is_id, post_idx, video)
    for pi, p in enumerate(posts):
        if p["stored_id"] and p["stored_id"] in by_id:
            cands.append((1.0, True, pi, by_id[p["stored_id"]]))
            continue
        if not p["caption"]:
            continue
        tgt = _norm(p["caption"])
        for nt, v in norm_titles:
            r = difflib.SequenceMatcher(None, tgt, nt).ratio()
            if r >= threshold:
                cands.append((r, False, pi, v))

    cands.sort(key=lambda c: (c[1], c[0]), reverse=True)   # id matches first, then by score
    used_posts, used_vids, matched = set(), set(), []
    for score, is_id, pi, v in cands:
        vid = v.get("id")
        if pi in used_posts or vid in used_vids:
            continue
        used_posts.add(pi); used_vids.add(vid)
        matched.append({**posts[pi], "video": v, "score": score, "via": "id" if is_id else "caption"})

    unmatched = [v for v in videos if v.get("id") not in used_vids]
    return matched, unmatched


def _ensure_history_tab(svc) -> None:
    titles = [s["properties"]["title"] for s in
              svc.spreadsheets().get(spreadsheetId=QUIZ_SHEET_ID).execute()["sheets"]]
    if HISTORY_TAB in titles:
        return
    svc.spreadsheets().batchUpdate(
        spreadsheetId=QUIZ_SHEET_ID,
        body={"requests": [{"addSheet": {"properties": {"title": HISTORY_TAB}}}]},
    ).execute()
    svc.spreadsheets().values().update(
        spreadsheetId=QUIZ_SHEET_ID, range=f"{HISTORY_TAB}!A1",
        valueInputOption="RAW", body={"values": [HISTORY_HEADER]},
    ).execute()
    print(f"  · created tab '{HISTORY_TAB}'", file=sys.stderr)


def _vstat(v: dict) -> list:
    return [v.get("view_count", 0), v.get("like_count", 0),
            v.get("comment_count", 0), v.get("share_count", 0)]


def _pubdate(v: dict) -> str:
    """The video's TikTok go-live timestamp (create_time, Unix seconds) as ISO.
    Constant per video — written next to the row so the sheet shows when each
    post actually published, distinct from 'Stats Updated' (last sync time)."""
    ct = v.get("create_time")
    if not ct:
        return ""
    return datetime.datetime.fromtimestamp(int(ct)).isoformat(timespec="seconds")


def _ensure_grid_width(svc) -> None:
    """Posts_Quiz ships with 29 columns (A:AC); our stats cols run to AI (35).
    Sheets won't auto-expand on write, so append the missing columns first."""
    needed = STATS_BASE + len(STATS_HEADERS)
    meta = svc.spreadsheets().get(spreadsheetId=QUIZ_SHEET_ID).execute()
    sheet = next(s for s in meta["sheets"] if s["properties"]["title"] == POSTS_TAB)
    cols = sheet["properties"]["gridProperties"].get("columnCount", 0)
    if cols < needed:
        svc.spreadsheets().batchUpdate(
            spreadsheetId=QUIZ_SHEET_ID,
            body={"requests": [{"appendDimension": {
                "sheetId": sheet["properties"]["sheetId"],
                "dimension": "COLUMNS", "length": needed - cols,
            }}]},
        ).execute()
        print(f"  · widened {POSTS_TAB} from {cols} to {needed} columns", file=sys.stderr)


def _write_stats_rows(svc, items: list[tuple[int, dict]], iso: str) -> None:
    """Write the latest snapshot (id + stats + timestamp) into cols AD:AI for
    each (rownum, video). Widens the grid + ensures the header row first."""
    _ensure_grid_width(svc)
    svc.spreadsheets().values().update(
        spreadsheetId=QUIZ_SHEET_ID, range=f"{POSTS_TAB}!{STATS_START_COL}1:{STATS_END_COL}1",
        valueInputOption="RAW", body={"values": [STATS_HEADERS]},
    ).execute()
    data = [{
        "range": f"{POSTS_TAB}!{STATS_START_COL}{rn}:{STATS_END_COL}{rn}",
        "values": [[v.get("id"), _pubdate(v), *_vstat(v), iso]],
    } for rn, v in items]
    svc.spreadsheets().values().batchUpdate(
        spreadsheetId=QUIZ_SHEET_ID, body={"valueInputOption": "RAW", "data": data},
    ).execute()


def _append_history(svc, items: list[tuple[str, dict]], iso: str) -> None:
    """Append one timestamped snapshot row per (slug, video) to Quiz_Stats."""
    _ensure_history_tab(svc)
    rows = [[iso, slug, v.get("id"), *_vstat(v)] for slug, v in items]
    svc.spreadsheets().values().append(
        spreadsheetId=QUIZ_SHEET_ID, range=f"{HISTORY_TAB}!A1",
        valueInputOption="RAW", insertDataOption="INSERT_ROWS", body={"values": rows},
    ).execute()


def link_published_video(account: str, slug: str, threshold: float = DEFAULT_THRESHOLD) -> dict:
    """Find the just-published TikTok video for `slug` by caption and store its
    ID + a baseline snapshot. Called at Mark-as-Published time so the row gets a
    durable, exact video ID (later syncs match by ID, no caption guessing).

    Returns {found, video_id?, score?, reason?}. Raises only on hard failures
    (missing tokens/creds) — the caller treats those as non-fatal.
    """
    data = fetch_stats(account)
    videos = data["videos"]
    iso = datetime.datetime.fromtimestamp(data["fetched_at"]).isoformat(timespec="seconds")

    svc = build_sheets(write=True)
    row = next((p for p in _read_grid(svc) if p["slug"] == slug), None)
    if not row:
        return {"found": False, "reason": f"slug {slug!r} not found in {POSTS_TAB}"}
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
    print(f"\n=== {account}: {len(matched)}/{len(videos)} videos matched to Posts_Quiz rows "
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
    ap.add_argument("--account", required=True, help="TikTok account tag (must have tokens; e.g. dailytrivia.tc)")
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD, help="caption match threshold (0-1)")
    ap.add_argument("--max-videos", type=int, default=20, help="recent videos to pull (API cap 20)")
    ap.add_argument("--apply", action="store_true", help="write to the sheet (default: dry-run preview)")
    args = ap.parse_args()
    return cmd_sync(args.account, args.threshold, args.apply, args.max_videos)


if __name__ == "__main__":
    raise SystemExit(main())
