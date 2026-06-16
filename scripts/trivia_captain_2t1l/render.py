"""Render the TriviaTwoTruthsK3 overlay over the Captain clip (the 'compose' stage).

Stages bg.mp4 into remotion-composer/public/ and renders the minimal, safe-zone
overlay: a single centered header lockup = the TC logo + "📍 <place>" pill (the
"2 TRUTHS, 1 LIE" title was removed), sized to hug content and parked below the
TikTok top tabs so it never collides with the top/bottom chrome or the right
action rail. The claims are spoken-only (no on-screen fact banners). props.json
carries theme/place (+ title/claims, ignored unless the legacy fact-bar layout
is re-enabled with minimal=false).

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
    render_props = {k: props[k] for k in ("themeName", "place", "title", "claims") if k in props}

    out = pdir / "renders" / f"{args.slug}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    print(f"→ rendering TriviaTwoTruthsK3 (theme={render_props.get('themeName')}) → {out}")
    subprocess.run([
        "npx", "remotion", "render",
        "src/index-trivia-2t1l-k3.tsx", "TriviaTwoTruthsK3",
        str(out), f"--props={json.dumps(render_props)}",
    ], cwd=str(REMOTION), check=True)
    print(f"✓ rendered {out}")
    print("  MANDATORY: extract frames at ~3s/6s/9s/13s and verify banners reveal in sync, "
          "place banner reads, Captain's face is clear, nothing clipped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
