#!/usr/bin/env python
"""Pre-fetch Piper TTS voice models into .piper_voices/.

The piper_tts tool auto-fetches on first use, but pre-fetching is useful when:
  - Provisioning a machine that will be offline at run-time
  - Bulk-downloading a roster of voices ahead of a recording session
  - Picking a different voice than the default en_US-lessac-medium

Usage:
    # Fetch one voice
    python scripts/piper_voices/fetch.py en_US-lessac-medium

    # Fetch several voices in one go
    python scripts/piper_voices/fetch.py en_US-ryan-high en_GB-alan-medium

    # Fetch the curated "good defaults" set
    python scripts/piper_voices/fetch.py --recommended

    # List what's already on disk
    python scripts/piper_voices/fetch.py --list
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tools.audio.piper_tts import (  # noqa: E402
    VOICE_DIR,
    fetch_voice,
    voice_files_present,
)

# Curated set: the voices we'd want available by default on a fresh machine.
# Tradeoff is size — the "high" tier is markedly better but ~115 MB each, so
# we lead with one solid medium-quality voice and offer the rest as opt-ins.
RECOMMENDED = [
    "en_US-lessac-medium",   # neutral US English narrator, ~63 MB
]


def _list_local() -> int:
    if not VOICE_DIR.exists():
        print(f"(no voices yet — directory does not exist: {VOICE_DIR})")
        return 0
    onnx_files = sorted(VOICE_DIR.glob("*.onnx"))
    if not onnx_files:
        print(f"(no voices yet — empty: {VOICE_DIR})")
        return 0
    print(f"voices in {VOICE_DIR}:")
    for f in onnx_files:
        cfg = f.with_suffix(".onnx.json")
        size_mb = f.stat().st_size / (1024 * 1024)
        ok = "✓" if cfg.is_file() else "⚠ missing .onnx.json"
        print(f"  {ok}  {f.stem}  ({size_mb:.1f} MB)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("voices", nargs="*", help="voice names, e.g. en_US-ryan-high")
    ap.add_argument("--recommended", action="store_true",
                    help=f"fetch the recommended set: {RECOMMENDED}")
    ap.add_argument("--list", action="store_true",
                    help="list voices already on disk")
    args = ap.parse_args()

    if args.list:
        return _list_local()

    targets: list[str] = list(args.voices)
    if args.recommended:
        targets = list(dict.fromkeys(targets + RECOMMENDED))
    if not targets:
        ap.error("specify at least one voice or use --recommended / --list")

    failures = 0
    for name in targets:
        try:
            if voice_files_present(name):
                print(f"  ✓ already present: {name}")
                continue
            fetch_voice(name)
        except Exception as e:
            failures += 1
            print(f"  ✗ {name}: {e}", file=sys.stderr)

    print(f"\ndone. {len(targets) - failures}/{len(targets)} voice(s) ready in {VOICE_DIR}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
