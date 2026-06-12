"""Drive OpenArt (Seedance 2.0) to generate the single 15s Captain clip.

Reads the assembled prompt from Queue!N. No reference image (the 2T1L format has
no in-camera sign — brand lives in the Remotion overlay). Character "Captain
Archibald", native audio (VO + in-prompt game-show music), 480p 9:16 15s.

Usage:
    python scripts/trivia_captain_2t1l/openart_generate.py <slug> [--headless] [--force]
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "common"))
sys.path.insert(0, str(REPO))
from openart_driver import generate_clip  # noqa: E402
from scripts.trivia_captain_2t1l import paths, queue_row  # noqa: E402

_TAIL_SILENCE_S = 0.30
_FADE_S = 0.08


def _ffprobe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def _apply_tail_fade(path: Path) -> None:
    """Mask Seedance's end-of-clip audio artifact: fade + silence the last 0.30s."""
    dur = _ffprobe_duration(path)
    if dur <= 0:
        return
    fade_st = max(0.0, dur - _TAIL_SILENCE_S)
    tmp = path.with_suffix(".tmp.mp4")
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-i", str(path),
        "-af", f"afade=t=out:st={fade_st}:d={_FADE_S},volume=enable='gte(t,{fade_st})':volume=0",
        "-c:v", "copy", str(tmp),
    ], check=True)
    tmp.replace(path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("slug")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--model-override", default=None)
    args = ap.parse_args()

    out_dir = paths.project_dir(args.slug) / "assets" / "video"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "clip.mp4"
    if out_path.exists() and not args.force:
        print(f"clip already exists: {out_path} (use --force to regenerate)")
        return 0

    sheets = queue_row.build_sheets(write=False)
    row = queue_row.find_row_by_slug(sheets, args.slug)
    if not row:
        sys.exit(f"slug {args.slug!r} not found in Queue")
    prompt = (queue_row.read_queue_row(sheets, row).get("openart_prompt") or "").strip()
    if not prompt:
        sys.exit("Queue OpenArt Prompt (col N) is empty — run build_prompt.py first")

    model = args.model_override or "Seedance 2.0"
    print(f"→ OpenArt generate (model={model}, 15s, 480p, char=Captain Archibald, audio=on)")
    generate_clip(
        prompt=prompt,
        model=model,
        duration_s=15,
        output_paths=[out_path],
        audio_on=True,
        character="Captain Archibald",
        resolution="480p",
        reference_image=None,
        headless=args.headless,
    )
    _apply_tail_fade(out_path)
    print(f"✓ saved {out_path} ({_ffprobe_duration(out_path):.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
