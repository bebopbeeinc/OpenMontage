"""Submit exactly one OpenArt render for one image.

There is deliberately no retry and no loop here. Duplicate submissions were the
single most expensive failure of the earlier ChatGPT-driven build — the model
resubmitted whenever an approval prompt interrupted it, and no instruction
stopped it. Here one call to `render_once` is one image, and a reroll is an
explicit second call by a caller that has stated why.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable, Optional

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

MODEL = "GPT Image 2.5 Sunburst"
ASPECT = "4:5"          # returns 2048x2560; customWidth/Height are ignored
RESOLUTION = "4K"
CHARACTER = "Chonky"    # stills in character_library/chonky/

# Chonky bills one named OpenArt workspace and nothing else.
#
# The shared driver falls back to another workspace when the primary runs out
# of credits. That is disabled here: a credit shortfall must fail loudly
# rather than quietly spending a balance nobody chose.
#
# The default is the workspace on the company account (contact@bebopbee.com)
# that actually carries credits. "BebopBee Art Team" exists on the same
# account but is a Free plan with a near-zero balance, so pointing at it just
# fails on the first render.
WORKSPACE = os.environ.get("CHONKY_OPENART_WORKSPACE", "R N")
FALLBACK_WORKSPACES: tuple[str, ...] = ()


def render_once(prompt: str, out_path: Path, *, driver: Optional[Callable] = None,
                log: Optional[list] = None) -> Path:
    """Render `prompt` to `out_path`. One submission, no retries.

    `driver` is injectable so tests never reach OpenArt. `log`, when given,
    collects the driver's own account of the submission — which workspace,
    and which visual references were attached.

    That last part is not decoration. "Did the model sheet reach OpenArt?" is
    the first question asked of any render that comes back with the wrong
    character, and without this it can only be inferred from the fact that
    nothing raised.
    """
    if driver is None:
        from scripts.trivia_images.openart_image_driver import generate_image
        driver = generate_image

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    import contextlib
    import io as _io

    captured = _io.StringIO()
    # The driver reports the references it attaches on stderr. Tee it rather
    # than swallow it, so a terminal operator still sees progress.
    with contextlib.redirect_stderr(_Tee(sys.stderr, captured)):
        saved = _submit(driver, prompt, out_path)
    if log is not None:
        log.extend(captured.getvalue().splitlines())
    return Path(saved[0])


class _Tee:
    """Write to both streams. `redirect_stderr` replaces the stream entirely."""

    def __init__(self, *streams):
        self._streams = streams

    def write(self, data):
        for st in self._streams:
            st.write(data)
        return len(data)

    def flush(self):
        for st in self._streams:
            st.flush()


def _submit(driver, prompt: str, out_path: Path):
    return driver(
        prompt=prompt,
        model=MODEL,
        output_paths=[out_path],
        aspect=ASPECT,
        resolution=RESOLUTION,
        character=CHARACTER,
        workspace=WORKSPACE,
        fallback_workspaces=FALLBACK_WORKSPACES,
    )
