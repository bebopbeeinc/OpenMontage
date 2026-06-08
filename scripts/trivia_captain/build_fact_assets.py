#!/usr/bin/env python
"""Phase 0 of the trivia-captain Generate chain: fact image -> tablet reference.

Self-contained so the web UI's Generate button produces everything in one run
(no pre-generating the fact image as a separate manual step). Reads the
per-row fact-image prompt from the Queue, generates the portrait fact image
via OpenArt, composites the full-bleed tablet reference (fact + Travel Crush
logo watermark), and writes the reference path into Queue!M.

Idempotent by default: if the tablet reference already exists it is REUSED
(only Queue!M is refreshed) — so avatar re-rolls don't burn an image credit
or change the reference. Pass --force to re-roll a fresh fact image.

Reads:
    Queue!N  Fact Image Prompt   (authored by the script director)
Writes:
    projects/trivia-captain/<slug>/assets/images/fact_<slug>.(jpg|png)
    projects/trivia-captain/<slug>/assets/images/tablet_ref.png
    Queue!M  Reference Image      (repo-relative path to tablet_ref.png)

Usage:
    python scripts/trivia_captain/build_fact_assets.py <slug>
    python scripts/trivia_captain/build_fact_assets.py <slug> --force      # re-roll the fact image
    python scripts/trivia_captain/build_fact_assets.py <slug> --headless
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from scripts.trivia_captain import build_tablet_ref, queue_row  # noqa: E402
from scripts.trivia_captain.paths import project_dir  # noqa: E402

# Fact image is reinterpreted on the tablet by Seedance, so a cheap/fast model
# at low resolution is plenty (see script-director). Portrait so it fills the
# full-bleed tablet screen without heavy side-cropping.
DEFAULT_MODEL = "Nano Banana 2"
DEFAULT_ASPECT = "3:4"
DEFAULT_RESOLUTION = "1K"


def _row_for_slug(ws, slug: str) -> dict | None:
    for r in queue_row.read_queue_bulk(ws):
        if (r.get("slug") or "").strip() == slug:
            return r
    return None


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slug", help="project slug (matches projects/trivia-captain/<slug>/)")
    ap.add_argument("--force", action="store_true",
                    help="re-roll a fresh fact image even if the reference already exists")
    ap.add_argument("--headless", action="store_true",
                    help="run the OpenArt image driver headless")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--aspect", default=DEFAULT_ASPECT)
    ap.add_argument("--resolution", default=DEFAULT_RESOLUTION)
    ap.add_argument("--logo", type=Path, default=None,
                    help="game logo PNG (default: build_tablet_ref's default)")
    args = ap.parse_args()

    imgdir = project_dir(args.slug) / "assets" / "images"
    tablet_ref = imgdir / "tablet_ref.png"
    rel_ref = tablet_ref.relative_to(REPO)

    ws = queue_row.build_sheets(write=True)
    row = _row_for_slug(ws, args.slug)
    if row is None:
        sys.exit(f"No TriviaCaptainQueue row for slug={args.slug!r}.")

    # Idempotent reuse: keep the existing reference unless --force.
    if tablet_ref.exists() and not args.force:
        print(f"  reusing existing tablet reference: {rel_ref}")
        if (row.get("reference_image") or "").strip() != str(rel_ref):
            queue_row.update_cells(ws, row["row"], reference_image=str(rel_ref))
            print(f"  Queue!M (row {row['row']}) -> {rel_ref}")
        return 0

    fact_prompt = (row.get("fact_image_prompt") or "").strip()
    if not fact_prompt:
        sys.exit(
            f"Queue!N (Fact Image Prompt) is empty for slug={args.slug!r}. "
            "Author it (script-director) before Generate.")

    # 1. Generate the portrait fact image via OpenArt.
    from tools.graphics.openart_image import OpenArtImage  # noqa: E402
    imgdir.mkdir(parents=True, exist_ok=True)
    fact_out = imgdir / f"fact_{args.slug}.png"  # keep_source_ext may save .jpg
    print(f"→ openart_image (model={args.model}, aspect={args.aspect}, "
          f"res={args.resolution}) — fact image for {args.slug}")
    res = OpenArtImage().execute({
        "prompt": fact_prompt,
        "model": args.model,
        "aspect": args.aspect,
        "resolution": args.resolution,
        "variants": 1,
        "output_path": str(fact_out),
        "headless": args.headless,
    })
    if not res.success:
        sys.exit(f"openart_image failed: {res.error}")
    saved = [Path(p) for p in res.data.get("saved_paths", [])]
    if not saved:
        sys.exit("openart_image reported success but saved no file")
    fact_path = saved[0]
    print(f"✓ fact image: {fact_path.relative_to(REPO)}")

    # 2. Composite the full-bleed tablet reference (fact + logo watermark).
    logo = (args.logo if args.logo and args.logo.is_absolute()
            else (REPO / args.logo) if args.logo
            else Path.home() / "Downloads" / "tc_logo.png")
    if not logo.is_file():
        sys.exit(f"logo not found: {logo} (pass --logo)")
    build_tablet_ref.build_video(fact_path, logo, tablet_ref)
    print(f"✓ tablet reference: {rel_ref}")

    # 3. Point Queue!M at the reference.
    queue_row.update_cells(ws, row["row"], reference_image=str(rel_ref))
    print(f"  Queue!M (row {row['row']}) -> {rel_ref}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
