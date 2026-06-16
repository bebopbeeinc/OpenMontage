"""Normalize the Captain clip + compute the per-row Remotion props (the 'edit' stage).

- Normalizes assets/video/clip.mp4 → assets/video/bg.mp4 (1080x1920 h264 + AAC).
- Transcribes the native VO (Whisper) to find when "one / two / three" are spoken,
  so each fact banner reveals exactly as the Captain counts it.
- Writes assets/props.json: { themeName, place, title, claims:[{label, revealAtSec}] }
  consumed by the TriviaTwoTruthsK3 Remotion composition.

Usage:
    python scripts/trivia_captain_2t1l/assemble.py <slug>
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
from scripts.trivia_captain_2t1l import paths, queue_row  # noqa: E402

FALLBACK_REVEALS = [2.0, 5.0, 7.7]
STOPWORDS = {"a", "an", "the", "it", "its", "is", "has", "have", "of", "to",
             "and", "in", "on", "at", "that", "this", "with", "out", "your"}


def _norm(w: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (w or "").lower())


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


def reveal_times(bg: Path, claims: list[str]) -> list[float]:
    """Start time (s) of each CLAIM in the VO — when its banner should reveal.

    Aligns each claim to the transcript by matching its first 1-2 distinctive
    (non-stopword) tokens against the word timeline, scanning forward so the
    claims stay in order. Falls back to even spacing if a claim can't be found.
    """
    try:
        from tools.tool_registry import registry
        registry.discover()
        res = registry._tools["transcriber"].execute(
            {"input_path": str(bg), "model_size": "small", "language": "en",
             "word_timestamps": True})
        words = [w for s in (res.data or {}).get("segments", []) for w in (s.get("words") or [])]
    except Exception as e:  # noqa: BLE001
        print(f"  ! transcription failed ({e}); using fallback reveal times")
        return list(FALLBACK_REVEALS)

    seq = [(_norm(w.get("word")), float(w.get("start", 0))) for w in words]

    # Primary: fire each banner on the spoken number ("one/two/three"; whisper
    # sometimes hears "two" as "to"/"too"). If all three are found in order, use them.
    NUM = {"one": 0, "two": 1, "three": 2, "to": 1, "too": 1}
    num_hits: dict[int, float] = {}
    for norm, start in seq:
        if start < 0.5:
            continue
        idx = NUM.get(norm)
        if idx is not None and idx not in num_hits:
            num_hits[idx] = start
    if len(num_hits) == 3:
        out = [round(num_hits[i], 2) for i in range(3)]
        for i in range(1, 3):
            if out[i] <= out[i - 1]:
                out[i] = round(out[i - 1] + 2.0, 2)
        return out

    # Fallback: align each claim to its first distinctive spoken words.
    times: list[float] = []
    search = 0
    for ci, claim in enumerate(claims):
        toks = [t for t in (_norm(x) for x in claim.split()) if t]
        anchors = [t for t in toks if t not in STOPWORDS][:2] or toks[:1]
        found = None
        for i in range(search, len(seq)):
            if anchors and seq[i][0] == anchors[0]:
                if len(anchors) < 2 or any(
                        seq[j][0] == anchors[1] for j in range(i + 1, min(i + 6, len(seq)))):
                    found = i
                    break
        if found is None:
            fallback = FALLBACK_REVEALS[ci] if ci < len(FALLBACK_REVEALS) else (
                (times[-1] + 2.5) if times else 2.0)
            print(f"  ! claim {ci+1} not matched in transcript; using {fallback}s")
            times.append(fallback)
            continue
        times.append(round(seq[found][1], 2))
        search = found + max(1, len(toks) - 1)
    for i in range(1, len(times)):
        if times[i] <= times[i - 1]:
            times[i] = round(times[i - 1] + 2.0, 2)
    return times


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

    # Pull claims/labels/place/theme from the Queue (script-director authored them).
    sheets = queue_row.build_sheets(write=False)
    row = queue_row.find_row_by_slug(sheets, args.slug)
    r = queue_row.read_queue_row(sheets, row) if row else {}
    claims = [r.get("claim_1") or "", r.get("claim_2") or "", r.get("claim_3") or ""]
    labels = [r.get("label_1") or "Claim 1", r.get("label_2") or "Claim 2",
              r.get("label_3") or "Claim 3"]

    # Minimal layout (default) shows only the header lockup — no per-claim
    # reveal banners — so the Whisper word-alignment is unnecessary. Skip it and
    # fall back to even spacing (kept in props only so the legacy K3 fact bars
    # still work if rendered with minimal=false). Set TC2T1L_REVEAL_ALIGN=1 to
    # force the alignment pass.
    import os
    if os.environ.get("TC2T1L_REVEAL_ALIGN") == "1":
        print("→ transcribing + aligning each claim to its banner reveal")
        reveals = reveal_times(bg, claims)
    else:
        reveals = [2.0, 5.0, 8.0]
        print("→ minimal layout: skipping Whisper reveal-alignment (even spacing)")
    print(f"  reveal times: {reveals}")
    place = r.get("place") or "Today"
    theme = r.get("theme") or "goldround"

    props = {
        "themeName": theme,
        "place": place,
        "title": "2 TRUTHS, 1 LIE",
        "claims": [{"label": labels[i], "revealAtSec": reveals[i]} for i in range(3)],
        "durationS": round(_ffprobe_duration(bg), 2),
    }
    (pdir / "assets" / "props.json").write_text(json.dumps(props, indent=2))
    print(f"✓ wrote {pdir / 'assets' / 'props.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
