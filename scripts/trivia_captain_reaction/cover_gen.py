#!/usr/bin/env python
"""Generate a scroll-stopping cover image for a trivia-reaction reel.

Recipe (validated 2026-07-02):
  1. Pick a peak-emotion frame from the rendered reel (auto, or --frame-time).
  2. Feed it to OpenArt Nano Banana Pro as a reference image so her real face
     stays authentic, and let the MODEL render the headline text + treatment
     (Python-burned text looks flat — never ship it).
  3. Save N variants to assets/images/<slug>_cover_v{i}.png and copy the
     picked variant to the canonical <slug>_cover.png that publish + the web
     UI consume.

The frame, headline, emotion and any prop/character are creative decisions the
agent makes per row after reviewing the render (emotion should match the joke —
laughing for absurd, wide-eyed for shocking, deadpan for disbelief — and a prop
or recurring character like the husband/cat can be introduced when the fact
calls for it).

Usage:
    python scripts/trivia_captain_reaction/cover_gen.py <slug> \
        --headline "MOWING IS ILLEGAL?!" --highlight "ILLEGAL?!" \
        --emotion "roaring with laughter, head thrown back" \
        --prop "a red 'no lawn-mowing' prohibition sign (a lawnmower crossed out with a red circle)" \
        [--frame-time 8.9] [--character "Captain Archibald"] [--variants 2] [--headless]
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "trivia_images"))

import openart_image_driver as D  # noqa: E402
from scripts.trivia_captain_reaction.paths import project_dir  # noqa: E402

MODEL = "Nano Banana Pro"
ASPECT = "9:16"
RESOLUTION = "2K"
# The saved character lives in the personal "R N" workspace (same as the video
# pipeline); reference-only runs use the default art-team workspace.
CHARACTER_WORKSPACE = "R N"

# Typographic treatments for the headline — so covers don't all look identical.
# (font phrase, highlight-word treatment). Pick one per row, or "auto" to rotate.
STYLE_PRESETS: dict[str, tuple[str, str]] = {
    "condensed": ("a heavy condensed sans-serif with a thick black outline",
                  "highlighted with a rough yellow brush stroke"),
    "marker":    ("a chunky hand-drawn marker display font with a bold black outline",
                  "under a rough hand-drawn yellow underline"),
    "impact":    ("a tall bold impact-style poster font, all caps, with a heavy black stroke",
                  "sitting on a bright solid-yellow highlight box"),
    "sticker":   ("a rounded bold sans-serif with a thick white sticker outline and soft drop shadow",
                  "on a solid yellow rounded panel"),
    "comic":     ("a bold comic-book display font with a heavy black outline, slightly tilted",
                  "highlighted with a rough yellow brush stroke"),
}


def pick_style(slug: str) -> str:
    """Deterministically rotate a style per slug so covers vary but a given row
    is stable across rebuilds."""
    import hashlib
    names = sorted(STYLE_PRESETS)
    h = int(hashlib.sha1(slug.encode()).hexdigest(), 16)
    return names[h % len(names)]


def _probe_duration(video: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(video)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def _extract_frame(video: Path, t: float, dest: Path) -> Path:
    subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-ss", f"{t}",
         "-i", str(video), "-frames:v", "1", str(dest), "-y"],
        check=True,
    )
    return dest


def _pick_peak_frame(video: Path, workdir: Path) -> float:
    """Heuristic peak-emotion pick: sample frames and choose the one with the
    most 'open, bright, high-energy' face (a laughter/surprise proxy — bright
    teeth + wide expression read as near-white pixels in the central region).
    Good default; the agent overrides with --frame-time when it knows better."""
    from PIL import Image

    dur = _probe_duration(video)
    # Skip the first/last 8% (intro settle + sign-off tail).
    lo, hi = dur * 0.08, dur * 0.92
    n = 18
    best_t, best_score = (lo + hi) / 2, -1.0
    for i in range(n):
        t = lo + (hi - lo) * i / (n - 1)
        f = _extract_frame(video, t, workdir / f"scan_{i:02d}.png")
        im = Image.open(f).convert("L")
        w, h = im.size
        # central face-ish box (mouth/teeth live here in a selfie framing)
        box = im.crop((int(w * 0.30), int(h * 0.28), int(w * 0.70), int(h * 0.62)))
        px = list(box.getdata())
        bright = sum(1 for p in px if p > 200) / len(px)   # teeth / open mouth
        score = bright
        if score > best_score:
            best_score, best_t = score, t
    return round(best_t, 2)


def build_prompt(headline: str, highlight: str, emotion: str, prop: str,
                 style: str = "condensed") -> str:
    font_phrase, hl_phrase = STYLE_PRESETS.get(
        (style or "").strip() or "condensed", STYLE_PRESETS["condensed"])
    hl = highlight.strip()
    if hl and hl.lower() in headline.lower():
        color_clause = f"the words rendered in white except '{hl}' in bright yellow {hl_phrase}"
    else:
        color_clause = f"the words in white with the most surprising word in bright yellow {hl_phrase}"
    prop_clause = ""
    if prop.strip():
        prop_clause = (
            f" Add a small hand-drawn white arrow from the headline pointing "
            f"down to {prop.strip()} in the lower-left corner."
        )
    emo = emotion.strip() or "his exact expression and pose from the photo"
    return (
        "Vibrant vertical 9:16 YouTube-style thumbnail. Keep the man EXACTLY "
        f"as he is — same face, same {emo}, same clothing, same identity, do "
        "not alter his features. Recompose so he sits in the lower-right of "
        "the frame, zoomed out slightly with clear headroom above him, leaving "
        "the upper-left area as clean, slightly darkened background. In that "
        f"clear upper-left space place a bold uppercase poster headline reading "
        f"'{headline}' in {font_phrase} — {color_clause}. The headline must NOT "
        f"overlap his face.{prop_clause} Warm cinematic lighting, subtle dark "
        "vignette at the edges, punchy vivid color grading, high contrast, clean "
        "professional social-media thumbnail. The text must be perfectly spelled "
        "and clearly legible."
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slug")
    ap.add_argument("--prompt", default="", help="full prompt, used VERBATIM (overrides the assembled one); the reviewable/tweakable SoT")
    ap.add_argument("--headline", default="", help="cover hook, e.g. 'MOWING IS ILLEGAL?!' (required unless --prompt given)")
    ap.add_argument("--highlight", default="", help="the word(s) rendered in yellow (default: model picks)")
    ap.add_argument("--emotion", default="", help="expression to preserve, e.g. 'wide-eyed in disbelief'")
    ap.add_argument("--prop", default="", help="subject cutout the arrow points to (optional)")
    ap.add_argument("--style", default="auto",
                    help=f"headline text style: {', '.join(sorted(STYLE_PRESETS))}, or 'auto' (rotate by slug)")
    ap.add_argument("--frame-time", type=float, default=None, help="seconds into the render for the base frame (default: auto peak-pick)")
    ap.add_argument("--character", default="", help="OpenArt saved character (e.g. Captain Archibald) — locks his face when the scene is reimagined")
    ap.add_argument("--variants", type=int, default=2)
    ap.add_argument("--pick", type=int, default=1, help="which variant becomes the canonical <slug>_cover.png (1-indexed)")
    ap.add_argument("--headless", action="store_true")
    args = ap.parse_args()

    if not args.prompt.strip() and not args.headline.strip():
        ap.error("--headline is required unless --prompt is given")

    render = project_dir(args.slug) / "renders" / f"{args.slug}.mp4"
    if not render.exists():
        sys.exit(f"render not found at {render} — run Generate first")

    images_dir = project_dir(args.slug) / "assets" / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        t = args.frame_time
        if t is None:
            print("→ auto-picking peak-emotion frame…", file=sys.stderr)
            t = _pick_peak_frame(render, tdp)
        print(f"→ base frame at {t}s", file=sys.stderr)
        frame = _extract_frame(render, t, tdp / "base.png")

        # The prompt is the reviewable/tweakable source of truth: use --prompt
        # verbatim when given, else assemble from the structured fields.
        style = pick_style(args.slug) if (args.style or "auto") == "auto" else args.style
        prompt = args.prompt.strip() or build_prompt(
            args.headline, args.highlight, args.emotion, args.prop, style=style)
        # Persist the effective prompt so the web UI / sheet can review + tweak it.
        (images_dir / f"{args.slug}_cover_prompt.txt").write_text(prompt)
        print(f"→ prompt ({len(prompt)} chars):\n{prompt}\n", file=sys.stderr)

        out_paths = [images_dir / f"{args.slug}_cover_v{i+1}.png" for i in range(args.variants)]
        char = args.character.strip() or None
        workspace = CHARACTER_WORKSPACE if char else D.OPENART_WORKSPACE

        saved = D.generate_image(
            prompt=prompt,
            model=MODEL,
            output_paths=out_paths,
            headless=args.headless,
            aspect=ASPECT,
            resolution=RESOLUTION,
            reference_image_path=frame,
            character=char,
            workspace=workspace,
        )

    if not saved:
        sys.exit("cover generation returned no images")
    pick = max(1, min(args.pick, len(saved)))
    canonical = images_dir / f"{args.slug}_cover.png"
    shutil.copy(saved[pick - 1], canonical)
    for s in saved:
        print(f"variant: {s}")
    print(f"✓ canonical cover (v{pick}): {canonical.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
