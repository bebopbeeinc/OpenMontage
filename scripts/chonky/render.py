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

    # The driver appends its progress to `log` directly. Capturing it by
    # swapping sys.stderr was a process-global change made from one of several
    # concurrent render threads: the real stderr was never restored, and one
    # render's references were recorded against another's job.
    return Path(_submit(driver, prompt, out_path, log=log)[0])


def _submit(driver, prompt: str, out_path: Path, *, log=None):
    return driver(
        prompt=prompt,
        model=MODEL,
        output_paths=[out_path],
        aspect=ASPECT,
        resolution=RESOLUTION,
        character=CHARACTER,
        workspace=WORKSPACE,
        fallback_workspaces=FALLBACK_WORKSPACES,
        log=log,
    )
