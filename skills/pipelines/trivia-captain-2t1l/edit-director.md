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
2. **Transcribes** the native VO to word-level timestamps via
   `scripts/common/transcribe.py` (model `small.en`, `local_files_only` so it
   never hits the HF Hub user-agent bug) → `artifacts/words.json`. These feed the
   bottom word-by-word karaoke captions.
3. Writes `assets/props.json`:
   `{ themeName, place, title:"2 TRUTHS, 1 LIE", claims:[{label, revealAtSec}×3], durationS }`
   — consumed by the `TriviaTwoTruthsK3` composition. `claims` are only used by the
   legacy fact-bar layout (`minimal=false`); the live render uses captions, not pills.

## Verify
- `bg.mp4` is 1080×1920 with an audio track.
- `artifacts/words.json` exists and the printed transcript reads correctly
  (brand tokens like "Captain" are cased; numbers merged).

No TTS, no music bed added here (music is baked in the clip). No new overlay assets.
