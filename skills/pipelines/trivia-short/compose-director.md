# Compose Director - Trivia Short Pipeline

## When To Use

`bg.mp4` exists from the edit stage and you need to (1) transcribe it,
(2) render the Remotion `TriviaWithBg` composition with TikTok-style word
highlighting, and (3) **review the rendered output** before signaling done.

## Prerequisites

| Layer | Resource | Purpose |
|---|---|---|
| Artifact | `projects/<slug>/artifacts/edit_decisions.json` | bg.mp4 path |
| Asset | `projects/<slug>/assets/video/bg.mp4` | Source video w/ audio |
| Script | `scripts/common/transcribe.py` | faster-whisper transcription |
| Comp | `remotion-composer/src/TriviaWithBg.tsx` | The renderer |
| Schema | `schemas/artifacts/render_report.schema.json` | Artifact validation |
| Memory | `feedback_review_renders` | Frame extraction is mandatory |
| Memory | `feedback_review_autofix` | Fix defects in-turn, don't list and ask |
| Upstream | `feedback_router.py` | If `feedback.json` exists, the web pipeline runs the router at Phase 0 — the resulting `feedback_plan.json` may already encode caption fixes you'd otherwise apply by hand. Check it before patching `words.json` manually. |

## Process

### 1. Transcribe

```bash
source .venv/bin/activate
python scripts/common/transcribe.py <slug>
```

Outputs:
- `projects/<slug>/artifacts/words.json` — source of truth for captions
- `remotion-composer/public/words.json` — staged for renderer
- `remotion-composer/public/bg.mp4` — staged for renderer

Brand-token post-processing runs automatically (see `BRAND_TOKENS` in the
script). The script reports the fix count — record it in the render report.
If you see brand tokens in the source that aren't in `BRAND_TOKENS`, flag for
the user to add them — don't silently let them through lowercased.

### 2. Render

```bash
cd remotion-composer
npx remotion render src/index-trivia.tsx TriviaWithBg \
  ../projects/<slug>/renders/final_with_bg.mp4
```

Wall time: 3-5 minutes for a 14.4s render at 1080x1920 / 30fps. Announce
before running.

If Remotion fails:
- **Asset 404 (`public/bg.mp4` missing)**: transcribe step didn't stage it —
  re-run step 1.
- **Composition error in TriviaWithBg.tsx**: read the error, fix the
  composition or words.json, re-render. Do not work around with FFmpeg
  subtitles — that violates the TikTok green-pill design.
- **OOM**: lower concurrency in the Remotion config or render in chunks.

### 3. MANDATORY: Frame Review

This is non-negotiable. Per user memory, the render is not "ready for review"
until you have visually verified captions on frames.

For each VO beat (extract from `edit_decisions.metadata.vo_windows`), extract
a frame and look at it:

```bash
ffmpeg -y -ss <t> -i projects/<slug>/renders/final_with_bg.mp4 \
  -frames:v 1 projects/<slug>/review/frame_<t>.png
```

Beats to check at minimum:
- Hook beat (~1.5s): burned hook caption present in expected style
- Claim mid (~6.0s): TikTok pill on the active word, no overlap with check/X
  button art in the source
- Resolution (~11.0s): captions transition cleanly between pages
- CTA (~12.5s): CTA fully visible, no clipping at the bottom

For each frame, verify:
- Active word has the green pill (`#22E88A`)
- Inactive words are white with dark stroke
- Padding is consistent (no layout shift when the active word changes)
- Caption sits below the check/X button art (paddingBottom: 180)
- No drift between caption text and audio (cross-check against words.json)

### 4. MANDATORY: Auto-Fix

Per user memory: if you find a defect in frame review, fix it in this turn.
Do not list defects and ask the user to choose — make the call and re-render.

Common defects + fixes:

| Defect | Fix | Re-render? |
|---|---|---|
| Wrong word capitalized | Patch words.json brand token + extend `BRAND_TOKENS` in transcribe.py | yes |
| Caption/audio drift > 200ms at one timestamp | Patch the word's `start_ms` / `end_ms` in words.json | yes |
| Orphan 1-word page | Adjust `MAX_WORDS` constant or accept (rebalancer should have caught it) | yes if changed |
| Caption clipped at bottom | Adjust paddingBottom in TriviaWithBg.tsx (rare; usually a source-art issue) | yes |
| Active word pill missing | Compositor bug — read TriviaWithBg.tsx, fix, re-render | yes |

If after one auto-fix pass a defect persists, that's when you ask the user.

### 5. Write Render Report

Per `schemas/artifacts/render_report.schema.json`. Trivia-specific shape:

```jsonc
{
  "metadata": {
    "output_path": "projects/<slug>/renders/final_with_bg.mp4",
    "composition": "TriviaWithBg",
    "duration_s": 14.4,
    "resolution": "1080x1920",
    "transcription": {
      "model": "faster-whisper (CPU int8)",
      "brand_token_fixes": 2,
      "words_path": "projects/<slug>/artifacts/words.json"
    },
    "frame_review_passed": true,
    "frame_review_paths": [
      "projects/<slug>/review/frame_1.5.png",
      "projects/<slug>/review/frame_6.0.png",
      "projects/<slug>/review/frame_11.0.png",
      "projects/<slug>/review/frame_12.5.png"
    ],
    "auto_fixes_applied": []
  }
}
```

### 6. Checkpoint

`human_approval_default: true`. Present:
- Output path
- Duration / resolution
- Frame review status + the 4 frame paths
- Any auto-fixes applied
- Sample stills (if the harness can render images, embed them)

Wait for the user's "ship it" before advancing to publish.

## What Not To Do

- Do not say "ready for review" without frame extraction. The user memory rule
  was added because past Claudes did this and burned the user with broken
  captions.
- Do not silently swap to FFmpeg burned subtitles when Remotion has issues —
  that breaks the design system.
- Do not list defects without fixing them. Auto-fix is mandatory per memory.
- Do not skip the brand-token check. If a brand appears in the topic but
  isn't in `BRAND_TOKENS`, the captions will be wrong.
