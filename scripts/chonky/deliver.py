"""Encode, upload and record one approved image.

Called only from the review UI's Approve action. Nothing reaches Drive or the
sheet without a human having looked at the render and its ViewFrame, and
`deliver` refuses outright if the measurement says the image failed — the
guard exists because "verified" in the sheet has to mean something.
"""
from __future__ import annotations

import datetime as _dt
import io
import os
from pathlib import Path
from typing import Callable, Optional

from PIL import Image

from scripts.chonky.imaging import encode_delivery
from scripts.chonky.naming import build_filename

# Where approved renders and rows land.
#
# These are company resources and are committed the same way the trivia
# pipelines commit theirs (see trivia_images/drive_config.py and
# trivia/publish.py) — a Drive folder id is an address, not a secret, and
# committing it means a fresh checkout works without per-machine setup.
#
# Override per environment with CHONKY_DRIVE_FOLDER_ID / CHONKY_SHEET_ID.
#
#   Drive: "8. Chonky" shared drive → Images
#   Sheet: "Chonky Exclamations", Batches tab
#
# Both are shared with claude-sheets-config@travel-crush.iam.gserviceaccount.com.
DEFAULT_DRIVE_FOLDER_ID = "1GVpjyHEI40y2ewy6ksXKA88EdnYl1oN2"
DEFAULT_SHEET_ID = "1a36CLEy3VZRnpjsv_O0k3yZYM14KaG2SftINDaNxu8U"

DRIVE_FOLDER_ID = os.environ.get("CHONKY_DRIVE_FOLDER_ID") or DEFAULT_DRIVE_FOLDER_ID
SHEET_ID = os.environ.get("CHONKY_SHEET_ID") or DEFAULT_SHEET_ID
BATCHES_TAB = os.environ.get("CHONKY_BATCHES_TAB", "Batches")


def _require(name: str, value: str) -> str:
    if not value:
        raise RuntimeError(
            f"{name} is not set. Chonky writes to company Drive and Sheets "
            f"only; set {name} in .env or the environment before delivering."
        )
    return value

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
    # Appended rather than inserted: the sheet already has these columns in
    # this order. A pixel height means nothing without the frame it was
    # measured in, and this was being written into the row and then dropped
    # because COLUMNS never listed it.
    "frame_size",
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
        body={"name": filename,
              "parents": [_require("CHONKY_DRIVE_FOLDER_ID", DRIVE_FOLDER_ID)]},
        media_body=media,
        fields="id,name,webViewLink",
        supportsAllDrives=True,
    ).execute()
    # The id as well as the link: deleting by name works only until two files
    # share one, and two renders of the same city with the same three clue
    # words produce exactly that.
    return {"id": created["id"], "link": created["webViewLink"]}


def _default_sheet_writer(row: dict) -> None:
    _, sheets = _clients()
    sheets.spreadsheets().values().append(
        spreadsheetId=_require("CHONKY_SHEET_ID", SHEET_ID),
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
    status: Optional[str] = None,
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
        # In the frame it was measured in, not the reference one. Renders do
        # not all arrive at the reference size, and judging one against the
        # other is how a cat filling a sixth of the frame reads as a near miss.
        frame = measurement.get("frame_size")
        frame = tuple(frame) if frame else None
        if h is not None and box:
            size = size_verdict(h, frame)
            zone = classify_zone(box[0], box[2], box[1], box[3], frame)
        if size != "ok" or zone not in ("viewframe", "margin"):
            # A caller that has decided to file a near miss says so by passing
            # a status. Without one this stays a refusal, so a failing image
            # cannot be delivered by accident.
            if status is None:
                raise ValueError(
                    f"image is not verified (size={size}, zone={zone}); "
                    "reroll it rather than delivering it"
                )

    filename = build_filename(difficulty, city, country, clue_words)
    data, quality = encode_delivery(img)

    upload = uploader or _default_uploader
    uploaded = upload(data, filename)
    # An uploader may return just the link, which is the older contract.
    if isinstance(uploaded, dict):
        drive_link = uploaded.get("link", "")
        drive_file_id = uploaded.get("id")
    else:
        drive_link, drive_file_id = uploaded, None

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
        # "verified" is a claim about the checks, so an image that failed them
        # must not carry it. The sheet is the record other people read.
        "status": status or "verified",
        "chonky_zone": zone,
        "chonky_px_h": measurement.get("height_px"),
        # The pixel height only means something next to the frame it was
        # measured in: renders do not all arrive at the reference size, and a
        # bare number invites comparing two different rulers.
        "frame_size": "x".join(str(v) for v in measurement.get("frame_size", []))
                      or None,
        "chonky_x": box[0],
        "chonky_y": box[1],
    }
    (sheet_writer or _default_sheet_writer)(row)

    return {"filename": filename, "kb": len(data) // 1024, "quality": quality,
            "drive_link": drive_link, "drive_file_id": drive_file_id, "row": row}


# --------------------------------------------------------------------------
# Removal
# --------------------------------------------------------------------------

def _default_deleter(file_id: Optional[str], filename: str) -> None:
    """Delete the Drive file, by id when known and by name when not."""
    drive, _ = _clients()
    folder = _require("CHONKY_DRIVE_FOLDER_ID", DRIVE_FOLDER_ID)
    if not file_id:
        found = drive.files().list(
            q=f"name = '{filename}' and '{folder}' in parents and trashed = false",
            fields="files(id)", supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute().get("files", [])
        if not found:
            raise FileNotFoundError(f"no Drive file named {filename}")
        file_id = found[0]["id"]
    drive.files().delete(fileId=file_id, supportsAllDrives=True).execute()


def _default_sheet_remover(filename: str) -> None:
    """Delete the row whose filename column matches.

    Matched by filename rather than by a row number recorded at write time:
    rows shift when anything above them is removed, so a stored index goes
    stale the first time someone deletes an earlier image.
    """
    _, sheets = _clients()
    sheet_id = _require("CHONKY_SHEET_ID", SHEET_ID)
    values = sheets.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=f"{BATCHES_TAB}!A:Z",
    ).execute().get("values", [])

    col = COLUMNS.index("filename")
    target = next((i for i, row in enumerate(values)
                   if len(row) > col and row[col] == filename), None)
    if target is None:
        raise FileNotFoundError(f"no sheet row for {filename}")

    meta = sheets.spreadsheets().get(spreadsheetId=sheet_id).execute()
    tab = next((s for s in meta["sheets"]
                if s["properties"]["title"] == BATCHES_TAB), None)
    if tab is None:
        raise FileNotFoundError(f"no tab named {BATCHES_TAB}")

    sheets.spreadsheets().batchUpdate(
        spreadsheetId=sheet_id,
        body={"requests": [{"deleteDimension": {"range": {
            "sheetId": tab["properties"]["sheetId"], "dimension": "ROWS",
            "startIndex": target, "endIndex": target + 1}}}]},
    ).execute()


def remove(*, filename: str, drive_file_id: Optional[str] = None,
           deleter: Optional[Callable] = None,
           sheet_remover: Optional[Callable] = None) -> dict:
    """Undo a delivery: the Drive file and the sheet row.

    Each half is attempted independently and reported separately. Stopping at
    the first failure leaves the worst state of the three — a row pointing at
    a file that is gone, or a file nothing references — and the caller cannot
    tell which happened.
    """
    if not filename:
        raise ValueError("filename is required to remove a delivery")

    out = {"filename": filename, "drive": False, "sheet": False}

    try:
        (deleter or _default_deleter)(drive_file_id, filename)
        out["drive"] = True
    except Exception as exc:                          # noqa: BLE001 - reported
        out["drive_error"] = f"{type(exc).__name__}: {exc}"

    try:
        (sheet_remover or _default_sheet_remover)(filename)
        out["sheet"] = True
    except Exception as exc:                          # noqa: BLE001 - reported
        out["sheet_error"] = f"{type(exc).__name__}: {exc}"

    return out
