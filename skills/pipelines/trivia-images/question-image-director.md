# Question-Image Director - Trivia Images Pipeline

## When To Use

You are the asset director for stage 1 of the trivia-images pipeline. Your job
is to take one row of the `Brian` tab of the trivia-questions sheet and produce
the **question image** — the still that appears on the trivia card for that
question. This is also the image the next stage (`answer_image`) will use as a
reference when generating the answer image's "same scene" remix.

A single pipeline run = a single row, with slug `trivia-q-{N}` where `N` is the
value of column C (Number). Batch runs are orchestrated by the CLI wrapper
`scripts/trivia_images/generate.py` which invokes this stage once per row.

## Prerequisites

| Layer | Resource | Purpose |
|---|---|---|
| Sheet | `Brian` tab, col C | Question number — used as slug `q{N}` |
| Sheet | `Brian` tab, col D | Question-image completion mark (✓) |
| Sheet | `Brian` tab, col Q | Question IMAGE prompt — input to OpenArt |
| Tool | `openart_image` | Registered BaseTool — provider=openart, runtime=HYBRID |
| Library | `scripts/trivia_images/library/` | Where the saved PNG lands |
| Schema | `schemas/artifacts/asset_manifest.schema.json` | Stage output contract |

## Process

### 1. Read The Row

Use the service-account credential at `~/.google/claude-sheets-sa.json` to read
the target row from `Brian!A{row}:R{row}`. Validate:

- **Column C (Number)** is set — this is the slug. If blank, fail fast.
- **Column Q (Question IMAGE prompt)** is non-empty — without a prompt there's
  nothing to generate. Skip with reason `"no Q prompt"`.
- **Column D** is checked: if it already contains ✓ AND a matching file exists
  in `scripts/trivia_images/library/q{N}.<ext>`, skip with reason
  `"already complete"` unless the caller passed `force=true`.

### 2. Call openart_image

Use the registered tool, not the driver directly. The tool wraps the Playwright
driver and surfaces it through `image_selector` for future routing flexibility.

```python
from tools.tool_registry import registry
registry.discover()
tool = registry._tools["openart_image"]

result = tool.execute({
    "prompt": row_col_q,
    "model": "Nano Banana Pro",       # the current trivia-images default
    "aspect": "4:3",
    "resolution": "2K",
    "output_path": f"scripts/trivia_images/library/q{N}.jpg",
    "headless": False,                 # headed for first-run login
})
```

Announce the call before executing it (per the Decision Communication Contract):
state the tool name, provider (`openart`), model, and that this is a single
sample call (not a batch).

If `result.success` is False, escalate per "Escalate Blockers Explicitly":

- What was attempted (model + prompt summary).
- What failed (the `result.error` text).
- Whether it's auth (signed-out OpenArt), rate-limit, or a driver bug.
- Options: retry, switch model, fall back to a different `image_generation`
  provider (`image_selector` can route).

Do not silently swap to a different provider — Nano Banana Pro is the trivia-
short visual standard. Provider swaps need user approval.

### 3. Mark The Row

On success, write `✓` into `Brian!D{row}`. Skip this write when the caller
passed `no_mark=true` (matches the existing `generate.py --no-mark` flag).

The actual saved filename may differ from the requested `output_path` because
the driver may rewrite the extension to match the CDN's served format
(`.webp` / `.jpg` / `.png`). Use the path(s) returned in
`result.data["saved_paths"]` as authoritative.

### 4. Produce The asset_manifest

Write `projects/trivia-q-{N}/artifacts/asset_manifest.json`:

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
      "prompt": "<col Q text>",
      "model": "Nano Banana Pro",
      "provider": "openart",
      "format": "png",
      "subtype": "question",
      "generation_summary": "OpenArt /suite/create-image/nano-banana-pro, 4:3 2K"
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

Use the actual saved filename's extension under `path` and `format`. Validate
against the schema before saving.

## Review Focus (Self-Review Before Checkpoint)

Run these checks before marking the stage complete. They mirror
`pipeline_defs/trivia-images.yaml` -> `stages.question_image.review_focus`:

- Row number is present and the row has a non-empty col Q prompt.
- Output landed at `scripts/trivia_images/library/q{N}.<ext>`.
- `asset_manifest` records model, provider, prompt, and the slug `q{N}`.
- Image opens without error and matches the requested aspect (4:3). Quick
  check: `ffprobe -v error -select_streams v -show_entries stream=width,height
  <path>` — width/height ratio should be 4:3 within ~1%.

Critical findings → fix in-turn. Suggestions → note and proceed.

## Known Constraints

- The driver is **headed on first run** because OpenArt requires manual login.
  Subsequent runs reuse the saved storage state at
  `.playwright/openart-state.json` and can be `headless=True`.
- The driver is **one prompt at a time** — high-volume batches accumulate
  wall-clock time. ~60-90s per Nano Banana Pro image.
- OpenArt billing is subscription-based on the user's account; the tool reports
  `cost_usd=0` per call. This is correct for budgeting but does not mean the
  call is free.

## What This Stage Does NOT Do

- Generate the **answer image** — that's `answer_image` stage.
- Write the prompt itself. The prompt comes from the sheet (col Q), authored
  manually today. A future `prompts` stage may automate this.
- Pick a different model. Nano Banana Pro is the standard. Switching models
  needs user approval (Decision Communication Contract).
