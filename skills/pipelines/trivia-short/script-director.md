# Script Director - Trivia Short Pipeline

## When To Use

The brief is approved (idea stage done) and you need to materialize the
per-segment OpenArt prompts and the VO copy lines that the asset + edit
stages will consume. There is no creative writing here — the human wrote
the four copy fields in the sheet (Hook E, Question F, Resolution I,
CTA J). Your job is to budget them against the VO windows, brand-check
them, validate the no-reveal guardrail, and surface the per-segment
OpenArt prompts that the sheet's formulas already produced.

## Prerequisites

| Layer | Resource | Purpose |
|---|---|---|
| Schema | `schemas/artifacts/script.schema.json` | Artifact validation |
| Artifact | `projects/<slug>/artifacts/brief.json` | Mode, row copy, sheet_revision |
| Sheet | `cell_for(sheets, row, "reaction_prompt")` (VLOOKUP) | Materialized reaction prompt for context |
| Sheet | `cell_for(sheets, row, "body_prompt")` (formula) | Materialized OpenArt body prompt |
| Sheet | `cell_for(sheets, row, "closer_prompt")` (formula) | Materialized OpenArt closer prompt |
| Script | `scripts/trivia/shorten_vo.py` | LLM-driven recovery when VO copy overflows |
| Source | `scripts/common/transcribe.py` `BRAND_TOKENS` | Canonical brand capitalization list |

## Hard Numbers

VO timing windows (from `scripts/trivia/assemble_modular.py:VO_WINDOWS`):

| Window | Range | Budget | Roughly fits |
|---|---|---|---|
| hook | 0.3 – 2.7s | 2.4s | 6-8 words |
| claim | 3.0 – 10.3s | 7.3s | 18-22 words |
| resolution | 10.6 – 11.7s | 1.1s | 3-4 words |
| cta | 11.9 – 13.3s | 1.4s | 4-5 words |

Speech-rate budget: ~3 words/second at TikTok pacing. Use this as a guide,
not a hard rule — the `shorten_vo.py` recovery path handles edge cases.

## Process

### 1. Re-read The Brief And Verify Sheet Revision

Per `executive-producer.md` "Sheet Sync At Stage Entry": re-read the row,
recompute the cell hash, compare against `brief.json:metadata.sheet_revision`.
If mismatch → invalidate and re-enter idea.

### 2. Read Materialized Segment Prompts

The OpenArt prompts for each segment live in the sheet as formulas that
inject mode-aware templates. Read the **rendered** values (not the
formula strings) for these fields via `read_post_row` (or directly via
`cell_for`):

- `reaction_prompt` — VLOOKUP into CLIPS_SHEET; hardcoded Clip ID was
  patched by `pick_reactions_llm.py` at idea time.
- `body_prompt` — per-row formula.
- `closer_prompt` — per-row formula.

Each must be a non-empty string after the formula renders. If any is empty,
investigate the formula chain (Clip ID not in `CLIPS_SHEET!Clips`,
formula error, etc.) and ask the human — do not fabricate prompts.

### 3. Compose VO Copy

VO copy is assembled from the row's already-written fields. There are four
lines:

| Window | Source field(s) (Facts mode) | Source field(s) (Choices mode) |
|---|---|---|
| hook | (skipped — `--silent-hook` is the default) | (skipped) |
| claim | E (Hook) + F (Question) | E (Hook) + F (Question) + G (Answer Prompt / options) |
| resolution | I (Resolution) | I (Resolution) |
| cta | J (CTA) | J (CTA) |

The hook segment is silent in the burned-caption mode — its on-screen text
is E (Hook) burned into the reaction frame. There's no spoken hook line.

If a row needs a spoken hook (rare), opt out of `--silent-hook` at edit time
and add a `hook` VO field here. The default is silent.

### 4. Apply Brand-Token Capitalization

Run the same `BRAND_TOKENS` list `scripts/common/transcribe.py` uses
(`Captain`, `Travel Crush`, `Fennec`, etc.) against every VO line. Whisper
won't capitalize these later, so the transcript will mismatch the audio
unless they're capitalized here too.

If a brand appears in the row that isn't in `BRAND_TOKENS`, flag it for the
human to add to that tuple before proceeding. Do not silently let a
lowercased brand through.

### 5. Validate Lengths Against The Windows

For each VO line, estimate speech time at ~3 words/second. Compare against
the window budget:

| Result | Action |
|---|---|
| Fits with >=10% headroom | OK |
| Within 10% over budget | Warning — note in `decision_log`, proceed |
| >10% over budget | Run `scripts/trivia/shorten_vo.py` to compress the offending field via Claude. Re-validate. |
| Still over after one shorten pass | Stop and ask the human |

Do not shorten silently if it changes meaning. The `shorten_vo.py` script
preserves brand names and the trivia answer — verify the rewrite does too.

### 6. Re-check The No-Reveal Guardrail

`idea-director.md` already ran this. Defense in depth: if `brief.metadata.trivia.ending == "No reveal"`:

- Facts mode true/false claim: resolution + cta MUST NOT contain "true" or
  "false" (case-insensitive).
- Choices mode: resolution + cta MUST NOT name the correct option (read
  the answer from `G` — answer_prompt — and check against `I` + `J`).

If the guardrail fails here but passed at idea, the human edited the row
between stages. Re-invalidate brief.json and re-enter idea.

### 7. Write The Script Artifact

Write `projects/<slug>/artifacts/script.json` per `schemas/artifacts/script.schema.json`.

Trivia-specific shape:

```jsonc
{
  "metadata": {
    "sheet_revision": "a1b2c3d4e5f6",
    "segments": {
      "hook":   { "openart_prompt": "...", "model_hint": "Veo 3.1", "duration_s": 3.0, "source": "clips_catalog" },
      "body":   { "openart_prompt": "...", "model_hint": "Veo 3.1", "duration_s": 8.0, "source": "openart" },
      "closer": { "openart_prompt": "...", "model_hint": "Veo 3.1", "duration_s": 4.0, "source": "openart" }
    },
    "vo": {
      "claim":      { "text": "...", "window_s": [3.0, 10.3], "estimated_speech_s": 6.8 },
      "resolution": { "text": "...", "window_s": [10.6, 11.7], "estimated_speech_s": 1.0 },
      "cta":        { "text": "...", "window_s": [11.9, 13.3], "estimated_speech_s": 1.3 }
    },
    "brand_tokens_applied": ["Captain", "Travel Crush"],
    "shorten_passes": 0
  }
}
```

Schema requires `segments` to have exactly 3 entries and `vo` to have at
least the 3 spoken lines (claim/resolution/cta — hook is optional). Verify
before writing.

### 8. Self-Review

Run the meta reviewer against this stage's `review_focus`:

- Each segment prompt is photorealistic and asks for ZERO on-screen text
- VO copy fits the VO_WINDOWS budgets
- No-reveal: resolution + CTA don't leak the answer
- Brand tokens capitalized in VO copy

### 9. Checkpoint With Human Approval

Per the manifest, this stage is `human_approval_default: true`. Present:

- Each segment's materialized OpenArt prompt (reaction + body + closer)
- VO copy for each window with the estimated speech time
- Any `shorten_vo.py` rewrites that ran (show before/after)
- Brand tokens flagged + applied
- No-reveal guardrail result

Wait for "go" before advancing to assets.

## What Not To Do

- Do not rewrite the segment prompts. Those are formula-rendered from the
  sheet — if they need editing, the human edits the prompt formula or the
  row's source fields.
- Do not write to the sheet from this stage. Script is read-only against
  the sheet. The only stages that write are idea (reaction picker → Reaction
  Prompt + Reaction Filename formulas), edit (resolved emphasis → Emphasis
  Override via assemble_modular), and publish (Final Status + Final Video
  Link).
- Do not run `shorten_vo.py` more than once per VO line without asking.
  Repeated shortening drifts meaning.
- Do not skip brand-token capitalization. Caption/audio mismatches at
  compose time get blamed on this.
