# Edit Director — Trivia Captain Pipeline

## When To Use

You are running `scripts/trivia_captain/assemble.py` to produce two
inputs to the Remotion compose stage:

1. `projects/trivia-captain/<slug>/assets/video/bg.mp4` — the avatar clip normalized to
   1080x1920 / 30fps / h264 yuv420p, **with audio preserved**. Seedance
   2.0 generated the dialogue lip-synced inside this clip; we do NOT
   strip the audio track.
2. `projects/trivia-captain/<slug>/assets/meta.json` — Remotion-facing config:
   `videoSrc`, `remotion_props`, `vo_text`.

…plus the canonical `edit_decisions.json` artifact.

There is no TTS step in this pipeline. The narration lives inside the
Seedance clip. The compose stage transcribes that audio track to
produce word-level timestamps for the warm-red-pill captions.

## Prerequisites

| Layer | Resource | Purpose |
|---|---|---|
| Artifact | `projects/trivia-captain/<slug>/artifacts/brief.json` | Slug, Day, sheet revision |
| Artifact | `projects/trivia-captain/<slug>/artifacts/script.json` | Beat copy (for compose to align captions) |
| Artifact | `projects/trivia-captain/<slug>/artifacts/asset_manifest.json` | Avatar clip path |
| Library | `scripts/trivia_captain/library/clips/<slug>.mp4` | Seedance clip with native audio |
| Script | `scripts/trivia_captain/assemble.py` | Orchestrator (this stage's tool) |
| Schema | `schemas/artifacts/edit_decisions.schema.json` | Artifact validation |

## Process

### 1. Verify Audio Is Present

Before running assemble.py, confirm the Seedance clip has an audio track:

```bash
ffprobe -v error -select_streams a:0 -show_entries stream=codec_type \
  -of default=noprint_wrappers=1:nokey=1 \
  scripts/trivia_captain/library/clips/<slug>.mp4
# Expected output: audio
```

If no audio: the clip was generated with `audio_on=false` by mistake.
Go back to asset and regenerate. Don't try to substitute a separate VO
file — the lip-sync would not match.

### 2. Run assemble.py

```bash
python scripts/trivia_captain/assemble.py <slug>
```

What it does:

1. Locate the avatar clip in the library; fall back to `_v1` if canonical missing.
2. Verify the clip has an audio stream.
3. `ffmpeg` normalize: scale + crop to 1080x1920, 30 fps, h264 yuv420p,
   AAC audio at 192k/44.1k. Audio is preserved as-is from Seedance.
4. Write `meta.json` and `edit_decisions.json`.

No TTS. No concat. No loudnorm. The whole stage is ~5 seconds of ffmpeg.

### 3. Verify Outputs

- `bg.mp4` exists; `ffprobe` reports 1080x1920, 30fps, ~15s, with audio
- `meta.json` exists; `remotion_props.highlightColor == "#D63B2F"`,
  `audio_source == "seedance_native"`
- `edit_decisions.json.render_runtime == "remotion"`

### 4. Self-Review + Checkpoint

`human_approval_default: false` — auto-proceed to compose. Review focus:

- bg.mp4 has audio track (a:0 codec_type=audio)
- bg.mp4 ffprobes clean at 1080x1920/30fps
- No music bed, no SFX
- meta.json contains the locked Remotion props
- render_runtime locked to "remotion"

## What Not To Do

- Do not strip the audio track. The Seedance voice rides inside bg.mp4.
- Do not call a TTS provider. No ElevenLabs, no Piper.
- Do not add music. Reference has none.
- Do not loudness-normalize the Seedance audio. Seedance handles its own mix.
- Do not switch render_runtime. Locked to remotion in this pipeline.
