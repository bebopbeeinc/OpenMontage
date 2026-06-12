# Trivia Captain 2T1L — Edit Director

**Stage:** `edit` → normalize the clip + compute the per-row overlay props.
Human-approval default: **false** (auto-proceed).

## Command
```
python scripts/trivia_captain_2t1l/assemble.py <slug>
```
What it does:
1. Normalizes `assets/video/clip.mp4` → `assets/video/bg.mp4` (1080×1920, h264
   CRF 18, AAC 192k; audio preserved).
2. **Transcribes** the native VO (Whisper) to find when "one / two / three" are
   spoken → those become each fact banner's `revealAtSec` (so banners pop exactly
   as the Captain counts). Falls back to even spacing `[2.0, 5.0, 7.7]` if the
   number words aren't found.
3. Writes `assets/props.json`:
   `{ themeName, place, title:"2 TRUTHS, 1 LIE", claims:[{label, revealAtSec}×3], durationS }`
   — consumed by the `TriviaTwoTruthsK3` composition.

## Verify
- `bg.mp4` is 1080×1920 with an audio track.
- `props.json` has 3 claims with **monotonically increasing** reveal times and the
  labels from the Queue (J/K/L).
- Reveal times look sane (within the 0–12s speaking window).

No TTS, no music bed added here (music is baked in the clip). No new overlay assets.
