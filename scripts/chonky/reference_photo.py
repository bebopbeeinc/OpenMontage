"""Fetch a photograph of the real viewpoint, to anchor the render to it.

Until now the picture was the image model's impression of a city, drawn from
training data and constrained only by words. The place was verified; the
picture was never checked against it, and nothing in the pipeline could be.
"Cusco, plausibly" and "Choquechaka, actually" looked identical to every check
we had.

The model accepts up to sixteen visual references and the pipeline spends one
on Chonky. A real photograph in the second slot is what makes the difference.

On provenance: the source URL is recorded with every render. The photographs
come from open web search rather than a licence-filtered source, which was a
deliberate decision — so the one thing that must not be lost is where each
image came from, in case a particular one ever has to be audited or swapped.
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Callable, Optional

from PIL import Image

# Below this a photograph carries no usable geometry — it would add noise to
# the render rather than anchoring it.
MIN_EDGE = 320

_ACCEPTED_TYPES = ("image/jpeg", "image/jpg", "image/png", "image/webp")


class ReferenceError(RuntimeError):
    """The reference photograph could not be used."""


def _default_opener(url: str) -> tuple[str, bytes]:
    import urllib.request

    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Chonky/1.0 (BebopBee geography pipeline)"},
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip()
        return content_type.lower(), response.read()


def fetch_reference(url: str, destination, *,
                    opener: Optional[Callable] = None) -> Optional[dict]:
    """Download a photograph of the viewpoint, or explain why it cannot be used.

    Returns None for an empty URL rather than raising: a viewpoint with no
    findable photograph should still render, just unanchored, and the caller
    records that it was unanchored.
    """
    if not url:
        return None

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    try:
        content_type, data = (opener or _default_opener)(url)
    except Exception as exc:                          # noqa: BLE001 - reported
        raise ReferenceError(f"could not fetch {url}: {type(exc).__name__}: {exc}") from exc

    if content_type not in _ACCEPTED_TYPES:
        raise ReferenceError(f"{url} returned {content_type or 'no content type'}, "
                             f"not an image")

    try:
        with Image.open(io.BytesIO(data)) as im:
            im.load()
            width, height = im.size
            rgb = im.convert("RGB")
    except Exception as exc:                          # noqa: BLE001 - reported
        raise ReferenceError(f"{url} is not a readable image: {exc}") from exc

    if min(width, height) < MIN_EDGE:
        raise ReferenceError(
            f"{url} is {width}x{height}, too small to anchor a render "
            f"(needs {MIN_EDGE} px on the short edge)")

    rgb.save(destination, format="PNG")
    return {"path": destination, "source_url": url, "size": [width, height]}
