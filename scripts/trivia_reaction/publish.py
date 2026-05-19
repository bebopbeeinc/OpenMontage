#!/usr/bin/env python
"""Upload trivia-reaction deliverables to the ellie.travelcrush Drive folder
and write back to the TriviaReactionQueue row.

Two files per slug:
  1. <slug>.mp4       — captioned final render (the posted version). The
                        canonical Drive file. webViewLink → Queue!I.
  2. <slug>_clip.mp4  — raw Seedance avatar clip (no captions). Secondary
                        deliverable for reference / re-edits.
                        webViewLink → Queue!L.

First run: both files are created. Subsequent runs (re-renders) update the
file content in place so both links stay stable, and Queue!C stays at
"Ready to publish".

Usage:
    python scripts/trivia_reaction/publish.py <slug>
"""
from __future__ import annotations

import sys
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from scripts.trivia_reaction import queue_row  # noqa: E402
from scripts.trivia_reaction.paths import project_dir  # noqa: E402

SA_PATH = Path.home() / ".google" / "claude-sheets-sa.json"
DRIVE_FOLDER_ID = "1uDneOUH21xUqh4oifQTh5sqgIVk6EREg"   # ellie.travelcrush
LIBRARY_DIR = REPO / "scripts" / "trivia_reaction" / "library" / "clips"
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def build_clients():
    creds = service_account.Credentials.from_service_account_file(
        str(SA_PATH), scopes=SCOPES,
    )
    return (
        build("drive", "v3", credentials=creds, cache_discovery=False),
        build("sheets", "v4", credentials=creds, cache_discovery=False),
    )


def file_id_from_link(link: str) -> str | None:
    marker = "/d/"
    if not link or marker not in link:
        return None
    return link.split(marker, 1)[1].split("/", 1)[0]


def find_row(sheets, slug: str) -> tuple[int, dict]:
    rows = queue_row.read_queue_bulk(sheets)
    for r in rows:
        if r.get("slug") == slug:
            return r["row"], r
    raise SystemExit(f"no Queue row with slug={slug!r}; run select_row.py first")


def _upload_or_replace(
    drive, local_path: Path, drive_name: str, existing_link: str,
) -> tuple[str, str]:
    """Upload `local_path` to the ellie.travelcrush Drive folder as
    `drive_name`. If `existing_link` resolves to a Drive file id, replace
    that file's content in place (preserves the link). Returns
    (webViewLink, action) where action is "created" or "replaced"."""
    media = MediaFileUpload(str(local_path), mimetype="video/mp4", resumable=True)
    fid = file_id_from_link(existing_link)
    if fid:
        f = drive.files().update(
            fileId=fid, media_body=media,
            fields="id,name,modifiedTime,webViewLink",
            supportsAllDrives=True,
        ).execute()
        return f["webViewLink"], "replaced"
    f = drive.files().create(
        body={"name": drive_name, "parents": [DRIVE_FOLDER_ID]},
        media_body=media,
        fields="id,name,webViewLink",
        supportsAllDrives=True,
    ).execute()
    return f["webViewLink"], "created"


def main(slug: str) -> int:
    render = project_dir(slug) / "renders" / f"{slug}.mp4"
    clip = LIBRARY_DIR / f"{slug}.mp4"
    if not render.exists():
        sys.exit(f"render not found at {render}")
    if not clip.exists():
        sys.exit(f"raw clip not found at {clip} (run Generate first)")
    print(f"using render: {render.relative_to(REPO)}")
    print(f"using clip:   {clip.relative_to(REPO)}")

    drive, sheets = build_clients()
    sheet_row, qrow = find_row(sheets, slug)

    render_link, render_action = _upload_or_replace(
        drive, render, f"{slug}.mp4",
        existing_link=(qrow.get("drive_link") or "").strip(),
    )
    print(f"✓ render {render_action}: {render_link}")

    clip_link, clip_action = _upload_or_replace(
        drive, clip, f"{slug}_clip.mp4",
        existing_link=(qrow.get("drive_clip_link") or "").strip(),
    )
    print(f"✓ clip   {clip_action}: {clip_link}")

    queue_row.update_cells(
        sheets, sheet_row,
        status=queue_row.STATUS_READY_TO_PUBLISH,
        drive_link=render_link,
        drive_clip_link=clip_link,
    )
    print(f"✓ Queue row {sheet_row}: Status={queue_row.STATUS_READY_TO_PUBLISH}, "
          f"both Drive links set")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: publish.py <project-slug>")
    raise SystemExit(main(sys.argv[1]))
