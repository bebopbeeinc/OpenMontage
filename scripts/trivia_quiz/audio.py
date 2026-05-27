"""Audio layer for trivia-quiz: VO + music + SFX → mixed track → muxed into bg.mp4.

Patterns adapted from scripts/trivia/assemble_modular.py but scoped to the
5-segment quiz format (hook_card / q1_reveal / q2_reveal / q3_reveal / score_card).

Entry point: mix_and_attach_audio(...)
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[2]
MUSIC_LIBRARY = REPO / "music_library"
SFX_LIBRARY = REPO / "sfx_library"

# TTS provider resolution:
#   - Default to Piper (local, free, matches trivia-short's default).
#   - Switch to ElevenLabs via tts_provider="elevenlabs" if a different
#     timbre is needed (uses ELEVENLABS_API_KEY + voice_id).
#
# Piper model: defaults to .piper_voices/en_US-ryan-high.onnx, the same
# model trivia-short uses (see scripts/trivia/docs/setup.md). So the quiz
# show inherits the trivia voice without separate setup.
PIPER_VOICES_DIR = REPO / ".piper_voices"
DEFAULT_PIPER_MODEL = PIPER_VOICES_DIR / "en_US-ryan-high.onnx"

# ElevenLabs voice resolution (only used when tts_provider="elevenlabs"):
#   1. ELEVENLABS_VOICE_TRIVIA_QUIZ env var (per-pipeline override)
#   2. ELEVENLABS_VOICE_ID env var (global default)
#   3. Hardcoded fallback (Rachel — trivia-short's elevenlabs default)
DEFAULT_VOICE_ID_FALLBACK = "21m00Tcm4TlvDq8ikWAM"  # Rachel


def _ffmpeg(args: List[str], quiet: bool = True) -> None:
    """Run ffmpeg with sane defaults. Captures output unless quiet=False."""
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args]
    subprocess.run(cmd, check=True, capture_output=quiet)


def _ffprobe_duration(path: Path) -> float:
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1", str(path),
    ]).decode().strip()
    return float(out)


# ---------------------------------------------------------------------------
# ElevenLabs VO generation
# ---------------------------------------------------------------------------

def _resolve_voice_id() -> str:
    return (
        os.environ.get("ELEVENLABS_VOICE_TRIVIA_QUIZ")
        or os.environ.get("ELEVENLABS_VOICE_ID")
        or DEFAULT_VOICE_ID_FALLBACK
    )


def generate_vo_piper(text: str, out_path: Path, model_path: Optional[Path] = None) -> None:
    """Local TTS via Piper. Same voice as trivia-short by default
    (en_US-ryan-high). Output is WAV (Piper's native format) regardless of
    out_path extension — the caller can re-encode if needed."""
    model = model_path or DEFAULT_PIPER_MODEL
    if not model.exists():
        raise RuntimeError(
            f"Piper model missing: {model}. Install with "
            f"`python scripts/piper_voices/fetch.py {model.stem}` "
            f"or pass --voice-id with --tts-provider=elevenlabs to use ElevenLabs."
        )
    wav = out_path.with_suffix(".wav")
    subprocess.run(
        [sys.executable, "-m", "piper", "--model", str(model),
         "--output_file", str(wav)],
        input=text.encode(), check=True, capture_output=True,
    )
    if out_path.suffix.lower() != ".wav":
        # Convert to requested extension if different
        _ffmpeg(["-i", str(wav), str(out_path)])
        wav.unlink()


def generate_vo_elevenlabs(text: str, out_path: Path, voice_id: str) -> None:
    """Call ElevenLabs TTS. Mirrors the voice-settings tuning used by
    trivia-short (lower stability + style boost = more expressive delivery)."""
    import requests
    api_key = os.environ.get("ELEVENLABS_API_KEY") or ""
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY not set — required for --with-vo")
    r = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
        headers={"xi-api-key": api_key, "Content-Type": "application/json"},
        json={
            "text": text,
            "model_id": "eleven_multilingual_v2",
            "voice_settings": {
                "stability": 0.3,           # lower → more variation, more emotion
                "similarity_boost": 0.75,
                "style": 0.55,              # 0..1 — exaggerates voice character
                "use_speaker_boost": True,
            },
        },
        timeout=60,
    )
    r.raise_for_status()
    out_path.write_bytes(r.content)


# ---------------------------------------------------------------------------
# Window fitting — pad/atempo each VO line to land in its budget window
# ---------------------------------------------------------------------------

def fit_to_window(src: Path, dst: Path, start: float, end: float, label: str = "") -> dict | None:
    """Pad/trim audio to fit a [start, end] window in the final timeline.
    Speeds up the source up to 1.2x if it's too long; tail-clips beyond that
    and returns a warning record."""
    src_dur = _ffprobe_duration(src)
    window_dur = end - start
    tempo = 1.0
    warning = None
    if src_dur > window_dur:
        ratio = src_dur / window_dur
        tempo = min(1.2, ratio)
        if ratio > 1.2:
            print(
                f"    ! VO {label or '?'} too long: {src_dur:.2f}s into {window_dur:.2f}s "
                f"(needs {ratio:.2f}x; capped at 1.2x — tail clipped). Shorten the line.",
                file=sys.stderr,
            )
            warning = {"label": label, "speech_s": round(src_dur, 3),
                       "window_s": round(window_dur, 3), "needed_tempo": round(ratio, 3)}
    af = f"atempo={tempo},adelay={int(start*1000)}|{int(start*1000)}"
    _ffmpeg([
        "-i", str(src),
        "-af", af,
        "-ac", "2", "-ar", "48000",
        str(dst),
    ])
    return warning


# ---------------------------------------------------------------------------
# VO orchestration — read script.json, generate + pad each line
# ---------------------------------------------------------------------------

def generate_vo_lines(
    script: Dict[str, Any],
    audio_dir: Path,
    tts_provider: str = "piper",
    voice_id: Optional[str] = None,
    piper_model: Optional[Path] = None,
) -> List[Path]:
    """Generate VO for each line in script.metadata.vo, fit each to its
    [start, end] window, return the list of padded audio paths.

    Defaults to Piper (local, free, matches trivia-short). Pass
    tts_provider="elevenlabs" for ElevenLabs.
    """
    audio_dir.mkdir(parents=True, exist_ok=True)

    if tts_provider == "piper":
        model = piper_model or DEFAULT_PIPER_MODEL
        print(f"  → VO via Piper (model={model.stem})")
    elif tts_provider == "elevenlabs":
        vid = voice_id or _resolve_voice_id()
        print(f"  → VO via ElevenLabs (voice_id={vid[:8]}…)")
    else:
        raise ValueError(f"Unknown tts_provider: {tts_provider!r}")

    padded_paths: List[Path] = []
    for line in script["metadata"]["vo"]:
        line_id = line["id"]
        text = line["text"]
        start, end = line["window_s"]

        raw = audio_dir / f"vo_{line_id}_raw.wav"
        padded = audio_dir / f"vo_{line_id}.wav"
        print(f"    · {line_id}: {text[:70]}{'…' if len(text) > 70 else ''}")

        if tts_provider == "piper":
            generate_vo_piper(text, raw, model_path=piper_model)
        else:
            generate_vo_elevenlabs(text, raw, vid)

        fit_to_window(raw, padded, start, end, label=line_id)
        padded_paths.append(padded)
    return padded_paths


# ---------------------------------------------------------------------------
# SFX cue planning — derive timed SFX from the quiz_meta
# ---------------------------------------------------------------------------

# Volume curve (dB) — keep SFX clearly under VO, mostly there for energy
# rather than as a dominant audio element.
SFX_VOLUMES = {
    "tick_quiz":   -12.0,    # 3 spaced ticks with rising pitch — cleaner than tick_loop
    "slam_check":  -8.0,
    "whoosh":      -10.0,
    "ding":        -10.0,
    "impact":      -8.0,
}


def build_sfx_cues(quiz_meta: Dict[str, Any]) -> List[Tuple[Path, float, float]]:
    """Return a list of (sfx_path, start_s, volume_db) cues for the quiz timeline.

    Per-segment SFX choices:
      - Hook (0s): whoosh — show identity entrance
      - Each question countdown (countdown_start_s into segment):
          tick_quiz.wav — 3-tick countdown with slight pitch rise on the last
      - Each question reveal (reveal_at_s): slam_check.wav for the ✓ stamp
      - Score card (start): ding for the game-show wrap-up
    """
    cues: List[Tuple[Path, float, float]] = []

    # Hook entrance
    cues.append((SFX_LIBRARY / "whoosh.wav", 0.1, SFX_VOLUMES["whoosh"]))

    for q in quiz_meta["questions"]:
        seg_start = q["start_s"]
        countdown_start = seg_start + q["countdown_start_s"]
        reveal_at = seg_start + q["reveal_at_s"]

        # Cleaner countdown: tick_quiz.wav (3 evenly-spaced ticks, slight
        # pitch rise on the last; matches the factualquiz reference).
        cues.append((SFX_LIBRARY / "tick_quiz.wav", countdown_start, SFX_VOLUMES["tick_quiz"]))

        # Reveal stamp
        cues.append((SFX_LIBRARY / "slam_check.wav", reveal_at, SFX_VOLUMES["slam_check"]))

    # Score card wrap-up
    score_start = quiz_meta["score_card"]["start_s"]
    cues.append((SFX_LIBRARY / "ding.wav", score_start + 0.2, SFX_VOLUMES["ding"]))

    return cues


# ---------------------------------------------------------------------------
# Music bed — pick a track, trim/loop, fade
# ---------------------------------------------------------------------------

DEFAULT_MUSIC_TRACK = "hitslab-energetic-upbeat-energetic-background-music-357963.mp3"


def build_music_bed(
    track_filename: Optional[str],
    duration_s: float,
    out: Path,
    volume_db: float = -20.0,
) -> Path:
    """Resolve a music file from music_library/, trim/loop to duration with fades."""
    out.parent.mkdir(parents=True, exist_ok=True)
    name = (track_filename or "").strip() or DEFAULT_MUSIC_TRACK
    src = MUSIC_LIBRARY / name
    if not src.exists():
        raise FileNotFoundError(
            f"Music track not found: {src.relative_to(REPO)}. "
            f"Drop one into {MUSIC_LIBRARY.relative_to(REPO)}/ or set "
            f"music_track in the fixture."
        )

    src_dur = _ffprobe_duration(src)
    lin_vol = 10 ** (volume_db / 20)
    # If too short, loop; if long enough, just trim. Fade in/out for clean edges.
    if src_dur < duration_s:
        # Loop the source until we cover the duration, then trim + fade
        _ffmpeg([
            "-stream_loop", "-1", "-i", str(src),
            "-t", f"{duration_s:.3f}",
            "-af", f"volume={lin_vol:.4f},afade=t=in:st=0:d=0.6,afade=t=out:st={duration_s-0.8:.3f}:d=0.8",
            "-ac", "2", "-ar", "48000",
            str(out),
        ])
    else:
        _ffmpeg([
            "-i", str(src),
            "-t", f"{duration_s:.3f}",
            "-af", f"volume={lin_vol:.4f},afade=t=in:st=0:d=0.6,afade=t=out:st={duration_s-0.8:.3f}:d=0.8",
            "-ac", "2", "-ar", "48000",
            str(out),
        ])
    return out


# ---------------------------------------------------------------------------
# Final mix + mux into bg.mp4
# ---------------------------------------------------------------------------

def mix_final_audio(
    vo_padded_paths: List[Path],
    music_bed: Optional[Path],
    sfx_cues: List[Tuple[Path, float, float]],
    dst: Path,
    total_duration: float,
) -> None:
    """Mix VO lines (each already padded with adelay to its window start) +
    optional music bed + SFX cues into a single audio file."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    inputs: List[str] = []
    chains: List[str] = []
    labels: List[str] = []

    # VO inputs — each is already padded to its start position. Just label them.
    for i, vo_path in enumerate(vo_padded_paths):
        inputs += ["-i", str(vo_path)]
        labels.append(f"{i}:a")

    # Music bed input (full-length, already volume-attenuated + faded)
    if music_bed is not None:
        idx = len(vo_padded_paths)
        inputs += ["-i", str(music_bed)]
        labels.append(f"{idx}:a")

    # SFX inputs — each gets a chain that applies volume + adelay to its cue time
    base_idx = len(vo_padded_paths) + (1 if music_bed is not None else 0)
    for i, (path, start, vol_db) in enumerate(sfx_cues):
        if not path.exists():
            print(f"    ⚠ SFX missing: {path.relative_to(REPO)} — skipping", file=sys.stderr)
            continue
        inputs += ["-i", str(path)]
        idx = base_idx + i
        lin_vol = 10 ** (vol_db / 20)
        delay_ms = int(start * 1000)
        chains.append(
            f"[{idx}:a]volume={lin_vol:.4f},adelay={delay_ms}|{delay_ms}[s{i}]"
        )
        labels.append(f"s{i}")

    amix_inputs = "".join(f"[{l}]" for l in labels)
    chain_str = ";".join(chains) + ";" if chains else ""
    filter_complex = (
        f"{chain_str}"
        f"{amix_inputs}amix=inputs={len(labels)}:normalize=0,"
        f"alimiter=limit=0.97:attack=5:release=50,"
        f"apad=whole_dur={total_duration}[out]"
    )
    _ffmpeg([
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-ac", "2", "-ar", "48000",
        str(dst),
    ])


def attach_audio_to_video(video_in: Path, audio_in: Path, video_out: Path) -> None:
    """Mux an external audio track onto a silent video. Re-encodes audio to
    AAC; copies video stream as-is. Output duration = shortest stream."""
    _ffmpeg([
        "-i", str(video_in),
        "-i", str(audio_in),
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-map", "0:v:0", "-map", "1:a:0",
        "-shortest",
        str(video_out),
    ])


# ---------------------------------------------------------------------------
# Top-level entry point — orchestrate VO + music + SFX, mux into bg.mp4
# ---------------------------------------------------------------------------

def mix_and_attach_audio(
    *,
    bg_mp4: Path,
    script: Dict[str, Any],
    quiz_meta: Dict[str, Any],
    music_track: Optional[str],
    total_duration: float,
    audio_dir: Path,
    with_vo: bool,
    with_music: bool,
    with_sfx: bool,
    tts_provider: str = "piper",
    voice_id: Optional[str] = None,
    piper_model: Optional[Path] = None,
) -> Path:
    """Mix the requested audio layers + mux into bg.mp4. Returns the path
    of the bg.mp4 with audio (overwrites the silent input)."""
    audio_dir.mkdir(parents=True, exist_ok=True)

    vo_padded: List[Path] = []
    if with_vo:
        vo_padded = generate_vo_lines(
            script, audio_dir,
            tts_provider=tts_provider, voice_id=voice_id, piper_model=piper_model,
        )

    music_bed: Optional[Path] = None
    if with_music:
        music_bed = build_music_bed(
            music_track, total_duration, audio_dir / "music_bed.wav"
        )
        print(f"  → music bed: {music_bed.name}")

    sfx_cues: List[Tuple[Path, float, float]] = []
    if with_sfx:
        sfx_cues = build_sfx_cues(quiz_meta)
        print(f"  → SFX cues: {len(sfx_cues)} ({', '.join(p.name for p, _, _ in sfx_cues[:5])}{'…' if len(sfx_cues) > 5 else ''})")

    if not (vo_padded or music_bed or sfx_cues):
        print("  ⚠ no audio layers enabled — bg.mp4 stays silent")
        return bg_mp4

    final_audio = audio_dir / "final_mix.wav"
    mix_final_audio(vo_padded, music_bed, sfx_cues, final_audio, total_duration)
    print(f"  ✓ mixed audio: {final_audio.relative_to(REPO)}")

    # Mux into a sibling .mp4 then atomically replace the silent bg.mp4
    bg_with_audio = bg_mp4.parent / "bg_with_audio.mp4"
    attach_audio_to_video(bg_mp4, final_audio, bg_with_audio)
    bg_mp4.unlink()
    bg_with_audio.rename(bg_mp4)
    print(f"  ✓ muxed into {bg_mp4.relative_to(REPO)}")
    return bg_mp4
