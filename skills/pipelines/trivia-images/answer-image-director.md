# Answer-Image Director - Trivia Images Pipeline

## When To Use

You are the asset director for stage 2 of the trivia-images pipeline. The
question image already exists (stage 1 produced it at
`scripts/trivia_images/library/q{N}.png`). Your job is to generate the
**answer image** for the same row, using the question image as a visual
reference so both share the same environment.

The col R prompt on the sheet is intentionally written as a "Same Scene — STYLE:
..." prompt that **depends on** the question image being attached as a reference.
Submitting the col R prompt without the reference image will produce an image
that does not visually match the question image. That is a stage failure.

## Prerequisites

| Layer | Resource | Purpose |
|---|---|---|
| Artifact | `projects/trivia-q-{N}/artifacts/asset_manifest.json` | Stage 1 output — contains the question image path |
| Library | `scripts/trivia_images/library/q{N}.<ext>` | The reference image file |
| Sheet | `Brian` tab, col R | Answer IMAGE prompt — input to OpenArt |
| Tool | `openart_image` | Registered BaseTool — supports `reference_image_path` |
| Schema | `schemas/artifacts/asset_manifest.schema.json` | Stage output contract |

## Process

### 1. Resolve The Reference Image

Read `projects/trivia-q-{N}/artifacts/asset_manifest.json` from stage 1. Find
the asset with `scene_id == "q{N}"` and `subtype == "question"`. Verify the
file at `asset.path` exists on disk. If missing, escalate as a blocker — do
not regenerate the question image silently (that would create drift). Send
back to stage 1.

### 2. Read The Row

Use the service-account credential at `~/.google/claude-sheets-sa.json` to read
the answer-image prompt from `Brian!R{row}`. Validate:

- **Column R is non-empty.** If blank, the prompt was never authored. Either
  ask the user to fill it in or invoke the future `prompts` stage (when it
  exists). Do not invent prompts.
- **Column R starts with "Same Scene"** as a quick sanity check that the
  prompt was written for the reference-image flow. If it doesn't, warn the
  user — submitting a non-"Same Scene" prompt with a reference is still
  technically valid but may not produce the intended environment match.

### 3. Skip If Already Done

If `scripts/trivia_images/library/q{N}_answer.<ext>` already exists, skip
with reason `"already complete"` unless the caller passed `force=true`. The
filesystem is the source of truth for answer-image completion — no sheet
column tracks this today.

### 4. Call openart_image With The Reference

```python
from tools.tool_registry import registry
registry.discover()
tool = registry._tools["openart_image"]

result = tool.execute({
    "prompt": row_col_r,
    "model": "Nano Banana Pro",
    "aspect": "4:3",
    "resolution": "2K",
    "output_path": f"scripts/trivia_images/library/q{N}_answer.jpg",
    "reference_image_path": question_image_path,
    "headless": False,
})
```

Announce the call before executing it: state the tool name, provider, model,
that this is a reference-image submission (not a plain text-to-image call),
and the reference path.

### 5. Produce The asset_manifest

Write the updated `projects/trivia-q-{N}/artifacts/asset_manifest.json`
containing **both** images (carry forward the question image entry from
stage 1, append the answer image entry):

```json
{
  "version": "1.0",
  "assets": [
    {
      "id": "q{N}",
      "type": "image",
      "path": "scripts/trivia_images/library/q{N}.png",
      "source_tool": "openart_image",
      "scene_id": "q{N}",
      "subtype": "question",
      "prompt": "<col Q text>",
      "model": "Nano Banana Pro",
      "provider": "openart",
      "format": "png",
      "generation_summary": "OpenArt /suite/create-image/nano-banana-pro, 4:3 2K"
    },
    {
      "id": "q{N}_answer",
      "type": "image",
      "path": "scripts/trivia_images/library/q{N}_answer.png",
      "source_tool": "openart_image",
      "scene_id": "q{N}_answer",
      "subtype": "answer",
      "prompt": "<col R text>",
      "model": "Nano Banana Pro",
      "provider": "openart",
      "format": "png",
      "generation_summary": "OpenArt same-scene remix, ref=scripts/trivia_images/library/q{N}.png, 4:3 2K"
    }
  ],
  "total_cost_usd": 0.0,
  "metadata": {
    "row": <int>,
    "slug": "q{N}",
    "sheet_id": "1Kh9Ai9-sKyyK1q24jVkQqeIz-Y-0rdNVIjPc2EF8hPk",
    "sheet_tab": "Brian"
  }
}
```

Validate against the schema before saving.

## Review Focus (Self-Review Before Checkpoint)

Mirrors `pipeline_defs/trivia-images.yaml` -> `stages.answer_image.review_focus`:

- Question image `q{N}.<ext>` exists and was passed to OpenArt as the
  reference / source image.
- Col R prompt is the "Same Scene" variant — describes the answer scene while
  sharing the environment of col Q.
- Output landed at `scripts/trivia_images/library/q{N}_answer.<ext>`.
- `asset_manifest` accumulates BOTH images so the downstream trivia-short
  pipeline can read them as a pair.
- Visual continuity check: open both images side by side. The environment,
  lighting, and palette should match. If the answer image looks like a
  different scene, the reference was not attached correctly — this is a
  critical finding.

## Known Constraints

- **Driver is headed on first run** (manual OpenArt login). Subsequent runs
  reuse `.playwright/openart-state.json` and can be `headless=True`.
- **Reference uploads add ~3-10s** to the per-image generation time on top of
  the model's own 30-90s generation window. Total wall-clock budget per
  answer image: budget ~90s.
- **The CDN extension of the saved file may not match `output_path`.** The
  driver rewrites the extension to match what OpenArt serves (often `.png`).
  Use the path(s) returned by the tool as authoritative.
- **A non-"Same Scene" col R prompt is still technically valid** with a
  reference image — the model just may not honor the environment-preservation
  intent. Warn the user when the prompt doesn't start with "Same Scene".

## What This Stage Does NOT Do

- Re-generate the question image. If the question image is wrong, send back
  to stage 1.
- Author the col R prompt. The prompt comes from the sheet. A future
  `prompts` stage may automate this.
- Switch models. Nano Banana Pro is the standard for environment-preserving
  remixes on OpenArt.
