import random

import pytest
from PIL import Image

from scripts.chonky.deliver import deliver
from scripts.chonky.geometry import IMG_W, IMG_H


def _photo_like():
    rnd = random.Random(7)
    img = Image.new("RGB", (IMG_W, IMG_H))
    px = img.load()
    for y in range(IMG_H):
        g = (y * 255) // IMG_H
        for x in range(IMG_W):
            r = (x * 255) // IMG_W
            px[x, y] = (min(255, r + rnd.randrange(-18, 19)),
                        min(255, g + rnd.randrange(-18, 19)),
                        min(255, 180 + rnd.randrange(-18, 19)))
    return img


MEASURED = {"height_px": 89, "zone": "viewframe", "box": (1232, 1709, 1318, 1798)}


def _deliver(img, rows, **kw):
    return deliver(
        img, difficulty="1", city="Lisbon", country="Portugal",
        clue_words=["arch", "calcada", "clock"], measurement=MEASURED,
        uploader=lambda data, fn: "https://drive.google.com/file/d/FAKE/view",
        sheet_writer=rows.append, **kw,
    )


def test_names_encodes_and_records():
    rows = []
    result = _deliver(_photo_like(), rows)
    assert result["filename"] == "1_lisbon_portugal_arch_calcada_clock.jpg"
    assert 700 <= result["kb"] <= 1200, result["kb"]
    assert result["drive_link"].startswith("https://drive.google.com/")
    assert len(rows) == 1


def test_row_carries_the_measured_values_verbatim():
    rows = []
    _deliver(_photo_like(), rows)
    row = rows[0]
    assert row["chonky_px_h"] == 89
    assert row["chonky_zone"] == "viewframe"
    assert row["chonky_x"] == 1232
    assert row["chonky_y"] == 1709
    assert row["status"] == "verified"


def test_uploader_receives_the_encoded_bytes_and_the_final_name():
    seen = {}
    rows = []
    deliver(
        _photo_like(), difficulty="1", city="Lisbon", country="Portugal",
        clue_words=["arch", "calcada", "clock"], measurement=MEASURED,
        uploader=lambda data, fn: seen.update(n=fn, size=len(data)) or "https://x/y",
        sheet_writer=rows.append,
    )
    assert seen["n"] == "1_lisbon_portugal_arch_calcada_clock.jpg"
    assert 700 * 1024 <= seen["size"] <= 1200 * 1024


def test_refuses_to_deliver_an_unverified_image():
    rows = []
    with pytest.raises(ValueError, match="not verified"):
        deliver(
            _photo_like(), difficulty="1", city="Lisbon", country="Portugal",
            clue_words=["arch", "calcada", "clock"],
            measurement={"height_px": 310, "zone": "viewframe", "box": (1, 2, 3, 312)},
            uploader=lambda d, f: "x", sheet_writer=rows.append,
        )
    assert rows == [], "nothing may reach the sheet when the image failed"
