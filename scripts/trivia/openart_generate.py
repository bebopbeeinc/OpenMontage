#!/usr/bin/env python
"""Generate the missing OpenArt clips for one Post Calendar row.

Reads the row, figures out which of the 3 segments (reaction / body / closer)
already have a local file in their library, and drives openart.ai via the
Playwright driver to generate any that are still missing.

Routing:
    reaction  → HappyHorse,    duration 3s, → scripts/trivia/library/reactions/<reaction_filename>
    body      → Seedance 2.0,  duration 8s, → scripts/trivia/library/bodies/<body_filename>
    closer    → Seedance 2.0,  duration 4s, → scripts/trivia/library/closers/<closer_filename>

Usage:
    python scripts/trivia/openart_generate.py <row>
    python scripts/trivia/openart_generate.py <row> --segments body
    python scripts/trivia/openart_generate.py <row> --force            # re-gen even if exists
    python scripts/trivia/openart_generate.py <row> --headless

Example:
    python scripts/trivia/openart_generate.py 13
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

# Local imports: shared scripts live in scripts/common/, trivia-only ones live here.
_SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SCRIPTS / "common"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from openart_driver import generate_clip  # noqa: E402
from post_row import build_sheets, read_post_row  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
# Pipeline-local clip library. Each segment writes generated MP4s into its
# own sub-folder under scripts/trivia/library/. Gitignored — large media.
LIBRARY_BASE = Path(__file__).resolve().parent / "library"


@dataclass(frozen=True)
class SegmentSpec:
    name: str                         # "reaction" | "body" | "closer"
    library_dir: Path                 # destination folder
    filename_key: str                 # row-dict key for the canonical filename
    prompt_key: str                   # row-dict key for the prompt text
    model: str                        # OpenArt model name (Model picker label)
    duration_s: int                   # clip duration in seconds
    character: str | None = None      # OpenArt saved character (My Library → Characters)


SEGMENTS = [
    SegmentSpec("reaction", LIBRARY_BASE / "reactions", "reaction_filename", "reaction_prompt", "HappyHorse",     3),
    SegmentSpec("body",     LIBRARY_BASE / "bodies",    "body_filename",     "body_prompt",     "Seedance 2.0",  8),
    SegmentSpec("closer",   LIBRARY_BASE / "closers",   "closer_filename",   "closer_prompt",   "Seedance 2.0",  4,
                 character="Captain Archibald"),
]




def _variant_paths(library_dir: Path, filename: str, variants: int) -> list[Path]:
    """Build per-variant output paths.

    For variants=1, returns [<library>/<filename>] (no suffix — backward
    compatible with the existing assemble_modular pipeline).
    For variants≥2, returns [<stem>_v1<ext>, <stem>_v2<ext>, …].
    """
    if variants == 1:
        return [library_dir / filename]
    p = Path(filename)
    return [library_dir / f"{p.stem}_v{i + 1}{p.suffix}" for i in range(variants)]


def _resolve_jobs(
    row: dict,
    wanted: set[str],
    force: bool,
    variants: int,
) -> list[tuple[SegmentSpec, str, list[Path]]]:
    """Return a list of (spec, prompt, [paths]) for segments that need generating.

    Skip rules (any one triggers a skip unless --force):
      1. The canonical (no-suffix) filename already exists in the library —
         this is the user's "approved final" file (typically renamed from a
         variant after review). Once present, future runs leave it alone.
      2. Any of the per-variant paths already exists. We don't generate
         just the missing variants because OpenArt submits a single batch
         job for N variants.
    """
    jobs: list[tuple[SegmentSpec, str, list[Path]]] = []
    for spec in SEGMENTS:
        if spec.name not in wanted:
            continue
        filename = (row.get(spec.filename_key) or "").strip()
        prompt = (row.get(spec.prompt_key) or "").strip()
        if not filename:
            print(f"  · {spec.name}: skip (no filename in sheet)")
            continue
        if not prompt:
            print(f"  · {spec.name}: skip ({spec.prompt_key} empty in sheet)")
            continue
        canonical = spec.library_dir / filename
        if canonical.exists() and not force:
            print(f"  · {spec.name}: skip (final {canonical.name} already exists; use --force to regen)")
            continue
        paths = _variant_paths(spec.library_dir, filename, variants)
        existing = [p for p in paths if p.exists()]
        if existing and not force:
            print(f"  · {spec.name}: skip ({len(existing)}/{len(paths)} variants exist; use --force to regen)")
            continue
        jobs.append((spec, prompt, paths))
    return jobs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("row", type=int)
    ap.add_argument(
        "--segments",
        default="reaction,body,closer",
        help="comma-separated subset of {reaction,body,closer}",
    )
    ap.add_argument("--variants", type=int, default=2,
                    help="number of variants to generate per segment (default 2)")
    ap.add_argument("--audio", action="store_true",
                    help="leave OpenArt's audio on (default: off; trivia VO is added in post)")
    ap.add_argument("--force", action="store_true", help="re-generate even if local file exists")
    ap.add_argument("--headless", action="store_true")
    args = ap.parse_args()

    if args.variants < 1:
        sys.exit("--variants must be ≥ 1")

    wanted = {s.strip() for s in args.segments.split(",") if s.strip()}
    invalid = wanted - {s.name for s in SEGMENTS}
    if invalid:
        sys.exit(f"unknown segments: {sorted(invalid)}")

    sheets = build_sheets()
    row = read_post_row(sheets, args.row)
    print(f"row {args.row}: {row.get('post', '?')}  (slug={row.get('slug', '?')})")

    jobs = _resolve_jobs(row, wanted, args.force, args.variants)
    if not jobs:
        print("nothing to generate.")
        return 0

    print(f"\n{len(jobs)} segment(s) to generate (audio={'on' if args.audio else 'off'}):")
    for spec, _, paths in jobs:
        names = ", ".join(p.name for p in paths)
        print(f"  - {spec.name:8s} → {spec.model} {spec.duration_s}s × {len(paths)} → {names}")
    print()

    for spec, prompt, paths in jobs:
        print(f"=== {spec.name} ({spec.model}, {spec.duration_s}s × {len(paths)}) ===")
        try:
            saved = generate_clip(
                prompt=prompt,
                model=spec.model,
                duration_s=spec.duration_s,
                output_paths=paths,
                headless=args.headless,
                audio_on=args.audio,
                character=spec.character,
            )
            for p in saved:
                print(f"✓ {spec.name}: {p}")
        except Exception as e:
            print(f"✗ {spec.name}: {e}", file=sys.stderr)
            return 1

    print("\nall done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
