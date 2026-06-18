"""Render the TriviaTwoTruthsK3 overlay over the Captain clip (the 'compose' stage).

Stages bg.mp4 into remotion-composer/public/ and renders TWO deliverables with the
same K3 treatment (full-bleed Captain + centered header lockup: TC logo + "📍
<place>" pill), differing only in captions:
  - renders/<slug>.mp4       — the final WITH word-level karaoke captions (the
                               posted video); same caption style as the
                               ellie.travelcrush (trivia-reaction) videos.
  - renders/<slug>_clip.mp4  — the "clip": same treatment, captions OFF. This is
                               the secondary deliverable (NOT the raw Seedance clip).
Caption words come from artifacts/words.json (produced by scripts/common/transcribe.py
during assemble).

Usage:
    python scripts/trivia_captain_2t1l/render.py <slug>
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from scripts.trivia_captain_2t1l import paths  # noqa: E402

REMOTION = REPO / "remotion-composer"
PUBLIC = REMOTION / "public"
LOGO_SRC = REPO / "scripts" / "trivia_captain" / "assets" / "tc_logo.png"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("slug")
    args = ap.parse_args()

    pdir = paths.project_dir(args.slug)
    bg = pdir / "assets" / "video" / "bg.mp4"
    props_path = pdir / "assets" / "props.json"
    if not bg.exists() or not props_path.exists():
        sys.exit("missing bg.mp4 or props.json — run assemble.py first")

    # Stage assets into Remotion public/ (the K3 index reads staticFile names).
    PUBLIC.mkdir(parents=True, exist_ok=True)
    shutil.copy(bg, PUBLIC / "2t1l_bg.mp4")
    if not (PUBLIC / "tc_logo.png").exists() and LOGO_SRC.exists():
        shutil.copy(LOGO_SRC, PUBLIC / "tc_logo.png")

    props = json.loads(props_path.read_text())
    # Only the overlay props — videoSrc/logoSrc come from the index defaults (staticFile).
    base_props = {k: props[k] for k in ("themeName", "place", "title", "claims") if k in props}
    # Word-level karaoke captions (ellie.travelcrush style). words.json is written
    # by scripts/common/transcribe.py during assemble; pass it straight through.
    words_path = pdir / "artifacts" / "words.json"
    words = json.loads(words_path.read_text()) if words_path.exists() else []
    if not words:
        print(f"  ⚠ no words.json at {words_path} — final render will have no captions")

    renders = pdir / "renders"
    renders.mkdir(parents=True, exist_ok=True)
    # Two deliverables, identical K3 treatment (header lockup), differing only in
    # captions:
    #   <slug>.mp4       — the final, WITH word-level captions (the posted video)
    #   <slug>_clip.mp4  — the "clip", same treatment but captions OFF (words=[])
    targets = [
        (f"{args.slug}.mp4", {**base_props, "words": words}),
        (f"{args.slug}_clip.mp4", {**base_props, "words": []}),
    ]
    for name, render_props in targets:
        out = renders / name
        print(f"→ rendering TriviaTwoTruthsK3 (theme={render_props.get('themeName')}, "
              f"captions={'on' if render_props['words'] else 'off'}) → {out}")
        subprocess.run([
            "npx", "remotion", "render",
            "src/index-trivia-2t1l-k3.tsx", "TriviaTwoTruthsK3",
            str(out), f"--props={json.dumps(render_props)}",
        ], cwd=str(REMOTION), check=True)
        print(f"✓ rendered {out}")
    print("  MANDATORY: extract frames at ~3s/6s/9s/13s and verify the karaoke "
          "captions track the VO, the place header reads, Captain's face is clear, "
          "nothing clipped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
