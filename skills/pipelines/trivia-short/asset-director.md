# Asset Director - Trivia Short Pipeline

## When To Use

You are producing the three OpenArt video clips (hook 3s / body 8s / closer 4s)
that feed the edit stage. The brief specifies mode + segment prompts; you
choose between two production modes declared in `pipeline_defs/trivia-short.yaml`:

- `manual_openart` — the human generates the body + closer clips in OpenArt,
  drops the mp4s into `scripts/trivia/library/bodies/` and `scripts/trivia/library/closers/`,
  and writes the filenames into the **Body Filename** and **Closer Filename**
  cells. You wait. The reaction segment is already resolved at idea time
  (Reaction Filename VLOOKUP).
- `automated_openart` — `scripts/trivia/openart_driver.py` drives the OpenArt
  UI via Playwright, drops each mp4 into the matching library directory, and
  writes the filename back to the Body Filename / Closer Filename cells.

## Prerequisites

| Layer | Resource | Purpose |
|---|---|---|
| Artifact | `projects/<slug>/artifacts/brief.json` | Mode, slug, row |
| Artifact | `projects/<slug>/artifacts/script.json` | Per-segment prompts + VO copy |
| Script | `scripts/trivia/openart_driver.py` | Playwright driver |
| Script | `scripts/trivia_images/openart_image_driver.py` | Image variant (reaction) |
| Sheet | Reaction / Body / Closer Filename columns | Local filenames. Reaction is a VLOOKUP from the picker; body/closer are human-pasted |
| Schema | `schemas/artifacts/asset_manifest.schema.json` | Artifact validation |

## Process

### 1. Pick The Production Mode

Default to `manual_openart` unless the user explicitly opts into automation
(e.g. "drive OpenArt for me", "run the full batch"). Reasons to stay manual:

- The user is at the keyboard and prefers control over prompt iteration
- The automated driver is broken or rate-limited (check
  `scripts/trivia/openart_driver.py` recent runs)

Announce the chosen mode before doing any work.

### 2. Verify Segment Filenames Exist In The Sheet

The sheet is the source of truth for clip references. After the 2026-05
cleanup, every segment is filename-only — Drive URL fallback columns
(S/W/AB) were removed. For each segment, check the row has a filename:

| Segment | Filename column | Library directory | Source of the filename |
|---|---|---|---|
| hook (reaction) | Reaction Filename | `scripts/trivia/library/reactions/` | Clips catalog — picked at idea via `pick_reactions_llm.py` (VLOOKUP) |
| body | Body Filename | `scripts/trivia/library/bodies/` | Per-row OpenArt generation, human pastes filename |
| closer | Closer Filename | `scripts/trivia/library/closers/` | Per-row OpenArt generation, human pastes filename |

Code resolves these to actual cell addresses at runtime via
`post_row.cell_for(sheets, row, "reaction_filename" | "body_filename" |
"closer_filename")`.

The hook/reaction segment is special: its Clip ID is **picked at idea time**
by `pick_reactions_llm.py` from the Clips catalog. The picker patches the
Reaction Filename VLOOKUP formula to return that Clip ID's filename. If
the formula resolves cleanly to a file in
`scripts/trivia/library/reactions/`, the reaction is done — no asset
work needed for the hook segment.

For body and closer: `assemble_modular.py` reads the filename from T or V
and looks it up in the matching library directory. Your job here is to make
sure T and V are populated AND the named files exist locally. If a filename
is missing or the file isn't in the library, the segment needs generation.

### 3a. Manual OpenArt Branch

For each missing segment:

- Print the prompt from `script.json` segments and the suggested duration
  (3s hook / 8s body / 4s closer).
- Print the model recommendation from `script.json` (e.g. "Veo 3.1 — 8s body").
- **Pause** and tell the user: "Generate the <segment> clip in OpenArt with
  this prompt, paste the Drive link into `Posts!<col><row>`, then say go."
- When the user says go, re-read the row and resume.

Do not invent Drive links. Do not auto-fill the sheet.

### 3b. Automated OpenArt Branch

Run `scripts/trivia/openart_driver.py` per segment with the appropriate
prompt + model + duration. Default invocation pattern:

```bash
python scripts/trivia/openart_driver.py \
  --prompt "$(jq -r .metadata.segments.body.prompt artifacts/script.json)" \
  --model "Veo 3.1" \
  --duration 8 \
  --out scripts/trivia/library/bodies/<filename>.mp4 \
  --write-sheet-row <row> --write-sheet-field body_filename
```

(Exact CLI is in the script's docstring — read it before invoking.)

If the driver fails (login expired, rate-limited, page changed), fall back to
manual and tell the user what failed.

Announce each generation call before running — model name + cost class +
expected wall time. OpenArt credits are real money.

### 4. Reaction Clip Sanity Check

The reaction clip was picked at idea stage. By the time this director runs,
the brief's `metadata.trivia.reaction_clip_id` should be set and `Posts!Q<row>`
should resolve to a real filename in `scripts/trivia/library/reactions/`.

If Q doesn't resolve (file missing from the local library, or VLOOKUP returns
empty), do NOT generate a fallback here. Stop and surface the issue:

- If the Clip ID is `Generated` status in CLIPS_SHEET but the file is missing
  locally, the human needs to download it from Drive (or the library cache
  needs syncing).
- If the Clip ID is `Pending` status, the reaction needs to be generated in
  OpenArt first — punt back to the human.

Falling back silently here breaks the idea-stage decision and produces a
reaction that doesn't match the resolution-line pairing.

### 5. Write Asset Manifest

The asset manifest is mostly a **reference back to the sheet**, not an
authoritative path index. The actual path resolution is `assemble_modular.py`'s
job at edit time.

Write `projects/<slug>/artifacts/asset_manifest.json` per
`schemas/artifacts/asset_manifest.schema.json`. Trivia-specific shape:

```jsonc
{
  "metadata": {
    "sheet_revision": "a1b2c3d4e5f6",
    "segments": {
      "hook":   { "filename_cell": "Q5", "duration_target_s": 3.0, "source": "clips_catalog", "reaction_clip_id": "R077" },
      "body":   { "filename_cell": "T5", "duration_target_s": 8.0, "source": "openart" },
      "closer": { "filename_cell": "V5", "duration_target_s": 4.0, "source": "openart" }
    }
  }
}
```

The `sheet_revision` is copied from `brief.json` — if it differs at edit-stage
entry, the manifest is stale and the pipeline re-runs from idea.

### 6. Self-Review

Run the meta reviewer against this stage's `review_focus`. Fix critical
findings before checkpointing.

### 7. Checkpoint

`human_approval_default: false`. Auto-proceed to edit if all checks pass.

## What Not To Do

- Do not write Drive URLs to the sheet from this director (the OpenArt driver
  does that when invoked; otherwise the human does).
- Do not pre-resolve segment paths. Resolution is `assemble_modular.py`'s job
  and the sheet is the source of truth — duplicating logic here drifts.
- Do not concatenate or normalize clips here — that's edit's job.
- Do not silently swap a missing clip for a placeholder. If a segment can't be
  sourced, stop and tell the user.
