"""Drafting without rendering.

Prompt quality is what decides whether a render passes, and until now the only
way to see a written prompt was to pay for the image it produced.
"""

from fastapi.testclient import TestClient

from scripts.chonky import prompts
from scripts.chonky.web import server

client = TestClient(server.app)

DRAFT = {"city": "Prague", "country": "Czech Republic", "prompt": "a prompt",
         "clues": ["a", "b", "c"], "clue_words": ["x", "y", "z"]}


def test_draft_returns_a_prompt_without_rendering(monkeypatch):
    rendered = []
    monkeypatch.setattr(server, "render_once",
                        lambda p, o, log=None: (rendered.append(p), o)[1])
    monkeypatch.setattr(prompts, "draft", lambda **kw: dict(DRAFT))

    body = client.post("/api/draft", json={"difficulty": 1}).json()
    assert body["prompt"] == "a prompt"
    assert body["city"] == "Prague"
    assert rendered == [], "drafting must not spend a render"


def test_draft_reports_a_rejected_prompt_rather_than_raising(monkeypatch):
    def boom(**kw):
        raise prompts.DraftError("says 'no illustration style'")

    monkeypatch.setattr(prompts, "draft", boom)
    r = client.post("/api/draft", json={"difficulty": 1})
    assert r.status_code == 400
    assert "illustration" in r.json()["error"]
