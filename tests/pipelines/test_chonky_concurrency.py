"""Two renders at once.

/api/generate starts one render thread per image, so everything render_once
touches is touched concurrently. The submission log was captured by swapping
sys.stderr for the duration of a render, which is a process-global change made
from a thread: after two overlapping renders the real stderr never came back,
and one render's references were recorded against another's job.
"""

import sys
import threading
import time
from pathlib import Path

from scripts.chonky.render import render_once


def _driver_writing(marker, hold):
    """Stands in for the OpenArt driver, which reports through the `log` sink."""
    def driver(**kwargs):
        log = kwargs.get("log")
        if log is not None:
            log.append(f"  -> reference for {marker}")
        hold.wait(2)
        out = kwargs["output_paths"][0]
        Path(out).write_bytes(b"x")
        return [out]
    return driver


def test_two_renders_do_not_record_each_others_submissions(tmp_path):
    """A log that attributes one render's references to another is worse than none."""
    gate_a, gate_b = threading.Event(), threading.Event()
    log_a, log_b = [], []

    def run_a():
        render_once("a", tmp_path / "a.png",
                    driver=_driver_writing("A", gate_a), log=log_a)

    def run_b():
        render_once("b", tmp_path / "b.png",
                    driver=_driver_writing("B", gate_b), log=log_b)

    ta = threading.Thread(target=run_a)
    tb = threading.Thread(target=run_b)
    ta.start()
    time.sleep(0.05)          # A is inside its render when B starts
    tb.start()
    time.sleep(0.05)
    gate_a.set()
    gate_b.set()
    ta.join(5)
    tb.join(5)

    assert log_a == ["  -> reference for A"], log_a
    assert log_b == ["  -> reference for B"], log_b


def test_rendering_never_replaces_the_process_stderr(tmp_path):
    """The console is shared. A render must not take it away from everything else."""
    real = sys.stderr
    gate = threading.Event()
    gate.set()

    threads = [
        threading.Thread(target=lambda i=i: render_once(
            str(i), tmp_path / f"{i}.png",
            driver=_driver_writing(str(i), gate), log=[]))
        for i in range(3)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)

    assert sys.stderr is real, f"stderr was left replaced by {type(sys.stderr).__name__}"
