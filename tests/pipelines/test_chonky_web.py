from fastapi.testclient import TestClient

from scripts.chonky.web.server import app

client = TestClient(app)


def test_health_reports_the_authored_geometry():
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["image"] == "2048x2560"
    assert body["viewframe"] == "1200x2133 at x 424-1624, y 213-2346"
    assert body["chonky_height_px"] == "65-105"


def test_prompts_endpoint_parses_tsv_and_assigns_zones():
    tsv = ("Difficulty\tLocation\tprompt\n"
           "1\tLisbon, Portugal\tA photo of Rua Augusta\n"
           "2\tOslo, Norway\tA photo of Karl Johans gate\n")
    r = client.post("/api/prompts", json={"tsv": tsv})
    assert r.status_code == 200
    rows = r.json()["rows"]
    assert len(rows) == 2
    assert rows[0]["difficulty"] == "1"
    assert rows[0]["city"] == "Lisbon"
    assert rows[0]["country"] == "Portugal"
    assert rows[0]["target_zone"] in ("viewframe", "margin")
    assert rows[0]["prompt"].startswith("A photo of Rua Augusta")


def test_prompts_endpoint_tolerates_a_missing_header():
    tsv = "1\tLisbon, Portugal\tA photo\n"
    rows = client.post("/api/prompts", json={"tsv": tsv}).json()["rows"]
    assert len(rows) == 1
    assert rows[0]["difficulty"] == "1"


def test_measure_endpoint_accepts_a_corrected_box():
    r = client.post("/api/measure", json={"box": [1232, 1709, 1318, 1798]})
    assert r.status_code == 200
    body = r.json()
    assert body["height_px"] == 89
    assert body["zone"] == "viewframe"
    assert body["size"] == "ok"
    assert body["ok"] is True


def test_measure_endpoint_flags_an_oversized_cat():
    body = client.post("/api/measure", json={"box": [1190, 1240, 1440, 1630]}).json()
    assert body["size"] == "too_big"
    assert body["ok"] is False


def test_measure_endpoint_flags_centrestage():
    body = client.post("/api/measure", json={"box": [900, 1709, 1000, 1798]}).json()
    assert body["zone"] == "centrestage"
    assert body["ok"] is False


def test_index_is_served_and_sets_the_base_href():
    r = client.get("/")
    assert r.status_code == 200
    assert '<base href="/chonky/">' in r.text


def test_the_launcher_mounts_chonky():
    """The sub-app must be reachable at /chonky through the real launcher.

    index.html sets <base href="/chonky/">, so serving it anywhere else breaks
    every relative fetch. This is the contract the other pipelines document.
    """
    from web.server import PIPELINE_MODULES, app as launcher

    assert "chonky" in PIPELINE_MODULES
    mounted = [getattr(r, "path", "") for r in launcher.routes if hasattr(r, "app")]
    assert "/chonky" in mounted


def test_jobs_are_shaped_for_the_deploy_guard():
    """web/server.py disables Deploy while any pipeline job is in flight.

    It reads `jobs` off each module and looks for .status in ("queued",
    "running"), so a render must be visible to it or a deploy could land
    mid-render.
    """
    from scripts.chonky.web.server import Job, jobs

    j = Job(id="abc", slug="img1", image_id="img1")
    assert j.status == "running"
    for attr in ("id", "kind", "slug", "status", "started_at"):
        assert hasattr(j, attr), attr
    assert isinstance(jobs, dict)


def test_health_reports_readiness_of_every_dependency():
    """Health is the preflight check.

    The pipeline has four external dependencies and each fails differently and
    late: no Drive/sheet config means Approve dies at the end of a batch, no
    OpenArt token means Render dies at the start, no service account means the
    upload 403s, and no detector silently degrades measurement. Surfacing all
    four in one place is how an operator finds out before spending credits
    rather than after.
    """
    body = client.get("/api/health").json()
    assert "ready" in body
    checks = body["ready"]
    for key in ("drive_folder", "sheet", "openart_token", "service_account", "detector"):
        assert key in checks, f"health must report {key}"
        assert "ok" in checks[key]
        assert "detail" in checks[key]


def test_health_never_leaks_secrets():
    """Ids are masked and no key material is returned."""
    import json

    raw = json.dumps(client.get("/api/health").json())
    assert "private_key" not in raw
    assert "BEGIN PRIVATE KEY" not in raw
    # a configured id must be shown masked, never in full
    assert "1GVpjyHEI40y2ewy6ksXKA88EdnYl1oN2" not in raw


def test_health_reports_which_interpreter_is_serving():
    """"Dependency missing" is ambiguous on a machine with several Pythons.

    An install can succeed and still leave the check failing, because it went
    into a different interpreter than the one running the server. Reporting
    sys.executable turns a confusing result into an obvious one.
    """
    import sys

    detail = client.get("/api/health").json()["ready"]["python"]["detail"]
    assert sys.executable in detail
    assert sys.version.split()[0] in detail


def test_a_render_survives_a_restart(tmp_path, monkeypatch):
    """A deploy must not make an image you already paid credits for unreviewable.

    The in-memory index is a cache of the library, not the record of it.
    """
    from PIL import Image as _Image

    from scripts.chonky.web import server as srv

    img = _Image.new("RGB", (2048, 2560), (90, 90, 90))
    img.save(srv.LIBRARY / "restart-probe.png")
    try:
        srv._images.pop("restart-probe", None)      # as if the process just started
        assert srv._open("restart-probe") is not None
    finally:
        (srv.LIBRARY / "restart-probe.png").unlink(missing_ok=True)
        srv._images.pop("restart-probe", None)


def test_an_image_that_was_never_rendered_is_still_unknown():
    from scripts.chonky.web import server as srv

    assert srv._open("no-such-image-at-all") is None
