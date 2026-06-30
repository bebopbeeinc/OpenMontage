# Compose Director — Trivia Captain Reaction Pipeline

## When To Use

You are rendering the final mp4 by:

1. Transcribing the avatar clip's audio to word-level timestamps (`words.json`).
2. Running Remotion's `TriviaWithBg` composition with
   `showFactsOverlay=false` + `highlightColor=#C04FE0` (warm purple) against
   `bg.mp4` + `words.json` + `meta.json`.
3. Frame-reviewing the final mp4 and patching any caption drift in-turn
   (per user memory: always review renders before saying "ready").

## Prerequisites

| Layer | Resource | Purpose |
|---|---|---|
| Artifact | `projects/trivia-captain-reaction/<slug>/artifacts/edit_decisions.json` | bg + vo paths, render runtime |
| Asset | `projects/trivia-captain-reaction/<slug>/assets/video/bg.mp4` | Background (Seedance clip, audio inline) |
| Asset | `projects/trivia-captain-reaction/<slug>/assets/meta.json` | Remotion props bundle |
| Tool | `transcriber` (registry) | Whisper transcription |
| Runtime | Remotion (Node + `remotion-composer/`) | Render |
| Schema | `schemas/artifacts/render_report.schema.json` | Artifact validation |

## Process

### 1. Transcribe The Avatar Audio

The voice rides inside `bg.mp4` (Seedance native). Transcribe it to
word-level timestamps. Per the playbook's `motion.transcribe_recommendation`,
use a model strong enough to catch words spoken through the laugh:
`small.en`, `vad_filter=false`, `beam_size=5`. `base.en` drops words during
the recovery line.

```python
from tools.tool_registry import registry
registry.discover()
t = registry._tools["transcriber"]
r = t.execute({
    "input_path": str(bg_path),
    "model": "small.en",
    "word_timestamps": True,
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
`tools/analysis/transcriber.py`.

### 2. Render Remotion

The composition is `TriviaWithBg` (reused from trivia-short with
`showFactsOverlay=false`). This pipeline's locked props:

| Prop | Value |
|---|---|
| `highlightColor` | `#C04FE0` (warm purple) |
| `baseColor` | `#FFFFFF` |
| `fontSize` | `78` |
| `showFactsOverlay` | `false` |
| `mode` | `"Facts"` |
| `darkOverlay` | `0` |
| `videoSrc` | `<absolute path to bg.mp4>` |
| `words` | `<words.json contents>` |

The active word sits on the warm-purple pill with the comp's default dark
(`#0a061e`) active-text color, which reads cleanly on `#C04FE0` — no comp
change needed.

Invocation pattern (mirrors the sister reaction pipeline's compose stage):

```bash
cd remotion-composer
npx remotion render src/index-trivia.tsx TriviaWithBg \
  ../projects/trivia-captain-reaction/<slug>/renders/<slug>.mp4 \
  --props=../projects/trivia-captain-reaction/<slug>/assets/remotion_props.json
```

…where `remotion_props.json` is built from `meta.json` (which already
carries `highlight_color=#C04FE0` from assemble.py) plus the resolved
`videoSrc` (absolute) and `words` (inline contents of `words.json`).

### 3. Frame Review (MANDATORY)

Per user memory: always extract frames + verify captions before saying
"ready". Extract one frame per beat midpoint:

```bash
for beat in hook fact kicker; do
  midpoint_s=$(python -c "import json; m=json.load(open('projects/trivia-captain-reaction/<slug>/assets/meta.json')); o=m.get('beat_offsets_ms',{}).get('$beat',0); print(o/1000.0 + 0.5)")
  ffmpeg -y -ss $midpoint_s -i projects/trivia-captain-reaction/<slug>/renders/<slug>.mp4 \
    -frames:v 1 projects/trivia-captain-reaction/<slug>/renders/_review_$beat.png
done
```

Read each PNG. Verify:

- Caption text matches the beat copy (allow Whisper minor variations)
- The warm-purple pill (`#C04FE0`) is rendered on the active word
- Caption is bottom-aligned, no clipping
- bg.mp4 frame shows the Captain in the expected acting beat
- No check/X buttons (showFactsOverlay=false)
- No progress bar (FactsOverlay disabled)

### 4. Auto-Fix In-Turn (MANDATORY)

Per user memory: fix defects in the same turn before signaling done.

| Defect | Fix |
|---|---|
| Caption word doesn't match VO | Patch words.json (target word + nearest timestamp); re-render |
| Caption drift > 200ms | Re-transcribe with `small.en`; re-render |
| Warm-purple pill not showing | Verify `remotion_props.json` has `highlightColor=#C04FE0`; rebuild props; re-render |
| Check/X buttons visible | `showFactsOverlay=false` not propagated; fix props; re-render |
| bg.mp4 has no audio | Re-run edit stage; verify Seedance audio_on was true |
| Caption clipping at bottom | Trim VO at end or extend bg duration |

Do NOT mark `frame_review_passed: true` if any defect remains. List the
defects in `render_report.metadata.defects[]`, fix, re-render, re-check.

### 5. Write render_report.json

```jsonc
{
  "schema_version": "0.1",
  "pipeline": "trivia-captain-reaction",
  "frame_review_passed": true,
  "output_path": "projects/trivia-captain-reaction/<slug>/renders/<slug>.mp4",
  "metadata": {
    "sheet_revision": "...",
    "slug": "<slug>",
    "render_engine": "remotion",
    "composition_id": "TriviaWithBg",
    "duration_s": 15.4,
    "encoding_profile": "h264 yuv420p crf=18",
    "review_frames": {
      "hook": "projects/trivia-captain-reaction/<slug>/renders/_review_hook.png",
      "fact": "projects/trivia-captain-reaction/<slug>/renders/_review_fact.png",
      "kicker": "projects/trivia-captain-reaction/<slug>/renders/_review_kicker.png"
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
- The queue Status update

Wait for "go" before advancing to publish.

## What Not To Do

- Do not skip frame review. Always extract frames + verify captions.
- Do not signal "ready" without auto-fixing defects in the same turn.
- Do not switch render engine. `render_runtime: remotion` is locked.
- Do not edit the audio track at this stage. That's edit's job.
- Do not advance to publish without `frame_review_passed: true`.
