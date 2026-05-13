#!/usr/bin/env python
"""Assemble a 3-segment modular trivia video from Post Calendar row data.

Pulls Hook Clip (S), Body Clip (T), Closer Clip (U) URLs from the Posts sheet,
downloads from Drive, normalizes to 1080x1920, concatenates into a single
silent bg.mp4. Optionally generates VO via ElevenLabs and overlays it.

The output bg.mp4 is written to projects/<slug>/assets/video/bg.mp4, which is
the expected input for the existing trivia workflow:

    python scripts/common/transcribe.py <slug>    # produces words.json
    (render via Remotion TriviaWithBg composition)
    python scripts/trivia/publish.py <slug> <row>

Usage:
    python scripts/trivia/assemble_modular.py <row> <slug> [--with-vo]

Example:
    python scripts/trivia/assemble_modular.py 5 australia-wider-than-moon
    python scripts/trivia/assemble_modular.py 5 australia-wider-than-moon --with-vo
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from post_row import (  # noqa: E402
    CLIPS_SHEET, HOOK_SHEET, POST_SHEET, build_sheets, cell_for,
    read_post_row,
)

REPO = Path(__file__).resolve().parents[2]

# Local-clip library: filename-only resolution per segment (post-2026-05 cleanup
# removed the Drive-URL fallback path). Pipeline-local — assets are gitignored
# under scripts/trivia/library/ alongside the code that consumes them.
LIBRARY_BASE = Path(__file__).resolve().parent / "library"
SEGMENT_LIBRARY = {
    "reaction": LIBRARY_BASE / "reactions",
    "body":     LIBRARY_BASE / "bodies",
    "closer":   LIBRARY_BASE / "closers",
}

# Per-segment source resolution: which row column holds the local filename
# and which library subdir to look in. After the 2026-05 sheet cleanup the
# Drive-URL fallback columns (S/W/AB) were removed, so every segment now
# resolves filename-only.
# The "hook" segment of the assembled video is sourced from the reaction clip.
SEGMENT_SOURCES = [
    # (segment_name_in_pipeline, filename_field, library_subdir)
    ("hook",   "reaction_filename", "reaction"),
    ("body",   "body_filename",     "body"),
    ("closer", "closer_filename",   "closer"),
]

# Segment timing (seconds) — must match the prompts used to generate the clips
HOOK_DUR = 3.0
BODY_DUR = 8.0
CLOSER_DUR = 3.0

# Transitions: xfade overlaps clips by this duration at each seam.
# Total duration shrinks by (num_seams * TRANSITION_DUR) = 0.6s.
TRANSITION_DUR = 0.3
TOTAL_DUR = HOOK_DUR + BODY_DUR + CLOSER_DUR - 2 * TRANSITION_DUR  # 14.4

# Segment start times in the final (post-transition) timeline
SEG_HOOK_START   = 0.0
SEG_BODY_START   = HOOK_DUR - TRANSITION_DUR                  # 2.7
SEG_CLOSER_START = HOOK_DUR + BODY_DUR - 2 * TRANSITION_DUR   # 10.4

# VO timing offsets — absolute in the 14.4s timeline.
# Final VO (cta) ends ~1s before TOTAL_DUR so the closer can breathe at the end.
# A 0.5s gap between resolution and cta keeps them as two distinct sentences.
VO_WINDOWS = {
    "hook":       (0.3, 2.7),    # inside 0-3s hook (transition covers 2.7-3.0)
    "claim":      (3.0, 10.3),   # inside 2.7-10.7 body
    "resolution": (10.6, 11.7),  # short reveal line
    "cta":        (11.9, 13.3),  # 0.2s gap before this; ends 0.1s before video end
}

# SFX cues aligned to post-transition timeline
SFX_CUES_FACTS = [
    ("whoosh.wav",          2.70, -6),   # hook -> body transition (spans 2.7-3.0)
    ("impact.wav",          2.80, -6),   # claim lands during crossfade
    ("slam_check.wav",      3.70, -14),  # check button slams in
    ("slam_x.wav",          3.85, -14),  # X button slams in
    ("tick_loop.wav",       5.20, -16),  # fuse burning (constant ticks)
    ("suspense_riser.wav",  7.70, -8),   # bomb-fuse buildup: accel ticks + sub rumble
    ("explosion.wav",      10.00, -3),   # the bomb goes off (peak of progress bar)
    ("whoosh.wav",         10.40, -10),  # transition (ducked under explosion tail)
    ("pop.wav",            10.50, -8),   # Captain bounces in
    ("ding.wav",           13.40, -12),  # thumbs-up beat
]

SFX_CUES_CHOICES = [
    ("whoosh.wav",          2.70, -6),
    ("impact.wav",          2.80, -6),
    ("slam_check.wav",      3.70, -14),
    ("slam_check.wav",      3.85, -14),
    ("slam_check.wav",      4.00, -14),
    ("slam_check.wav",      4.15, -14),
    ("tick_loop.wav",       5.70, -18),
    ("suspense_riser.wav",  7.70, -10),
    ("whoosh.wav",         10.40, -6),
    ("pop.wav",            10.50, -8),
    ("ding.wav",           13.40, -12),
]


def resolve_reaction_filename(sheets, row_num: int, row: dict) -> None:
    """Emergency in-memory auto-pick when reaction_filename (the VLOOKUP in
    the Reaction Filename column) returns empty. The canonical reaction
    pick is `pick_reactions_llm.py` run at idea stage — this function
    exists as a no-network fallback that randomly picks a Generated clip
    with the matching archetype, mutates `row` in place, and DOES NOT
    write back to the sheet (writing would clobber the VLOOKUP formula).
    """
    if (row.get("reaction_filename") or "").strip():
        return  # already resolved (picker ran, VLOOKUP returns a filename)
    archetype = (row.get("reaction_archetype") or "").strip()
    if not archetype:
        return

    clips = sheets.spreadsheets().values().get(
        spreadsheetId=CLIPS_SHEET, range="Clips!A2:M200",
    ).execute().get("values", [])
    matches: list[tuple[str, str]] = []
    for c in clips:
        c = list(c) + [""] * (13 - len(c))
        clip_id, status, arch, _, _, _, _, _, _, _, _, _, fname = c[:13]
        if status.strip().lower() == "generated" and arch.strip() == archetype:
            if fname.strip():
                matches.append((clip_id.strip(), fname.strip()))
    if not matches:
        print(f"  [reaction] no Generated clip for archetype {archetype!r} — Q is empty and there's no fallback")
        return

    clip_id, fname = random.choice(matches)
    row["reaction_filename"] = fname
    print(f"  [reaction] in-memory fallback pick: {clip_id} from {len(matches)} match(es) -> {fname}")
    print(f"             run `pick_reactions_llm.py --row {row_num} --apply` to make this stable in the sheet")


def lookup_hook_emphasis(sheets, hook_text: str) -> str:
    """Look up the Emphasis Word from the Hook Library for the given hook text.
    Returns '' if not found.
    """
    if not hook_text:
        return ""
    r = sheets.spreadsheets().values().get(
        spreadsheetId=HOOK_SHEET, range="Hooks!C4:F69"
    ).execute().get("values", [])
    target = hook_text.strip().lower()
    for row in r:
        if not row:
            continue
        if row[0].strip().lower() == target and len(row) >= 4:
            return row[3].strip()
    return ""


def resolve_segment_source(segment: str, fname_field: str,
                           library_key: str, row: dict,
                           cache_dir: Path) -> Path:
    """Materialize a segment's source clip at cache_dir/{segment}.raw.mp4 by
    copying from SEGMENT_LIBRARY[library_key]/{row[fname_field]}.

    Raises FileNotFoundError if the filename is set but the local file is
    missing, or ValueError if the filename is empty.
    """
    raw = cache_dir / f"{segment}.raw.mp4"
    fname = (row.get(fname_field) or "").strip()
    if not fname:
        raise ValueError(f"{segment}: {fname_field} is empty in the sheet row")
    local = SEGMENT_LIBRARY[library_key] / fname
    if not local.exists():
        raise FileNotFoundError(
            f"{segment}: {fname_field}={fname!r} but file not found at {local}"
        )
    rel = local.relative_to(LIBRARY_BASE) if local.is_relative_to(LIBRARY_BASE) else local
    print(f"  using local {segment}: {rel}")
    shutil.copy(local, raw)
    return raw


def ffmpeg(args: list[str]) -> None:
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args]
    subprocess.run(cmd, check=True)


def normalize_clip(src: Path, dst: Path, duration: float) -> None:
    """Scale to 1080x1920, pad if needed, trim to exact duration, silent track, 24fps."""
    vf = (
        "scale=1080:1920:force_original_aspect_ratio=decrease,"
        "pad=1080:1920:(ow-iw)/2:(oh-ih)/2,"
        "fps=24,setsar=1"
    )
    ffmpeg([
        "-i", str(src),
        "-t", f"{duration:.3f}",
        "-an",
        "-vf", vf,
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-preset", "medium", "-crf", "18",
        str(dst),
    ])


def concat_clips_with_transitions(parts: list[Path], dst: Path,
                                   transition_dur: float) -> None:
    """Concat 3 clips with xfade transitions.

    Uses slideleft for hook->body and slideup for body->closer.
    Each transition overlaps the clips by `transition_dur` seconds.
    """
    assert len(parts) == 3, "concat_clips_with_transitions expects exactly 3 clips"
    # Offsets in the merged timeline where each xfade begins
    off01 = HOOK_DUR - transition_dur
    # After first xfade, clip 0+1 combined duration = HOOK_DUR + BODY_DUR - transition_dur
    off12 = HOOK_DUR + BODY_DUR - 2 * transition_dur
    fc = (
        f"[0:v][1:v]xfade=transition=slideleft:"
        f"duration={transition_dur}:offset={off01:.3f}[v01];"
        f"[v01][2:v]xfade=transition=slideup:"
        f"duration={transition_dur}:offset={off12:.3f}[v]"
    )
    ffmpeg([
        "-i", str(parts[0]), "-i", str(parts[1]), "-i", str(parts[2]),
        "-filter_complex", fc,
        "-map", "[v]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-preset", "medium", "-crf", "18",
        str(dst),
    ])


def generate_vo_elevenlabs(text: str, out_path: Path, voice_id: str) -> None:
    """Generate VO via ElevenLabs. Voice settings tuned for trivia tone:
    lower stability + style boost = more emotional/expressive delivery,
    speaker boost on for clearer projection.
    """
    import requests
    api_key = os.environ.get("ELEVENLABS_API_KEY") or ""
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY not set")
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


def generate_vo_piper(text: str, out_path: Path, voice_model: Path) -> None:
    """Generate VO locally with Piper. Writes WAV."""
    wav = out_path.with_suffix(".wav")
    subprocess.run(
        [sys.executable, "-m", "piper", "--model", str(voice_model),
         "--output_file", str(wav)],
        input=text.encode(), check=True,
    )
    if out_path.suffix.lower() != ".wav":
        ffmpeg(["-i", str(wav), str(out_path)])
        wav.unlink()


def generate_vo(text: str, out_path: Path, provider: str, voice_id: str,
                piper_model: Path) -> None:
    if provider == "elevenlabs":
        generate_vo_elevenlabs(text, out_path, voice_id)
    elif provider == "piper":
        generate_vo_piper(text, out_path, piper_model)
    else:
        raise ValueError(f"Unknown TTS provider: {provider}")


# Module-level list of structured warnings from the run. Cleared at top of main().
_assembly_warnings: list[dict] = []


def fit_to_window(src: Path, dst: Path, start: float, end: float, label: str = "") -> None:
    """Pad/trim audio to fit exactly in the window. Silence before start; atempo if too long."""
    # Get source duration
    dur_raw = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(src)]
    ).decode().strip()
    src_dur = float(dur_raw)
    window_dur = end - start
    # If VO is longer than window, speed up slightly. Cap at 1.2x — past that,
    # speech sounds rushed. Long lines that can't fit will be tail-clipped;
    # the warning below makes that visible so the line can be shortened.
    tempo = 1.0
    if src_dur > window_dur:
        ratio = src_dur / window_dur
        tempo = min(1.2, ratio)
        if ratio > 1.2:
            print(f"    ! VO too long for window: {src_dur:.2f}s into {window_dur:.2f}s "
                  f"(would need {ratio:.2f}x; capped at 1.2x — tail will be clipped). "
                  f"Consider shortening this line in Posts col I or J.", file=sys.stderr)
            _assembly_warnings.append({
                "kind": "vo_line",
                "label": label or "?",
                "speech_s": round(src_dur, 3),
                "window_s": round(window_dur, 3),
                "needed_tempo": round(ratio, 3),
            })
    # Build filter chain: delay by start, apply tempo, final duration = start + (src_dur / tempo)
    af = f"atempo={tempo},adelay={int(start*1000)}|{int(start*1000)}"
    ffmpeg([
        "-i", str(src),
        "-af", af,
        "-ac", "2", "-ar", "48000",
        str(dst),
    ])


def mix_vo_tracks(tracks: list[Path], dst: Path, total_duration: float) -> None:
    """Mix multiple padded VO tracks into a single 15s audio file."""
    inputs = []
    for t in tracks:
        inputs += ["-i", str(t)]
    filter_complex = (
        f"[{']['.join(str(i) + ':a' for i in range(len(tracks)))}]"
        f"amix=inputs={len(tracks)}:normalize=0,"
        f"apad=whole_dur={total_duration}[out]"
    )
    ffmpeg([*inputs, "-filter_complex", filter_complex,
            "-map", "[out]", "-ac", "2", "-ar", "48000",
            str(dst)])


def mix_audio_layers(vo_track: Path, sfx_cues: list[tuple[Path, float, float]],
                     dst: Path, total_duration: float) -> None:
    """Mix a VO track with a list of SFX cues (path, start_sec, vol_db) into dst.

    Each SFX gets its own input, volume-adjusted, delayed to its cue time, then
    all streams are amixed together.
    """
    inputs = ["-i", str(vo_track)]
    chains = []
    labels = ["0:a"]
    for i, (path, start, vol_db) in enumerate(sfx_cues):
        inputs += ["-i", str(path)]
        idx = i + 1
        lin_vol = 10 ** (vol_db / 20)
        delay_ms = int(start * 1000)
        chains.append(
            f"[{idx}:a]volume={lin_vol:.4f},adelay={delay_ms}|{delay_ms}[s{i}]"
        )
        labels.append(f"s{i}")
    # Final amix: VO + all SFX
    amix_inputs = "".join(f"[{l}]" for l in labels)
    chain_str = ";".join(chains) + ";" if chains else ""
    filter_complex = (
        f"{chain_str}"
        f"{amix_inputs}amix=inputs={len(labels)}:normalize=0,"
        f"alimiter=limit=0.97:attack=5:release=50,"  # prevent clipping
        f"apad=whole_dur={total_duration}[out]"
    )
    ffmpeg([*inputs, "-filter_complex", filter_complex,
            "-map", "[out]", "-ac", "2", "-ar", "48000",
            str(dst)])


MUSIC_QUERIES = {
    "Facts":   "upbeat playful curious trivia background",
    "Choices": "game show suspense curiosity quirky",
}


def resolve_music_bed(mode: str, duration: float, cache_dir: Path,
                      track_override: str = "") -> Path:
    """Resolve the music bed file. Precedence:
       1. Posts col AI track_override (filename in music_library/)
       2. music_library/bed_<mode>.mp3 cached default
       3. Fetch from Pixabay and cache as bed_<mode>.mp3
    Trim to `duration` with fades. Returns the trimmed file path.
    """
    music_dir = REPO / "music_library"
    music_dir.mkdir(parents=True, exist_ok=True)
    mode_slug = mode.lower().replace(" ", "_").replace("/", "_")

    if track_override.strip():
        cached = music_dir / track_override.strip()
        if not cached.exists():
            raise FileNotFoundError(
                f"Music track override not found: {cached.relative_to(REPO)}"
            )
        print(f"    - using override: {cached.name}")
    else:
        cached = music_dir / f"bed_{mode_slug}.mp3"
        if not cached.exists():
            sys.path.insert(0, str(REPO))
            from tools.audio.pixabay_music import PixabayMusic
            query = MUSIC_QUERIES.get(mode, MUSIC_QUERIES["Facts"])
            print(f"    - searching Pixabay: {query!r}")
            tool = PixabayMusic()
            result = tool.execute({
                "query": query,
                "min_duration": 20,
                "max_duration": 180,
                "output_path": str(cached),
            })
            if not result.success:
                raise RuntimeError(f"Pixabay fetch failed: {result.error}")
            print(f"    - downloaded: {result.data.get('track_title')!r} by "
                  f"{result.data.get('artist')!r} ({result.data.get('duration_seconds')}s)")
        else:
            print(f"    - using default: {cached.name}")

    # Trim to duration with fades
    trimmed = cache_dir / "music_bed.wav"
    ffmpeg([
        "-i", str(cached),
        "-t", f"{duration:.3f}",
        "-af", f"afade=t=in:st=0:d=0.5,afade=t=out:st={duration-0.8:.3f}:d=0.8",
        "-ac", "2", "-ar", "48000",
        str(trimmed),
    ])
    return trimmed


# Words that work well as emphasis — disbelief / surprise / status / scale.
# Walked-back: if any appear, the LAST one wins (punchline position).
EMPHASIS_PUNCH = {
    # Disbelief / surprise / quality
    "fake","real","made","wrong","right","true","false","weird","weirder","weirdest",
    "strange","stranger","strangest","bizarre","ridiculous","absurd","impossible",
    "unbelievable","insane","crazy","wild",
    # Difficulty / status / specialness
    "easy","hard","tough","simple","basic","obvious","secret","hidden","tricky",
    "peak","undefeated","cheating","romantic","adorable","free","actual","real",
    # Comparatives
    "wider","bigger","smaller","taller","shorter","heavier","lighter","faster",
    "slower","older","newer","deeper","higher","lower","longer","stronger","weaker",
    "richer","poorer","cuter","scarier","funnier","harder","easier",
    # Quantity
    "most","every","none","tons","mass","countless","millions","billions","zero",
    # Stakes verbs
    "miss","missed","exposes","destroys","ruins","breaks","beats","wins","loses",
    "fool","fools","prove","kills",
    # Boundaries
    "alone","forever","instantly","never","always","only",
    # Surprise topics that often anchor a hook
    "captain",
}

# Common low-content words to skip in the fallback pass.
EMPHASIS_STOPWORDS = {
    "a","an","the","is","are","was","were","am","be","been","being","do","does","did",
    "doing","done","can","could","should","would","will","may","might","must","shall",
    "of","in","on","at","by","to","for","with","from","about","into","over","under",
    "than","then","through","as","if","because","since","while","when","whenever",
    "i","my","me","mine","we","our","us","they","them","their","you","your","yours",
    "he","him","his","she","her","hers","it","its",
    "but","so","or","and","yet","nor",
    "who","what","where","why","how","which","whose","whom",
    "this","that","these","those","one","ones","some","any","each","all","no","not",
    "here","there","now","yes","very","too","quite","rather","really","actually",
    "seriously","honestly","literally","still","also","again","ever","sometimes",
    "get","got","gets","go","went","goes","come","came","comes","take","took","takes",
    "make","makes","give","gave","gives","have","has","had","having","say","said",
    "says","ask","asks","asked","look","looks","feel","feels","seem","seems",
    "sound","sounds","think","thinks","thought","know","knows","knew","like","liked",
    "want","wants","need","needs","much","many","more","less","few","several",
    "first","last","next","up","down","out","off",
    "time","times","day","days","year","years","minute","minutes","second","seconds",
    "way","ways","thing","things","stuff","kind","sort","type","place","places",
    "people","person","someone","everyone","anyone","nobody","everybody",
    "honest","just","even","such",
}


def pick_emphasis_index(words: list[str]) -> int:
    """Return the index of the word in `words` that should be emphasized.

    Strategy:
      1. If any word matches the PUNCH set, return the LAST such occurrence
         (hooks build to the punchline).
      2. Otherwise, walk back from the end and return the first word that is
         (a) not a stopword and (b) at least 4 characters long.
      3. Fallback: the last word.
    """
    def normalize(w: str) -> str:
        return re.sub(r"[^A-Za-z]", "", w).lower()

    # Pass 1: punch words
    for i in range(len(words) - 1, -1, -1):
        if normalize(words[i]) in EMPHASIS_PUNCH:
            return i
    # Pass 2: last non-stopword of length >= 4
    for i in range(len(words) - 1, -1, -1):
        plain = normalize(words[i])
        if plain in EMPHASIS_STOPWORDS:
            continue
        if len(plain) >= 4:
            return i
    return len(words) - 1 if words else 0


def render_hook_caption_png(text: str, png_path: Path,
                             width: int = 1080, height: int = 1920,
                             emphasis_override: str = "") -> str:
    """Render the hook caption to a transparent PNG via PIL.

    Up to 3-line layout: prefix words (smaller, white) above; emphasis word
    (larger, highlight green) middle; suffix words (smaller, white) below.
    Each has a semi-transparent rounded background. Anchored so the emphasis
    line is at ~1/4 of screen height.

    If emphasis_override is provided and matches a word in the hook text
    (case-insensitive), that word is used as the emphasis; otherwise the
    auto-picker chooses.
    """
    from PIL import Image, ImageDraw, ImageFont

    cleaned = text.strip().rstrip(".,!?\"'")
    words = cleaned.split()
    if not words:
        return ""
    idx = None
    if emphasis_override:
        target = re.sub(r"[^A-Za-z]", "", emphasis_override).lower()
        for i, w in enumerate(words):
            if re.sub(r"[^A-Za-z]", "", w).lower() == target:
                idx = i
                break
    if idx is None:
        idx = pick_emphasis_index(words)
    prefix = " ".join(words[:idx])
    suffix = " ".join(words[idx + 1:])
    emphasis = re.sub(r"[^A-Za-z]", "", words[idx]).upper()
    resolved_word = emphasis

    font_path = "/System/Library/Fonts/Supplemental/Arial Black.ttf"
    fs_prefix = 70
    fs_emphasis = 150
    pad_x = 28
    pad_y = 18
    line_gap = 26       # gap between prefix block and emphasis block
    inner_line_gap = 12 # gap between wrapped prefix lines
    side_margin = 40    # frame edge → caption box gap
    highlight = (34, 232, 138, 255)  # #22E88A
    box_color = (0, 0, 0, 140)       # ~55% alpha black

    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font_p = ImageFont.truetype(font_path, fs_prefix)
    font_e = ImageFont.truetype(font_path, fs_emphasis)

    def text_size(s, font):
        l, t, r, b = draw.textbbox((0, 0), s, font=font)
        return r - l, b - t, l, t

    # Greedy word-wrap so the prefix never exceeds usable width.
    max_text_width = width - 2 * side_margin - 2 * pad_x

    def wrap_to_width(s: str, font) -> list[str]:
        if not s:
            return []
        words = s.split()
        lines: list[str] = []
        cur: list[str] = []
        for w in words:
            candidate = " ".join(cur + [w])
            tw, _, _, _ = text_size(candidate, font)
            if tw > max_text_width and cur:
                lines.append(" ".join(cur))
                cur = [w]
            else:
                cur.append(w)
        if cur:
            lines.append(" ".join(cur))
        return lines

    def block_metrics(lines, font):
        metrics = [text_size(ln, font) for ln in lines]
        if not lines:
            return metrics, 0, 0
        bw = max(m[0] for m in metrics)
        bh = sum(m[1] for m in metrics) + (len(lines) - 1) * inner_line_gap
        return metrics, bw, bh

    prefix_lines = wrap_to_width(prefix, font_p)
    suffix_lines = wrap_to_width(suffix, font_p)
    prefix_metrics, prefix_w, prefix_h = block_metrics(prefix_lines, font_p)
    suffix_metrics, suffix_w, suffix_h = block_metrics(suffix_lines, font_p)

    ew, eh, el, et = text_size(emphasis, font_e)

    # Anchor: emphasis line at y = height/8; prefix above and suffix below
    # with line_gap between blocks.
    y_emph_top = height // 8
    y_prefix_top = (y_emph_top - line_gap - prefix_h - 2 * pad_y
                    if prefix_lines else y_emph_top)
    y_suffix_top = y_emph_top + eh + 2 * pad_y + line_gap

    def draw_text_block(lines, metrics, block_w, block_h, top_y, radius=14):
        if not lines:
            return
        bx1 = (width - block_w) // 2 - pad_x
        by1 = top_y
        bx2 = bx1 + block_w + 2 * pad_x
        by2 = by1 + block_h + 2 * pad_y
        draw.rounded_rectangle([(bx1, by1), (bx2, by2)], radius=radius, fill=box_color)
        cy = by1 + pad_y
        for line, (lw, lh, ll, lt_) in zip(lines, metrics):
            tx = (width - lw) // 2 - ll
            draw.text((tx, cy - lt_), line,
                      font=font_p, fill=(255, 255, 255, 255))
            cy += lh + inner_line_gap

    # Prefix block (above emphasis).
    draw_text_block(prefix_lines, prefix_metrics, prefix_w, prefix_h, y_prefix_top)

    # Emphasis box + text (single line; emphasis word never wraps).
    bx1 = (width - ew) // 2 - pad_x
    by1 = y_emph_top
    bx2 = bx1 + ew + 2 * pad_x
    by2 = by1 + eh + 2 * pad_y
    draw.rounded_rectangle([(bx1, by1), (bx2, by2)], radius=18, fill=box_color)
    draw.text((bx1 + pad_x - el, by1 + pad_y - et), emphasis,
              font=font_e, fill=highlight)

    # Suffix block (below emphasis).
    draw_text_block(suffix_lines, suffix_metrics, suffix_w, suffix_h, y_suffix_top)

    img.save(png_path)
    return resolved_word


def burn_hook_caption(video_src: Path, dst: Path, hook_text: str,
                      cache_dir: Path, start: float = 0.0, end: float = 3.0,
                      emphasis_override: str = "") -> str:
    """Render caption to PNG and overlay on video; returns the resolved word."""
    png = cache_dir / "hook_caption.png"
    resolved = render_hook_caption_png(hook_text, png, width=1080, height=1920,
                                       emphasis_override=emphasis_override)
    if not png.exists():
        return resolved
    ffmpeg([
        "-i", str(video_src), "-i", str(png),
        "-filter_complex",
        f"[0:v][1:v]overlay=0:0:enable='between(t,{start},{end})'[v]",
        "-map", "[v]", "-map", "0:a?",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-preset", "medium", "-crf", "18",
        "-c:a", "copy",
        str(dst),
    ])
    return resolved


def mux_audio(video_src: Path, audio_src: Path, dst: Path) -> None:
    ffmpeg([
        "-i", str(video_src), "-i", str(audio_src),
        "-map", "0:v", "-map", "1:a",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        str(dst),
    ])


def parse_choices_options(answer_prompt: str) -> list[str]:
    """Split 'A. Foo  B. Bar  C. Baz  D. Qux' into ['A. Foo', 'B. Bar', ...].

    The OpenArt prompt formula uses double-space as separator between options.
    """
    parts = re.split(r"(?=(?:^|\s)[A-D]\.\s)", (answer_prompt or "").strip())
    return [p.strip() for p in parts if p.strip()]


def probe_duration(path: Path) -> float:
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)]
    ).decode().strip()
    return float(out)


def build_choices_claim_vo(
    question_text: str,
    options: list[str],
    cache_dir: Path,
    window: tuple[float, float],
    tts_provider: str,
    voice_id: str,
    piper_model: Path,
) -> tuple[Path, list[float], tuple[float, float]]:
    """Render the Choices-mode claim VO as a sequence of TTS clips with
    inter-segment pauses, positioned within the absolute claim window.

    Returns (windowed_wav_path,
             option_reveal_times_in_video_seconds,
             option_caption_suppress_window_seconds).

    The caption-suppress window covers from the first option's start to
    slightly past the last option's end — the question itself stays
    captioned.
    """
    win_start, win_end = window
    win_dur = win_end - win_start

    segments_text = [("question", question_text)]
    for i, opt in enumerate(options):
        segments_text.append((f"opt{i}", opt))

    raws: list[tuple[str, str, Path]] = []
    for label, text in segments_text:
        raw = cache_dir / f"vo_choice_{label}.raw.wav"
        generate_vo(text, raw, tts_provider, voice_id, piper_model)
        raws.append((label, text, raw))

    durs = [probe_duration(r[2]) for r in raws]
    speech_total = sum(durs)
    n_pauses = max(0, len(durs) - 1)

    pause_dur = (win_dur - speech_total) / n_pauses if n_pauses > 0 else 0.0
    tempo = 1.0
    MIN_PAUSE = 0.30
    MAX_PAUSE = 0.90

    if pause_dur < MIN_PAUSE:
        # Shrink pauses to MIN, time-stretch speech to fit (cap 1.2x)
        target_speech = max(0.1, win_dur - n_pauses * MIN_PAUSE)
        tempo = min(1.2, speech_total / target_speech)
        pause_dur = MIN_PAUSE
        budget = speech_total / tempo + n_pauses * pause_dur
        if budget > win_dur + 0.05:
            print(f"    ! Choices claim VO too long for window: speech={speech_total:.2f}s "
                  f"+ {n_pauses}*{MIN_PAUSE}s pauses into {win_dur:.2f}s "
                  f"(would need >{tempo:.2f}x; capped at 1.2x — tail may be clipped). "
                  f"Consider shorter options.", file=sys.stderr)
            _assembly_warnings.append({
                "kind": "choices_claim",
                "label": "claim",
                "speech_s": round(speech_total, 3),
                "pause_s": round(n_pauses * MIN_PAUSE, 3),
                "window_s": round(win_dur, 3),
                "needed_tempo": round(tempo, 3),
            })
    elif pause_dur > MAX_PAUSE:
        pause_dur = MAX_PAUSE  # extra slack falls at the tail of the window

    # Place each segment relative to t=0 in the video; first segment at win_start.
    placements: list[tuple[str, Path, float, float]] = []  # (label, raw, abs_start, eff_dur)
    cursor = win_start
    for i, ((label, _text, raw), dur) in enumerate(zip(raws, durs)):
        eff_dur = dur / tempo
        placements.append((label, raw, cursor, eff_dur))
        cursor += eff_dur
        if i < len(raws) - 1:
            cursor += pause_dur

    # ffmpeg amix: each segment time-stretched + delayed to its absolute start
    inputs: list[str] = []
    chains: list[str] = []
    amix_labels: list[str] = []
    for i, (lbl, raw, abs_start, _eff_dur) in enumerate(placements):
        inputs += ["-i", str(raw)]
        delay_ms = int(abs_start * 1000)
        chains.append(
            f"[{i}:a]atempo={tempo:.4f},adelay={delay_ms}|{delay_ms}[a{i}]"
        )
        amix_labels.append(f"a{i}")
    amix = (
        "".join(f"[{l}]" for l in amix_labels)
        + f"amix=inputs={len(amix_labels)}:normalize=0,"
        + f"apad=whole_dur={win_end:.3f}"
    )
    fc = ";".join(chains) + ";" + amix
    win_path = cache_dir / "vo_claim.win.wav"
    ffmpeg([*inputs, "-filter_complex", fc,
            "-ac", "2", "-ar", "48000", str(win_path)])

    opt_placements = [p for p in placements if p[0].startswith("opt")]
    reveal_times = [round(p[2], 3) for p in opt_placements]

    # Caption-suppress window: from first option start to past the last option's
    # end (with a small tail buffer to swallow trailing audio + the body→closer
    # xfade boundary).
    if opt_placements:
        first_start = opt_placements[0][2]
        last_start, last_eff = opt_placements[-1][2], opt_placements[-1][3]
        suppress = (first_start, min(win_end + 0.3, last_start + last_eff + 0.4))
    else:
        suppress = (win_end, win_end)
    return win_path, reveal_times, suppress


def write_meta_json(artifacts_dir: Path, public_dir: Path,
                    mode: str, options: list[str],
                    reveal_times: list[float],
                    suppress_window_ms: tuple[int, int] | None,
                    cta_text: str | None,
                    cta_nominal_start_ms: int | None) -> None:
    """Write meta.json describing visual data for the renderer.

    `cta_text` + `cta_nominal_start_ms` enable the caption renderer to
    force a page break at the resolution → CTA boundary, even when the
    actual transcribed timing of the CTA's first word drifts from the
    nominal VO-window start (Whisper's word boundaries are imprecise,
    and fit_to_window's atempo/adelay can shift things by 100s of ms).
    The renderer matches `cta_text`'s first word against the transcript
    (searching only past `cta_nominal_start_ms - tolerance`) to find the
    real break point.
    """
    meta = {
        "mode": mode or "Facts",
        "options": options,
        "option_reveal_times_s": reveal_times,
        "suppress_captions_window_ms": list(suppress_window_ms) if suppress_window_ms else None,
        "cta_text": (cta_text or "").strip() or None,
        "cta_nominal_start_ms": cta_nominal_start_ms,
    }
    payload = json.dumps(meta, indent=2)
    (artifacts_dir / "meta.json").write_text(payload)
    public_dir.mkdir(parents=True, exist_ok=True)
    (public_dir / "meta.json").write_text(payload)
    print(f"  wrote meta.json (mode={meta['mode']}, options={len(options)}, "
          f"reveal_times={reveal_times}, cta_nominal_start_ms={cta_nominal_start_ms})")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("row", type=int, help="Post Calendar row (e.g. 5)")
    ap.add_argument("slug", help="Project slug (e.g. australia-wider-than-moon)")
    ap.add_argument("--with-vo", action="store_true",
                    help="Generate VO and mux into bg.mp4")
    ap.add_argument("--tts-provider", default="piper", choices=["piper", "elevenlabs"],
                    help="TTS provider (default: piper — fully local)")
    ap.add_argument("--voice-id", default="21m00Tcm4TlvDq8ikWAM",
                    help="ElevenLabs voice_id (only used if provider=elevenlabs)")
    ap.add_argument("--piper-model", default=str(REPO / ".piper_voices" / "en_US-lessac-medium.onnx"),
                    help="Piper voice .onnx path (only used if provider=piper)")
    ap.add_argument("--with-sfx", action="store_true",
                    help="Mix SFX cues from sfx_library/ into the audio")
    ap.add_argument("--with-music", action="store_true",
                    help="Mix a Pixabay background music bed into the audio")
    ap.add_argument("--silent-hook", action="store_true",
                    help="No VO during the 0-3s hook; burn a static caption instead")
    ap.add_argument("--music-volume-db", type=float, default=-14.0,
                    help="Music bed volume in dB (default: -14)")
    args = ap.parse_args()

    project_dir = REPO / "projects" / args.slug
    work_dir = project_dir / "assets" / "video"
    cache_dir = project_dir / "assets" / "modular_cache"
    renders_dir = project_dir / "renders"
    artifacts_dir = project_dir / "artifacts"
    for d in (work_dir, cache_dir, renders_dir, artifacts_dir):
        d.mkdir(parents=True, exist_ok=True)

    # Fresh start: clear any stale warning records from a previous run.
    _assembly_warnings.clear()
    warnings_path = artifacts_dir / "assembly_warnings.json"
    if warnings_path.exists():
        warnings_path.unlink()

    sheets = build_sheets(write=True)   # writes resolved emphasis to the Emphasis Override column
    row = read_post_row(sheets, args.row)
    print(f"Post row {args.row}: {row['post']!r}")

    # Apply per-project text overrides (used by the web server's auto-shorten
    # recovery flow). These take precedence over sheet content for this render
    # only; the sheet itself is untouched.
    overrides_path = artifacts_dir / "text_overrides.json"
    if overrides_path.exists():
        try:
            overrides = json.loads(overrides_path.read_text())
            for k, v in (overrides or {}).items():
                if k in row and isinstance(v, str) and v.strip():
                    print(f"  [override] using shortened {k}: {v!r}")
                    row[k] = v
        except json.JSONDecodeError as e:
            print(f"  WARN: text_overrides.json malformed ({e}); ignoring", file=sys.stderr)

    # Lazy reaction pick: if archetype is set in P but R is empty, pick a
    # random Generated clip from the Clips catalog and write it back to R.
    resolve_reaction_filename(sheets, args.row, row)

    # Up-front sanity: each segment needs a filename whose file exists locally.
    missing: list[str] = []
    for seg, fname_field, lib_key in SEGMENT_SOURCES:
        fname = (row.get(fname_field) or "").strip()
        if not fname:
            missing.append(f"{seg} (no {fname_field})")
        elif not (SEGMENT_LIBRARY[lib_key] / fname).exists():
            missing.append(
                f"{seg} ({fname_field}={fname!r} not in {SEGMENT_LIBRARY[lib_key]})"
            )
    if missing:
        print(f"ERROR: missing clip sources: {missing}", file=sys.stderr)
        return 2

    # 1) Resolve source clips (local-library copy), then normalize
    segment_durs = {"hook": HOOK_DUR, "body": BODY_DUR, "closer": CLOSER_DUR}
    normalized = []
    for seg, fname_field, lib_key in SEGMENT_SOURCES:
        raw = resolve_segment_source(seg, fname_field, lib_key, row, cache_dir)
        norm = cache_dir / f"{seg}.norm.mp4"
        dur = segment_durs[seg]
        print(f"  normalizing {seg} to 1080x1920 / {dur}s ...")
        normalize_clip(raw, norm, dur)
        normalized.append(norm)

    # 2) Concat with transitions into silent bg.mp4
    bg = work_dir / "bg.mp4"
    print(f"  concatenating with xfade transitions -> {bg.relative_to(REPO)}")
    concat_clips_with_transitions(normalized, bg, TRANSITION_DUR)

    # 3) Optional VO
    if args.with_vo:
        if args.tts_provider == "elevenlabs" and not os.environ.get("ELEVENLABS_API_KEY"):
            print("ERROR: tts-provider=elevenlabs but ELEVENLABS_API_KEY not set",
                  file=sys.stderr)
            return 2
        if args.tts_provider == "piper" and not Path(args.piper_model).exists():
            print(f"ERROR: piper model not found at {args.piper_model}",
                  file=sys.stderr)
            return 2
        piper_model = Path(args.piper_model)
        is_choices = (row["mode"] or "").strip().lower() == "choices"
        choices_options: list[str] = []
        choices_reveal_times: list[float] = []

        vo_lines: dict[str, str] = {
            "hook":       row["hook"],
            "resolution": row["resolution"],
            "cta":        row["cta"],
        }
        if not is_choices:
            # Facts mode: claim VO is question + answer-prompt as a single utterance.
            vo_lines["claim"] = f"{row['question']}. {row['answer_prompt']}"
        if args.silent_hook:
            vo_lines.pop("hook", None)
            print("  [silent-hook] skipping hook VO; static caption will be burned in")

        print(f"  generating VO lines via {args.tts_provider} ...")
        windowed = []
        for name, text in vo_lines.items():
            if not text.strip():
                continue
            raw = cache_dir / f"vo_{name}.raw.wav"
            win = cache_dir / f"vo_{name}.win.wav"
            print(f"    - {name}: {text!r}")
            generate_vo(text, raw, args.tts_provider, args.voice_id, piper_model)
            start, end = VO_WINDOWS[name]
            fit_to_window(raw, win, start, end, label=name)
            windowed.append(win)

        choices_suppress_window: tuple[float, float] | None = None
        if is_choices:
            choices_options = parse_choices_options(row["answer_prompt"])
            print(f"    - choices claim ({len(choices_options)} options): "
                  f"{row['question']!r} → {choices_options}")
            claim_win, choices_reveal_times, choices_suppress_window = build_choices_claim_vo(
                question_text=row["question"],
                options=choices_options,
                cache_dir=cache_dir,
                window=VO_WINDOWS["claim"],
                tts_provider=args.tts_provider,
                voice_id=args.voice_id,
                piper_model=piper_model,
            )
            windowed.append(claim_win)
            print(f"    - option reveal times (s): {choices_reveal_times}")
            print(f"    - caption suppress window (s): {choices_suppress_window}")
        mixed = cache_dir / "vo_mix.wav"
        print("  mixing VO tracks ...")
        mix_vo_tracks(windowed, mixed, TOTAL_DUR)

        layer_cues: list[tuple[Path, float, float]] = []

        if args.with_music:
            print("  resolving music bed ...")
            bed = resolve_music_bed(row["mode"] or "Facts", TOTAL_DUR, cache_dir,
                                    track_override=row.get("music_track", ""))
            layer_cues.append((bed, 0.0, args.music_volume_db))

        if args.with_sfx:
            sfx_dir = REPO / "sfx_library"
            cue_table = SFX_CUES_CHOICES if row["mode"] == "Choices" else SFX_CUES_FACTS
            missing = []
            for fname, start, db in cue_table:
                p = sfx_dir / fname
                if not p.exists():
                    missing.append(fname); continue
                layer_cues.append((p, start, db))
            if missing:
                print(f"WARNING: missing SFX files: {missing}", file=sys.stderr)
            print(f"  queued {len(cue_table)-len(missing)} SFX cues ({row['mode']} cue list)")

        if layer_cues:
            print(f"  mixing VO + {len(layer_cues)} audio layers ...")
            final_mix = cache_dir / "audio_mix.wav"
            mix_audio_layers(mixed, layer_cues, final_mix, TOTAL_DUR)
            mixed = final_mix

        print("  muxing audio into bg.mp4 ...")
        tmp = cache_dir / "bg_with_audio.mp4"
        mux_audio(bg, mixed, tmp)
        bg.unlink()
        tmp.rename(bg)

    if args.silent_hook and row["hook"]:
        override = (row.get("emphasis_override") or "").strip()
        source = "Emphasis Override (per-post override)"
        if not override:
            override = lookup_hook_emphasis(sheets, row["hook"])
            source = "Hooks!F (library default)"
        if override:
            print(f"  burning static hook caption (0-3s) — {source}: {override!r}")
        else:
            print("  burning static hook caption (0-3s) — auto-picking emphasis word")
        tmp = cache_dir / "bg_with_caption.mp4"
        resolved_word = burn_hook_caption(bg, tmp, row["hook"], cache_dir,
                                          start=0.0, end=3.0,
                                          emphasis_override=override)
        bg.unlink()
        tmp.rename(bg)
        # Write the resolved emphasis back to the Emphasis Override column so
        # the sheet reflects what was actually rendered.
        if resolved_word:
            emphasis_cell = cell_for(sheets, args.row, "emphasis_override")
            sheets.spreadsheets().values().update(
                spreadsheetId=POST_SHEET, range=emphasis_cell,
                valueInputOption="RAW", body={"values": [[resolved_word]]}
            ).execute()
            print(f"  wrote resolved emphasis to {emphasis_cell}: {resolved_word!r}")

    # Emit meta.json for the renderer (Choices-mode option overlay + caption suppression).
    is_choices = (row["mode"] or "").strip().lower() == "choices"
    suppress_window = None
    suppress_seconds = locals().get("choices_suppress_window")
    if is_choices and suppress_seconds:
        suppress_window = (
            int(suppress_seconds[0] * 1000),
            int(suppress_seconds[1] * 1000),
        )
    # CTA boundary signal for the caption renderer — both the literal text
    # (so the renderer can match the first CTA word in the transcript) and
    # the nominal timeline start (so the matcher knows where to start
    # looking and won't accidentally fire on an earlier "Lock"-like word).
    cta_text_raw = (row.get("cta") or "").strip()
    cta_nominal_start_ms = int(VO_WINDOWS["cta"][0] * 1000)
    write_meta_json(
        artifacts_dir=artifacts_dir,
        public_dir=REPO / "remotion-composer" / "public",
        mode=row["mode"] or "Facts",
        options=parse_choices_options(row["answer_prompt"]) if is_choices else [],
        reveal_times=locals().get("choices_reveal_times", []) if args.with_vo else [],
        suppress_window_ms=suppress_window,
        cta_text=cta_text_raw,
        cta_nominal_start_ms=cta_nominal_start_ms if cta_text_raw else None,
    )

    # Persist any structured warnings so the web server can pick them up.
    if _assembly_warnings:
        warnings_path.write_text(json.dumps(_assembly_warnings, indent=2))
        print(f"  wrote {len(_assembly_warnings)} warning(s) to {warnings_path.relative_to(REPO)}")

    print()
    print(f"DONE -> {bg.relative_to(REPO)}")
    print()
    print("Next steps:")
    print(f"  python scripts/common/transcribe.py {args.slug}")
    print(f"  # render Remotion TriviaWithBg against words.json")
    print(f"  python scripts/trivia/publish.py {args.slug} {args.row}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
