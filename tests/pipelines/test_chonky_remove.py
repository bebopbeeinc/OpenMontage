"""Deleting a delivered image.

Approve is the only irreversible write, so Delete is the only way back. It has
to reach everywhere the image went: the Drive file, the sheet row, and the
local render — otherwise the ledger keeps claiming a location is used and the
writer keeps avoiding it.
"""

import pytest

from scripts.chonky import deliver as D


def test_remove_deletes_the_drive_file_and_the_sheet_row():
    deleted, rows = [], []
    D.remove(filename="1_paris_france_tower_flag_fountain.jpg",
             drive_file_id="abc123",
             deleter=lambda file_id, name: deleted.append((file_id, name)),
             sheet_remover=lambda name: rows.append(name))
    assert deleted == [("abc123", "1_paris_france_tower_flag_fountain.jpg")]
    assert rows == ["1_paris_france_tower_flag_fountain.jpg"]


def test_remove_reports_what_it_removed():
    out = D.remove(filename="f.jpg", drive_file_id="id",
                   deleter=lambda *a: None, sheet_remover=lambda *a: None)
    assert out["filename"] == "f.jpg"
    assert out["drive"] is True
    assert out["sheet"] is True


def test_a_drive_failure_does_not_stop_the_sheet_row_going():
    """Half-deleted is the worst outcome: the row would point at nothing."""
    rows = []

    def boom(file_id, name):
        raise RuntimeError("404 file not found")

    out = D.remove(filename="f.jpg", drive_file_id="id", deleter=boom,
                   sheet_remover=lambda name: rows.append(name))
    assert rows == ["f.jpg"], "the row was left behind pointing at a deleted file"
    assert out["drive"] is False
    assert "404" in out["drive_error"]


def test_remove_needs_a_filename():
    with pytest.raises(ValueError):
        D.remove(filename="", deleter=lambda *a: None, sheet_remover=lambda *a: None)


def test_the_sheet_row_records_the_frame_a_height_was_measured_in():
    """frame_size was written into the row dict but never listed in COLUMNS,
    so the one number that makes a pixel height meaningful was dropped."""
    assert "frame_size" in D.COLUMNS


def test_deliver_records_the_drive_file_id_for_later_removal():
    """Finding the file again by name works, but only until two share a name."""
    from PIL import Image

    out = D.deliver(
        Image.new("RGB", (1344, 1680), (120, 120, 120)),
        difficulty="1", city="Paris", country="France",
        clue_words=["tower", "flag", "fountain"],
        measurement={"box": [500, 900, 560, 960], "height_px": 60, "size": "ok",
                     "zone": "viewframe", "ok": True, "frame_size": [1344, 1680]},
        uploader=lambda data, name: {"id": "file-123",
                                     "link": "https://drive/file-123"},
        sheet_writer=lambda row: None,
    )
    assert out["drive_file_id"] == "file-123"
    assert out["drive_link"] == "https://drive/file-123"


def test_an_uploader_that_returns_only_a_link_still_works():
    """The older contract stays valid; the id is simply not known."""
    from PIL import Image

    out = D.deliver(
        Image.new("RGB", (1344, 1680), (120, 120, 120)),
        difficulty="1", city="Paris", country="France",
        clue_words=["tower", "flag", "fountain"],
        measurement={"box": [500, 900, 560, 960], "height_px": 60, "size": "ok",
                     "zone": "viewframe", "ok": True, "frame_size": [1344, 1680]},
        uploader=lambda data, name: "https://drive/only-a-link",
        sheet_writer=lambda row: None,
    )
    assert out["drive_link"] == "https://drive/only-a-link"
    assert out["drive_file_id"] is None
