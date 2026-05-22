#!/usr/bin/env python
"""Assemble the trivia-reaction bg video + meta for one row.

Seedance 2.0 generates the avatar clip with native synced voice — there is
no separate TTS step. The OpenArt clip already carries the dialogue
lip-synced; this stage just normalizes the clip and writes the metadata
the compose stage needs.

Inputs (preferred → fallback):
    projects/trivia-reaction/<slug>/artifacts/brief.json   (optional; sheet_revision tag)
    projects/trivia-reaction/<slug>/artifacts/script.json  (optional; beats)
    TriviaReactionQueue row matching <slug>                (used when either file is missing)
    scripts/trivia_reaction/library/clips/<slug>.mp4       (avatar clip from Seedance, audio inline)

Outputs:
    projects/trivia-reaction/<slug>/assets/video/bg.mp4        (normalized 1080x1920 / h264 / AAC, audio preserved)
    projects/trivia-reaction/<slug>/assets/meta.json           (consumed by Remotion TriviaWithBg)
    projects/trivia-reaction/<slug>/artifacts/edit_decisions.json

The compose stage transcribes bg.mp4's audio track to produce word-level
timestamps (words.json), then renders Remotion's TriviaWithBg with
showFactsOverlay=false + highlightColor=#FF6A2C.

Usage:
    python scripts/trivia_reaction/assemble.py <slug>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from scripts.trivia_reaction import queue_row  # noqa: E402
from scripts.trivia_reaction.paths import project_dir  # noqa: E402

LIBRARY_DIR = REPO / "scripts" / "trivia_reaction" / "library" / "clips"


def ffmpeg(args: list[str]) -> None:
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args], check=True)


def ffprobe_duration(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(r.stdout.strip())


def ffprobe_has_audio(path: Path) -> bool:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=codec_type",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return r.stdout.strip() == "audio"


def _find_queue_row(slug: str) -> dict | None:
    """Look up `slug` in TriviaReactionQueue. Returns the row dict or None."""
    try:
        ws = queue_row.build_sheets(write=False)
        rows = queue_row.read_queue_bulk(ws)
    except Exception as e:  # noqa: BLE001
        print(f"⚠ Queue lookup failed: {e}", file=sys.stderr)
        return None
    for r in rows:
        if (r.get("slug") or "").strip() == slug:
            return r
    return None


def _queue_revision_hash(qrow: dict) -> str:
    """SHA-1 over the Queue cells that drive this pipeline. Same intent as
    select_row.compute_revision_hash (which hashes DailyTriviaConfig) — this
    variant hashes the workflow-SoT Queue row instead, so a hand-edit to
    Queue!D/F/G/H is reflected in edit_decisions.metadata.sheet_revision
    even when brief.json was never materialized locally."""
    payload = [
        (qrow.get("day") or "").strip(),
        (qrow.get("slug") or "").strip(),
        (qrow.get("question_en") or "").strip(),
        (qrow.get("correct_answer_en") or "").strip(),
        (qrow.get("hook_vo") or "").strip(),
        (qrow.get("fact_vo") or "").strip(),
        (qrow.get("kicker_vo") or "").strip(),
    ]
    return "q" + hashlib.sha1(json.dumps(payload).encode()).hexdigest()[:11]


_TAIL_FADE_S = 0.30  # Mask the model-generated artifact in the few hundred ms
                     # after Seedance dialogue ends. Tuned on the la-tomatina
                     # row where the glitch ran ~350ms past speech end. If a
                     # future row has speech that runs hard against the clip
                     # tail, the fade will gently taper it — that's
                     # cinematically fine; a hard cut would be worse.


def normalize_bg(src: Path, dst: Path) -> None:
    """Normalize the Seedance clip to 1080x1920 h264 / AAC, with a short
    end-of-audio fade-out so tail-glitches don't escape into the final
    render (see _TAIL_FADE_S)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    duration = ffprobe_duration(src)
    fade_st = max(0.0, duration - _TAIL_FADE_S)
    ffmpeg([
        "-i", str(src),
        "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,"
               "crop=1080:1920,setsar=1",
        "-af", f"afade=t=out:st={fade_st:.3f}:d={_TAIL_FADE_S:.3f}",
        "-r", "30",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-preset", "medium", "-crf", "18",
        # Audio is the native Seedance voice — re-encode to AAC at 44.1k.
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
        str(dst),
    ])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("slug", type=str, help="project slug (matches projects/trivia-reaction/<slug>/)")
    args = ap.parse_args()

    slug = args.slug
    project = project_dir(slug)
    artifacts = project / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    brief_path = artifacts / "brief.json"
    script_path = artifacts / "script.json"

    # brief.json / script.json are optional caches. When either is missing we
    # fall back to TriviaReactionQueue (the workflow-state SoT) the same way
    # openart_generate.py does — see commit 337a6fb. Only fetch the Queue row
    # if we actually need it, so the existing-files path keeps working
    # offline.
    qrow: dict | None = None

    def _ensure_qrow() -> dict:
        nonlocal qrow
        if qrow is None:
            qrow = _find_queue_row(slug)
            if qrow is None:
                sys.exit(
                    f"no brief.json/script.json on disk AND no TriviaReactionQueue row "
                    f"for slug={slug!r}. Run select_row.py --day N --slug {slug} "
                    f"(or add the row to the sheet) and retry."
                )
        return qrow

    if brief_path.exists():
        brief = json.loads(brief_path.read_text())
        sheet_revision = brief.get("metadata", {}).get("sheet_revision")
        brief_source = f"brief.json ({brief_path.relative_to(REPO)})"
    else:
        row = _ensure_qrow()
        sheet_revision = _queue_revision_hash(row)
        brief_source = "TriviaReactionQueue (sheet)"

    if script_path.exists():
        script = json.loads(script_path.read_text())
        beats = script.get("metadata", {}).get("beats", {})
        script_source = f"script.json ({script_path.relative_to(REPO)})"
    else:
        row = _ensure_qrow()
        beats = {
            "hook":   (row.get("hook_vo") or "").strip(),
            "fact":   (row.get("fact_vo") or "").strip(),
            "kicker": (row.get("kicker_vo") or "").strip(),
        }
        script_source = "TriviaReactionQueue (sheet)"

    print(f"  brief source:  {brief_source}")
    print(f"  script source: {script_source}")

    # Locate the Seedance clip. Prefer canonical <slug>.mp4; fall back to v1.
    clip = LIBRARY_DIR / f"{slug}.mp4"
    if not clip.exists():
        v1 = LIBRARY_DIR / f"{slug}_v1.mp4"
        if v1.exists():
            print(f"  · using {v1.name} (canonical {clip.name} not present)")
            clip = v1
        else:
            sys.exit(f"no Seedance clip at {clip}; run openart_generate.py first")

    if not ffprobe_has_audio(clip):
        sys.exit(
            f"⚠ {clip.name} has no audio stream. Trivia-reaction relies on Seedance "
            f"2.0's native synced voice — verify audio_on was true on the OpenArt run."
        )

    # 1. Normalize bg.mp4 (audio preserved)
    bg = project / "assets" / "video" / "bg.mp4"
    print(f"→ normalize bg: {clip.relative_to(REPO)} → {bg.relative_to(REPO)}")
    normalize_bg(clip, bg)
    bg_dur = ffprobe_duration(bg)
    has_audio = ffprobe_has_audio(bg)
    print(f"  bg duration: {bg_dur:.2f}s  audio: {'present' if has_audio else 'MISSING'}")
    if not has_audio:
        sys.exit("audio dropped during normalize — re-encode args broken")

    # 2. meta.json — Remotion TriviaWithBg consumes this at render time.
    # Schema must match remotion-composer/src/RootTrivia.tsx TriviaMetaFile.
    # Required fields come from trivia-short's renderer; the snake_case
    # override fields (duration_s, highlight_color, show_facts_overlay, ...)
    # are trivia-reaction extensions that trivia-short ignores.
    meta = {
        # --- required by the existing renderer ---
        "mode": "Facts",
        "options": [],
        "option_reveal_times_s": [],
        "suppress_captions_window_ms": None,
        "cta_text": None,
        "cta_nominal_start_ms": None,
        # --- trivia-reaction render overrides ---
        "duration_s": bg_dur,
        "highlight_color": "#FF6A2C",
        "show_facts_overlay": False,
        "base_color": "#FFFFFF",
        "font_size": 78,
        "dark_overlay": 0,
        # --- pipeline metadata (renderer ignores these) ---
        "schema_version": "0.1",
        "pipeline": "trivia-reaction",
        "slug": slug,
        "audio_source": "seedance_native",
        "vo_text": beats,
    }
    meta_path = project / "assets" / "meta.json"
    meta_path.write_text(json.dumps(meta, indent=2) + "\n")
    print(f"  meta: {meta_path.relative_to(REPO)}")

    # 3. edit_decisions.json — the canonical artifact this stage produces
    edit_decisions = {
        "schema_version": "0.1",
        "pipeline": "trivia-reaction",
        "render_runtime": "remotion",
        "metadata": {
            "sheet_revision": sheet_revision,
            "slug": slug,
            "bg_path": str(bg.relative_to(REPO)),
            "bg_duration_s": bg_dur,
            "audio_source": "seedance_native",
            "music": None,
            "sfx": [],
        },
    }
    ed_path = artifacts / "edit_decisions.json"
    ed_path.write_text(json.dumps(edit_decisions, indent=2) + "\n")
    print(f"  edit_decisions: {ed_path.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
