#!/usr/bin/env python
"""Backfill 512×384 resized copies on Google Drive from existing originals.

New generations create both a full-res original and a 512×384 resized copy (in
a `Resized` subfolder). This optional one-time script backfills resized copies
for images that already existed before that step landed: for each original in
the staging (`WIP/`) and approved (`Question Images/`) folders, it writes a
512×384 lossless-PNG copy into that folder's `Resized` subfolder. **Originals
are never moved or modified.**

Idempotent: a resized copy already at 512×384 is skipped (no re-download).

Usage:
    python scripts/trivia_images/optimize_drive.py --dry-run        # preview
    python scripts/trivia_images/optimize_drive.py                  # both folders
    python scripts/trivia_images/optimize_drive.py --folder staging
"""
from __future__ import annotations

import argparse
import re
import sys
import tempfile
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent
REPO = PKG_DIR.parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(PKG_DIR))

from tools.publishers.google_drive import get_client  # noqa: E402
from drive_config import APPROVED_FOLDER_ID, STAGING_FOLDER_ID  # noqa: E402
from image_optimize import GAME_HEIGHT, GAME_WIDTH, optimize_image_bytes  # noqa: E402

# Canonical asset names produced by drive_name(): "1Q.png", "12A.png", …
_ASSET_NAME = re.compile(r"^\d+[QA]\.png$")
_RESIZED_SUBFOLDER = "Resized"


def _list_assets(folder_id: str) -> dict[str, dict]:
    """Map canonical asset name → {id, modifiedTime} for originals directly
    under `folder_id` (skips subfolders)."""
    drive = get_client()._drive()
    out: dict[str, dict] = {}
    page_token = None
    while True:
        resp = drive.files().list(
            q=f"'{folder_id}' in parents and trashed=false",
            fields="nextPageToken,files(id,name,modifiedTime)",
            pageSize=200,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
            pageToken=page_token,
        ).execute()
        for f in resp.get("files", []):
            if _ASSET_NAME.match(f["name"]):
                out[f["name"]] = {"id": f["id"], "modifiedTime": f.get("modifiedTime", "")}
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return out


def _resized_dims(folder_id: str) -> dict[str, tuple]:
    """Map name → (width, height) for files already in the Resized subfolder,
    so we can skip ones already at the target size without downloading."""
    drive = get_client()._drive()
    out: dict[str, tuple] = {}
    resp = drive.files().list(
        q=f"'{folder_id}' in parents and trashed=false",
        fields="files(name,imageMediaMetadata(width,height))",
        pageSize=1000,
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()
    for f in resp.get("files", []):
        md = f.get("imageMediaMetadata") or {}
        out[f["name"]] = (md.get("width"), md.get("height"))
    return out


def _backfill_folder(folder_id: str, label: str, dry_run: bool) -> tuple[int, int, int]:
    client = get_client()
    originals = _list_assets(folder_id)
    resized_folder = client.find_or_create_folder(folder_id, _RESIZED_SUBFOLDER)
    have = _resized_dims(resized_folder.id)
    print(f"\n[{label}] {len(originals)} original(s); "
          f"{sum(1 for d in have.values() if d == (GAME_WIDTH, GAME_HEIGHT))} "
          f"already in {label}/Resized")

    changed = skipped = failed = 0
    for name in sorted(originals, key=lambda s: (len(s), s)):
        try:
            if have.get(name) == (GAME_WIDTH, GAME_HEIGHT):
                skipped += 1
                continue
            if dry_run:
                print(f"  · would create {label}/Resized/{name}")
                changed += 1
                continue
            data, _mime, _ = client.download_bytes(originals[name]["id"],
                                                   originals[name]["modifiedTime"])
            png = optimize_image_bytes(data)
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
                tf.write(png)
                tmp = Path(tf.name)
            try:
                client.upload_or_replace(resized_folder.id, name, tmp, mime_type="image/png")
            finally:
                tmp.unlink(missing_ok=True)
            print(f"  ✓ {label}/Resized/{name}  ({len(png) // 1024} KB)")
            changed += 1
        except Exception as e:
            print(f"  ✗ {name}: {e}", file=sys.stderr)
            failed += 1
    return changed, skipped, failed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--folder", choices=("staging", "approved", "both"), default="both")
    ap.add_argument("--dry-run", action="store_true",
                    help="list what would be created without modifying Drive")
    args = ap.parse_args()

    targets: list[tuple[str, str]] = []
    if args.folder in ("staging", "both"):
        targets.append((STAGING_FOLDER_ID, "WIP"))
    if args.folder in ("approved", "both"):
        targets.append((APPROVED_FOLDER_ID, "Approved"))

    mode = "DRY RUN — " if args.dry_run else ""
    print(f"{mode}backfilling Resized/ copies → {GAME_WIDTH}×{GAME_HEIGHT} lossless PNG "
          f"(originals untouched)")
    total_changed = total_skipped = total_failed = 0
    for folder_id, label in targets:
        c, s, f = _backfill_folder(folder_id, label, args.dry_run)
        total_changed += c
        total_skipped += s
        total_failed += f

    verb = "would create" if args.dry_run else "created"
    print(f"\ndone. {total_changed} {verb}, {total_skipped} already-resized, "
          f"{total_failed} failed.")
    return 1 if total_failed else 0


if __name__ == "__main__":
    sys.exit(main())
