"""Publish stage for trivia-quiz — Drive upload + sheet writeback + caption output.

What it does:
    1. Uploads projects/trivia-quiz/<slug>/renders/final_quiz.mp4 to the Drive
       folder shared with claude-sheets-config@travel-crush.iam.gserviceaccount.com.
       If the slug's Posts_Quiz row already has a Final Video Link, the file
       is REPLACED in place so the link stays stable across re-renders.
    2. Writes Final Status = "Ready to publish" and Final Video Link back to
       the Posts_Quiz row.
    3. Prints the caption + pinned comment templates verbatim for manual
       copy-paste at posting time (TikTok/IG APIs aren't wired yet).

Per user memory `feedback_trivia_local_approval`: the build script never
auto-publishes — this script must be invoked explicitly by the human.

Usage:
    python -m scripts.trivia_quiz.publish --slug <slug>
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

# Auto-load .env so SA_PATH / TRIVIA_QUIZ_SHEET env overrides work
_REPO = Path(__file__).resolve().parents[2]
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(_REPO / ".env")
except ImportError:
    pass

from google.oauth2 import service_account
from googleapiclient.discovery import build as build_api
from googleapiclient.http import MediaFileUpload

from scripts.trivia_quiz.sheets import (
    QUIZ_SHEET_ID,
    POSTS_TAB,
    POST_FIELDS,
    DATA_START,
    SA_PATH,
    READWRITE_SCOPES,
    read_posts_bulk,
    write_post_field,
    _col_letter,
)

# Same Drive folder trivia-short uses — operationally tidy, the service
# account already has editor access. Override with $TRIVIA_QUIZ_DRIVE_FOLDER
# if you want a separate folder.
DRIVE_FOLDER_ID = os.environ.get(
    "TRIVIA_QUIZ_DRIVE_FOLDER",
    "1930CVitXd4d6BsZ39EleWyxmtsgaXVGY",
)

DRIVE_SCOPES = READWRITE_SCOPES + ["https://www.googleapis.com/auth/drive"]


def build_clients():
    creds = service_account.Credentials.from_service_account_file(
        str(SA_PATH), scopes=DRIVE_SCOPES,
    )
    return (
        build_api("drive", "v3", credentials=creds, cache_discovery=False),
        build_api("sheets", "v4", credentials=creds, cache_discovery=False),
    )


def _file_id_from_link(link: str) -> str | None:
    """Extract a Drive file ID from a webViewLink (handles common URL shapes)."""
    if not link:
        return None
    m = re.search(r"/d/([a-zA-Z0-9_-]+)", link)
    if m:
        return m.group(1)
    m = re.search(r"[?&]id=([a-zA-Z0-9_-]+)", link)
    return m.group(1) if m else None


def _existing_link_and_status(sheets, slug: str) -> tuple[str, str]:
    """Read the current final_video_link + final_status for a slug."""
    posts = read_posts_bulk(sheets)
    for p in posts:
        if p["slug"].strip() == slug.strip():
            return (
                (p.get("final_video_link") or "").strip(),
                (p.get("final_status") or "").strip(),
            )
    return ("", "")


def upload_or_replace(drive, sheets, slug: str, render: Path) -> str:
    """Upload render to Drive, replacing an existing file if its link is
    already set on the Posts_Quiz row. Returns the webViewLink."""
    existing_link, _ = _existing_link_and_status(sheets, slug)
    fid = _file_id_from_link(existing_link)

    media = MediaFileUpload(str(render), mimetype="video/mp4", resumable=True)

    if fid:
        f = drive.files().update(
            fileId=fid, media_body=media,
            fields="id,name,modifiedTime,webViewLink",
            supportsAllDrives=True,
        ).execute()
        print(f"  ✓ replaced Drive file {fid} @ {f.get('modifiedTime')}")
        return existing_link

    f = drive.files().create(
        body={"name": f"{slug}.mp4", "parents": [DRIVE_FOLDER_ID]},
        media_body=media,
        fields="id,name,webViewLink",
        supportsAllDrives=True,
    ).execute()
    link = f["webViewLink"]
    print(f"  ✓ created Drive file {f['id']}")
    print(f"  ✓ {link}")
    return link


def fetch_caption_blocks(sheets, slug: str) -> dict:
    """Read the caption + pinned comment fields for the slug from Posts_Quiz."""
    posts = read_posts_bulk(sheets)
    for p in posts:
        if p["slug"].strip() == slug.strip():
            return {
                "caption":        (p.get("caption") or "").strip(),
                "pinned_comment": (p.get("pinned_comment") or "").strip(),
            }
    raise SystemExit(f"✗ slug {slug!r} not found in {POSTS_TAB}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--slug", required=True, help="kebab-case project slug")
    args = ap.parse_args()

    render = _REPO / "projects" / "trivia-quiz" / args.slug / "renders" / "final_quiz.mp4"
    if not render.exists():
        sys.exit(f"✗ render missing: {render}")

    print(f"→ Publishing {args.slug}")
    print(f"  render:  {render.relative_to(_REPO)} ({render.stat().st_size // 1024} KB)")
    print(f"  drive:   folder {DRIVE_FOLDER_ID}")
    print(f"  sheet:   {QUIZ_SHEET_ID} → {POSTS_TAB}")
    print()

    drive, sheets = build_clients()

    # 1. Drive upload (replace-in-place if link already set)
    link = upload_or_replace(drive, sheets, args.slug, render)

    # 2. Sheet writeback — Final Status + Final Video Link
    write_post_field(sheets, args.slug, "final_status", "Ready to publish")
    write_post_field(sheets, args.slug, "final_video_link", link)
    print(f"  ✓ wrote final_status='Ready to publish' + final_video_link to row")
    print()

    # 3. Caption + pinned comment templates — printed verbatim for copy-paste.
    blocks = fetch_caption_blocks(sheets, args.slug)
    print("─" * 60)
    print("CAPTION (TikTok + Instagram) — paste verbatim at posting time:")
    print("─" * 60)
    print(blocks["caption"] or "(none — fill in the Caption column on the sheet)")
    print()
    print("─" * 60)
    print("PINNED COMMENT — post under your own video after publishing:")
    print("─" * 60)
    print(blocks["pinned_comment"] or "(none — fill in the Pinned Comment column on the sheet)")
    print()
    print(f"→ Drive link: {link}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
