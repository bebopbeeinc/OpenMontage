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
ASPECT = "4:5"
RESOLUTION = "4k"

# Straight from the model's own form schema (openart_model_form_get for
# gpt-image-2-5-sunburst/image2image), not from memory. The schema forbids
# unknown keys and names the field `resolutionTier` with lowercase tiers, so
# the "4K" this pipeline had been sending under the key `resolution` was
# dropped and every render came back at the 2k default — 1344x1680 for 4:5,
# against geometry constants describing the 2048x2560 that 4k gives.
ASPECT_RATIOS = ("1:1", "3:2", "2:3", "16:9", "9:16", "4:3",
                 "3:4", "5:4", "4:5", "21:9", "9:21")
RESOLUTION_TIERS = ("1k", "2k", "4k")


def normalise_tier(tier: Optional[str]) -> str:
    """Accept what a human writes, send what the schema accepts."""
    if tier is None:
        return RESOLUTION
    lowered = str(tier).strip().lower()
    if lowered not in RESOLUTION_TIERS:
        raise ValueError(
            f"resolution {tier!r} is not one of {', '.join(RESOLUTION_TIERS)}")
    return lowered


def normalise_aspect(aspect: Optional[str]) -> str:
    if aspect is None:
        return ASPECT
    cleaned = str(aspect).strip()
    if cleaned not in ASPECT_RATIOS:
        raise ValueError(
            f"aspect ratio {aspect!r} is not one of {', '.join(ASPECT_RATIOS)}")
    return cleaned
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
                log: Optional[list] = None, aspect: Optional[str] = None,
                resolution: Optional[str] = None,
                width: Optional[int] = None, height: Optional[int] = None) -> Path:
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
    return Path(_submit(driver, prompt, out_path, log=log,
                        aspect=normalise_aspect(aspect),
                        resolution=normalise_tier(resolution),
                        width=width, height=height)[0])


def _submit(driver, prompt: str, out_path: Path, *, log=None,
            aspect: str = ASPECT, resolution: str = RESOLUTION,
            width=None, height=None):
    return driver(
        prompt=prompt,
        model=MODEL,
        output_paths=[out_path],
        aspect=aspect,
        resolution=resolution,
        character=CHARACTER,
        workspace=WORKSPACE,
        fallback_workspaces=FALLBACK_WORKSPACES,
        log=log,
        width=width,
        height=height,
    )
