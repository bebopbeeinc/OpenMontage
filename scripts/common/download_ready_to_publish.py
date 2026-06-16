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


# ---------------------------------------------------------------------------
# Local reconcile — pull published assets back into the per-project paths the
# web UIs probe (NOT the MuMuPlayer upload folders that do_ellie/do_dailytrivia
# target). Used by the web servers to self-heal rows that were published on
# another machine: status 'Ready to publish' + a Drive link but no local
# render, which otherwise shows as 'Generate' with no Review button.
# ---------------------------------------------------------------------------
def pull_published_assets(render_link, clip_link, render_dest, clip_dest,
                          *, drive=None, log=print):
    """Download a published render (+ raw clip) from Drive into local project
    paths. Skips any asset that already exists locally or lacks a Drive link.
    Idempotent (size-checked by _download). Returns the labels actually pulled."""
    pulled = []
    for label, link, dest in (
        ("render", render_link, render_dest),
        ("clip", clip_link, clip_dest),
    ):
        if dest is None or dest.exists():
            continue
        fid = _file_id_from_link((link or "").strip())
        if not fid:
            continue
        if drive is None:
            drive = build_drive()
        action = _download(drive, fid, dest)
        log(f"[pull] {label} {action}: {dest}")
        pulled.append(label)
    return pulled


class DriveReconciler:
    """Page-access-triggered reconcile of local assets from Drive.

    Call `kick(rows)` from a sub-app's /api/rows handler. For each row at
    `ready_status` whose local render is missing while a Drive link exists, it
    spawns a non-blocking background pull (render + raw clip) into the dests
    returned by `dest_for(slug) -> (render_dest, clip_dest | None)`. In-flight
    pulls are deduped and failures back off for `cooldown_s`, so the frequent
    /api/rows polls can't storm Drive. Safe to call on every request."""

    def __init__(self, dest_for, *, ready_status="Ready to publish",
                 cooldown_s=300, log=print,
                 status_of=lambda r: (r.get("status") or "").strip(),
                 drive_link_of=lambda r: (r.get("drive_link") or "").strip(),
                 clip_link_of=lambda r: (r.get("drive_clip_link") or "").strip(),
                 render_exists_of=lambda r: bool((r.get("files") or {}).get("render_exists"))):
        # Accessors default to the reaction-family row shape (status / drive_link
        # / files.render_exists / drive_clip_link). Pipelines that name those
        # fields differently (e.g. trivia-short's final_status / final_video_link
        # and a top-level render_exists) pass overrides.
        self.dest_for = dest_for
        self.ready_status = ready_status
        self.cooldown_s = cooldown_s
        self.log = log
        self.status_of = status_of
        self.drive_link_of = drive_link_of
        self.clip_link_of = clip_link_of
        self.render_exists_of = render_exists_of
        self._inflight: set[str] = set()
        self._cooldown: dict[str, float] = {}

    def kick(self, rows) -> None:
        import asyncio
        import time
        now = time.monotonic()
        for r in rows:
            if self.status_of(r) != self.ready_status:
                continue
            slug = (r.get("slug") or "").strip()
            if not slug or slug in self._inflight:
                continue
            if self.render_exists_of(r):
                continue
            link = self.drive_link_of(r)
            if not link:
                continue
            if self._cooldown.get(slug, 0.0) > now:
                continue
            self._inflight.add(slug)
            asyncio.create_task(self._pull_one(slug, link, self.clip_link_of(r)))

    async def _pull_one(self, slug, render_link, clip_link) -> None:
        import asyncio
        import time
        try:
            render_dest, clip_dest = self.dest_for(slug)
            pulled = await asyncio.to_thread(
                pull_published_assets, render_link, clip_link,
                render_dest, clip_dest, log=self.log,
            )
            if pulled:
                self.log(f"[auto-reconcile] {slug}: pulled {pulled} from Drive")
            self._cooldown.pop(slug, None)
        except Exception as e:  # noqa: BLE001
            self._cooldown[slug] = time.monotonic() + self.cooldown_s
            self.log(f"[auto-reconcile] {slug}: failed ({e}); backing off {self.cooldown_s}s")
        finally:
            self._inflight.discard(slug)


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
    # Angle changed: dailytrivia.tc now pulls from the Posts_2T1L tab
    # (Captain's Two Truths & a Lie) instead of trivia-quiz / Posts_Quiz.
    # Same spreadsheet + service-account auth as trivia-quiz; row 1 is a
    # banner, row 2 the header, data from row 3. Columns:
    #   B Slug | C Status | P Drive Link (captioned final) | Q Drive Clip (raw)
    sheets = quiz_sheets.build_sheets()
    resp = sheets.spreadsheets().values().get(
        spreadsheetId=quiz_sheets.QUIZ_SHEET_ID,
        range="Posts_2T1L!A3:S",
    ).execute()
    rows = resp.get("values", [])
    ready = []
    for row in rows:
        row = (list(row) + [""] * 19)[:19]
        slug, status = row[1].strip(), row[2].strip()
        if slug and status == "Ready to publish":
            ready.append({"slug": slug, "drive_link": row[15], "drive_clip_link": row[16]})
    dest_dir = DEST["dailytrivia"]
    print(f"\n=== dailytrivia.tc  (trivia-captain / Posts_2T1L)  →  {dest_dir} ===")
    print(f"{len(ready)} row(s) at Status='Ready to publish'")
    for p in ready:
        slug = p["slug"]
        print(f"  {slug}")
        _pull(drive, "render", _file_id_from_link(p.get("drive_link") or ""),
              dest_dir / f"{slug}.mp4")
        _pull(drive, "clip  ", _file_id_from_link(p.get("drive_clip_link") or ""),
              dest_dir / f"{slug}_clip.mp4")
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
