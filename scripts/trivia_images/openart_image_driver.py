#!/usr/bin/env python
"""OpenArt image generation over the MCP API.

Drop-in replacement for the Playwright driver that used to live here (kept at
`openart_image_driver_playwright.py` for reference — nothing dispatches to it).
Same public API, no browser:

    generate_image(prompt, model, output_paths, ...) -> list[Path]

What changed behind the signature:

  * No Chromium, no hashed-class selectors, no popovers. The 2026-09 UI refresh
    that broke the suite URLs can't break this.
  * Credit checks read the real balance from `openart_account_get` instead of
    scraping paywall phrases out of the DOM.
  * Failures come back with OpenArt's own status and code, so a content-policy
    block (permanent) is distinguishable from a 429 (retryable).
  * Callers may run concurrently — there is no shared browser profile to race
    on, so the single-worker lock in the trivia-images server is no longer
    load-bearing.

Auth is a one-time OAuth consent per machine:

    python scripts/common/openart_mcp.py --login

Standalone smoke test:
    python scripts/trivia_images/openart_image_driver.py \
      --prompt "a red barn at golden hour" --model "Nano Banana 2" \
      --out /tmp/barn.png
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parents[2]
COMMON = REPO / "scripts" / "common"
if str(COMMON) not in sys.path:
    sys.path.insert(0, str(COMMON))

import openart_api as api  # noqa: E402
import openart_characters as characters  # noqa: E402
from openart_api import (  # noqa: E402,F401 - re-exported; callers import these from here
    OpenArtAuthError,
    OpenArtGenerationError,
    OpenArtModelUnavailableError,
    OpenArtOutOfCreditsError,
    OpenArtRateLimitError,
)

# The trivia-images art lives in the team workspace; the personal one is the
# credit fallback. Both are re-asserted per run because OpenArt persists the
# active workspace per account and other sessions can move it.
OPENART_WORKSPACE = "BebopBee Art Team"
OPENART_FALLBACK_WORKSPACES: tuple[str, ...] = ("R N",)

# Nano Banana Pro typically returns in 20-90s per image; give a batch of 8 room.
GENERATION_TIMEOUT_S = 600


def generate_image(
    prompt: str,
    model: str,
    output_paths: list[Path],
    headless: bool = True,
    aspect: str = "4:3",
    resolution: str = "2K",
    keep_source_ext: bool = True,
    reference_image_path: Optional[Path] = None,
    workspace: Optional[str] = OPENART_WORKSPACE,
    character: Optional[str] = None,
    fallback_workspaces: tuple[str, ...] = OPENART_FALLBACK_WORKSPACES,
) -> list[Path]:
    """Generate `len(output_paths)` image variants on OpenArt.

    Args:
        prompt: full prompt text.
        model: model display name (e.g. "Nano Banana 2").
        output_paths: one destination path per variant; the length is the
            variant count. Extensions are rewritten to match the CDN source
            when `keep_source_ext` is True.
        headless: accepted and ignored — kept so existing callers and the
            `openart_image` tool schema keep working. There is no browser.
        aspect: aspect ratio, e.g. "4:3" or "9:16".
        resolution: "1K", "2K" or "4K".
        keep_source_ext: rewrite each output path's extension to match what the
            CDN served (.png / .webp / .jpg).
        reference_image_path: local image attached as a visual reference. The
            model keeps its scene and applies the prompt's content on top.
        workspace: OpenArt workspace to bill. None/"" uses whatever is active.
        character: saved-character name, resolved to local stills in
            `character_library/<slug>/` (see scripts/common/openart_characters.py).
            OpenArt's MCP API cannot address saved characters by name.
        fallback_workspaces: workspaces to retry in, in order, when the primary
            one is out of credits. Pass () to surface the credit failure
            instead. Ignored when `character` is set, because the uploaded
            stills are workspace-scoped.

    Returns saved paths aligned with `output_paths` (newest variant first).

    Raises:
        OpenArtOutOfCreditsError: no candidate workspace can pay.
        OpenArtGenerationError: OpenArt ran the job but returned nothing usable
            (typically a content-policy block).
        OpenArtRateLimitError: upstream provider quota; retry later.
        OpenArtAuthError: this machine has never run `openart_mcp.py --login`.
    """
    if not output_paths:
        raise ValueError("output_paths must contain at least one path")
    output_paths = [Path(p).expanduser().resolve() for p in output_paths]

    if character:
        # Character stills are uploaded into one workspace, so a credit
        # fallback would silently generate without the character.
        fallback_workspaces = ()

    ref_path: Optional[Path] = None
    if reference_image_path is not None:
        ref_path = Path(reference_image_path).expanduser().resolve()
        if not ref_path.exists():
            raise FileNotFoundError(f"reference_image_path does not exist: {ref_path}")

    has_references = bool(character or ref_path)
    mode = "image2image" if has_references else "text2image"

    def build_params() -> dict:
        """Built per workspace attempt — uploads are workspace-scoped."""
        references: list[dict] = []
        if character:
            print(f"  → character stills: {character}", file=sys.stderr)
            references.extend(
                characters.visual_references(character, purpose="create-image"),
            )
        if ref_path is not None:
            print(f"  → reference image: {ref_path.name}", file=sys.stderr)
            references.append(api.upload_reference(ref_path, "image", purpose="create-image"))
        params = {
            "prompt": prompt,
            "imageCount": len(output_paths),
            "aspectRatio": aspect,
            "resolution": resolution,
        }
        if references:
            params["visualReferences"] = references
        return params

    return api.generate(
        media="image",
        model_display=model,
        mode=mode,
        params=build_params,
        output_paths=output_paths,
        workspace=workspace,
        fallback_workspaces=fallback_workspaces,
        keep_source_ext=keep_source_ext,
        total_timeout_s=GENERATION_TIMEOUT_S,
    )


def _main() -> int:
    ap = argparse.ArgumentParser(description="Generate images on OpenArt via MCP.")
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--model", default="Nano Banana 2")
    ap.add_argument("--out", required=True, nargs="+", type=Path,
                    help="one output path per variant")
    ap.add_argument("--aspect", default="4:3")
    ap.add_argument("--resolution", default="2K")
    ap.add_argument("--reference", type=Path, default=None)
    ap.add_argument("--character", default=None)
    ap.add_argument("--workspace", default=OPENART_WORKSPACE)
    args = ap.parse_args()

    saved = generate_image(
        prompt=args.prompt,
        model=args.model,
        output_paths=list(args.out),
        aspect=args.aspect,
        resolution=args.resolution,
        reference_image_path=args.reference,
        character=args.character,
        workspace=args.workspace or None,
    )
    for path in saved:
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(_main())
