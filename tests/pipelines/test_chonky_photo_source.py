"""Where the reference photograph comes from, and whether it is worth looking at.

Measured on the live server: 4 of 4 anchored renders used upload.wikimedia.org.
There was no domain restriction anywhere — the bias came from the prompt's own
demand for "a direct link to an image file", which Commons is one of the few
sources to reliably satisfy.

And nothing asked for an attractive photograph. The only mention of beauty
argued against it: "A pretty view with nothing to read is not a geography
puzzle." Manual 3.3 has required "colourful, cinematic, realistic vacation
photography" all along — of the render. Now that the photograph IS the scene,
it governs the pick too.
"""

import json
import time

import pytest

from scripts.chonky import verify_place as V

# Bound at import, before conftest's offline guard replaces the attribute.
verify_viewpoint = V.verify_viewpoint

FOUND = {"real": True, "confidence": "high", "note": "n",
         "photo_url": "https://images.example.com/street.jpg", "details": {}}


def _seen_user(payload):
    seen = {}

    def caller(system, user, *, model=None):
        seen["user"] = user
        return json.dumps(payload)

    return seen, caller


# --- the ban ---------------------------------------------------------------

def test_the_request_rules_out_wikipedia():
    seen, caller = _seen_user(FOUND)
    verify_viewpoint(city="Cusco", country="Peru", viewpoint="x", caller=caller)
    user = seen["user"].lower()
    assert "wikipedia" in user and "wikimedia" in user


def test_the_request_names_somewhere_else_to_look():
    """A ban alone strands the search: Commons is what satisfied the
    direct-image-URL requirement in the first place."""
    seen, caller = _seen_user(FOUND)
    verify_viewpoint(city="Cusco", country="Peru", viewpoint="x", caller=caller)
    user = seen["user"].lower()
    assert "tourism" in user or "stock" in user or "photo-sharing" in user


@pytest.mark.parametrize("url", [
    "https://upload.wikimedia.org/wikipedia/commons/8/8f/Cuesta.jpg",
    "https://commons.wikimedia.org/wiki/File:Cuesta.jpg",
    "https://en.wikipedia.org/wiki/Cusco.jpg",
    "https://WIKIMEDIA.ORG/x.jpg",
])
def test_a_wikimedia_photograph_is_discarded_whatever_the_model_says(url):
    """Instructing the model has not been enough anywhere else in this project."""
    out = verify_viewpoint(city="Cusco", country="Peru", viewpoint="x",
                           caller=lambda s, u, model=None: json.dumps(
                               dict(FOUND, photo_url=url)))
    assert out["photo_url"] == ""
    assert "wikipedia" in out["photo_note"].lower() or \
        "wikipedia" in (out.get("photo_error") or "").lower()


def test_a_photograph_from_anywhere_else_is_kept():
    out = verify_viewpoint(city="Cusco", country="Peru", viewpoint="x",
                           caller=lambda s, u, model=None: json.dumps(FOUND))
    assert out["photo_url"] == "https://images.example.com/street.jpg"


def test_a_host_merely_containing_wiki_is_not_banned():
    """The rule is the Wikimedia family, not the four letters."""
    url = "https://images.wikiwand-lookalike.com/a.jpg"
    out = verify_viewpoint(city="Cusco", country="Peru", viewpoint="x",
                           caller=lambda s, u, model=None: json.dumps(
                               dict(FOUND, photo_url=url)))
    assert out["photo_url"] == url


# --- the look --------------------------------------------------------------

def test_the_request_asks_for_a_photograph_worth_looking_at():
    seen, caller = _seen_user(FOUND)
    verify_viewpoint(city="Paris", country="France", viewpoint="x",
                     difficulty=1, caller=caller)
    user = seen["user"].lower()
    assert "cinematic" in user or "travel photograph" in user


def test_low_levels_ask_for_the_postcard_shot():
    seen, caller = _seen_user(FOUND)
    verify_viewpoint(city="Paris", country="France", viewpoint="x",
                     difficulty=1, caller=caller)
    assert "postcard" in seen["user"].lower()


def test_high_levels_ask_for_a_beautiful_ordinary_place():
    """A landmark is banned at 4+, so glamour has to mean something else."""
    seen, caller = _seen_user(FOUND)
    verify_viewpoint(city="Cusco", country="Peru", viewpoint="x",
                     difficulty=5, caller=caller)
    user = seen["user"].lower()
    assert "ordinary" in user
    assert "landmark" in user


def test_beauty_and_evidence_are_both_required_not_traded():
    """The old line opposed them: "a pretty view with nothing to read"."""
    seen, caller = _seen_user(FOUND)
    verify_viewpoint(city="Cusco", country="Peru", viewpoint="x", caller=caller)
    assert "a pretty view with nothing to read" not in seen["user"].lower()


def test_watermarked_and_captioned_photographs_are_ruled_out():
    """Manual 3.3 forbids them in the render, and stock sites are full of them."""
    seen, caller = _seen_user(FOUND)
    verify_viewpoint(city="Cusco", country="Peru", viewpoint="x", caller=caller)
    user = seen["user"].lower()
    assert "watermark" in user
    assert "collage" in user or "caption" in user


# --- the two things that make the ban safe ---------------------------------

def test_a_url_need_not_end_in_a_file_extension():
    """images.unsplash.com serves the picture with no extension at all.

    The old wording demanded "a real image file (.jpg/.png)", which would make
    the model self-reject the best replacements for Commons. fetch_reference
    validates Content-Type, so the extension was never load-bearing.
    """
    seen, caller = _seen_user(FOUND)
    verify_viewpoint(city="Cusco", country="Peru", viewpoint="x", caller=caller)
    # Collapsed: the prompt is hard-wrapped, and the assertion is about what
    # it says, not where the lines break.
    user = " ".join(seen["user"].lower().split())
    assert "does not need to end in" in user or "no file extension" in user


def test_banned_hosts_are_rejected_before_anything_is_downloaded(tmp_path):
    """A banned URL must cost nothing — not a request, not a redirect."""
    from scripts.chonky import reference_photo as RP

    called = []

    def opener(url):
        called.append(url)
        raise AssertionError("must not reach the network")

    with pytest.raises(RP.ReferenceError):
        RP.fetch_reference("https://upload.wikimedia.org/a.jpg",
                           tmp_path / "r.png", opener=opener)
    assert called == [], "a banned host was fetched before it was checked"


@pytest.mark.parametrize("url", [
    "https://images.unsplash.com/photo-wikimedia.org-1.jpg",
    "https://notwikimedia.org.example.com/a.jpg",
    "https://live.staticflickr.com/1/2_b.jpg",
])
def test_an_allowed_host_is_not_caught_by_the_ban(url, tmp_path):
    """Hostname parsing, not substring matching. This is the test that stops
    the obvious wrong implementation."""
    import io

    from PIL import Image

    from scripts.chonky import reference_photo as RP

    buf = io.BytesIO()
    Image.new("RGB", (800, 600), (90, 110, 130)).save(buf, format="PNG")
    out = RP.fetch_reference(url, tmp_path / "r.png",
                             opener=lambda u: ("image/png", buf.getvalue()))
    assert out["source_url"] == url


# --- never redraw a photograph that is not there ---------------------------

def test_no_photograph_sends_the_writer_somewhere_else():
    """Caglar's decision: every delivered image stays anchored to a real photo.

    An empty photo_url used to fall through to the writer's invented scene,
    which is the thing anchoring exists to replace.
    """
    from scripts.chonky import prompts

    drafted = {
        "city": "Cusco", "country": "Peru",
        "viewpoint": "Cuesta de San Blas, facing downhill",
        # Compliant, so the photo check is what fails rather than the
        # depth-ordering rule getting there first.
        "prompt": ("An invented street scene. Chonky sits far beyond all of "
                   "them on the cobblestones."),
        "chonky_line": "Chonky sits far beyond all of them on the cobbles.",
        "clues": ["a", "b", "c"], "clue_words": ["x", "y", "z"],
    }
    unphotographed = {"real": True, "confidence": "high", "note": "found it",
                      "verified": True, "photo_url": "", "photo_note": "",
                      "details": {}}

    with pytest.raises(prompts.DraftError) as exc:
        prompts.draft(difficulty=1, target_zone="viewframe", used=[],
                      caller=lambda s, u, model=None: json.dumps(drafted),
                      verifier=lambda **kw: unphotographed)
    assert "photograph" in str(exc.value).lower()


def test_a_render_is_not_asked_to_redraw_a_photograph_it_does_not_have(monkeypatch):
    """The prompt says REDRAW THE ATTACHED PHOTOGRAPH. With nothing attached
    the model invents a street and every downstream check reports success —
    the original failure, wearing the words of the fix."""
    import json as _json

    from PIL import Image

    from scripts.chonky import prompts as prompts_mod
    from scripts.chonky import reference_photo as rp
    from scripts.chonky.web import server as srv
    from fastapi.testclient import TestClient

    client = TestClient(srv.app)
    rendered = []

    monkeypatch.setattr(
        srv, "render_once",
        lambda p, o, log=None, **kw: (rendered.append(p),
                                      Image.new("RGB", (64, 80)).save(o), o)[2])
    monkeypatch.setattr(prompts_mod, "draft", lambda **kw: {
        "city": "Cusco", "country": "Peru", "viewpoint": "v",
        "prompt": "REDRAW THE ATTACHED PHOTOGRAPH. ...",
        "chonky_line": "c", "clues": ["a", "b", "c"],
        "clue_words": ["x", "y", "z"], "photo_url": "https://x/gone.jpg",
    })

    def boom(url, dest, **kw):
        raise rp.ReferenceError("404 not found")

    monkeypatch.setattr(rp, "fetch_reference", boom)

    body = client.post("/api/generate", json={"count": 1}).json()
    ids = [j["image_id"] for j in body["jobs"]]
    try:
        end = time.time() + 15
        while time.time() < end:
            st = client.get(f"/api/jobs/{body['jobs'][0]['job_id']}").json()
            if st["state"] not in ("drafting", "running"):
                break
            time.sleep(0.05)
        assert rendered == [], "rendered a redraw prompt with no photograph"
        assert st["state"] == "failed"
        assert "photograph" in (st["error"] or "").lower()
    finally:
        for i in ids:
            (srv.LIBRARY / f"{i}.png").unlink(missing_ok=True)
            (srv.LIBRARY / f"{i}.json").unlink(missing_ok=True)
            srv._images.pop(i, None)
