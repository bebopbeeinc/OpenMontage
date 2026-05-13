#!/usr/bin/env python
"""Transcribe a project's source video to word-level timestamps.

Pipeline-agnostic: reads from projects/<slug>/assets/video/bg.mp4 regardless
of which pipeline produced it. Per-project brand-token additions land in
projects/<slug>/artifacts/brand_tokens_extra.json (written by
apply_feedback_patches or similar).

Usage:
    python scripts/common/transcribe.py <project-slug>

Reads:   projects/<slug>/assets/video/bg.mp4
Writes:  projects/<slug>/artifacts/words.json
         remotion-composer/public/words.json   (for renderer)
         remotion-composer/public/bg.mp4       (for renderer)

Post-processing: Whisper never capitalizes proper nouns, so the phrases in
BRAND_TOKENS below are case-corrected before writing. Both single-word
("Captain") and multi-word ("Travel Crush") entries are supported.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

from faster_whisper import WhisperModel

REPO = Path(__file__).resolve().parents[2]

# Case-sensitive spellings to enforce on the transcript. Case-insensitive match,
# whitespace tolerant. For multi-word phrases the sequence is detected across
# adjacent word entries.
BRAND_TOKENS: tuple[str, ...] = (
    "Travel Crush",
    "Captain",
    "Fennec",
)

# Whisper sometimes mis-tokenizes brand words into multiple short tokens
# (e.g. "Captain" → "Cap" + "in" because the glottal-stop 't' gets dropped).
# Each entry: (lowered, no-punct token sequence) → corrected single word.
# These run BEFORE fix_casing to merge the split entries into one.
WHISPER_MERGE_FIXES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("cap", "in"), "Captain"),
    (("cap", "tin"), "Captain"),
    (("capt", "in"), "Captain"),
    (("travel", "crash"), "Travel Crush"),
    (("travel", "krush"), "Travel Crush"),
    (("fen", "ick"), "Fennec"),
    (("fen", "eck"), "Fennec"),
    (("fen", "ec"), "Fennec"),
)


def _strip_punct(s: str) -> str:
    return re.sub(r"[^\w'-]+$", "", s.lstrip())


def _apply_casing(original: str, correct: str) -> str:
    """Replace the word portion of `original` with `correct`, preserving
    trailing punctuation (`.,!?:;`)."""
    m = re.match(r"^(\S+?)([.,!?:;\"']*)$", original)
    if not m:
        return correct
    _, trailing = m.groups()
    return correct + trailing


def merge_split_tokens(words: list[dict]) -> int:
    """In-place: merge adjacent Whisper words whose joined form matches a known
    split-fix pattern. Returns merge count. Mutates `words`."""
    merges = 0
    i = 0
    while i < len(words):
        for pattern, replacement in WHISPER_MERGE_FIXES:
            n = len(pattern)
            if i + n > len(words):
                continue
            window = tuple(_strip_punct(words[i + k]["word"]).lower()
                           for k in range(n))
            if window != pattern:
                continue
            # Merge: combine timings, replace with corrected single word, keep
            # any trailing punctuation from the LAST original token.
            last_orig = words[i + n - 1]["word"]
            m = re.match(r"^\S+?([.,!?:;\"']*)$", last_orig)
            trailing = m.group(1) if m else ""
            merged = {
                "word": replacement + trailing,
                "startMs": words[i]["startMs"],
                "endMs": words[i + n - 1]["endMs"],
            }
            words[i:i + n] = [merged]
            merges += 1
            break  # don't re-test this position, advance
        i += 1
    return merges


def _project_brand_tokens(slug: str) -> tuple[str, ...]:
    """Read per-project brand additions from brand_tokens_extra.json (written
    by apply_feedback_patches.py when the reviewer flagged a missing brand)."""
    import json
    extra_path = REPO / "projects" / slug / "artifacts" / "brand_tokens_extra.json"
    if not extra_path.exists():
        return ()
    try:
        data = json.loads(extra_path.read_text())
        if isinstance(data, list):
            return tuple(str(t).strip() for t in data if str(t).strip())
    except (json.JSONDecodeError, OSError):
        pass
    return ()


def fix_casing(words: list[dict], extra_tokens: tuple[str, ...] = ()) -> int:
    """In-place case correction based on BRAND_TOKENS + per-project additions.
    Returns fix count."""
    fixes = 0
    all_tokens = tuple(BRAND_TOKENS) + tuple(extra_tokens)
    # Longest tokens first so multi-word phrases match before their prefixes.
    tokens = sorted(set(all_tokens), key=lambda t: -len(t.split()))
    for token in tokens:
        parts = token.split()
        n = len(parts)
        lowered = [p.lower() for p in parts]
        i = 0
        while i <= len(words) - n:
            window = [_strip_punct(words[i + k]["word"]).lower() for k in range(n)]
            if window == lowered:
                for k, correct in enumerate(parts):
                    orig = words[i + k]["word"]
                    fixed = _apply_casing(orig, correct)
                    if fixed != orig:
                        words[i + k]["word"] = fixed
                        fixes += 1
                i += n
            else:
                i += 1
    return fixes


def main(slug: str) -> None:
    project = REPO / "projects" / slug
    src = project / "assets" / "video" / "bg.mp4"
    if not src.exists():
        sys.exit(f"source video not found: {src}")

    words_out = project / "artifacts" / "words.json"
    words_out.parent.mkdir(parents=True, exist_ok=True)

    print(f"transcribing {src} …")
    model = WhisperModel("base.en", device="cpu", compute_type="int8")
    segments, _info = model.transcribe(
        str(src), word_timestamps=True, vad_filter=True,
    )
    words = [
        {
            "word": w.word.strip(),
            "startMs": int(w.start * 1000),
            "endMs": int(w.end * 1000),
        }
        for seg in segments
        for w in (seg.words or [])
    ]

    merges = merge_split_tokens(words)
    extra = _project_brand_tokens(slug)
    if extra:
        print(f"  per-project brand tokens: {list(extra)}")
    fixes = fix_casing(words, extra_tokens=extra)

    with words_out.open("w") as f:
        json.dump(words, f, indent=2)

    # Stage into remotion-composer/public/ for the renderer
    public = REPO / "remotion-composer" / "public"
    shutil.copy(src, public / "bg.mp4")
    shutil.copy(words_out, public / "words.json")

    notes = []
    if merges:
        notes.append(f"{merges} split-token merge{'s' if merges != 1 else ''}")
    if fixes:
        notes.append(f"{fixes} case fix{'es' if fixes != 1 else ''}")
    note_str = f" ({', '.join(notes)})" if notes else ""
    print(f"✓ {len(words)} words{note_str} → {words_out}")
    print("  " + " ".join(w["word"] for w in words))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: transcribe.py <project-slug>")
    main(sys.argv[1])
