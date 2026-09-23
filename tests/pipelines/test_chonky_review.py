"""Approve / reject / edit, from the tile the reviewer is looking at.

Everything a delivery needs is already recorded beside the render, so the
reviewer clicking Approve should not have to re-supply any of it.
"""

import json
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from scripts.chonky import prompts
from scripts.chonky.web import server

client = TestClient(server.app)

MEASUREMENT = {"box": [500, 900, 560, 960], "height_px": 60, "size": "ok",
               "zone": "viewframe", "ok": True, "frame_size": [1344, 1680]}


def _render(image_id, **side):
    Image.new("RGB", (1344, 1680), (150, 150, 150)).save(server.LIBRARY / f"{image_id}.png")
    payload = {"image_id": image_id, "measurement": MEASUREMENT,
               "location": "Paris, France", "difficulty": "1",
               "prompt": "a prompt", "target_zone": "viewframe",
               "clues": ["Landmark: the [Eiffel Tower] ...",
                         "Flag: a [French flag] ...",
                         "Street furniture: [Wallace fountains] ..."],
               "clue_words": ["tower", "flag", "fountain"]}
    payload.update(side)
    (server.LIBRARY / f"{image_id}.json").write_text(json.dumps(payload))


def _cleanup(*ids):
    for i in ids:
        (server.LIBRARY / f"{i}.png").unlink(missing_ok=True)
        (server.LIBRARY / f"{i}.json").unlink(missing_ok=True)
        server._images.pop(i, None)


def test_approve_takes_everything_from_the_record_beside_the_render(monkeypatch):
    """One click. The reviewer re-types nothing that was already recorded."""
    seen = {}

    def fake_deliver(img, **kw):
        seen.update(kw)
        return {"drive_url": "https://drive/x", "filename": "f.jpg", "row": 2}

    monkeypatch.setattr(server, "deliver", fake_deliver)
    _render("appr-a")
    try:
        r = client.post("/api/approve", json={"image_id": "appr-a"})
        assert r.status_code == 200, r.text
        assert seen["city"] == "Paris"
        assert seen["country"] == "France"
        assert seen["difficulty"] == "1"
        assert seen["clue_words"] == ["tower", "flag", "fountain"]
        assert seen["measurement"]["height_px"] == 60
        assert seen["prompt"] == "a prompt"
    finally:
        _cleanup("appr-a")


def test_approve_records_that_it_was_approved(monkeypatch):
    monkeypatch.setattr(server, "deliver",
                        lambda img, **kw: {"drive_url": "u", "filename": "f", "row": 2})
    _render("appr-b")
    try:
        client.post("/api/approve", json={"image_id": "appr-b"})
        side = json.loads((server.LIBRARY / "appr-b.json").read_text())
        assert side["approved"] is True
        assert side["drive_url"] == "u"
    finally:
        _cleanup("appr-b")


def test_approve_refuses_a_render_that_failed_its_checks(monkeypatch):
    """The band is a hard constraint; one click must not quietly bypass it."""
    monkeypatch.setattr(server, "deliver",
                        lambda img, **kw: {"drive_url": "u", "filename": "f", "row": 2})
    _render("appr-bad", measurement=dict(MEASUREMENT, ok=False, size="too_big"))
    try:
        r = client.post("/api/approve", json={"image_id": "appr-bad"})
        assert r.status_code == 400
        assert "too_big" in r.text or "failed" in r.text
    finally:
        _cleanup("appr-bad")


def test_reject_marks_the_render_and_keeps_the_file(monkeypatch):
    """Rejecting records a judgement; it does not destroy evidence."""
    _render("rej-a")
    try:
        r = client.post("/api/reject", json={"image_id": "rej-a",
                                             "reason": "he is too big"})
        assert r.status_code == 200
        side = json.loads((server.LIBRARY / "rej-a.json").read_text())
        assert side["rejected"] is True
        assert side["reject_reason"] == "he is too big"
        assert (server.LIBRARY / "rej-a.png").exists()
    finally:
        _cleanup("rej-a")


def test_regenerate_renders_the_edited_prompt_as_a_new_image(monkeypatch):
    """The original stays; an edit is a new attempt, not a rewrite of history."""
    monkeypatch.setattr(server, "render_once",
                        lambda p, o, log=None: (Image.new("RGB", (1344, 1680),
                                                          (9, 9, 9)).save(o), o)[1])
    _render("regen-a")
    made = []
    try:
        body = client.post("/api/regenerate", json={
            "image_id": "regen-a", "prompt": "an edited prompt"}).json()
        made.append(body["image_id"])
        assert body["image_id"] != "regen-a"
        end = time.time() + 10
        while time.time() < end:
            if client.get(f"/api/jobs/{body['job_id']}").json()["state"] != "running":
                break
            time.sleep(0.05)
        side = json.loads((server.LIBRARY / f"{body['image_id']}.json").read_text())
        assert side["prompt"] == "an edited prompt"
        assert side["location"] == "Paris, France"
        assert side["clues"] == json.loads(
            (server.LIBRARY / "regen-a.json").read_text())["clues"]
        assert (server.LIBRARY / "regen-a.png").exists()
    finally:
        _cleanup("regen-a", *made)


def test_regenerate_refuses_an_edit_that_breaks_the_manual(monkeypatch):
    """A hand-edited prompt gets the same check the written one gets."""
    rendered = []
    monkeypatch.setattr(server, "render_once",
                        lambda p, o, log=None: (rendered.append(p), o)[1])
    _render("regen-bad")
    try:
        r = client.post("/api/regenerate", json={
            "image_id": "regen-bad",
            "prompt": "A street, photoreal, no illustration style."})
        assert r.status_code == 400
        assert "illustration" in r.text
        assert rendered == []
    finally:
        _cleanup("regen-bad")


def test_the_writer_is_asked_for_three_filename_words():
    """The filename needs three single words; parsing them back out of prose guesses."""
    payload = {"city": "Paris", "country": "France", "prompt": "p",
               "clues": ["a", "b", "c"], "clue_words": ["tower", "flag", "fountain"]}
    draft = prompts.draft(difficulty=1, target_zone="viewframe", used=[],
                          caller=lambda s, u, model=None: json.dumps(payload))
    assert draft["clue_words"] == ["tower", "flag", "fountain"]


def test_a_draft_without_clue_words_is_rejected():
    payload = {"city": "Paris", "country": "France", "prompt": "p",
               "clues": ["a", "b", "c"]}
    with pytest.raises(prompts.DraftError):
        prompts.draft(difficulty=1, target_zone="viewframe", used=[],
                      caller=lambda s, u, model=None: json.dumps(payload))
