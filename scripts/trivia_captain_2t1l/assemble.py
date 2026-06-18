"""Normalize the Captain clip + compute the per-row Remotion props (the 'edit' stage).

- Normalizes assets/video/clip.mp4 → assets/video/bg.mp4 (1080x1920 h264 + AAC).
- Transcribes the native VO to word-level timestamps via scripts/common/transcribe.py,
  writing assets/../artifacts/words.json for the bottom karaoke captions (the same
  caption style as the ellie.travelcrush / trivia-reaction videos).
- Writes assets/props.json: { themeName, place, title, claims:[{label, revealAtSec}] }
  consumed by the TriviaTwoTruthsK3 Remotion composition.

Usage:
    python scripts/trivia_captain_2t1l/assemble.py <slug>
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from scripts.trivia_captain_2t1l import paths, queue_row  # noqa: E402


def _ffprobe_duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                         capture_output=True, text=True)
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def normalize_bg(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-i", str(src),
        "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1",
        "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100", str(dst),
    ], check=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("slug")
    args = ap.parse_args()

    pdir = paths.project_dir(args.slug)
    clip = pdir / "assets" / "video" / "clip.mp4"
    if not clip.exists():
        sys.exit(f"clip not found: {clip} — run openart_generate.py first")
    bg = pdir / "assets" / "video" / "bg.mp4"
    print(f"→ normalizing {clip.name} → bg.mp4")
    normalize_bg(clip, bg)

    # Pull labels/place/theme from the Queue (script-director authored them).
    sheets = queue_row.build_sheets(write=False)
    row = queue_row.find_row_by_slug(sheets, args.slug)
    r = queue_row.read_queue_row(sheets, row) if row else {}
    labels = [r.get("label_1") or "Claim 1", r.get("label_2") or "Claim 2",
              r.get("label_3") or "Claim 3"]
    place = r.get("place") or "Today"
    theme = r.get("theme") or "goldround"

    # Bottom karaoke captions come from word-level transcription of the VO.
    # scripts/common/transcribe.py reads <root>/<slug>/assets/video/bg.mp4 and
    # writes <root>/<slug>/artifacts/words.json (with brand-token casing for
    # "Captain"/"Travel Crush" and number-merge fixes). It runs Whisper with
    # local_files_only so it never hits the HF Hub user-agent bug.
    print("→ transcribing VO → words.json (for bottom captions)")
    from scripts.common import transcribe as common_transcribe
    common_transcribe.main(args.slug, root=paths.PROJECTS_ROOT)

    # claims/revealAtSec are only consumed by the legacy fact-bar layout
    # (minimal=false); kept with even spacing so that path still works if re-enabled.
    props = {
        "themeName": theme,
        "place": place,
        "title": "2 TRUTHS, 1 LIE",
        "claims": [{"label": labels[i], "revealAtSec": [2.0, 5.0, 8.0][i]} for i in range(3)],
        "durationS": round(_ffprobe_duration(bg), 2),
    }
    (pdir / "assets" / "props.json").write_text(json.dumps(props, indent=2))
    print(f"✓ wrote {pdir / 'assets' / 'props.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
