#!/usr/bin/env python
"""Upload a rendered trivia video to Drive and update the Post Calendar row.

First run for a row: uploads as a new file, writes the link to column M,
and sets column L = "Ready to publish".

Subsequent runs (re-renders) replace the Drive file content in place so the
link is stable, and flip column L back to "Ready to publish".

Usage:
    python scripts/trivia/publish.py <project-slug> <row>

Example:
    python scripts/trivia/publish.py turtles-breathing-butts 6
"""
from __future__ import annotations

import sys
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

REPO = Path(__file__).resolve().parents[2]
SA_PATH = Path.home() / ".google" / "claude-sheets-sa.json"
SHEET_ID = "1EzucrS6yUPfodtt7WVuvW3PjZ1yhWUgfWUowPkMP6Eg"
DRIVE_FOLDER_ID = "1930CVitXd4d6BsZ39EleWyxmtsgaXVGY"
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def build_clients():
    creds = service_account.Credentials.from_service_account_file(
        str(SA_PATH), scopes=SCOPES,
    )
    return (
        build("drive", "v3", credentials=creds),
        build("sheets", "v4", credentials=creds),
    )


def existing_link(sheets, row: int) -> str | None:
    r = sheets.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range=f"Posts!M{row}",
    ).execute()
    vals = r.get("values", [])
    return vals[0][0] if vals and vals[0] else None


def file_id_from_link(link: str) -> str | None:
    # https://drive.google.com/file/d/<id>/view?...
    marker = "/d/"
    if marker not in link:
        return None
    return link.split(marker, 1)[1].split("/", 1)[0]


def main(slug: str, row: int) -> None:
    # Prefer the modular pipeline output; fall back to the legacy filename.
    renders_dir = REPO / "projects" / slug / "renders"
    render = next(
        (renders_dir / n for n in ("final_modular.mp4", "final_with_bg.mp4")
         if (renders_dir / n).exists()),
        None,
    )
    if render is None:
        sys.exit(f"render not found in {renders_dir}")
    print(f"using render: {render.name}")

    drive, sheets = build_clients()
    media = MediaFileUpload(str(render), mimetype="video/mp4", resumable=True)

    link = existing_link(sheets, row)
    fid = file_id_from_link(link) if link else None

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

    data = [
        {"range": f"Posts!L{row}", "values": [["Ready to publish"]]},
        {"range": f"Posts!M{row}", "values": [[link]]},
    ]
    resp = sheets.spreadsheets().values().batchUpdate(
        spreadsheetId=SHEET_ID,
        body={"valueInputOption": "USER_ENTERED", "data": data},
    ).execute()
    print(f"✓ sheet row {row}: L=Ready to publish, M=<link> "
          f"({resp.get('totalUpdatedCells')} cells)")
    print(f"  link: {link}")

    # The rendered file already lives at projects/<slug>/renders/ — no
    # external copy needed. (Previously dropped a duplicate to ~/Downloads;
    # removed as part of the all-paths-repo-local cleanup.)
    print(f"  render available at: {render.relative_to(REPO)}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: publish.py <project-slug> <row>")
    main(sys.argv[1], int(sys.argv[2]))
