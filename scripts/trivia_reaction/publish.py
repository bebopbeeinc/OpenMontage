#!/usr/bin/env python
"""Upload a rendered trivia-reaction reel to the ellie.travelcrush Drive folder
and write back to the TriviaReactionQueue row.

First run for a slug: uploads as a new file, writes the webViewLink to
Queue!J, and sets Queue!C = "Ready to publish".

Subsequent runs (re-renders) replace the Drive file content in place so the
link stays stable, and flip Queue!C back to "Ready to publish".

Usage:
    python scripts/trivia_reaction/publish.py <slug>

Example:
    python scripts/trivia_reaction/publish.py guinea-pigs-switzerland
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


def main(slug: str) -> int:
    renders_dir = project_dir(slug) / "renders"
    # trivia-reaction writes <slug>.mp4 (self-identifying name) into
    # the per-pipeline namespaced /projects/trivia-reaction/<slug>/ tree.
    render = renders_dir / f"{slug}.mp4"
    if not render.exists():
        render = None
    if render is None:
        sys.exit(f"render not found in {renders_dir}")
    print(f"using render: {render.relative_to(REPO)}")

    drive, sheets = build_clients()
    sheet_row, qrow = find_row(sheets, slug)
    existing_link = (qrow.get("drive_link") or "").strip()
    fid = file_id_from_link(existing_link)

    media = MediaFileUpload(str(render), mimetype="video/mp4", resumable=True)

    if fid:
        f = drive.files().update(
            fileId=fid, media_body=media,
            fields="id,name,modifiedTime,webViewLink",
            supportsAllDrives=True,
        ).execute()
        print(f"✓ replaced Drive file {fid} @ {f['modifiedTime']}")
        link = f["webViewLink"]
    else:
        f = drive.files().create(
            body={"name": f"{slug}.mp4", "parents": [DRIVE_FOLDER_ID]},
            media_body=media,
            fields="id,name,webViewLink",
            supportsAllDrives=True,
        ).execute()
        link = f["webViewLink"]
        print(f"✓ uploaded new Drive file: {f['id']}")

    queue_row.update_cells(
        sheets, sheet_row,
        status=queue_row.STATUS_READY_TO_PUBLISH,
        drive_link=link,
    )
    print(f"✓ Queue row {sheet_row}: Status={queue_row.STATUS_READY_TO_PUBLISH}, "
          f"Drive Link set")
    print(f"  link: {link}")
    print(f"  render available at: {render.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: publish.py <project-slug>")
    raise SystemExit(main(sys.argv[1]))
