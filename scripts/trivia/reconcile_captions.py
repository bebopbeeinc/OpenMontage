#!/usr/bin/env python
"""Repair Whisper transcription errors using the sheet's intended VO text.

Whisper is deterministic on the same audio but it does mishear individual
words (the classic case: 'fly' -> 'find'). The Post Calendar already holds
the canonical VO script — question, answer_prompt, resolution, cta — so we
can align the transcript against that ground truth and patch obvious word
swaps without re-rendering.

Conservative policy: only handles short, equal-length 'replace' segments
(up to 3 words on each side, length diff ≤ 1). Whisper-extra tokens are
kept; Whisper-missed tokens are not fabricated.

Usage:
    python scripts/trivia/reconcile_captions.py <row> <slug>

Reads:   projects/<slug>/artifacts/words.json
         Post Calendar row <row>
Writes:  projects/<slug>/artifacts/words.json (in place)
         remotion-composer/public/words.json   (staged for renderer)
"""
from __future__ import annotations

import difflib
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from post_row import build_sheets, read_post_row  # noqa: E402

REPO = Path(__file__).resolve().parents[2]

_PUNCT_RE = re.compile(r"[^\w'-]+")


def _norm(s: str) -> str:
    """Lowercase + strip surrounding punctuation, preserving apostrophes/hyphens."""
    return _PUNCT_RE.sub("", s.lower())


def read_row_intended_vo(row_num: int) -> tuple[str, dict[str, str]]:
    """Return (joined_intended_vo_text, row_dict).

    Empty text if the sheet read fails. Caller decides whether to skip.
    """
    try:
        d = read_post_row(build_sheets(), row_num)
    except Exception as e:
        print(f"  WARN: sheet read failed ({e}); skipping reconcile", file=sys.stderr)
        return "", {}

    row = {
        "mode":          d["mode"].strip(),
        "hook":          d["hook"].strip(),
        "question":      d["question"].strip(),
        "answer_prompt": d["answer_prompt"].strip(),
        "resolution":    d["resolution"].strip(),
        "cta":           d["cta"].strip(),
    }
    # The pipeline runs --silent-hook by default, so the hook is NOT spoken.
    # Both facts and choices modes speak: question + answer_prompt + resolution + cta.
    parts = [row["question"], row["answer_prompt"], row["resolution"], row["cta"]]
    return " ".join(p for p in parts if p), row


def reconcile(words: list[dict], intended_text: str) -> tuple[list[dict], list[str]]:
    """Align Whisper words against intended text, fixing short replacements.

    Returns (corrected_words, fix_descriptions).
    """
    if not words or not intended_text:
        return words, []

    whisper_norm = [_norm(w["word"]) for w in words]
    intended_tokens = intended_text.split()
    intended_norm = [_norm(t) for t in intended_tokens]

    sm = difflib.SequenceMatcher(a=whisper_norm, b=intended_norm)
    fixes: list[str] = []
    out: list[dict] = []

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            out.extend(words[i1:i2])
            continue
        if tag == "delete":
            # Whisper produced tokens not in the script — likely filler ("um", "uh").
            # Keep them; trimming would risk losing real content.
            out.extend(words[i1:i2])
            continue
        if tag == "insert":
            # Script has tokens Whisper missed. Can't fabricate timing — skip.
            continue
        # tag == "replace"
        ww = words[i1:i2]
        iw = intended_tokens[j1:j2]
        if not ww or not iw:
            out.extend(ww)
            continue
        # Conservative: only attempt repair for short, similar-length spans.
        if len(ww) > 3 or len(iw) > 3 or abs(len(ww) - len(iw)) > 1:
            out.extend(ww)
            continue

        start_ms = ww[0]["startMs"]
        end_ms = ww[-1]["endMs"]
        span = max(end_ms - start_ms, len(iw))  # avoid divide-by-zero
        for k, tok in enumerate(iw):
            s = int(start_ms + (k / len(iw)) * span)
            e = int(start_ms + ((k + 1) / len(iw)) * span) if k < len(iw) - 1 else end_ms
            out.append({"word": tok, "startMs": s, "endMs": e})
        before = " ".join(_norm(w["word"]) for w in ww)
        after = " ".join(_norm(t) for t in iw)
        if before != after:
            fixes.append(f"{before!r} -> {after!r}")

    return out, fixes


def main(row: int, slug: str) -> int:
    words_path = REPO / "projects" / slug / "artifacts" / "words.json"
    if not words_path.exists():
        print(f"ERROR: {words_path} not found", file=sys.stderr)
        return 2

    words = json.loads(words_path.read_text())
    intended, row_data = read_row_intended_vo(row)
    if not intended:
        print("  no intended text available; skipping reconcile")
        return 0

    print(f"intended VO ({len(intended.split())} tokens):")
    print(f"  {intended}")
    print(f"whisper transcript ({len(words)} tokens):")
    print(f"  {' '.join(w['word'] for w in words)}")

    corrected, fixes = reconcile(words, intended)

    if not fixes:
        print("\n✓ no corrections needed; transcript matches intended VO")
        return 0

    print(f"\nfixes ({len(fixes)}):")
    for f in fixes:
        print(f"  {f}")
    words_path.write_text(json.dumps(corrected, indent=2))
    public = REPO / "remotion-composer" / "public" / "words.json"
    public.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(words_path, public)
    print(f"\n✓ wrote {len(corrected)} words to {words_path.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: reconcile_captions.py <row> <slug>")
    sys.exit(main(int(sys.argv[1]), sys.argv[2]))
