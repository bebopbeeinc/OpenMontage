#!/usr/bin/env python
"""Generate a polished cartoon-style SFX pack via ffmpeg.

Designed to match the Duolingo-flat body style: punchy, bouncy, clean.
Each SFX layers multiple components (low thumps + high cracks, harmonics, etc.)
to feel richer than a single oscillator.

Writes 8 effects to sfx_library/. Drop in higher-quality replacements with the
same filenames any time — the assembler picks them up automatically.

Usage:
    python scripts/common/generate_sfx.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "sfx_library"


def ffmpeg(args: list[str]) -> None:
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args]
    subprocess.run(cmd, check=True)


def synth(out_path: Path, filter_complex: str, duration: float) -> None:
    ffmpeg([
        "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={duration}",
        "-filter_complex", filter_complex,
        "-map", "[a]",
        "-ac", "2", "-ar", "48000",
        str(out_path),
    ])


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)

    # 1) WHOOSH — bright→dark filtered noise sweep (cartoon transition)
    synth(OUT / "whoosh.wav",
          # bright high-band burst
          "anoisesrc=c=pink:d=0.5:a=0.7[hi];"
          "[hi]bandpass=frequency=1800:width_type=h:w=1200,"
          "afade=t=in:st=0:d=0.02,"
          "afade=t=out:st=0.18:d=0.22[hib];"
          # darker low-band tail (delayed slightly for sweep illusion)
          "anoisesrc=c=brown:d=0.5:a=0.7[lo];"
          "[lo]bandpass=frequency=350:width_type=h:w=400,"
          "adelay=120|120,"
          "afade=t=in:st=0:d=0.02,"
          "afade=t=out:st=0.2:d=0.18[lob];"
          "[hib][lob]amix=inputs=2:normalize=0,"
          "volume=1.2[a]", 0.6)

    # 2) IMPACT — layered low thump + high crack (the "claim lands" beat)
    synth(OUT / "impact.wav",
          # low thump
          "sine=frequency=55:duration=0.3[thump];"
          "[thump]afade=t=in:st=0:d=0.005,"
          "afade=t=out:st=0.05:d=0.25[thumpe];"
          # high transient crack
          "anoisesrc=c=white:d=0.06:a=0.9[crack];"
          "[crack]highpass=frequency=2500,"
          "afade=t=in:st=0:d=0.001,"
          "afade=t=out:st=0.005:d=0.05[cracke];"
          "[thumpe][cracke]amix=inputs=2:normalize=0,"
          "volume=1.4[a]", 0.4)

    # 3) SLAM_CHECK — bouncy, springy slam (button slams in)
    synth(OUT / "slam_check.wav",
          # Mid-bright noise burst
          "anoisesrc=c=white:d=0.18:a=0.85[noise];"
          "[noise]bandpass=frequency=900:width_type=h:w=1400,"
          "afade=t=in:st=0:d=0.003,"
          "afade=t=out:st=0.04:d=0.13[nb];"
          # Subtle low-end body
          "sine=frequency=120:duration=0.18[body];"
          "[body]afade=t=in:st=0:d=0.005,"
          "afade=t=out:st=0.04:d=0.13[bodyf];"
          "[nb][bodyf]amix=inputs=2:weights=1 0.6:normalize=0,"
          "volume=1.2[a]", 0.22)

    # 4) SLAM_X — same recipe, slightly darker pitch (X feels more 'no')
    synth(OUT / "slam_x.wav",
          "anoisesrc=c=pink:d=0.18:a=0.85[noise];"
          "[noise]bandpass=frequency=550:width_type=h:w=900,"
          "afade=t=in:st=0:d=0.003,"
          "afade=t=out:st=0.04:d=0.13[nb];"
          "sine=frequency=85:duration=0.18[body];"
          "[body]afade=t=in:st=0:d=0.005,"
          "afade=t=out:st=0.04:d=0.13[bodyf];"
          "[nb][bodyf]amix=inputs=2:weights=1 0.7:normalize=0,"
          "volume=1.2[a]", 0.22)

    # 5) TICK_LOOP — clean woodblock-style tick every 0.2s for 2.5s
    parts = []
    for i in range(13):  # ticks at 0, 0.2, ..., 2.4
        # Each tick: short 3kHz transient + tiny noise click
        parts.append(
            f"sine=frequency=3200:duration=0.025,"
            f"afade=t=in:st=0:d=0.001,"
            f"afade=t=out:st=0.005:d=0.019,"
            f"adelay={i*200}|{i*200}"
        )
    filt = ";".join(f"{p}[t{i}]" for i, p in enumerate(parts))
    amix = "".join(f"[t{i}]" for i in range(13))
    filt += f";{amix}amix=inputs=13:normalize=0,volume=2.2[a]"
    synth(OUT / "tick_loop.wav", filt, 2.6)

    # 6) SUSPENSE_RISER — bomb-fuse buildup: accelerating ticks layered over
    # a sub-bass rumble that swells during the buildup. The rumble alone
    # already feels like impending detonation; the ticks ride on top.
    tick_times = [0.00, 0.22, 0.42, 0.60, 0.76, 0.90, 1.02, 1.12, 1.20,
                  1.26, 1.31, 1.35]
    tick_parts = []
    n_ticks = len(tick_times)
    for i, tt in enumerate(tick_times):
        is_final = (i == n_ticks - 1)
        freq = 2400 if is_final else 3200 + i * 40
        amp = 1.4 if is_final else 0.55 + 0.45 * (i / max(n_ticks - 1, 1))
        dur = 0.05 if is_final else 0.025
        delay_ms = int(tt * 1000)
        tick_parts.append(
            f"sine=frequency={freq}:duration={dur},"
            f"afade=t=in:st=0:d=0.001,"
            f"afade=t=out:st={dur*0.2:.3f}:d={dur*0.8:.3f},"
            f"volume={amp:.2f},"
            f"adelay={delay_ms}|{delay_ms}"
        )
    filt = ";".join(f"{p}[at{i}]" for i, p in enumerate(tick_parts))
    # Sub-bass rumble: 35Hz drone with slight tremolo, swelling in volume
    filt += (";sine=frequency=35:duration=1.5[rmb];"
             "[rmb]tremolo=f=4:d=0.4,"
             "afade=t=in:st=0:d=0.4,"
             "afade=t=out:st=1.3:d=0.2,"
             "volume=0.7[rmbe]")
    amix = "".join(f"[at{i}]" for i in range(n_ticks))
    filt += f";{amix}[rmbe]amix=inputs={n_ticks+1}:normalize=0,volume=1.8[a]"
    synth(OUT / "suspense_riser.wav", filt, 1.6)

    # 6b) EXPLOSION — sub-bass thump + mid crack + high sizzle. Lands at the
    # progress bar peak; intentionally hot and short.
    synth(OUT / "explosion.wav",
          # Sub-bass impact (sustains for 0.5s)
          "sine=frequency=42:duration=0.55[sub];"
          "[sub]afade=t=in:st=0:d=0.005,"
          "afade=t=out:st=0.1:d=0.45,"
          "volume=1.6[sube];"
          # Mid-range crack (filtered noise burst)
          "anoisesrc=c=brown:d=0.4:a=0.95[mid];"
          "[mid]bandpass=frequency=700:width_type=h:w=1400,"
          "afade=t=in:st=0:d=0.002,"
          "afade=t=out:st=0.04:d=0.36[mide];"
          # High sizzle (broadband, very short)
          "anoisesrc=c=white:d=0.18:a=0.8[hi];"
          "[hi]highpass=frequency=3500,"
          "afade=t=in:st=0:d=0.001,"
          "afade=t=out:st=0.02:d=0.16[hie];"
          # Combine
          "[sube][mide][hie]amix=inputs=3:weights=1.6 1.1 0.65:normalize=0,"
          "volume=1.3[a]", 0.6)

    # 7) POP — cartoon "boing" with downward freq bend
    synth(OUT / "pop.wav",
          "aevalsrc=exprs='sin(2*PI*(900-4500*t)*t)':c=stereo:d=0.1[bend];"
          "[bend]afade=t=in:st=0:d=0.005,"
          "afade=t=out:st=0.03:d=0.07,"
          "volume=1.5[a]", 0.12)

    # 8) DING — bell with three harmonics (fundamental + perfect fifth + octave)
    synth(OUT / "ding.wav",
          "sine=frequency=880:duration=0.7[h1];"
          "sine=frequency=1320:duration=0.7[h2];"
          "sine=frequency=1760:duration=0.7[h3];"
          "[h1][h2][h3]amix=inputs=3:weights=1 0.4 0.25:normalize=0,"
          "afade=t=in:st=0:d=0.005,"
          "afade=t=out:st=0.05:d=0.65,"
          "volume=1.1[a]", 0.75)

    print(f"Generated {len(list(OUT.glob('*.wav')))} SFX in {OUT.relative_to(REPO)}/")
    for p in sorted(OUT.glob("*.wav")):
        size_kb = p.stat().st_size // 1024
        print(f"  {p.name} ({size_kb}K)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
