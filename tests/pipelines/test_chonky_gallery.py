"""The page must show the work that exists.

Five renders sat on the server, each costing credits, and the page opened to
an empty box because cards were only ever built from a freshly-parsed TSV.
"""

import json
import time

from fastapi.testclient import TestClient
from PIL import Image

from scripts.chonky.web import server

client = TestClient(server.app)


def _write_render(image_id, measurement=None, **extra):
    Image.new("RGB", (1344, 1680), (140, 140, 140)).save(server.LIBRARY / f"{image_id}.png")
    if measurement is not None or extra:
        side = {"image_id": image_id, "measurement": measurement, **extra}
        (server.LIBRARY / f"{image_id}.json").write_text(json.dumps(side))


def _cleanup(*ids):
    for i in ids:
        (server.LIBRARY / f"{i}.png").unlink(missing_ok=True)
        (server.LIBRARY / f"{i}.json").unlink(missing_ok=True)
        server._images.pop(i, None)


def test_renders_lists_what_is_on_disk():
    try:
        _write_render("gal-a", measurement={"height_px": 60, "ok": True},
                      location="Prague, Czech Republic", prompt="a prompt")
        rows = client.get("/api/renders").json()["renders"]
        ids = [r["image_id"] for r in rows]
        assert "gal-a" in ids
        row = next(r for r in rows if r["image_id"] == "gal-a")
        assert row["measurement"]["height_px"] == 60
        assert row["location"] == "Prague, Czech Republic"
        assert row["prompt"] == "a prompt"
    finally:
        _cleanup("gal-a")


def test_a_render_with_no_sidecar_is_still_listed():
    """Renders made before sidecars existed must not be invisible."""
    try:
        _write_render("gal-orphan")
        rows = client.get("/api/renders").json()["renders"]
        row = next((r for r in rows if r["image_id"] == "gal-orphan"), None)
        assert row is not None, "a render on disk was hidden from the page"
        assert row["measurement"] is None
    finally:
        _cleanup("gal-orphan")


def test_newest_render_comes_first():
    try:
        _write_render("gal-old")
        time.sleep(0.02)
        _write_render("gal-new")
        ids = [r["image_id"] for r in client.get("/api/renders").json()["renders"]]
        assert ids.index("gal-new") < ids.index("gal-old")
    finally:
        _cleanup("gal-old", "gal-new")


def test_a_finished_render_records_itself_beside_the_file():
    """What a render was must outlive the process that made it."""
    import scripts.chonky.web.server as srv

    image_id = "sidecar-probe"
    try:
        def fake_render(prompt, out, log=None):
            Image.new("RGB", (1344, 1680), (150, 150, 150)).save(out)
            return out

        real = srv.render_once
        srv.render_once = fake_render
        try:
            job = client.post("/api/run", json={
                "id": image_id, "prompt": "a prompt",
                "location": "Prague, Czech Republic", "difficulty": "1",
            }).json()
            for _ in range(100):
                if client.get(f"/api/jobs/{job['job_id']}").json()["state"] != "running":
                    break
                time.sleep(0.05)
        finally:
            srv.render_once = real

        side = json.loads((srv.LIBRARY / f"{image_id}.json").read_text())
        assert side["prompt"] == "a prompt"
        assert side["location"] == "Prague, Czech Republic"
        assert side["measurement"] is not None
    finally:
        _cleanup(image_id)
