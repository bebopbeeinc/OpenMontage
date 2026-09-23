"""One button: pick a location, write the prompt, render, measure.

The person using this tool never writes a prompt, so everything between the
button and a reviewable card has to happen without them.
"""

import json
import time

from fastapi.testclient import TestClient
from PIL import Image

from scripts.chonky import prompts
from scripts.chonky.web import server

client = TestClient(server.app)

DRAFT = {
    "city": "Prague",
    "country": "Czech Republic",
    "prompt": "A photograph of Charles Bridge, with Chonky far beyond the crowd.",
    "clues": ["the bridge tower", "the castle", "baroque statues"],
    "clue_words": ["tower", "castle", "statues"],
}


def _fake_render(prompt, out, log=None, **kw):
    Image.new("RGB", (1344, 1680), (150, 150, 150)).save(out)
    return out


def _drain(job_ids, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        states = [client.get(f"/api/jobs/{j}").json()["state"] for j in job_ids]
        if all(s not in ("running", "queued", "drafting") for s in states):
            return states
        time.sleep(0.05)
    raise AssertionError(f"jobs did not finish: {states}")


def _cleanup(ids):
    for i in ids:
        (server.LIBRARY / f"{i}.png").unlink(missing_ok=True)
        (server.LIBRARY / f"{i}.json").unlink(missing_ok=True)
        server._images.pop(i, None)


def test_generate_drafts_renders_and_records_without_a_pasted_prompt(monkeypatch):
    monkeypatch.setattr(server, "render_once", _fake_render)
    monkeypatch.setattr(prompts, "draft", lambda **kw: dict(DRAFT))

    body = client.post("/api/generate", json={"count": 1, "difficulty": 1}).json()
    ids = [j["image_id"] for j in body["jobs"]]
    try:
        _drain([j["job_id"] for j in body["jobs"]])
        side = json.loads((server.LIBRARY / f"{ids[0]}.json").read_text())
        assert side["prompt"] == DRAFT["prompt"]
        assert side["location"] == "Prague, Czech Republic"
        assert side["clues"] == DRAFT["clues"]
        assert side["measurement"] is not None
    finally:
        _cleanup(ids)


def test_generate_makes_one_job_per_image(monkeypatch):
    monkeypatch.setattr(server, "render_once", _fake_render)
    monkeypatch.setattr(prompts, "draft", lambda **kw: dict(DRAFT))

    body = client.post("/api/generate", json={"count": 3}).json()
    ids = [j["image_id"] for j in body["jobs"]]
    try:
        assert len(body["jobs"]) == 3
        assert len(set(ids)) == 3, "image ids must be distinct"
        _drain([j["job_id"] for j in body["jobs"]])
    finally:
        _cleanup(ids)


def test_locations_already_rendered_are_passed_to_the_writer(monkeypatch):
    """Reuse is the failure the manual spends its first section on."""
    seen = {}

    def spy(**kw):
        seen.update(kw)
        return dict(DRAFT)

    monkeypatch.setattr(server, "render_once", _fake_render)
    monkeypatch.setattr(prompts, "draft", spy)

    Image.new("RGB", (64, 80), (1, 2, 3)).save(server.LIBRARY / "used-probe.png")
    (server.LIBRARY / "used-probe.json").write_text(
        json.dumps({"image_id": "used-probe", "location": "Lisbon, Portugal"}))
    try:
        body = client.post("/api/generate", json={"count": 1}).json()
        ids = [j["image_id"] for j in body["jobs"]]
        _drain([j["job_id"] for j in body["jobs"]])
        assert "Lisbon, Portugal" in seen["used"]
        _cleanup(ids)
    finally:
        _cleanup(["used-probe"])


def test_each_image_gets_a_zone_from_the_ledger(monkeypatch):
    seen = []
    monkeypatch.setattr(server, "render_once", _fake_render)
    monkeypatch.setattr(prompts, "draft",
                        lambda **kw: (seen.append(kw["target_zone"]), dict(DRAFT))[1])

    body = client.post("/api/generate", json={"count": 3}).json()
    ids = [j["image_id"] for j in body["jobs"]]
    try:
        _drain([j["job_id"] for j in body["jobs"]])
        assert len(seen) == 3
        assert all(z in ("viewframe", "margin") for z in seen), seen
    finally:
        _cleanup(ids)


def test_a_rejected_draft_fails_that_job_without_rendering(monkeypatch):
    """A bad prompt must not become a paid render."""
    rendered = []

    def counting_render(prompt, out, log=None):
        rendered.append(prompt)
        return _fake_render(prompt, out, log)

    monkeypatch.setattr(server, "render_once", counting_render)

    def boom(**kw):
        raise prompts.DraftError("says 'no illustration style'")

    monkeypatch.setattr(prompts, "draft", boom)

    body = client.post("/api/generate", json={"count": 1}).json()
    ids = [j["image_id"] for j in body["jobs"]]
    try:
        states = _drain([j["job_id"] for j in body["jobs"]])
        assert states == ["failed"]
        assert rendered == [], "a rejected draft was rendered anyway"
        detail = client.get(f"/api/jobs/{body['jobs'][0]['job_id']}").json()
        assert "no illustration style" in detail["error"]
    finally:
        _cleanup(ids)


def test_each_image_in_a_batch_knows_what_the_earlier_ones_took(monkeypatch):
    """Two drafts in one batch picked Sydney.

    The used list was computed once and handed to every draft, so nothing told
    the second image what the first had chosen — and not reusing a location is
    what the manual's whole first section is about.
    """
    monkeypatch.setattr(server, "render_once", _fake_render)

    seen_used = []
    cities = iter(["Prague", "Vienna", "Porto"])

    def spy(**kw):
        seen_used.append(list(kw["used"]))
        return dict(DRAFT, city=next(cities), country="X")

    monkeypatch.setattr(prompts, "draft", spy)

    body = client.post("/api/generate", json={"count": 3}).json()
    ids = [j["image_id"] for j in body["jobs"]]
    try:
        _drain([j["job_id"] for j in body["jobs"]], timeout=20)
        assert len(seen_used) == 3
        assert "Prague, X" in seen_used[1], seen_used
        assert "Prague, X" in seen_used[2] and "Vienna, X" in seen_used[2], seen_used
    finally:
        _cleanup(ids)


def test_a_generated_render_records_the_words_its_filename_needs(monkeypatch):
    """Approve refuses a render with no clue words, so generating without them
    would have made every generated image unapprovable."""
    monkeypatch.setattr(server, "render_once", _fake_render)
    monkeypatch.setattr(prompts, "draft", lambda **kw: dict(DRAFT))

    body = client.post("/api/generate", json={"count": 1}).json()
    ids = [j["image_id"] for j in body["jobs"]]
    try:
        _drain([j["job_id"] for j in body["jobs"]], timeout=20)
        side = json.loads((server.LIBRARY / f"{ids[0]}.json").read_text())
        assert side["clue_words"] == DRAFT["clue_words"]
    finally:
        _cleanup(ids)


def test_all_levels_generates_the_count_for_each_level(monkeypatch):
    """"All" means the count per level, not the count split across them."""
    monkeypatch.setattr(server, "render_once", _fake_render)
    seen = []

    def spy(**kw):
        seen.append(kw["difficulty"])
        return dict(DRAFT)

    monkeypatch.setattr(prompts, "draft", spy)

    body = client.post("/api/generate", json={"count": 2, "difficulty": "all"}).json()
    ids = [j["image_id"] for j in body["jobs"]]
    try:
        assert len(body["jobs"]) == 10, "2 per level across 5 levels"
        _drain([j["job_id"] for j in body["jobs"]], timeout=30)
        assert sorted(seen) == [1, 1, 2, 2, 3, 3, 4, 4, 5, 5]
    finally:
        _cleanup(ids)


def test_each_job_reports_the_level_it_is_for(monkeypatch):
    """The page shows the level on the card while it is still rendering."""
    monkeypatch.setattr(server, "render_once", _fake_render)
    monkeypatch.setattr(prompts, "draft", lambda **kw: dict(DRAFT))

    body = client.post("/api/generate", json={"count": 1, "difficulty": "all"}).json()
    ids = [j["image_id"] for j in body["jobs"]]
    try:
        assert sorted(j["difficulty"] for j in body["jobs"]) == [1, 2, 3, 4, 5]
        _drain([j["job_id"] for j in body["jobs"]], timeout=30)
    finally:
        _cleanup(ids)


def test_the_level_reaches_the_record_beside_the_render(monkeypatch):
    monkeypatch.setattr(server, "render_once", _fake_render)
    monkeypatch.setattr(prompts, "draft", lambda **kw: dict(DRAFT))

    body = client.post("/api/generate", json={"count": 1, "difficulty": 4}).json()
    ids = [j["image_id"] for j in body["jobs"]]
    try:
        _drain([j["job_id"] for j in body["jobs"]], timeout=20)
        side = json.loads((server.LIBRARY / f"{ids[0]}.json").read_text())
        assert side["difficulty"] == 4
    finally:
        _cleanup(ids)


def test_a_single_level_still_makes_exactly_that_many(monkeypatch):
    monkeypatch.setattr(server, "render_once", _fake_render)
    monkeypatch.setattr(prompts, "draft", lambda **kw: dict(DRAFT))

    body = client.post("/api/generate", json={"count": 3, "difficulty": 2}).json()
    ids = [j["image_id"] for j in body["jobs"]]
    try:
        assert len(body["jobs"]) == 3
        assert {j["difficulty"] for j in body["jobs"]} == {2}
        _drain([j["job_id"] for j in body["jobs"]], timeout=20)
    finally:
        _cleanup(ids)
