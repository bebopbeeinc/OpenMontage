# Compose Director — Trivia Captain Pipeline

## When To Use

You are rendering the final mp4 by:

1. Transcribing `vo.wav` to word-level timestamps (`words.json`).
2. Running Remotion's `TriviaWithBg` composition with
   `showFactsOverlay=false` + `highlightColor=#D63B2F` against
   `bg.mp4` + `words.json` + `meta.json`.
3. Frame-reviewing the final mp4 and patching any caption drift in-turn
   (per user memory: always review renders before saying "ready").

## Prerequisites

| Layer | Resource | Purpose |
|---|---|---|
| Artifact | `projects/trivia-captain/<slug>/artifacts/edit_decisions.json` | bg + vo paths, render runtime |
| Asset | `projects/trivia-captain/<slug>/assets/video/bg.mp4` | Background |
| Asset | `projects/trivia-captain/<slug>/assets/audio/vo.wav` | Narration |
| Asset | `projects/trivia-captain/<slug>/assets/meta.json` | Remotion props bundle |
| Tool | `transcriber` (registry) | Whisper transcription |
| Runtime | Remotion (Node + `remotion-composer/`) | Render |
| Schema | `schemas/artifacts/render_report.schema.json` | Artifact validation |

## Process

### 1. Transcribe vo.wav

```python
from tools.tool_registry import registry
registry.discover()
t = registry._tools["transcriber"]
r = t.execute({
    "input_path": str(vo_path),
    "model": "small.en",
    "word_timestamps": True,
    "vad_filter": False,
    "beam_size": 5,
})
assert r.success, r.error
words = [
    {"word": s.get("text") or w.get("word"),
     "startMs": int(s["start"] * 1000),
     "endMs":   int(s["end"]   * 1000)}
    for seg in r.data["segments"]
    for w in seg.get("words", [seg])
]
(project / "assets" / "words.json").write_text(json.dumps(words, indent=2))
```

The exact transcriber output shape is documented in
`tools/analysis/transcriber.py`. Use `small.en` with `vad_filter=false`
and `beam_size=5`. Note that Archibald's mid-reel pause (the "short beat"
between fact and kicker) is ~0.5-2.0s — shorter than ellie's 1.5-3.0s
laugh break in trivia-reaction — so keep VAD off to avoid the transcriber
swallowing the surrounding words at that gap.

### 2. Render Remotion

The composition is `TriviaWithBg` (reused from trivia-short with
`showFactsOverlay=false`). Trivia-captain's locked props:

| Prop | Value |
|---|---|
| `highlightColor` | `#D63B2F` |
| `baseColor` | `#FFFFFF` |
| `fontSize` | `78` |
| `showFactsOverlay` | `false` |
| `mode` | `"Facts"` |
| `darkOverlay` | `0` |
| `videoSrc` | `<absolute path to bg.mp4>` |
| `words` | `<words.json contents>` |

Invocation pattern (mirrors trivia-short's compose stage):

```bash
cd remotion-composer
npx remotion render src/index-trivia.tsx TriviaWithBg \
  ../projects/trivia-captain/<slug>/renders/<slug>.mp4 \
  --props=../projects/trivia-captain/<slug>/assets/remotion_props.json
```

…where `remotion_props.json` is built from `meta.json.remotion_props`
plus the resolved `videoSrc` (absolute) and `words` (inline contents
of `words.json`).

If the existing trivia-short render driver (`scripts/trivia/render.py` or
similar) supports custom prop injection, prefer that. Otherwise, write
`remotion_props.json` at this stage and invoke `npx remotion render`
directly.

### 3. Frame Review (MANDATORY)

Per user memory: always extract frames + verify captions before saying
"ready". For trivia-captain the beats are at known offsets in
`meta.json.beat_offsets_ms`. Extract one frame per beat midpoint:

```bash
for beat in hook fact kicker; do
  midpoint_s=$(python -c "import json; m=json.load(open('projects/trivia-captain/<slug>/assets/meta.json')); o=m['beat_offsets_ms']['$beat']; print(o/1000.0 + 0.5)")
  ffmpeg -y -ss $midpoint_s -i projects/trivia-captain/<slug>/renders/<slug>.mp4 \
    -frames:v 1 projects/trivia-captain/<slug>/renders/_review_$beat.png
done
```

Read each PNG. Verify:

- Caption text matches the beat copy (allow Whisper minor variations)
- Warm-red pill (`#D63B2F`) is rendered on the active word, with white
  (`#FFFFFF`) text on the pill
- Caption is bottom-aligned, no clipping
- bg.mp4 frame shows Archibald in the expected acting beat
- No check/X buttons (showFactsOverlay=false)
- No progress bar (FactsOverlay disabled)

### 4. Auto-Fix In-Turn (MANDATORY)

Per user memory: fix defects in the same turn before signaling done.
Common defects + fixes:

| Defect | Fix |
|---|---|
| Caption word doesn't match VO | Patch words.json (target word + nearest timestamp); re-render |
| Caption drift > 200ms | Re-transcribe with `small.en` + `beam_size=5`; re-render |
| Warm-red pill not showing | Verify `remotion_props.json` has the right highlightColor; rebuild props; re-render |
| Check/X buttons visible | `showFactsOverlay=false` not propagated; fix props; re-render |
| bg.mp4 has audio | Re-run edit stage with `-an` ffmpeg flag |
| Caption clipping at bottom | Trim VO at end or extend bg duration |

Do NOT mark `frame_review_passed: true` if any defect remains. List the
defects in `render_report.metadata.defects[]`, fix, re-render, re-check.

### 5. Write render_report.json

```jsonc
{
  "schema_version": "0.1",
  "pipeline": "trivia-captain",
  "frame_review_passed": true,
  "output_path": "projects/trivia-captain/<slug>/renders/<slug>.mp4",
  "metadata": {
    "sheet_revision": "...",
    "slug": "<slug>",
    "render_engine": "remotion",
    "composition_id": "TriviaWithBg",
    "duration_s": 15.4,
    "encoding_profile": "h264 yuv420p crf=18",
    "review_frames": {
      "hook": "projects/trivia-captain/<slug>/renders/_review_hook.png",
      "fact": "projects/trivia-captain/<slug>/renders/_review_fact.png",
      "kicker": "projects/trivia-captain/<slug>/renders/_review_kicker.png"
    },
    "defects": [],
    "fixes_applied": []
  }
}
```

### 6. Update The Queue

```python
queue_row.update_cells(ws, row, status=queue_row.STATUS_READY_TO_PUBLISH)
```

### 7. Self-Review + Checkpoint

`human_approval_default: true`. Present:

- Path to the rendered `<slug>.mp4`
- The three review frame PNGs
- Duration
- A one-line summary of any defects-and-fixes
- The Queue!C status update

Wait for "go" before advancing to publish.

## What Not To Do

- Do not skip frame review. Always extract frames + verify captions.
- Do not signal "ready" without auto-fixing defects in the same turn.
- Do not switch render engine. `render_runtime: remotion` is locked.
- Do not edit the audio track at this stage. That's edit's job.
- Do not advance to publish without `frame_review_passed: true`.
