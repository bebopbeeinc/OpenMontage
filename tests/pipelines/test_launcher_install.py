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


# --------------------------------------------------------------------------
# Deploy has to restart when the RUNNING CODE is stale — not when the pull
# happened to move HEAD.
#
# On the machine where development happens, the local repo is the source:
# commits are made here and pushed out, so `git pull` always answers "Already
# up to date" and `changed` is always False. The old condition keyed the
# restart off `changed`, so Deploy could never restart this machine. The
# server was found running code from 21 Sep — ten days stale, with /api/renders
# 404ing while health answered — because every Deploy press was a no-op.
#
# The real question is not "did the pull bring anything" but "is the code this
# process is running older than the code on disk". Comparing HEAD against the
# HEAD the process booted at answers that, and strictly generalises the old
# behaviour: a pull that moves HEAD past the boot commit is still a restart.
# --------------------------------------------------------------------------

def _deploy_with(monkeypatch, *, boot_head, head, pull_rc=0, supervised=True):
    """Drive POST /api/deploy with git and the restart stubbed out."""
    monkeypatch.setattr(server, "_BOOT_HEAD", boot_head)
    monkeypatch.setattr(server, "SUPERVISED", supervised)
    monkeypatch.setattr(server, "_scan_active_jobs", lambda: [])
    monkeypatch.setattr(server, "_git_head", lambda: head)
    # Without this the background task exits the test runner with code 75.
    monkeypatch.setattr(server, "_schedule_supervised_restart", lambda: None)

    real_run = server.subprocess.run
    monkeypatch.setattr(server.subprocess, "run", lambda a, **k: (
        subprocess.CompletedProcess(a, pull_rc, "Already up to date.\n", "")
        if a[:2] == ["git", "pull"] else real_run(a, **k)))

    return client.post("/api/deploy").json()


def test_a_stale_process_restarts_even_when_the_pull_brings_nothing(monkeypatch):
    """The dev-machine case: local commits, so the pull is always a no-op."""
    body = _deploy_with(monkeypatch, boot_head="old111", head="new222")
    assert body["changed"] is False, "the pull genuinely brought nothing"
    assert body["stale"] is True, "but the running process is behind the tree"
    assert body["restart_method"] == "supervisor"


def test_no_restart_when_the_running_code_already_matches_head(monkeypatch):
    """Guards against a restart loop: a fresh process must not re-restart."""
    body = _deploy_with(monkeypatch, boot_head="same333", head="same333")
    assert body["stale"] is False
    assert body["restart_method"] == "none"


def test_the_running_commit_is_reported(monkeypatch):
    """The operator needs to see WHICH commit the live process is serving."""
    body = _deploy_with(monkeypatch, boot_head="old111", head="new222")
    assert body["running_head"] == "old111"


def test_it_falls_back_to_the_pull_result_when_the_boot_commit_is_unknown(monkeypatch):
    """_git_head returns "" when git fails; never treat that as infinitely stale."""
    body = _deploy_with(monkeypatch, boot_head="", head="new222")
    assert body["stale"] is False, "unknown boot commit must not force a restart"
    assert body["restart_method"] == "none"


def test_a_failed_pull_never_restarts(monkeypatch):
    body = _deploy_with(monkeypatch, boot_head="old111", head="new222", pull_rc=1)
    assert body["ok"] is False
    assert body["restart_method"] == "none"


def test_the_boot_commit_is_recorded_at_import():
    """If this is not captured at start-up there is nothing to compare against."""
    assert hasattr(server, "_BOOT_HEAD")


def test_the_launcher_page_is_not_cached():
    """Same fault as the Chonky UI: inline JS, served from disk, cached anyway.

    This is the page carrying the Deploy button, so a stale copy of it is a
    stale copy of the control used to un-stale everything else.
    """
    r = client.get("/")
    assert r.status_code == 200
    assert "no-store" in r.headers.get("cache-control", "").lower()
