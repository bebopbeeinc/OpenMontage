#!/usr/bin/env python3
"""Build the tablet-screen reference image for one trivia-captain row.

The reference is what OpenArt renders in-camera on Archibald's tablet (uploaded
via Upload Media; see asset-director).

Two layouts:

  video  (DEFAULT, 2026-06-08) — the fact image fills the whole tablet screen
         like a frame of a playing video, with the Travel Crush logo as a
         branded overlay badge in a top scrim band:

             ┌──────────────┐
             │ [TRAVEL CRUSH]│   top band — logo over a dark scrim, like a
             │  la tomatina  │              video player's branding watermark
             │  crowd scene  │   full-bleed — the per-row FACT image fills the
             │  (full bleed) │              entire screen so it reads clearly
             └──────────────┘

         User feedback (2026-06-08): the old split made the fact too small to
         see; the fact must dominate, and the logo rides on top of it (it is
         "included in the video"). The prompt directs Seedance to render the
         screen as a *playing video* of the fact (see styles/trivia-captain.yaml
         PROPS) — this still-image reference is the frame it animates from.

  split  (LEGACY) — 50/50 top-bottom split: logo on a brand-blue gradient over
         the fact image. Kept for reference / rollback; the fact reads small.

Portrait 3:4 (1080x1440) to match how the tablet is held in-frame. For the
`video` layout the fact image should be generated PORTRAIT (3:4) so it fills
the screen without heavy side-cropping (cover-crop still handles 4:3, but
crops the sides hard).

The fact image is generated upstream (mirroring trivia-quiz's backdrop path) —
this script only composites. The logo lives outside the repo by default
(~/Downloads/tc_logo.png); pass --logo to override.

Usage:
    python scripts/trivia_captain/build_tablet_ref.py <slug> \
        --fact projects/trivia-captain/<slug>/assets/images/fact_<slug>.png
    # writes projects/trivia-captain/<slug>/assets/images/tablet_ref.png
    # (video layout by default; pass --layout split for the legacy look)

    # also write the path into TriviaCaptainQueue!M:
    python scripts/trivia_captain/build_tablet_ref.py <slug> --fact ... --update-queue
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

# Canvas: portrait 3:4, the tablet-screen aspect when held in-frame.
CANVAS_W, CANVAS_H = 1080, 1440
HALF_H = CANVAS_H // 2                       # 720 — each half is 1080x720 (3:2)

# Brand-blue gradient behind the logo — bright sky-blue, matching the Travel
# Crush splash background so the navy+yellow logo pops.
GRAD_TOP = (205, 235, 255)                   # #CDEBFF pale sky
GRAD_BOTTOM = (143, 205, 243)                # #8FCDF3 deeper sky
LOGO_WIDTH_FRAC = 0.82                        # logo fills 82% of the top half's width
DIVIDER_PX = 6
DIVIDER_COLOR = (255, 255, 255)


def _vertical_gradient(w: int, h: int, top: tuple, bottom: tuple) -> Image.Image:
    grad = Image.new("RGB", (w, h))
    px = grad.load()
    for y in range(h):
        t = y / max(h - 1, 1)
        r = round(top[0] + (bottom[0] - top[0]) * t)
        g = round(top[1] + (bottom[1] - top[1]) * t)
        b = round(top[2] + (bottom[2] - top[2]) * t)
        for x in range(w):
            px[x, y] = (r, g, b)
    return grad


def _cover_crop(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """Scale + center-crop `img` to exactly target_w x target_h (CSS object-fit:
    cover)."""
    src_ratio = img.width / img.height
    dst_ratio = target_w / target_h
    if src_ratio > dst_ratio:
        # source too wide — match height, crop width
        new_h = target_h
        new_w = round(target_h * src_ratio)
    else:
        new_w = target_w
        new_h = round(target_w / src_ratio)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    return img.crop((left, top, left + target_w, top + target_h))


# --- video layout: full-bleed fact + logo in a top scrim band ---
VIDEO_BAND_FRAC = 0.20                        # top 20% carries the logo + scrim
VIDEO_SCRIM_TOP_ALPHA = 150                   # darkness at the very top (0-255)
VIDEO_LOGO_WIDTH_FRAC = 0.52                  # logo width as a frac of canvas
VIDEO_LOGO_TOP_PAD = 40                       # px from the top edge to the logo


def build_video(fact_path: Path, logo_path: Path, out_path: Path) -> Path:
    """Full-bleed fact image with the logo as a top-band watermark.

    The fact image fills the entire tablet screen so it reads clearly; the
    Travel Crush logo rides over a dark gradient scrim at the top, like a
    branded video player's watermark, so it stays legible over any fact image.
    """
    # Full-bleed fact image.
    fact = Image.open(fact_path).convert("RGB")
    canvas = _cover_crop(fact, CANVAS_W, CANVAS_H).convert("RGBA")

    # Dark scrim gradient over the top band (opaque-ish at the very top,
    # fading to transparent at the band's bottom) so the logo always reads.
    band_h = round(CANVAS_H * VIDEO_BAND_FRAC)
    scrim = Image.new("RGBA", (CANVAS_W, band_h), (0, 0, 0, 0))
    spx = scrim.load()
    for y in range(band_h):
        a = round(VIDEO_SCRIM_TOP_ALPHA * (1 - y / max(band_h - 1, 1)))
        for x in range(CANVAS_W):
            spx[x, y] = (0, 0, 0, a)
    canvas.alpha_composite(scrim, (0, 0))

    # Logo centered in the top band.
    logo = Image.open(logo_path).convert("RGBA")
    target_w = round(CANVAS_W * VIDEO_LOGO_WIDTH_FRAC)
    target_h = round(logo.height * (target_w / logo.width))
    # Clamp so the logo stays inside the band.
    max_h = band_h - VIDEO_LOGO_TOP_PAD - 16
    if target_h > max_h:
        target_h = max_h
        target_w = round(logo.width * (target_h / logo.height))
    logo = logo.resize((target_w, target_h), Image.LANCZOS)
    lx = (CANVAS_W - target_w) // 2
    canvas.alpha_composite(logo, (lx, VIDEO_LOGO_TOP_PAD))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(out_path)
    return out_path


def build(fact_path: Path, logo_path: Path, out_path: Path) -> Path:
    canvas = Image.new("RGB", (CANVAS_W, CANVAS_H), GRAD_BOTTOM)

    # --- Top half: logo on brand-blue gradient ---
    top_bg = _vertical_gradient(CANVAS_W, HALF_H, GRAD_TOP, GRAD_BOTTOM)
    canvas.paste(top_bg, (0, 0))

    logo = Image.open(logo_path).convert("RGBA")
    target_w = round(CANVAS_W * LOGO_WIDTH_FRAC)
    target_h = round(logo.height * (target_w / logo.width))
    # Clamp so the logo never overflows the top half vertically.
    if target_h > HALF_H - 80:
        target_h = HALF_H - 80
        target_w = round(logo.width * (target_h / logo.height))
    logo = logo.resize((target_w, target_h), Image.LANCZOS)
    lx = (CANVAS_W - target_w) // 2
    ly = (HALF_H - target_h) // 2
    canvas.paste(logo, (lx, ly), logo)        # use alpha as mask

    # --- Bottom half: fact image, cover-cropped to 3:2 ---
    fact = Image.open(fact_path).convert("RGB")
    fact = _cover_crop(fact, CANVAS_W, HALF_H)
    canvas.paste(fact, (0, HALF_H))

    # --- Divider line between the two halves ---
    if DIVIDER_PX > 0:
        div = Image.new("RGB", (CANVAS_W, DIVIDER_PX), DIVIDER_COLOR)
        canvas.paste(div, (0, HALF_H - DIVIDER_PX // 2))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path)
    return out_path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slug", help="project slug (matches projects/trivia-captain/<slug>/)")
    ap.add_argument("--fact", required=True, type=Path,
                    help="repo-relative or absolute path to the generated fact image")
    ap.add_argument("--logo", type=Path, default=Path.home() / "Downloads" / "tc_logo.png",
                    help="game logo PNG (default ~/Downloads/tc_logo.png)")
    ap.add_argument("--layout", choices=("video", "split"), default="video",
                    help="video (default): full-bleed fact + logo watermark; "
                         "split (legacy): 50/50 logo-over-fact")
    ap.add_argument("--out", type=Path, default=None,
                    help="output path (default projects/trivia-captain/<slug>/assets/images/tablet_ref.png)")
    ap.add_argument("--update-queue", action="store_true",
                    help="write the repo-relative output path into TriviaCaptainQueue!M")
    args = ap.parse_args()

    fact = args.fact if args.fact.is_absolute() else REPO / args.fact
    logo = args.logo if args.logo.is_absolute() else REPO / args.logo
    for label, p in (("fact image", fact), ("logo", logo)):
        if not p.is_file():
            sys.exit(f"{label} not found: {p}")

    out = args.out or (REPO / "projects" / "trivia-captain" / args.slug
                       / "assets" / "images" / "tablet_ref.png")
    out = out if out.is_absolute() else REPO / out

    builder = build_video if args.layout == "video" else build
    built = builder(fact, logo, out)
    rel = built.relative_to(REPO)
    print(f"✓ tablet reference: {rel}  ({Image.open(built).size[0]}x{Image.open(built).size[1]})")

    if args.update_queue:
        from scripts.trivia_captain import queue_row
        ws = queue_row.build_sheets(write=True)
        row = next((r for r in queue_row.read_queue_bulk(ws)
                    if (r.get("slug") or "").strip() == args.slug), None)
        if not row:
            sys.exit(f"--update-queue: no TriviaCaptainQueue row for slug={args.slug!r}")
        queue_row.update_cells(ws, row["row"], reference_image=str(rel))
        print(f"  TriviaCaptainQueue!M (row {row['row']}) -> {rel}")


if __name__ == "__main__":
    main()
