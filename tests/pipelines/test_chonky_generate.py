"""One button: pick a location, write the prompt, render, measure.

The person using this tool never writes a prompt, so everything between the
button and a reviewable card has to happen without them.
"""

import json
import time

import pytest

from fastapi.testclient import TestClient
from PIL import Image

from scripts.chonky import prompts
from scripts.chonky.web import server

client = TestClient(server.app)

DRAFT = {
    "city": "Prague",
    "country": "Czech Republic",
    "viewpoint": "Charles Bridge, a third of the way across from the Old Town end, facing west",
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


def test_the_whole_batch_is_placed_in_one_decision(monkeypatch):
    """Two drafts in one batch once both chose Sydney.

    They were each handed the same "already used" list, computed before any of
    them ran, so nothing told the second what the first had taken. It was
    fixed by writing the prompts one after another; that made a batch of
    fifteen take twenty minutes before the last render even started.

    The guarantee now comes from deciding every location up front, in one
    request that can see all of them at once — which is also what frees the
    writing to happen in parallel. This asserts the decision is made once, for
    the whole batch; the picker's own tests cover it refusing a repeat.
    """
    monkeypatch.setattr(server, "render_once", _fake_render)
    monkeypatch.setattr(prompts, "draft",
                        lambda **kw: dict(DRAFT, city=kw["city"], country=kw["country"]))

    calls = []
    monkeypatch.setattr(
        prompts, "pick_locations",
        lambda **kw: (calls.append(kw["slots"]),
                      [{"city": f"City{i}", "country": "X"}
                       for i in range(len(kw["slots"]))])[1])

    body = client.post("/api/generate", json={"count": 3}).json()
    ids = [j["image_id"] for j in body["jobs"]]
    try:
        _drain([j["job_id"] for j in body["jobs"]], timeout=25)
        assert len(calls) == 1, f"the batch was placed in {len(calls)} decisions"
        assert len(calls[0]) == 3, "the decision must see every image at once"
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


def test_the_prompts_are_written_at_the_same_time(monkeypatch):
    """The slow part of a batch must overlap, not queue.

    Each stub draft waits until all three have started. Serialized, the first
    never returns and the barrier times out — which is precisely the wall
    clock this change exists to remove.
    """
    import threading

    monkeypatch.setattr(server, "render_once", _fake_render)
    monkeypatch.setattr(prompts, "pick_locations",
                        lambda **kw: [{"city": f"City{i}", "country": "X"}
                                      for i in range(len(kw["slots"]))])

    started = threading.Barrier(3, timeout=8)

    def overlapping(**kw):
        started.wait()
        return dict(DRAFT, city=kw["city"], country=kw["country"])

    monkeypatch.setattr(prompts, "draft", overlapping)

    body = client.post("/api/generate", json={"count": 3}).json()
    ids = [j["image_id"] for j in body["jobs"]]
    try:
        states = _drain([j["job_id"] for j in body["jobs"]], timeout=25)
        assert states == ["done", "done", "done"], states
    finally:
        _cleanup(ids)


def test_each_prompt_is_written_for_the_location_that_was_chosen(monkeypatch):
    """Parallel writing must not cost the no-repeat guarantee."""
    monkeypatch.setattr(server, "render_once", _fake_render)
    monkeypatch.setattr(prompts, "pick_locations",
                        lambda **kw: [{"city": "Prague", "country": "CZ"},
                                      {"city": "Porto", "country": "PT"}])
    asked = []
    monkeypatch.setattr(prompts, "draft",
                        lambda **kw: (asked.append(kw["city"]),
                                      dict(DRAFT, city=kw["city"],
                                           country=kw["country"]))[1])

    body = client.post("/api/generate", json={"count": 2}).json()
    ids = [j["image_id"] for j in body["jobs"]]
    try:
        _drain([j["job_id"] for j in body["jobs"]], timeout=20)
        assert sorted(asked) == ["Porto", "Prague"]
    finally:
        _cleanup(ids)


def test_the_picker_sees_what_is_already_in_the_library(monkeypatch):
    monkeypatch.setattr(server, "render_once", _fake_render)
    seen = {}
    monkeypatch.setattr(prompts, "pick_locations",
                        lambda **kw: (seen.update(kw),
                                      [{"city": "Prague", "country": "CZ"}])[1])
    monkeypatch.setattr(prompts, "draft", lambda **kw: dict(DRAFT))

    Image.new("RGB", (64, 80), (1, 2, 3)).save(server.LIBRARY / "picker-probe.png")
    (server.LIBRARY / "picker-probe.json").write_text(
        json.dumps({"image_id": "picker-probe", "location": "Lisbon, Portugal"}))
    try:
        body = client.post("/api/generate", json={"count": 1}).json()
        ids = [j["image_id"] for j in body["jobs"]]
        _drain([j["job_id"] for j in body["jobs"]], timeout=20)
        assert "Lisbon, Portugal" in seen["used"]
        _cleanup(ids)
    finally:
        _cleanup(["picker-probe"])


def test_the_clues_are_rewritten_from_the_finished_render(monkeypatch):
    """The clues on the tile must describe the image, not the prompt.

    Tallinn's clue described a plate marking the render never produced. The
    clues written at draft time are a prediction; these are an observation.
    """
    from scripts.chonky import inspect as inspect_mod

    monkeypatch.setattr(server, "render_once", _fake_render)
    monkeypatch.setattr(prompts, "draft", lambda **kw: dict(DRAFT))
    monkeypatch.setattr(
        inspect_mod, "inspect_render",
        lambda path, **kw: {"clues": ["seen one", "seen two", "seen three"],
                            "clue_words": ["one", "two", "three"],
                            "names_the_place": False,
                            "names_the_place_detail": ""})

    body = client.post("/api/generate", json={"count": 1}).json()
    ids = [j["image_id"] for j in body["jobs"]]
    try:
        _drain([j["job_id"] for j in body["jobs"]], timeout=20)
        side = json.loads((server.LIBRARY / f"{ids[0]}.json").read_text())
        assert side["clues"] == ["seen one", "seen two", "seen three"]
        assert side["clue_words"] == ["one", "two", "three"]
    finally:
        _cleanup(ids)


def test_a_render_that_spells_its_own_answer_is_flagged(monkeypatch):
    """A sign reading "Seattle" is the answer key, and the reviewer must see it."""
    from scripts.chonky import inspect as inspect_mod

    monkeypatch.setattr(server, "render_once", _fake_render)
    monkeypatch.setattr(prompts, "draft", lambda **kw: dict(DRAFT))
    monkeypatch.setattr(
        inspect_mod, "inspect_render",
        lambda path, **kw: {"clues": ["a", "b", "c"], "clue_words": ["a", "b", "c"],
                            "names_the_place": True,
                            "names_the_place_detail": "a sign reads Seattle"})

    body = client.post("/api/generate", json={"count": 1}).json()
    ids = [j["image_id"] for j in body["jobs"]]
    try:
        _drain([j["job_id"] for j in body["jobs"]], timeout=20)
        side = json.loads((server.LIBRARY / f"{ids[0]}.json").read_text())
        assert side["names_the_place"] is True
        assert "Seattle" in side["names_the_place_detail"]
    finally:
        _cleanup(ids)


def test_the_render_survives_an_inspection_failure(monkeypatch):
    """A paid render must not be lost because the clue pass fell over."""
    from scripts.chonky import inspect as inspect_mod

    monkeypatch.setattr(server, "render_once", _fake_render)
    monkeypatch.setattr(prompts, "draft", lambda **kw: dict(DRAFT))

    def boom(path, **kw):
        raise inspect_mod.InspectError("the CLI fell over")

    monkeypatch.setattr(inspect_mod, "inspect_render", boom)

    body = client.post("/api/generate", json={"count": 1}).json()
    ids = [j["image_id"] for j in body["jobs"]]
    try:
        states = _drain([j["job_id"] for j in body["jobs"]], timeout=20)
        assert states == ["done"], states
        side = json.loads((server.LIBRARY / f"{ids[0]}.json").read_text())
        assert side["measurement"] is not None
        # Falls back to what the writer predicted, and says that it did.
        assert side["clues"] == DRAFT["clues"]
        assert "the CLI fell over" in side["clue_source_error"]
    finally:
        _cleanup(ids)
