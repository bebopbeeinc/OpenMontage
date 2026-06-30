#!/usr/bin/env python
"""Schedule an ellie.travelcrush reel to TikTok + Instagram via Buffer.

Flow per slug:
  1. Read the Queue row → caption (Queue!K) + drive_link (Queue!I).
  2. Make the Drive render public-by-link and build a DIRECT-download URL
     (Buffer fetches the media itself; it does not host video).
  3. createPost on each target channel (TikTok + Instagram) with that media
     URL, the caption, and — if --due is given — a scheduled time.

Requires that Publish has already run (the row must carry a Drive link). This
keeps Drive as the archive/source-of-truth for the posted file; Buffer just
references it.

Usage:
    # Schedule both channels for a specific UTC time:
    python -m scripts.trivia_reaction.buffer_push <slug> --due 2026-06-25T15:00:00Z

    # Add to Buffer's next queue slot (no fixed time):
    python -m scripts.trivia_reaction.buffer_push <slug>

    # Only one channel:
    python -m scripts.trivia_reaction.buffer_push <slug> --channels tiktok
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from scripts.trivia_reaction import buffer_api, queue_row  # noqa: E402

SA_PATH = Path.home() / ".google" / "claude-sheets-sa.json"
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]


def _build_drive():
    creds = service_account.Credentials.from_service_account_file(
        str(SA_PATH), scopes=DRIVE_SCOPES,
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _file_id_from_link(link: str) -> str | None:
    marker = "/d/"
    if not link or marker not in link:
        return None
    return link.split(marker, 1)[1].split("/", 1)[0]


def make_public_direct_url(drive, file_id: str) -> str:
    """Grant anyone-with-link reader access (idempotent) and return a direct
    download URL Buffer can fetch. Drive's `uc?export=download` serves the raw
    bytes for small files (short vertical reels qualify)."""
    try:
        drive.permissions().create(
            fileId=file_id,
            body={"type": "anyone", "role": "reader"},
            supportsAllDrives=True,
        ).execute()
    except HttpError as e:
        # A duplicate "anyone" permission is fine — the file is already public.
        if e.resp.status not in (400, 409):
            raise
    return f"https://drive.google.com/uc?export=download&id={file_id}"


def _normalize_due(due: str | None) -> str | None:
    """Accept ISO-8601 with 'Z' or an offset; return Buffer-friendly UTC
    (millisecond, trailing Z). None → addToQueue mode."""
    if not due:
        return None
    s = due.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        raise SystemExit(
            f"✗ bad --due {due!r}; use ISO-8601 e.g. 2026-06-25T15:00:00Z"
        )
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    if dt < datetime.now(timezone.utc):
        raise SystemExit(f"✗ --due {due!r} is in the past.")
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def main(slug: str, due: str | None, services: list[str], draft: bool = False) -> int:
    due_iso = _normalize_due(due)

    sheets = queue_row.build_sheets(write=False)
    row = next(
        (r for r in queue_row.read_queue_bulk(sheets) if r.get("slug") == slug),
        None,
    )
    if not row:
        sys.exit(f"✗ no Queue row with slug={slug!r}")
    caption = (row.get("caption") or "").strip()
    if not caption:
        sys.exit(f"✗ Queue row for {slug} has no caption (Queue!K) — write it first")

    print(f"slug:    {slug}")
    print(f"caption: {caption[:80]}{'…' if len(caption) > 80 else ''}")
    print(f"due:     {due_iso or '(next queue slot)'}")
    print(f"mode:    {'DRAFT (no auto-publish)' if draft else 'scheduled (auto-publish)'}")

    drive = _build_drive()

    chans = buffer_api.resolve_channels(services=services)
    missing = [s for s in services if s not in chans]
    if missing:
        sys.exit(
            f"✗ Buffer channel(s) not connected: {', '.join(missing)}. "
            "Connect them in Buffer, then run "
            "`python -m scripts.trivia_reaction.buffer_api channels --refresh`."
        )

    # Per-channel posting strategy:
    #   Instagram → captioned render (Queue!I), AUTOMATIC (Buffer direct-publishes).
    #   TikTok    → no-caption clip (Queue!L), NOTIFICATION — Buffer pings your
    #               phone and you finish the post in the TikTok app, where you
    #               enable TikTok's native auto-captions. (Direct Post never
    #               adds captions, so a no-caption clip would publish bare.)
    # field, label, schedulingType. Unknown services default to captioned+automatic.
    SERVICE_PLAN = {
        buffer_api.SERVICE_INSTAGRAM: ("drive_link", "captioned", "automatic"),
        buffer_api.SERVICE_TIKTOK: ("drive_clip_link", "no-caption", "notification"),
    }
    url_cache: dict[str, str] = {}  # file_id → public direct url (dedupe perms)

    failures = 0
    for svc in services:
        ch = chans[svc]
        field, label, sched = SERVICE_PLAN.get(svc, ("drive_link", "captioned", "automatic"))
        link = (row.get(field) or "").strip()
        if not link:
            failures += 1
            print(f"✗ {svc:10} no {label} clip on the row (Queue!{field}) — "
                  "run Publish first", file=sys.stderr)
            continue
        file_id = _file_id_from_link(link)
        if not file_id:
            failures += 1
            print(f"✗ {svc:10} couldn't parse a Drive file id from {link!r}",
                  file=sys.stderr)
            continue
        if file_id not in url_cache:
            url_cache[file_id] = make_public_direct_url(drive, file_id)
        video_url = url_cache[file_id]
        print(f"  {svc:10} ← {label} clip [{sched}]: {video_url}")
        res = buffer_api.create_video_post(
            channel_id=ch["id"],
            text=caption,
            video_url=video_url,
            service=svc,
            due_at=due_iso,
            draft=draft,
            scheduling_type=sched,
        )
        if res["ok"]:
            post = res["post"]
            note = " (finish in TikTok app to add captions)" if sched == "notification" else ""
            print(f"✓ {svc:10} scheduled [{sched}] — post {post.get('id')} "
                  f"status={post.get('status')} dueAt={post.get('dueAt')}{note}")
        else:
            failures += 1
            print(f"✗ {svc:10} failed: {res['error']}", file=sys.stderr)

    if failures:
        return 1
    print(f"✓ pushed {slug} to Buffer ({', '.join(services)}). "
          "Review/confirm in the Buffer app before it publishes.")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("slug", help="project slug (must already be Published to Drive)")
    ap.add_argument(
        "--due", default=None,
        help="ISO-8601 UTC schedule time (e.g. 2026-06-25T15:00:00Z); "
             "omit to add to Buffer's next queue slot",
    )
    ap.add_argument(
        "--channels", default="tiktok,instagram",
        help="comma-separated Buffer services to post to (default: tiktok,instagram)",
    )
    ap.add_argument(
        "--draft", action="store_true",
        help="save as a Buffer draft (no auto-publish) — you send it from the "
             "Buffer app. Use for testing or a manual review gate.",
    )
    args = ap.parse_args()
    svcs = [s.strip().lower() for s in args.channels.split(",") if s.strip()]
    raise SystemExit(main(args.slug, args.due, svcs, draft=args.draft))
