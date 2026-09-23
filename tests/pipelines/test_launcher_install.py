"""The launcher's dependency installer.

The bug this covers: a package can be installed successfully and the server
still not see it, because it went into a different Python than the one
serving. From outside, that is indistinguishable from a failed install.
"""

import subprocess
import sys

from fastapi.testclient import TestClient

from web import server

client = TestClient(server.app)


def test_installs_into_the_interpreter_that_is_serving():
    """The whole point: pip must target sys.executable, not whatever `pip` resolves to."""
    seen = {}

    def fake_run(args, **kwargs):
        seen["args"] = args
        return subprocess.CompletedProcess(args, 0, "ok", "")

    real_run = server.subprocess.run
    server.subprocess.run = lambda a, **k: (
        fake_run(a, **k) if a[:3] == [sys.executable, "-m", "pip"] else real_run(a, **k)
    )
    try:
        server._run_install()
    finally:
        server.subprocess.run = real_run

    assert seen["args"][0] == sys.executable
    assert seen["args"][1:4] == ["-m", "pip", "install"]


def test_only_installs_requirements_files_git_knows_about():
    """It executes what these files name, so a stray working-tree file must not count."""
    files = server._requirements_files()
    assert files, "expected at least one committed requirements file"
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=str(server.REPO),
        capture_output=True, text=True, timeout=30,
    ).stdout.split()
    for f in files:
        assert f in tracked
    assert all(f.startswith("requirements") and f.endswith(".txt") for f in files)


def test_takes_no_package_names_from_the_caller():
    """An HTTP caller must not be able to choose what runs on the build machine."""
    import inspect

    sig = inspect.signature(server.install_deps)
    assert not sig.parameters, f"installer accepts caller input: {sig}"


def test_refuses_while_a_second_install_is_in_flight():
    server._install_state["state"] = "running"
    try:
        r = client.post("/api/install-deps")
        assert r.status_code == 409
        assert r.json()["reason"] == "already_running"
    finally:
        server._install_state["state"] = "idle"


def test_refuses_while_pipeline_jobs_are_running():
    """Swapping a library out from under a running render is worse than waiting."""
    real = server._scan_active_jobs
    server._scan_active_jobs = lambda: [{"pipeline": "chonky", "id": "j1"}]
    try:
        r = client.post("/api/install-deps")
        assert r.status_code == 409
        assert r.json()["reason"] == "active_jobs"
    finally:
        server._scan_active_jobs = real


def test_status_is_readable_without_starting_one():
    body = client.get("/api/install-deps").json()
    assert body["state"] in {"idle", "running", "done", "failed"}


def test_the_deploy_guard_counts_a_job_that_is_still_writing_its_prompt():
    """Drafting is serialized, so a batch can sit in `drafting` for minutes.

    A guard blind to that state lets a deploy restart kill the batch, and lets
    an install swap libraries out from under renders about to start.
    """
    assert "drafting" in server.ACTIVE_JOB_STATUSES
