# Trivia Captain 2T1L — Asset Director

**Stage:** `assets` → the single 15s Captain clip. Human-approval default: **false**
(auto-proceed if checks pass).

## Command
```
python scripts/trivia_captain_2t1l/openart_generate.py <slug> [--headless] [--force]
```
Reads Queue!N (prompt), drives OpenArt / Seedance 2.0 — **single 15s clip, NO
reference image** (2T1L has no in-camera sign), character "Captain Archibald",
native audio (VO + in-prompt music), 480p 9:16. Applies a tail-fade to mask
Seedance's end-of-clip audio artifact. Output: `assets/video/clip.mp4`.

Announce before firing (paid): OpenArt · Seedance 2.0 · single 15s clip · 480p.

## Manual fallback
If the driver is flaky, generate in the OpenArt web UI from Queue!N and drop the
mp4 at `projects/trivia-captain-2t1l/<slug>/assets/video/clip.mp4`.

## Verify (before edit)
- `clip.mp4` exists, ~15s, 9:16, **has an audio track** (ffprobe a:0).
- VO matches the script — the numbered facts ("one/two/three") land.
- **Captain ON-MODEL (mandatory check):** red beret, blue chunky glasses, full white
  beard, cream cable-knit. If you see a generic person, the saved "Captain Archibald"
  character didn't attach — re-roll. (This is why generation runs HEADED, not
  `--headless`: headless dropped the character.)
- **No finger counting** (dropped — Seedance can't sync an exact count). He gestures
  naturally; the on-screen banners carry the count. Don't re-add finger-count direction.
- Bright festive destination set; **no sign/text in frame** (overlays come in post).
- Voice should sound like the same Captain every episode (locked VOICE spec); flag
  if it drifts badly.

If the clip is wrong, re-roll with `--force` (or revise the prompt and re-run script).
