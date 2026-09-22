"""Submit exactly one OpenArt render for one image.

There is deliberately no retry and no loop here. Duplicate submissions were the
single most expensive failure of the earlier ChatGPT-driven build — the model
resubmitted whenever an approval prompt interrupted it, and no instruction
stopped it. Here one call to `render_once` is one image, and a reroll is an
explicit second call by a caller that has stated why.
"""
from __future__ import annotations

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

# Chonky bills the company art workspace and nothing else. The shared driver
# defaults to falling back to a personal workspace when the primary one runs
# out of credits; that is wrong here, so the fallback is disabled and a credit
# shortfall fails loudly instead of quietly spending someone's own balance.
WORKSPACE = "BebopBee Art Team"
FALLBACK_WORKSPACES: tuple[str, ...] = ()


def render_once(prompt: str, out_path: Path, *, driver: Optional[Callable] = None) -> Path:
    """Render `prompt` to `out_path`. One submission, no retries.

    `driver` is injectable so tests never reach OpenArt.
    """
    if driver is None:
        from scripts.trivia_images.openart_image_driver import generate_image
        driver = generate_image

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    saved = driver(
        prompt=prompt,
        model=MODEL,
        output_paths=[out_path],
        aspect=ASPECT,
        resolution=RESOLUTION,
        character=CHARACTER,
        workspace=WORKSPACE,
        fallback_workspaces=FALLBACK_WORKSPACES,
    )
    return Path(saved[0])
