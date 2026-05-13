#!/usr/bin/env python
"""Audition speakers from a multi-speaker Piper LibriTTS voice model.

The `en_US-libritts-high` model has 904 distinct human readers (LibriTTS-R,
derived from LibriVox audiobook recordings). The model file embeds only the
numeric `speaker_id -> p{LibriSpeech ID}` mapping — no gender, name, or
accent metadata. This script joins the model's speaker map against the
upstream LibriSpeech SPEAKERS.TXT (gender + LibriVox reader name) so you can
pre-filter (e.g. `--gender F`) and audition representative samples.

Workflow:
    # First-run: also downloads SPEAKERS.TXT (~125 KB) into .piper_voices/.
    # Synth every 50th speaker — 18 clips, gender visible in the filename:
    python scripts/piper_voices/sample_libritts.py --every 50

    # Female speakers only, evenly spread across the catalog:
    python scripts/piper_voices/sample_libritts.py --gender F --count 12

    # Specific speaker IDs (model-internal, not LibriSpeech IDs):
    python scripts/piper_voices/sample_libritts.py --ids 0,17,93,400

    # Custom preview text:
    python scripts/piper_voices/sample_libritts.py --every 100 \\
        --text "The mosquito is the deadliest animal to humans, by a long shot."
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tools.audio.piper_tts import (  # noqa: E402
    VOICE_DIR,
    fetch_voice,
    voice_files_present,
)
from tools.tool_registry import registry  # noqa: E402

MODEL = "en_US-libritts-high"
DEFAULT_TEXT = (
    "Welcome to Open Montage. Which animal kills the most humans every year? "
    "You might be surprised — it's not the shark."
)
SPEAKERS_URL = (
    "https://raw.githubusercontent.com/oscarknagg/voicemap/master/"
    "data/LibriSpeech/SPEAKERS.TXT"
)
SPEAKERS_CACHE = VOICE_DIR / "LibriSpeech_SPEAKERS.TXT"
OUT_DIR = REPO / ".previews" / "piper" / "libritts"


@dataclass(frozen=True)
class Speaker:
    """One row of the joined model + LibriSpeech speaker table."""
    model_id: int            # 0..903 — what you pass as `speaker_id` to piper
    librispeech_id: int      # 14..9026 — what the LibriVox/LibriSpeech world calls them
    gender: str              # 'F' or 'M' or '?'
    subset: str              # e.g. 'train-clean-360'
    name: str                # LibriVox reader name (free text)


def fetch_speakers_metadata() -> Path:
    """Download SPEAKERS.TXT once. Idempotent."""
    if SPEAKERS_CACHE.is_file() and SPEAKERS_CACHE.stat().st_size > 0:
        return SPEAKERS_CACHE
    SPEAKERS_CACHE.parent.mkdir(parents=True, exist_ok=True)
    print(f"  → fetching SPEAKERS.TXT from {SPEAKERS_URL}", file=sys.stderr)
    req = urllib.request.Request(SPEAKERS_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 - controlled URL
        data = r.read()
    tmp = SPEAKERS_CACHE.with_suffix(".part")
    tmp.write_bytes(data)
    tmp.replace(SPEAKERS_CACHE)
    mb = SPEAKERS_CACHE.stat().st_size / 1024
    print(f"  ✓ saved SPEAKERS.TXT ({mb:.1f} KB)", file=sys.stderr)
    return SPEAKERS_CACHE


def parse_speakers_txt(path: Path) -> dict[int, tuple[str, str, str]]:
    """Parse SPEAKERS.TXT into {librispeech_id: (gender, subset, name)}."""
    out: dict[int, tuple[str, str, str]] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line or line.startswith(";"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 5:
            continue
        try:
            lid = int(parts[0])
        except ValueError:
            continue
        gender = parts[1] or "?"
        subset = parts[2] or ""
        name = parts[4] if len(parts) >= 5 else ""
        out[lid] = (gender, subset, name)
    return out


def load_speaker_table() -> list[Speaker]:
    """Build the full {model_id -> Speaker} table, joining model + SPEAKERS.TXT."""
    if not voice_files_present(MODEL):
        print(f"  → voice {MODEL} not present, fetching first…", file=sys.stderr)
        fetch_voice(MODEL)
    cfg = json.loads((VOICE_DIR / f"{MODEL}.onnx.json").read_text())
    speaker_map: dict[str, int] = cfg["speaker_id_map"]  # 'p3922' -> 0, ...

    fetch_speakers_metadata()
    metadata = parse_speakers_txt(SPEAKERS_CACHE)

    table: list[Speaker] = []
    for key, mid in speaker_map.items():
        # Key is like 'p3922' — strip the 'p' prefix.
        m = re.match(r"^p(\d+)$", key)
        if not m:
            continue
        lid = int(m.group(1))
        gender, subset, name = metadata.get(lid, ("?", "", ""))
        table.append(Speaker(
            model_id=mid,
            librispeech_id=lid,
            gender=gender,
            subset=subset,
            name=name,
        ))
    table.sort(key=lambda s: s.model_id)
    return table


def _safe_name(s: str, maxlen: int = 24) -> str:
    """Filesystem-safe slug of a free-text reader name."""
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_")
    return (s[:maxlen] or "Unknown").rstrip("_")


def pick_ids(
    table: list[Speaker],
    *,
    explicit_ids: list[int] | None,
    every: int | None,
    count: int | None,
    gender: str | None,
) -> list[Speaker]:
    """Resolve CLI selectors into a concrete speaker list."""
    if gender:
        gender = gender.upper()
        table = [s for s in table if s.gender == gender]
    if not table:
        return []

    if explicit_ids is not None:
        wanted = set(explicit_ids)
        return [s for s in table if s.model_id in wanted]

    if every:
        return [s for s in table if s.model_id % every == 0]

    if count:
        # Even spread across the (possibly filtered) range.
        if count >= len(table):
            return table
        step = len(table) / count
        return [table[int(i * step)] for i in range(count)]

    # No selector — default to every 50th.
    return [s for s in table if s.model_id % 50 == 0]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sel = ap.add_mutually_exclusive_group()
    sel.add_argument("--every", type=int, help="synth every Nth speaker (e.g. 50)")
    sel.add_argument("--count", type=int, help="synth K speakers evenly across the range")
    sel.add_argument("--ids", help="comma-separated explicit model_ids, e.g. 0,17,93,400")
    ap.add_argument("--gender", choices=["M", "F", "m", "f"],
                    help="filter to one gender BEFORE selecting samples")
    ap.add_argument("--text", default=DEFAULT_TEXT, help="preview text to synth")
    ap.add_argument("--out-dir", type=Path, default=OUT_DIR)
    ap.add_argument("--list", action="store_true",
                    help="list the resolved speaker table without synthesising")
    args = ap.parse_args()

    table = load_speaker_table()
    n_f = sum(1 for s in table if s.gender == "F")
    n_m = sum(1 for s in table if s.gender == "M")
    n_u = len(table) - n_f - n_m
    print(f"speakers loaded: {len(table)} (F={n_f}, M={n_m}, unknown={n_u})")

    explicit_ids = (
        [int(x) for x in args.ids.split(",")] if args.ids else None
    )
    picks = pick_ids(
        table,
        explicit_ids=explicit_ids,
        every=args.every,
        count=args.count,
        gender=args.gender,
    )

    if not picks:
        print("(no speakers matched the filters)", file=sys.stderr)
        return 1

    if args.list:
        for s in picks:
            print(f"  id={s.model_id:>3}  ls={s.librispeech_id:>4}  "
                  f"{s.gender}  {s.subset:<18}  {s.name}")
        return 0

    args.out_dir.mkdir(parents=True, exist_ok=True)
    registry.discover()
    tool = registry._tools["piper_tts"]

    print(f"\nsynthing {len(picks)} sample(s) -> {args.out_dir}")
    print(f"text ({len(args.text)} chars): {args.text}\n")
    failures = 0
    for s in picks:
        slug = f"id{s.model_id:03d}_{s.gender}_{_safe_name(s.name)}.wav"
        out = args.out_dir / slug
        if out.exists():
            print(f"  ✓ {slug:<48} (exists, skipping)")
            continue
        r = tool.execute({
            "text": args.text,
            "model": MODEL,
            "speaker_id": s.model_id,
            "output_path": str(out),
        })
        if r.success:
            kb = out.stat().st_size / 1024
            print(f"  ✓ {slug:<48} {r.duration_seconds:.1f}s  {kb:.0f}KB")
        else:
            failures += 1
            print(f"  ✗ {slug}: {r.error}", file=sys.stderr)

    print(f"\ndone. {len(picks) - failures}/{len(picks)} ready in {args.out_dir}")
    print(f"audition: open {args.out_dir}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
