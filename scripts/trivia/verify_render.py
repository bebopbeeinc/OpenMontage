#!/usr/bin/env python
"""Verify a rendered trivia video and emit a JSON report.

Checks:
  1. Duration is within the expected range (assembled clip is ~13.4-14.4s).
  2. Four keyframes extract cleanly to projects/<slug>/renders/frames/.
  3. Caption sanity: word count, max silent gap, last-word vs duration.
  4. Audio levels: peak (clipping check) and mean (loudness check).

Usage:
    python scripts/trivia/verify_render.py <slug>

Writes:
    projects/<slug>/artifacts/verify_report.json
    projects/<slug>/renders/frames/f*.jpg

Exits 0 on pass/warn, 1 on hard fail. Warnings do not block — they are
informational findings to surface before publishing.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# Acceptable assembled duration. assemble_modular's TOTAL_DUR depends on the
# 3 segment durations + 2 xfade transitions; keep this range loose enough to
# accept both 3+8+3 and 3+8+4 closer configurations.
DURATION_MIN_S = 12.5
DURATION_MAX_S = 15.5

CAPTION_MIN_WORDS = 15
CAPTION_MAX_GAP_MS = 4000
# Silent-hook configuration leaves ~2.7s of silence at the start while the
# burned caption shows. Anything longer than the full hook window is unusual.
CAPTION_MAX_LEADING_SILENCE_MS = 3500

# Audio thresholds in dBFS. Warn = surface to human; fail = trigger recovery.
AUDIO_PEAK_WARN_DB = -0.5
AUDIO_PEAK_FAIL_DB = 0.0      # actual clipping
AUDIO_MEAN_WARN_MIN_DB = -25.0
AUDIO_MEAN_WARN_MAX_DB = -10.0
AUDIO_MEAN_FAIL_MIN_DB = -30.0  # effectively silent
AUDIO_MEAN_FAIL_MAX_DB = -5.0   # extreme loudness

FRAME_TIMESTAMPS = [
    (1.0,  "f01_hook.jpg"),
    (5.0,  "f02_body_mid.jpg"),
    (9.0,  "f03_body_end.jpg"),
    (12.0, "f04_closer.jpg"),
]


def _check(name: str, status: str, detail: str) -> dict:
    return {"name": name, "status": status, "detail": detail}


def probe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error",
         "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


def check_duration(video: Path, duration_s: float) -> dict:
    if DURATION_MIN_S <= duration_s <= DURATION_MAX_S:
        return _check(
            "duration", "pass",
            f"{duration_s:.2f}s (within {DURATION_MIN_S}-{DURATION_MAX_S}s)",
        )
    return _check(
        "duration", "fail",
        f"{duration_s:.2f}s (expected {DURATION_MIN_S}-{DURATION_MAX_S}s)",
    )


def extract_frames(video: Path, frames_dir: Path) -> list[str]:
    frames_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for t, name in FRAME_TIMESTAMPS:
        out = frames_dir / name
        subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-ss", f"{t:.2f}", "-i", str(video),
             "-frames:v", "1", "-q:v", "3", str(out)],
            check=True,
        )
        written.append(name)
    return written


def check_captions(words_path: Path, duration_s: float) -> dict:
    if not words_path.exists():
        return _check("captions", "fail", f"words.json missing at {words_path}")
    try:
        words = json.loads(words_path.read_text())
    except json.JSONDecodeError as e:
        return _check("captions", "fail", f"words.json malformed: {e}")
    if not isinstance(words, list) or not words:
        return _check("captions", "fail", "words.json is empty")
    n = len(words)
    if n < CAPTION_MIN_WORDS:
        return _check("captions", "fail", f"{n} words (expected ≥{CAPTION_MIN_WORDS})")

    max_gap = 0
    max_gap_idx = 0
    for i in range(1, n):
        gap = words[i]["startMs"] - words[i - 1]["endMs"]
        if gap > max_gap:
            max_gap = gap
            max_gap_idx = i

    first_start = words[0]["startMs"]
    last_end = words[-1]["endMs"]
    dur_ms = duration_s * 1000
    issues: list[str] = []
    if max_gap > CAPTION_MAX_GAP_MS:
        before = words[max_gap_idx - 1]["word"]
        after = words[max_gap_idx]["word"]
        issues.append(f"silence gap {max_gap}ms after {before!r} before {after!r}")
    if last_end > dur_ms + 500:
        issues.append(f"last word ends at {last_end}ms but video is {dur_ms:.0f}ms")
    if first_start > CAPTION_MAX_LEADING_SILENCE_MS:
        issues.append(
            f"first word starts at {first_start}ms "
            f"(>{CAPTION_MAX_LEADING_SILENCE_MS}ms; longer than silent-hook window)"
        )

    summary = f"{n} words, max gap {max_gap}ms, first@{first_start}ms last@{last_end}ms"
    if issues:
        return _check("captions", "warn", f"{summary}; {'; '.join(issues)}")
    return _check("captions", "pass", summary)


def check_audio(video: Path) -> dict:
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats",
         "-i", str(video), "-af", "volumedetect",
         "-f", "null", "-"],
        capture_output=True, text=True,
    )
    out = result.stderr
    peak_m = re.search(r"max_volume:\s*(-?[\d.]+)\s*dB", out)
    mean_m = re.search(r"mean_volume:\s*(-?[\d.]+)\s*dB", out)
    if not (peak_m and mean_m):
        return _check("audio", "warn", "could not parse volumedetect output")
    peak = float(peak_m.group(1))
    mean = float(mean_m.group(1))
    summary = f"peak={peak:.1f}dB mean={mean:.1f}dB"
    if peak > AUDIO_PEAK_FAIL_DB:
        return _check("audio", "fail", f"{summary}; clipping (peak >{AUDIO_PEAK_FAIL_DB}dB)")
    if mean > AUDIO_MEAN_FAIL_MAX_DB:
        return _check("audio", "fail", f"{summary}; too loud (mean >{AUDIO_MEAN_FAIL_MAX_DB}dB)")
    if mean < AUDIO_MEAN_FAIL_MIN_DB:
        return _check("audio", "fail", f"{summary}; too quiet (mean <{AUDIO_MEAN_FAIL_MIN_DB}dB)")
    issues: list[str] = []
    if peak > AUDIO_PEAK_WARN_DB:
        issues.append(f"peak {peak:.1f}dB (close to clipping)")
    if mean > AUDIO_MEAN_WARN_MAX_DB:
        issues.append(f"mean {mean:.1f}dB (loud)")
    if mean < AUDIO_MEAN_WARN_MIN_DB:
        issues.append(f"mean {mean:.1f}dB (quiet)")
    if issues:
        return _check("audio", "warn", f"{summary}; {'; '.join(issues)}")
    return _check("audio", "pass", summary)


def overall_verdict(checks: list[dict]) -> str:
    if any(c["status"] == "fail" for c in checks):
        return "fail"
    if any(c["status"] == "warn" for c in checks):
        return "warn"
    return "pass"


def determine_recovery(checks: list[dict]) -> dict | None:
    """If any failed check has a known auto-recovery, return its strategy.

    Strategies are consumed by the web server's render pipeline to set
    assemble_modular CLI overrides on a retry attempt.
    """
    failed = [c for c in checks if c["status"] == "fail"]
    if not failed:
        return None

    audio = next((c for c in failed if c["name"] == "audio"), None)
    if audio:
        d = audio["detail"]
        if "clipping" in d:
            return {"strategy": "audio_clipping", "reason": d}
        if "too loud" in d:
            return {"strategy": "audio_too_loud", "reason": d}
        if "too quiet" in d:
            return {"strategy": "audio_too_quiet", "reason": d}

    captions = next((c for c in failed if c["name"] == "captions"), None)
    if captions and ("words (expected" in captions["detail"] or "empty" in captions["detail"]):
        return {"strategy": "low_word_count", "reason": captions["detail"]}

    # duration / frames / malformed words.json: no automatic recovery
    return None


def main(slug: str) -> int:
    project = REPO / "projects" / slug
    video = project / "renders" / "final_with_bg.mp4"
    words = project / "artifacts" / "words.json"
    frames_dir = project / "renders" / "frames"
    report_path = project / "artifacts" / "verify_report.json"

    if not video.exists():
        print(f"ERROR: render not found at {video}", file=sys.stderr)
        return 2

    print(f"verifying {video.relative_to(REPO)}")
    checks: list[dict] = []

    duration_s = probe_duration(video)
    dc = check_duration(video, duration_s)
    print(f"  [{dc['status']:4s}] duration: {dc['detail']}")
    checks.append(dc)

    print(f"  extracting {len(FRAME_TIMESTAMPS)} frames -> {frames_dir.relative_to(REPO)}")
    try:
        written = extract_frames(video, frames_dir)
        fc = _check("frames", "pass", f"extracted {len(written)} frames")
    except subprocess.CalledProcessError as e:
        written = []
        fc = _check("frames", "fail", f"ffmpeg failed: {e}")
    print(f"  [{fc['status']:4s}] frames: {fc['detail']}")
    checks.append(fc)

    cc = check_captions(words, duration_s)
    print(f"  [{cc['status']:4s}] captions: {cc['detail']}")
    checks.append(cc)

    ac = check_audio(video)
    print(f"  [{ac['status']:4s}] audio: {ac['detail']}")
    checks.append(ac)

    verdict = overall_verdict(checks)
    recovery = determine_recovery(checks) if verdict == "fail" else None
    report = {
        "slug": slug,
        "video": str(video.relative_to(REPO)),
        "duration_s": duration_s,
        "frames": written,
        "checks": checks,
        "verdict": verdict,
        "recovery": recovery,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2))

    print(f"\nverdict: {verdict}")
    if recovery:
        print(f"recovery: {recovery['strategy']} ({recovery['reason']})")
    elif verdict == "fail":
        print("recovery: none available (manual intervention required)")
    print(f"report:  {report_path.relative_to(REPO)}")

    return 1 if verdict == "fail" else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: verify_render.py <project-slug>")
    sys.exit(main(sys.argv[1]))
