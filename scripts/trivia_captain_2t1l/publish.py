"""Upload the final render + raw clip to Drive and write links back to the Queue.

NEVER auto-runs — the operator triggers it after reviewing the render.

The Drive folder must be created by a human and shared with the service account
(the SA cannot create Drive files). Paste its ID into DRIVE_FOLDER_ID below.

Usage:
    python scripts/trivia_captain_2t1l/publish.py <slug>
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from scripts.trivia_captain_2t1l import paths, queue_row  # noqa: E402

# The @dailytrivia.tc deliverables folder — the SA already has editor access
# (shared by the sister trivia-short / trivia-quiz pipelines). Override with
# $TRIVIA_2T1L_DRIVE_FOLDER for a dedicated folder.
DRIVE_FOLDER_ID = os.environ.get(
    "TRIVIA_2T1L_DRIVE_FOLDER",
    "1930CVitXd4d6BsZ39EleWyxmtsgaXVGY",
)
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def _clients():
    creds = service_account.Credentials.from_service_account_file(str(queue_row.SA_PATH), scopes=SCOPES)
    return (build("drive", "v3", credentials=creds, cache_discovery=False),
            build("sheets", "v4", credentials=creds, cache_discovery=False))


def _file_id(link: str) -> str | None:
    if not link or "/d/" not in link:
        return None
    return link.split("/d/", 1)[1].split("/", 1)[0]


def _upload_or_replace(drive, local: Path, name: str, existing_link: str) -> tuple[str, str]:
    media = MediaFileUpload(str(local), mimetype="video/mp4", resumable=False)
    fid = _file_id(existing_link)
    if fid:
        f = drive.files().update(fileId=fid, media_body=media,
                                 fields="id,webViewLink").execute()
        return f["webViewLink"], "replaced"
    f = drive.files().create(
        body={"name": name, "parents": [DRIVE_FOLDER_ID]},
        media_body=media, fields="id,webViewLink", supportsAllDrives=True,
    ).execute()
    return f["webViewLink"], "created"


def main(slug: str) -> int:
    if DRIVE_FOLDER_ID.startswith("REPLACE_"):
        sys.exit("Set DRIVE_FOLDER_ID in publish.py (create a Drive folder, share it with the SA).")
    pdir = paths.project_dir(slug)
    render = pdir / "renders" / f"{slug}.mp4"
    clip = pdir / "assets" / "video" / "clip.mp4"
    if not render.exists():
        sys.exit(f"render not found: {render}")

    drive, sheets = _clients()
    wsheets = queue_row.build_sheets(write=True)
    row = queue_row.find_row_by_slug(wsheets, slug)
    if not row:
        sys.exit(f"slug {slug!r} not found in Queue")
    r = queue_row.read_queue_row(wsheets, row)

    render_link, a1 = _upload_or_replace(drive, render, f"{slug}.mp4", r.get("drive_link", ""))
    updates = {"drive_link": render_link, "status": queue_row.STATUS_READY_TO_PUBLISH}
    if clip.exists():
        clip_link, a2 = _upload_or_replace(drive, clip, f"{slug}_clip.mp4", r.get("drive_clip_link", ""))
        updates["drive_clip_link"] = clip_link
        print(f"  clip: {a2} {clip_link}")
    queue_row.update_cells(wsheets, row, **updates)
    print(f"✓ render: {a1} {render_link}")
    print(f"✓ row {row} → Ready to publish")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: publish.py <slug>")
    raise SystemExit(main(sys.argv[1]))
