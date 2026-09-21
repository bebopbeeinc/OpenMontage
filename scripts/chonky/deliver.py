"""Encode, upload and record one approved image.

Called only from the review UI's Approve action. Nothing reaches Drive or the
sheet without a human having looked at the render and its ViewFrame, and
`deliver` refuses outright if the measurement says the image failed — the
guard exists because "verified" in the sheet has to mean something.
"""
from __future__ import annotations

import datetime as _dt
import io
from pathlib import Path
from typing import Callable, Optional

from PIL import Image

from scripts.chonky.imaging import encode_delivery
from scripts.chonky.naming import build_filename

# Where approved renders land. Created by the operator; see docs/design.md.
DRIVE_FOLDER_ID = "1T1vnLNGeIp-cM1lA2Rb_xO3yRVd4zFSY"
SHEET_ID = "1a36CLEy3VZRnpjsv_O0k3yZYM14KaG2SftINDaNxu8U"
BATCHES_TAB = "Batches"

# Same service account the trivia pipelines use.
SA_PATH = Path.home() / ".google" / "claude-sheets-sa.json"
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

COLUMNS = [
    "batch_no", "date", "difficulty", "city", "country", "viewpoint",
    "scene_type", "gag", "prompt", "clue1", "clue2", "clue3",
    "filename", "drive_link", "openart_url", "status",
    "chonky_zone", "chonky_px_h", "chonky_x", "chonky_y",
]


def _clients():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    if not SA_PATH.exists():
        raise FileNotFoundError(
            f"Google service account not found at {SA_PATH}. The trivia "
            "pipelines use the same file; copy it here to enable delivery."
        )
    creds = service_account.Credentials.from_service_account_file(str(SA_PATH), scopes=SCOPES)
    return (build("drive", "v3", credentials=creds, cache_discovery=False),
            build("sheets", "v4", credentials=creds, cache_discovery=False))


def _default_uploader(data: bytes, filename: str) -> str:
    from googleapiclient.http import MediaIoBaseUpload

    drive, _ = _clients()
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype="image/jpeg", resumable=True)
    created = drive.files().create(
        body={"name": filename, "parents": [DRIVE_FOLDER_ID]},
        media_body=media,
        fields="id,name,webViewLink",
        supportsAllDrives=True,
    ).execute()
    return created["webViewLink"]


def _default_sheet_writer(row: dict) -> None:
    _, sheets = _clients()
    sheets.spreadsheets().values().append(
        spreadsheetId=SHEET_ID,
        range=f"{BATCHES_TAB}!A1",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": [[row.get(c, "") for c in COLUMNS]]},
    ).execute()


def deliver(
    img: Image.Image,
    *,
    difficulty: str,
    city: str,
    country: str,
    clue_words: list[str],
    measurement: dict,
    clues: Optional[list[str]] = None,
    prompt: str = "",
    viewpoint: str = "",
    scene_type: str = "",
    gag: str = "",
    batch_no: int = 1,
    openart_url: str = "",
    uploader: Optional[Callable[[bytes, str], str]] = None,
    sheet_writer: Optional[Callable[[dict], None]] = None,
) -> dict:
    """Encode to JPEG, upload, and append the row. Refuses a failed image."""
    size = measurement.get("size")
    zone = measurement.get("zone")
    if size != "ok" or zone not in ("viewframe", "margin"):
        # Recompute the verdict when the caller passed raw numbers only.
        from scripts.chonky.geometry import classify_zone, size_verdict
        h = measurement.get("height_px")
        box = measurement.get("box")
        if h is not None and box:
            size = size_verdict(h)
            zone = classify_zone(box[0], box[2], box[1], box[3])
        if size != "ok" or zone not in ("viewframe", "margin"):
            raise ValueError(
                f"image is not verified (size={size}, zone={zone}); "
                "reroll it rather than delivering it"
            )

    filename = build_filename(difficulty, city, country, clue_words)
    data, quality = encode_delivery(img)

    upload = uploader or _default_uploader
    drive_link = upload(data, filename)

    box = measurement.get("box") or (None, None, None, None)
    clues = clues or ["", "", ""]
    row = {
        "batch_no": batch_no,
        "date": _dt.date.today().isoformat(),
        "difficulty": difficulty,
        "city": city,
        "country": country,
        "viewpoint": viewpoint,
        "scene_type": scene_type,
        "gag": gag,
        "prompt": prompt,
        "clue1": clues[0],
        "clue2": clues[1],
        "clue3": clues[2],
        "filename": filename,
        "drive_link": drive_link,
        "openart_url": openart_url,
        "status": "verified",
        "chonky_zone": zone,
        "chonky_px_h": measurement.get("height_px"),
        "chonky_x": box[0],
        "chonky_y": box[1],
    }
    (sheet_writer or _default_sheet_writer)(row)

    return {"filename": filename, "kb": len(data) // 1024, "quality": quality,
            "drive_link": drive_link, "row": row}
