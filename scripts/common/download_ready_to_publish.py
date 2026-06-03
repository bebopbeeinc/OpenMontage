#!/usr/bin/env python
"""Download "Ready to publish" videos from Drive into the MuMuPlayer
Download/ subfolders so the TikTok app on the emulator can upload them.

Mirror image of the per-pipeline publish.py uploaders: those push the local
renders TO Drive + flip the sheet status; this pulls the published Drive
files DOWN into the account-matching local subfolder.

Two accounts, each backed by its own pipeline + queue sheet:
  dailytrivia.tc   <- trivia-quiz     Posts_Quiz tab, Final Status / Final Video Link
  ellie.travelcrush <- trivia-reaction Queue tab, Status / Drive Link (+ Drive Clip)

Idempotent: if a local file already matches the Drive file's byte size it's
skipped; otherwise it's (re)downloaded (Drive may carry a re-render).

Usage:
    python -m scripts.common.download_ready_to_publish [dailytrivia|ellie|both]
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(REPO / ".env")
except ImportError:
    pass

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

from scripts.trivia_reaction import queue_row
from scripts.trivia_quiz import sheets as quiz_sheets

DOWNLOAD_ROOT = Path(
    "/Users/bbb/Library/Application Support/com.netease.mumu.nemux-global/"
    "MuMuPlayerShared.localized/Download"
)
DEST = {
    "dailytrivia": DOWNLOAD_ROOT / "dailytrivia.tc",
    "ellie": DOWNLOAD_ROOT / "ellie.travelcrush",
}

DRIVE_SCOPE = ["https://www.googleapis.com/auth/drive"]


def _file_id_from_link(link: str) -> str | None:
    if not link:
        return None
    link = link.strip()
    if "/d/" in link:
        return link.split("/d/", 1)[1].split("/", 1)[0]
    if "id=" in link:
        return link.split("id=", 1)[1].split("&", 1)[0]
    return None


def build_drive():
    creds = service_account.Credentials.from_service_account_file(
        str(queue_row.SA_PATH), scopes=DRIVE_SCOPE,
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _download(drive, file_id: str, dest: Path) -> str:
    """Download Drive file_id -> dest. Returns one of created/replaced/skipped."""
    meta = drive.files().get(
        fileId=file_id, fields="size,name", supportsAllDrives=True,
    ).execute()
    remote_size = int(meta.get("size") or 0)

    if dest.exists() and remote_size and dest.stat().st_size == remote_size:
        return "skipped"
    action = "replaced" if dest.exists() else "created"

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with io.FileIO(tmp, "wb") as fh:
        req = drive.files().get_media(fileId=file_id, supportsAllDrives=True)
        dl = MediaIoBaseDownload(fh, req, chunksize=8 * 1024 * 1024)
        done = False
        while not done:
            _, done = dl.next_chunk()
    tmp.replace(dest)
    return action


def _pull(drive, label: str, file_id: str, dest: Path) -> None:
    if not file_id:
        print(f"    ! {label}: no Drive link — skipping")
        return
    action = _download(drive, file_id, dest)
    glyph = {"created": "✓", "replaced": "↻", "skipped": "·"}[action]
    size_kb = dest.stat().st_size // 1024
    print(f"    {glyph} {label} {action}: {dest.name} ({size_kb} KB)")


def do_ellie(drive) -> int:
    sheets = queue_row.build_sheets(write=False)
    rows = queue_row.read_queue_bulk(sheets)
    ready = [r for r in rows
             if (r.get("status") or "").strip() == queue_row.STATUS_READY_TO_PUBLISH]
    dest_dir = DEST["ellie"]
    print(f"\n=== ellie.travelcrush  (trivia-reaction)  →  {dest_dir} ===")
    print(f"{len(ready)} row(s) at status='{queue_row.STATUS_READY_TO_PUBLISH}'")
    for r in ready:
        slug = (r.get("slug") or "").strip()
        if not slug:
            continue
        print(f"  {slug}")
        _pull(drive, "render", _file_id_from_link(r.get("drive_link") or ""),
              dest_dir / f"{slug}.mp4")
        _pull(drive, "clip  ", _file_id_from_link(r.get("drive_clip_link") or ""),
              dest_dir / f"{slug}_clip.mp4")
    return len(ready)


def do_dailytrivia(drive) -> int:
    sheets = quiz_sheets.build_sheets()
    posts = quiz_sheets.read_posts_bulk(sheets)
    ready = [p for p in posts
             if (p.get("final_status") or "").strip() == "Ready to publish"]
    dest_dir = DEST["dailytrivia"]
    print(f"\n=== dailytrivia.tc  (trivia-quiz / Posts_Quiz)  →  {dest_dir} ===")
    print(f"{len(ready)} row(s) at Final Status='Ready to publish'")
    for p in ready:
        slug = (p.get("slug") or "").strip()
        if not slug:
            continue
        print(f"  {slug}")
        _pull(drive, "render", _file_id_from_link(p.get("final_video_link") or ""),
              dest_dir / f"{slug}.mp4")
    return len(ready)


def main(which: str) -> int:
    drive = build_drive()
    if which in ("dailytrivia", "both"):
        do_dailytrivia(drive)
    if which in ("ellie", "both"):
        do_ellie(drive)
    print("\n✓ done")
    return 0


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "both"
    if arg not in ("dailytrivia", "ellie", "both"):
        raise SystemExit("usage: download_ready_to_publish.py [dailytrivia|ellie|both]")
    raise SystemExit(main(arg))
