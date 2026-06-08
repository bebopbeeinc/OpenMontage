#!/usr/bin/env python
"""Drive OpenArt to generate the avatar talking-head clip for one trivia-captain row.

Reads (TriviaCaptainQueue sheet is the single source of truth):
    Queue!J  OpenArt prompt   (authored by the script director)
    Queue!M  reference image  (tablet-screen splash, repo-relative path)
    styles/trivia-captain.yaml  asset_generation.openart  # pipeline constants

Routing:
    model:       Seedance 2.0
    character:   Captain Archibald  (OpenArt saved character — user maintains)
    duration:    from script.metadata.openart.duration_s (12-18s)
    audio:       OFF  (VO is added in post)
    output:      scripts/trivia_captain/library/clips/<slug>.mp4
                 (or <slug>_v1.mp4, <slug>_v2.mp4 when variants > 1)

Usage:
    python scripts/trivia_captain/openart_generate.py <slug>
    python scripts/trivia_captain/openart_generate.py <slug> --headless
    python scripts/trivia_captain/openart_generate.py <slug> --force
    python scripts/trivia_captain/openart_generate.py <slug> --variants 1

After download the script also writes the chosen filename back to the Queue
row so the asset/edit stages can resolve the clip without re-reading
artifacts.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "common"))

from openart_driver import generate_clip  # noqa: E402
from scripts.trivia_captain import queue_row  # noqa: E402

LIBRARY_DIR = REPO / "scripts" / "trivia_captain" / "library" / "clips"

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


def _read_row_from_queue(slug: str) -> dict | None:
    """Look up `slug` in TriviaCaptainQueue and return its full row dict
    (keyed by queue_row.ROW_KEYS), or None if no row matches."""
    try:
        ws = queue_row.build_sheets(write=False)
        rows = queue_row.read_queue_bulk(ws)
    except Exception as e:  # noqa: BLE001
        print(f"⚠ sheet lookup failed: {e}", file=sys.stderr)
        return None
    for r in rows:
        if (r.get("slug") or "").strip() == slug:
            return r
    return None


def _playbook_openart_defaults() -> dict:
    """Pipeline-level OpenArt constants from the playbook
    (styles/trivia-captain.yaml -> asset_generation.openart). These are NOT
    per-row — per-row content lives in the sheet (Queue!J / Queue!M)."""
    pb = yaml.safe_load((REPO / "styles" / "trivia-captain.yaml").read_text())
    return ((pb or {}).get("asset_generation", {}) or {}).get("openart", {}) or {}


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
    ap.add_argument("slug", type=str, help="project slug (matches projects/trivia-captain/<slug>/)")
    ap.add_argument("--variants", type=int, default=1,
                    help="number of variants to render in one OpenArt batch")
    ap.add_argument("--force", action="store_true",
                    help="regenerate even if the clip already exists locally")
    ap.add_argument("--headless", action="store_true",
                    help="run Playwright headless (default: visible window)")
    ap.add_argument("--model-override", type=str, default=None,
                    help="override the OpenArt model name (default: Seedance 2.0)")
    args = ap.parse_args()

    # The TriviaCaptainQueue sheet is the SINGLE source of truth for this
    # pipeline. Per-row content is read live from the sheet every run:
    #   Queue!J  openart_prompt   (the prompt the script director authored)
    #   Queue!M  reference_image  (tablet-screen splash, repo-relative path)
    # Pipeline constants (model / duration / character / resolution / audio)
    # come from the playbook. script.json caches were removed 2026-06-04 —
    # they silently shadowed sheet edits and produced stale takes.
    row = _read_row_from_queue(args.slug)
    if row is None:
        sys.exit(
            f"No TriviaCaptainQueue row for slug={args.slug!r}. "
            f"Run select_row.py --day N --slug {args.slug} and author Queue!J first."
        )
    prompt = (row.get("openart_prompt") or "").strip()
    if not prompt:
        sys.exit(
            f"Queue!J (OpenArt Prompt) is empty for slug={args.slug!r}. "
            "Author the prompt in the sheet before running."
        )
    # Tablet-screen reference (in-camera render; see asset-director).
    reference_image = (row.get("reference_image") or "").strip() or None
    if reference_image and not Path(reference_image).is_absolute():
        reference_image = str(REPO / reference_image)
    source = "TriviaCaptainQueue (sheet)"

    # Pipeline constants from the playbook (NOT per-row).
    pb = _playbook_openart_defaults()
    duration_s = int(pb.get("duration_s") or 15)
    character = pb.get("character", "Captain Archibald")
    model = args.model_override or pb.get("model") or "Seedance 2.0"
    audio_on = bool(pb.get("audio_on", True))
    resolution = (str(pb.get("resolution") or "480p")).strip()

    print(f"  prompt source: {source}")
    if not prompt:
        sys.exit(f"prompt is empty (source: {source})")
    if not (10 <= duration_s <= 20):
        sys.exit(f"duration_s must be 10..20 (got {duration_s})")
    if character != "Captain Archibald":
        print(f"⚠ character override: {character} (expected Captain Archibald)",
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
        reference_image=reference_image,
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
