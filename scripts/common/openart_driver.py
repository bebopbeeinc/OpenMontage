#!/usr/bin/env python
"""OpenArt video generation over the MCP API.

Drop-in replacement for the Playwright driver that used to live here (kept at
`openart_driver_playwright.py` for reference — nothing dispatches to it). Same
public API, no browser:

    generate_clip(prompt, model, duration_s, output_paths, ...) -> list[Path]
    download_resource(resource_id, output_path, ...)            -> Path

Two behaviour changes the signatures can't show:

  * `character=` no longer selects an OpenArt saved character. The MCP surface
    has no tool that resolves saved characters by name, so the identity
    reference now comes from local stills in `character_library/<slug>/` and
    rides along as element2video references. See
    `scripts/common/openart_characters.py` for the layout and the one-time
    export step.
  * "HappyHorse" has no MCP route at all and raises
    OpenArtModelUnavailableError rather than quietly running a different
    generator.

Auth is a one-time OAuth consent per machine:

    python scripts/common/openart_mcp.py --login

Standalone smoke test:
    python scripts/common/openart_driver.py \
      --prompt "test" --model "Seedance 2.0" --duration 8 \
      --out scripts/trivia/library/_smoketest.mp4
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parents[2]
COMMON = REPO / "scripts" / "common"
if str(COMMON) not in sys.path:
    sys.path.insert(0, str(COMMON))

import openart_api as api  # noqa: E402
import openart_characters as characters  # noqa: E402
from openart_api import (  # noqa: E402,F401 - re-exported for callers
    OpenArtAuthError,
    OpenArtGenerationError,
    OpenArtModelUnavailableError,
    OpenArtOutOfCreditsError,
    OpenArtRateLimitError,
)

# Saved characters used to live in the personal "R N" workspace, which is why
# the video driver defaults there while the image driver defaults to the team.
OPENART_WORKSPACE = "R N"

# Vertical, to match every trivia surface.
ASPECT = "9:16"

# Video is slow; Seedance at 1080p can sit in the queue for several minutes.
GENERATION_TIMEOUT_S = 1800

# Pages of creation history to scan when resolving a bare resource id.
RESOURCE_LOOKUP_PAGES = 10


def generate_clip(
    prompt: str,
    model: str,
    duration_s: int,
    output_paths: list[Path],
    headless: bool = True,
    audio_on: bool = False,
    character: str | None = None,
    resolution: str = "480p",
    reference_image: Path | str | None = None,
    workspace: str | None = OPENART_WORKSPACE,
) -> list[Path]:
    """Generate `len(output_paths)` clip variants on OpenArt and download each.

    Args:
        prompt: full prompt text.
        model: model display name (e.g. "Seedance 2.0").
        duration_s: clip duration in seconds. OpenArt's video models accept
            4-15s; shorter asks are raised to the model's floor and the real
            value is logged.
        output_paths: one destination path per variant; the length is the
            variant count.
        headless: accepted and ignored — there is no browser.
        audio_on: keep the generated audio track. Default off: captions and VO
            are added in post for the trivia pipelines.
        character: saved-character name, resolved to local stills in
            `character_library/<slug>/` and passed as element references.
        resolution: "480p", "720p", "1080p" or "4k".
        reference_image: local image passed as an additional visual reference.
        workspace: OpenArt workspace to bill. None/"" uses whatever is active.

    Returns saved paths aligned with `output_paths` (newest variant first).
    """
    if not output_paths:
        raise ValueError("output_paths must contain at least one path")
    output_paths = [Path(p).expanduser().resolve() for p in output_paths]

    # Fails fast and loudly for models with no MCP route (HappyHorse), instead
    # of substituting a different generator.
    mid = api.model_id(model)

    ref_path: Optional[Path] = None
    if reference_image:
        ref_path = Path(reference_image).expanduser().resolve()
        if not ref_path.exists():
            raise FileNotFoundError(f"reference_image does not exist: {ref_path}")

    # element2video treats the references as identity, not as a locked first
    # frame — that is what a saved character used to do.
    mode = "element2video" if (character or ref_path) else "text2video"
    duration = _clamp_duration(mid, mode, duration_s)

    def build_params() -> dict:
        """Built per workspace attempt — uploads are workspace-scoped."""
        references: list[dict] = []
        if character:
            print(f"  → character stills: {character}", file=sys.stderr)
            references.extend(
                characters.visual_references(character, purpose="create-video"),
            )
        if ref_path is not None:
            print(f"  → reference image: {ref_path.name}", file=sys.stderr)
            references.append(api.upload_reference(ref_path, "image", purpose="create-video"))
        params = {
            "prompt": prompt,
            "videoCount": len(output_paths),
            "duration": duration,
            "aspectRatio": ASPECT,
            "resolution": resolution,
            "generateAudio": bool(audio_on),
        }
        if references:
            params["visualReferences"] = references
        return params

    saved = api.generate(
        media="video",
        model_display=model,
        mode=mode,
        params=build_params,
        output_paths=output_paths,
        workspace=workspace,
        keep_source_ext=False,
        total_timeout_s=GENERATION_TIMEOUT_S,
    )

    if not audio_on:
        for path in saved:
            _strip_audio_in_place(path)
    return saved


def _clamp_duration(model: str, mode: str, seconds: int) -> int:
    """Hold `seconds` inside the model's advertised range.

    The old UI accepted a 3s reaction clip; the API floor is 4s on every video
    model it exposes. Clamping (and saying so) beats a rejected submission.
    """
    schema = api.form_schema(model, mode).get("properties", {})
    spec = schema.get("duration")
    if not isinstance(spec, dict):
        return seconds
    low, high = spec.get("minimum"), spec.get("maximum")
    clamped = seconds
    if isinstance(low, (int, float)):
        clamped = max(clamped, int(low))
    if isinstance(high, (int, float)):
        clamped = min(clamped, int(high))
    if clamped != seconds:
        print(f"  ! duration {seconds}s is outside {model}'s {low}-{high}s range; "
              f"using {clamped}s", file=sys.stderr)
    return clamped


def download_resource(
    resource_id: str,
    output_path: Path,
    headless: bool = True,
    audio_on: bool = False,
) -> Path:
    """Download an already-generated OpenArt asset by id. Spends no credits.

    Accepts either a resource id or the historyId of the generation that made
    it, and resolves it against the workspace's creation history.

    Args:
        resource_id: OpenArt resource id or historyId.
        output_path: destination file.
        headless: accepted and ignored — there is no browser.
        audio_on: keep the audio track; strip it after download when False.
    """
    url = _resolve_resource_url(resource_id)
    if not url:
        raise OpenArtGenerationError(
            f"resource {resource_id!r} was not found in the last "
            f"{RESOURCE_LOOKUP_PAGES} pages of this workspace's creation history",
        )
    saved = api.download(url, Path(output_path))
    if not audio_on:
        _strip_audio_in_place(saved)
    return saved


def _resolve_resource_url(resource_id: str) -> Optional[str]:
    cursor: Optional[str] = None
    for _ in range(RESOURCE_LOOKUP_PAGES):
        args: dict = {"limit": 50}
        if cursor:
            args["cursor"] = cursor
        page = api.call_tool("openart_creation_list", args)
        for item in page.get("items", []):
            if resource_id in (item.get("id"), item.get("historyId")):
                return item.get("url")
        if not page.get("hasMore"):
            return None
        cursor = page.get("nextCursor")
    return None


def _strip_audio_in_place(path: Path) -> None:
    """Remux the file to drop any audio track. Pure stream copy — fast.

    Some models ignore `generateAudio: false` and return a clip with audio
    anyway (Wan 2.7 in particular), so strip after download to make the
    on-disk file match what was asked for.
    """
    tmp = path.with_name(f".muted_{path.name}")
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(path),
        "-an",            # drop audio
        "-c:v", "copy",   # no re-encode
        "-movflags", "+faststart",
        str(tmp),
    ], check=True)
    tmp.replace(path)


def _main() -> int:
    ap = argparse.ArgumentParser(description="Generate video clips on OpenArt via MCP.")
    ap.add_argument("--prompt")
    ap.add_argument("--model", default="Seedance 2.0")
    ap.add_argument("--duration", type=int, default=8)
    ap.add_argument("--out", nargs="+", type=Path, help="one output path per variant")
    ap.add_argument("--resolution", default="480p")
    ap.add_argument("--audio", action="store_true", help="keep the generated audio track")
    ap.add_argument("--character", default=None)
    ap.add_argument("--reference", type=Path, default=None)
    ap.add_argument("--workspace", default=OPENART_WORKSPACE)
    ap.add_argument("--download", metavar="RESOURCE_ID",
                    help="download an existing resource instead of generating")
    args = ap.parse_args()

    if args.download:
        if not args.out:
            ap.error("--download needs a single --out path")
        print(download_resource(args.download, args.out[0], audio_on=args.audio))
        return 0

    if not args.prompt or not args.out:
        ap.error("--prompt and --out are required")
    for path in generate_clip(
        prompt=args.prompt,
        model=args.model,
        duration_s=args.duration,
        output_paths=list(args.out),
        audio_on=args.audio,
        character=args.character,
        resolution=args.resolution,
        reference_image=args.reference,
        workspace=args.workspace or None,
    ):
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(_main())
