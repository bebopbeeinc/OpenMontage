# Question-Image Director - Trivia Images Pipeline

## When To Use

You are the asset director for stage 1 of the trivia-images pipeline. Your job
is to take one row of a question tab of the trivia-questions sheet (default
`1-100`) and produce the **question image** — the still that appears on the
trivia card for that question. This is also the image the next stage
(`answer_image`) will use as a reference when generating the answer image's
"same scene" remix.

`1-100` is the canonical tab layout. Only tabs carrying 1-100's header set
resolve — the legacy Brian-style tabs (`BrianOld`, `1-250`, …) and the `RN` tab
use different labels and are skipped by tab discovery. Never assume fixed
column letters — `scripts/trivia_images/sheet_schema.py` resolves each field to
its live column by header label (which also tolerates column inserts/reorders
within the layout), so this skill refers to columns by their field name, not
letter.

A single pipeline run = a single row, with slug `trivia-q-{N}` where `N` is the
row's **Number** value (header `#`). Batch runs are orchestrated by
the CLI wrapper `scripts/trivia_images/generate.py` which invokes this stage
once per row.

## Prerequisites

Columns are resolved by header label via `sheet_schema.py` (the listed labels
are what the resolver matches, not fixed letters):

| Layer | Resource | Purpose |
|---|---|---|
| Sheet | Number column (`#`) | Question number — used as slug `q{N}` |
| Sheet | completion column (`image complete`) | Question-image completion mark (✓). **Optional** — the 1-100 tab has no such column, so the mark is skipped and disk presence is the completion signal. |
| Sheet | Question-image-prompt column (`Question IMAGE Prompt`) | Question IMAGE prompt — input to OpenArt |
| Tool | `openart_image` | Registered BaseTool — provider=openart, runtime=HYBRID |
| Library | `scripts/trivia_images/library/` | Where the saved PNG lands |
| Schema | `schemas/artifacts/asset_manifest.schema.json` | Stage output contract |

## Process

### 1. Read The Row

Use the service-account credential at `~/.google/claude-sheets-sa.json` to read
the target row. The CLI/server resolve the read range from the live header rows
(`SheetSchema.max_index()`); don't hardcode an A1 range. Validate:

- **Number** is set — this is the slug. If blank, fail fast.
- **Question IMAGE prompt** is non-empty — without a prompt there's
  nothing to generate. Skip with reason `"no Q prompt"`.
- **completion column**, if the tab has one, is checked: if it already contains
  ✓ AND a matching file exists in `scripts/trivia_images/library/q{N}.<ext>`,
  skip with reason `"already complete"` unless the caller passed `force=true`.
  Tabs without a completion column (the `1-100` layout) rely on on-disk presence
  alone.

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

### 3. Optimize For The Game (keep both)

The game wants **512×384** (true 4:3) assets, small on disk, but the full-res
render is kept too. OpenArt returns a ~2400×1792 2K image; the tooling
(`scripts/trivia_images/image_optimize.py`) writes a 512×384 **lossless
optimized PNG** copy alongside the original — it never discards the original.

- **CLI** (`generate.py`): original at `library/q{N}.png`, resized copy at
  `library/resized/q{N}.png`.
- **Web server**: original (full-res) → `WIP/`; resized → `WIP/Resized/`.
  Approving moves both to `Question Images/` and `Question Images/Resized/` in
  lockstep. The UI shows the resized; the game reads `Question Images/Resized/`.

The full-res original is what the answer-remix references (best fidelity —
"kept just in case" is exactly this use).

### 4. Mark The Row

On success, write `✓` into the row's completion column. Skip this write when the
caller passed `no_mark=true` (matches the existing `generate.py --no-mark` flag)
**or when the tab has no completion column** (the `1-100` layout) — the schema lookup
raises for the missing field and the write is tolerated/skipped.

The driver may rewrite the extension to match the CDN's served format
(`.webp` / `.jpg` / `.png`); use the path(s) in `result.data["saved_paths"]` as
authoritative. After the optimize step (above) the library holds the full-res
original at `q{N}.png` plus the 512×384 copy at `resized/q{N}.png`.

### 5. Produce The asset_manifest

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
      "prompt": "<question prompt text>",
      "model": "Nano Banana Pro",
      "provider": "openart",
      "format": "png",
      "subtype": "question",
      "generation_summary": "OpenArt /suite/create-image/nano-banana-pro, 4:3 2K original + 512×384 lossless-PNG resized copy"
    }
  ],
  "total_cost_usd": 0.0,
  "metadata": {
    "row": <int>,
    "slug": "q{N}",
    "sheet_id": "1Kh9Ai9-sKyyK1q24jVkQqeIz-Y-0rdNVIjPc2EF8hPk",
    "sheet_tab": "<the tab this row came from, e.g. 1-100>"
  }
}
```

Use the actual saved filename's extension under `path` and `format`. Validate
against the schema before saving.

## Review Focus (Self-Review Before Checkpoint)

Run these checks before marking the stage complete. They mirror
`pipeline_defs/trivia-images.yaml` -> `stages.question_image.review_focus`:

- Row number is present and the row has a non-empty Question IMAGE prompt.
- Original landed at `scripts/trivia_images/library/q{N}.png` and the resized
  copy at `scripts/trivia_images/library/resized/q{N}.png`.
- `asset_manifest` records model, provider, prompt, and the slug `q{N}`.
- The resized copy opens without error and is exactly **512×384** (the game
  size). Quick check: `ffprobe -v error -select_streams v -show_entries
  stream=width,height resized/q{N}.png` → `width=512` / `height=384`.

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
- Write the prompt itself. The prompt comes from the sheet (Question IMAGE prompt column), authored
  manually today. A future `prompts` stage may automate this.
- Pick a different model. Nano Banana Pro is the standard. Switching models
  needs user approval (Decision Communication Contract).
