#!/usr/bin/env python
"""Drive OpenArt to generate the avatar talking-head clip for one trivia-reaction row.

Reads:
    projects/trivia-reaction/<slug>/artifacts/script.json    # OpenArt prompt + duration + variant count

Routing:
    model:       Seedance 2.0
    character:   ellie.travelcrush  (OpenArt saved character — user maintains)
    duration:    from script.metadata.openart.duration_s (12-18s)
    audio:       OFF  (VO is added in post)
    output:      scripts/trivia_reaction/library/clips/<slug>.mp4
                 (or <slug>_v1.mp4, <slug>_v2.mp4 when variants > 1)

Usage:
    python scripts/trivia_reaction/openart_generate.py <slug>
    python scripts/trivia_reaction/openart_generate.py <slug> --headless
    python scripts/trivia_reaction/openart_generate.py <slug> --force
    python scripts/trivia_reaction/openart_generate.py <slug> --variants 1

After download the script also writes the chosen filename back to the Queue
row so the asset/edit stages can resolve the clip without re-reading
artifacts.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "common"))

from openart_driver import generate_clip  # noqa: E402
from scripts.trivia_reaction import queue_row  # noqa: E402
from scripts.trivia_reaction.paths import project_dir  # noqa: E402

LIBRARY_DIR = REPO / "scripts" / "trivia_reaction" / "library" / "clips"

# Seedance 2.0 clips often carry a short model-generated artifact in the
# few hundred ms after dialogue ends. Hard-silence the last
# _TAIL_SILENCE_S seconds: a quick _FADE_DURATION_S linear fade (so the
# transition isn't a click) followed by dead silence to clip end.
# A pure linear fade across the same window leaves the loudest part of
# the glitch only 4-6 dB attenuated — still audible — which is what
# bit us the first time.
_TAIL_SILENCE_S = 0.30
_FADE_DURATION_S = 0.08


def _ffprobe_duration(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(r.stdout.strip())


def _apply_tail_fade(path: Path) -> None:
    """Re-encode `path` in place: short fade then dead silence over the
    last _TAIL_SILENCE_S seconds. Video stream is copied; only audio is
    touched."""
    duration = _ffprobe_duration(path)
    fade_st = max(0.0, duration - _TAIL_SILENCE_S)
    tmp = path.with_name(path.stem + ".faded.tmp" + path.suffix)
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-i", str(path),
         "-af", f"afade=t=out:st={fade_st:.3f}:d={_FADE_DURATION_S:.3f}",
         "-c:v", "copy",
         "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
         str(tmp)],
        check=True,
    )
    tmp.replace(path)


def _read_prompt_from_queue(slug: str) -> str:
    """Look up `slug` in TriviaReactionQueue and return its Queue!J prompt.
    Returns '' if the row doesn't exist or the prompt cell is empty."""
    try:
        ws = queue_row.build_sheets(write=False)
        rows = queue_row.read_queue_bulk(ws)
    except Exception as e:  # noqa: BLE001
        print(f"⚠ sheet lookup failed: {e}", file=sys.stderr)
        return ""
    for r in rows:
        if (r.get("slug") or "").strip() == slug:
            return (r.get("openart_prompt") or "").strip()
    return ""


def _next_take_number(slug: str) -> int:
    """Return the next take number for this slug — looks at existing
    <slug>_takeNN.mp4 files in the library and returns max + 1.
    Takes start at 1.
    """
    pattern = re.compile(rf"^{re.escape(slug)}_take(\d+)(?:_v\d+)?\.mp4$")
    existing = [
        int(m.group(1))
        for p in LIBRARY_DIR.glob(f"{slug}_take*.mp4")
        if (m := pattern.match(p.name))
    ]
    return max(existing, default=0) + 1


def _variant_paths(slug: str, variants: int) -> list[Path]:
    """Generate per-take, per-variant paths so regens never overwrite a
    good take. Each invocation gets a fresh takeNN number; variants are
    suffixed _v1, _v2, ... within the take.

    Canonical filename `<slug>.mp4` is also written as a symlink to
    take{N}_v1 so downstream tools (assemble.py) keep finding the latest
    by the legacy short name.
    """
    take = _next_take_number(slug)
    if variants == 1:
        return [LIBRARY_DIR / f"{slug}_take{take:02d}.mp4"]
    return [LIBRARY_DIR / f"{slug}_take{take:02d}_v{i + 1}.mp4" for i in range(variants)]


def _update_canonical_symlink(slug: str, latest: Path) -> None:
    canonical = LIBRARY_DIR / f"{slug}.mp4"
    if canonical.exists() or canonical.is_symlink():
        canonical.unlink()
    canonical.symlink_to(latest.name)  # relative — works wherever the library moves


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("slug", type=str, help="project slug (matches projects/trivia-reaction/<slug>/)")
    ap.add_argument("--variants", type=int, default=1,
                    help="number of variants to render in one OpenArt batch")
    ap.add_argument("--force", action="store_true",
                    help="regenerate even if the clip already exists locally")
    ap.add_argument("--headless", action="store_true",
                    help="run Playwright headless (default: visible window)")
    ap.add_argument("--model-override", type=str, default=None,
                    help="override the OpenArt model name (default: Seedance 2.0)")
    args = ap.parse_args()

    # Source of truth for the prompt is now the sheet (Queue!J, written
    # by the script director). Older rows / dev machines may still have
    # a local script.json; we prefer that when present because it carries
    # per-row overrides for duration / model / character / resolution.
    # When script.json is absent, fall back to sheet + playbook defaults.
    script_path = project_dir(args.slug) / "artifacts" / "script.json"
    openart_cfg: dict = {}
    if script_path.exists():
        script = json.loads(script_path.read_text())
        openart_cfg = script.get("metadata", {}).get("openart", {})
        source = f"script.json ({script_path.relative_to(REPO)})"
    else:
        # Resolve prompt from the sheet.
        prompt_from_sheet = _read_prompt_from_queue(args.slug)
        if not prompt_from_sheet:
            sys.exit(
                f"No prompt found for slug={args.slug!r}: "
                f"script.json missing at {script_path} AND Queue!J is empty. "
                "Edit the prompt in TriviaReactionQueue!J before running."
            )
        openart_cfg = {"prompt": prompt_from_sheet}
        source = "TriviaReactionQueue!J (sheet)"

    prompt = (openart_cfg.get("prompt") or "").strip()
    # Playbook defaults (styles/trivia-reaction.yaml.asset_generation.openart).
    # script.json overrides win when present.
    duration_s = int(openart_cfg.get("duration_s") or 15)
    # `.get(..., default)` instead of `or` so explicit null/None in script.json
    # passes through as None (driver skips character selection). Used for
    # models that don't expose OpenArt's saved-character UI (e.g. Kling 3.0 Omni).
    character = openart_cfg.get("character", "ellie.travelcrush")
    model = args.model_override or openart_cfg.get("model") or "Seedance 2.0"
    audio_on = bool(openart_cfg.get("audio_on", True))  # trivia-reaction default
    resolution = (openart_cfg.get("resolution") or "480p").strip()

    print(f"  prompt source: {source}")
    if not prompt:
        sys.exit(f"prompt is empty (source: {source})")
    if not (10 <= duration_s <= 20):
        sys.exit(f"duration_s must be 10..20 (got {duration_s})")
    if character != "ellie.travelcrush":
        print(f"⚠ character override: {character} (expected ellie.travelcrush)",
              file=sys.stderr)

    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    paths = _variant_paths(args.slug, args.variants)

    canonical = LIBRARY_DIR / f"{args.slug}.mp4"
    # Take-numbered filenames are always fresh — `--force` only matters
    # for the canonical-symlink swap. Existing canonical points at the
    # last take and is updated to point at this one when we finish.
    if canonical.exists() and not args.force:
        print(f"⚠ canonical clip already exists: {canonical.relative_to(REPO)}")
        print(f"  This run will create a new take ({paths[0].name}) but will not")
        print(f"  update the canonical symlink. Use --force to swap canonical.")

    print(f"→ OpenArt generate (model={model}, dur={duration_s}s, res={resolution}, "
          f"variants={args.variants}, char={character}, audio={'on' if audio_on else 'off'})")
    print(f"  prompt: {prompt[:200]}{'…' if len(prompt) > 200 else ''}")

    saved = generate_clip(
        prompt=prompt,
        model=model,
        duration_s=duration_s,
        output_paths=paths,
        headless=args.headless,
        audio_on=audio_on,
        character=character,
        resolution=resolution,
    )
    print(f"✓ saved {len(saved)} variant(s):")
    for p in saved:
        print(f"  · {Path(p).relative_to(REPO)}")

    # Mask Seedance's end-of-clip audio artifact at the source so every
    # downstream consumer (Drive upload, bg.mp4, re-edits) gets a clean
    # signal. See _TAIL_SILENCE_S / _FADE_DURATION_S.
    for p in saved:
        _apply_tail_fade(Path(p))
    print(f"  · audio tail-silenced ({_TAIL_SILENCE_S * 1000:.0f}ms, "
          f"fade={_FADE_DURATION_S * 1000:.0f}ms)")

    # Update canonical symlink to point at the first new variant unless
    # canonical exists and we weren't forced to swap it.
    canonical = LIBRARY_DIR / f"{args.slug}.mp4"
    canonical_exists_before = canonical.exists() or canonical.is_symlink()
    if not canonical_exists_before or args.force:
        first = Path(saved[0])
        _update_canonical_symlink(args.slug, first)
        print(f"✓ canonical symlink → {first.name}")
    else:
        print(f"  · canonical {canonical.name} unchanged (use --force to swap)")

    # Write the chosen filename back to the Queue row. For now: the first
    # variant is the default. Humans rename to drop the _v suffix when
    # picking a different take.
    try:
        ws = queue_row.build_sheets(write=True)
        # Find the row by slug
        rows = queue_row.read_queue_bulk(ws)
        target_row = next((r["row"] for r in rows if r.get("slug") == args.slug), None)
        if target_row:
            # No dedicated clip-filename column yet — append to feedback as a note.
            # If/when we add a Clip Filename column, route it through update_cells.
            print(f"  note: queue row {target_row} found (no Clip Filename column "
                  f"yet — local file is the source of truth at edit stage)")
        else:
            print(f"  ⚠ no Queue row matches slug={args.slug!r}; "
                  f"run select_row.py --day N --slug {args.slug} first.")
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠ skipped queue write: {e}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
