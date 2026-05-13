# Trivia Video Workflow

End-to-end operational guide for the **trivia-short pipeline**. The Posts
spreadsheet is the human-facing source of truth; everything else (clip
generation, assembly, captioning, publishing, feedback handling) is owned
by the pipeline. Use this doc when you need a narrative read of the flow —
the formal contract lives in `pipeline_defs/trivia-short.yaml` and the
seven director skills under `skills/pipelines/trivia-short/`.

## What's actually a pipeline now

This used to be a loose collection of scripts the human invoked by hand
between OpenArt drops and a Drive upload. After the 2026-05 cleanup it has
the same shape as every other OpenMontage pipeline:

- `pipeline_defs/trivia-short.yaml` — declarative manifest (stages,
  required skills, external state, success criteria).
- `skills/pipelines/trivia-short/{idea,script,asset,edit,compose,publish}-director.md`
  — one stage director per stage, plus an `executive-producer.md` that
  orchestrates them.
- `scripts/trivia/` — the executable layer (assemble, transcribe via
  `scripts/common/transcribe.py`, render via Remotion, publish, shorten_vo,
  feedback router/applier, OpenArt automation).
- `scripts/trivia/web/` — local FastAPI app for the human review loop
  (one URL per row, plus job streaming via SSE).
- `scripts/trivia/library/{reactions,bodies,closers}/` — pipeline-local
  clip library (gitignored — populated by the asset stage).

The Posts sheet still owns row identity, script copy, and the public
"Ready to publish / Approved / Published" status flag. Everything else
flows through `projects/<slug>/artifacts/` like a normal pipeline.

## System overview

```
┌──────────────────────────────────────────────────────────────────────┐
│  Post Calendar sheet                                                 │
│  https://docs.google.com/spreadsheets/d/1EzucrS6…MP6Eg               │
│                                                                      │
│   Posts tab — rows 1–3 decorative banner. Header is row 4.           │
│   Data starts at row 5. 26 columns (A:Z) post-2026-05 cleanup.      │
│                                                                      │
│   Script (A-K):       Order · Post · Mode · Topic · Hook ·           │
│                       Question · Answer Prompt · Ending ·            │
│                       Resolution · CTA · Trivia UID                  │
│   Final (L-M):        Final Status · Final Video Link                │
│   Reaction (N-P):     Archetype · Prompt (VLOOKUP) · Filename (VLOOKUP) │
│   Hook emphasis (Q):  Emphasis Override (resolved by assemble script)│
│   Body (R-S):         Prompt · Filename                              │
│   Closer (T-U):       Prompt · Filename                              │
│   Posting (V-Z):      TikTok Description · Pinned Comment ·          │
│                       Hero Visuals · Slug · Music Track              │
└──────────────────────────────────────────────────────────────────────┘
                 │
                 │  idea stage: pick a row, run pick_reactions_llm.py to
                 │              lock a Clip ID into P+Q, fill in the
                 │              script columns (D-J) if not already.
                 ▼
         ┌──────────────────┐
         │  asset stage     │  ← openart_generate.py drives OpenArt via
         │                  │    Playwright per segment (reaction picked
         │                  │    at idea time; body+closer generated
         │                  │    here). Outputs land in
         │                  │    scripts/trivia/library/{reactions,bodies,closers}/
         │                  │    Filenames written back to P/Q (already)
         │                  │    and T/V.
         └──────────────────┘
                 │
                 ▼
         ┌──────────────────┐
         │ edit stage:      │
         │ assemble_modular │  ← downloads each clip, normalizes to
         │      .py         │    1080×1920, xfade-concats into bg.mp4,
         └──────────────────┘    adds VO + music + SFX + burned hook
                 │                caption. Emits assembly_warnings.json
                 │                if a VO line overshoots its window;
                 │                that triggers an in-stage retry with
                 │                shorten_vo.py before re-assemble.
                 ▼
         ┌──────────────────┐
         │  transcribe      │  ← scripts/common/transcribe.py →
         │  (faster-whisper)│    words.json (+ brand-token capitalization
         └──────────────────┘    post-processing).
                 │
                 ▼
         ┌──────────────────┐
         │  compose stage:  │
         │ Remotion render  │  ← TriviaWithBg composition reads bg.mp4
         │ TriviaWithBg     │    + words.json + meta.json (mode, option
         └──────────────────┘    reveal times, caption suppress window,
                 │                cta_text for the resolution→CTA force-
                 │                break). Output: renders/final_with_bg.mp4
                 ▼
         ┌──────────────────┐
         │  publish stage:  │
         │ publish.py       │  ← Uploads/replaces the Drive file in place
         └──────────────────┘    (link in M stays stable across re-renders),
                 │                writes L=Ready to publish.
                 ▼
         ┌──────────────────┐
         │  human review    │  ← Web UI at scripts/trivia/web/. Either
         │  (web UI)        │    flips L=Approved, or saves feedback to
         └──────────────────┘    projects/<slug>/artifacts/feedback.json.
                 │
                 │  feedback loop:
                 │   Phase 0  feedback_router.py classifies feedback →
                 │            feedback_plan.json (uses Claude SDK if
                 │            ANTHROPIC_API_KEY is set, else falls back
                 │            to the local `claude` CLI subscription).
                 │   Phase 0b apply_feedback_patches.py --phase pre
                 │            (brand tokens, music volume, shorten_vo gate).
                 │   …assemble → transcribe …
                 │   Phase 3b apply_feedback_patches.py --phase post
                 │            (word/timing edits on words.json).
                 │   …re-render → re-publish in place …
                 ▼
              (approved)
```

## The post-calendar sheet

The Posts tab has a 4-segment column layout (reaction · hook · body ·
closer), with each segment grouping its prompt and filename in adjacent
columns. See `scripts/trivia/post_row.py` for the canonical schema
(`ROW_KEYS`).

### Pinned columns

Some columns are referenced by hardcoded cell address from the
publish/assemble scripts. **Do not move these:**

| Col | Field | Referenced by |
|-----|-------|---------------|
| L | Final Status | `publish.py` writes "Ready to publish" |
| M | Final Video Link | `publish.py` writes the Drive link |
| O | Reaction Prompt (VLOOKUP) | `pick_reactions_llm.py` patches the hardcoded Clip ID |
| P | Reaction Filename (VLOOKUP) | `pick_reactions_llm.py` patches the hardcoded Clip ID |
| Q | Emphasis Override | `assemble_modular.py` writes resolved hook emphasis |
| F | Question | `shorten_vo.py` may rewrite this and attach a tracking note |

Code should look these up via `post_row.cell_for(sheets, row, field)` —
the letters above are reference only, the helper resolves the live header
row so reorderings don't require touching scripts.

### Per-segment column groups

Each segment groups its source-of-truth fields together so the human can
scan one block at a time. After the 2026-05 cleanup, every segment is
filename-only — no more Drive URL fallbacks:

| Segment | Prompt | Filename | Library |
|---------|--------|----------|---------|
| Reaction | O (VLOOKUP) | P (VLOOKUP) | `scripts/trivia/library/reactions/` |
| Hook | (text in E, burned in) | (same file as Reaction; Hook=Reaction visually) | — |
| Body | R | S | `scripts/trivia/library/bodies/` |
| Closer | T | U | `scripts/trivia/library/closers/` |

The `*_filename` cell holds the local filename of the rendered/cached
asset (e.g. `b005__australia-wider-than-moon.mp4`); `assemble_modular.py`
looks it up under the matching library directory. P/Q both resolve via
VLOOKUP against the Clips library spreadsheet (`CLIPS_SHEET`); the
hardcoded Clip ID inside each formula is patched by
`pick_reactions_llm.py` at idea time.

### Status, prompts, and feedback

**Final Status (L)** is a dropdown with:
`Draft · Ready to publish · Needs revision · Approved · Published`.
Per-segment status columns were removed in the 2026-05 cleanup — feedback
on a specific segment is now expressed in
`projects/<slug>/artifacts/feedback.json` along with which segment to
re-run.

**Emphasis Override (R)** is the per-row override word for the burned-in
hook caption (rendered when `assemble_modular.py --silent-hook` is set).
If empty, `assemble_modular.py` looks up a default in the Hooks library
tab, then falls back to auto-picking. The resolved word is written back
to R after each render.

**Final Feedback** used to live in an inline column; it was removed in
the 2026-05 cleanup. Reviewer feedback now lives at
`projects/<slug>/artifacts/feedback.json`, written by the web app's
`/api/feedback` POST endpoint. The Save & Re-render button writes the
file then triggers a render job that starts with **Phase 0:
`feedback_router.py`** — a Claude call that classifies the feedback and
emits a structured `feedback_plan.json`. Downstream phases consume the
plan:

- **Pre-assemble** (`apply_feedback_patches.py --phase pre`) writes
  side-effect files the existing scripts already read:
  `brand_tokens_extra.json` (transcribe), `assemble_overrides.json`
  (music volume → assemble flags), `feedback_blockers.json` (segment-regen
  tasks for the human).
- **Post-reconcile** (`apply_feedback_patches.py --phase post`) applies
  word + timing edits to `words.json` (matched by word + nearest
  timestamp, so the patch survives transcribe re-runs).
- The `shorten_vo.py` retry path also reads feedback as Claude prompt
  context, so VO-length feedback like "hook is sped up" steers the
  rewrite.

**No-key fallback.** `feedback_router.py`, `shorten_vo.py`, and
`pick_reactions_llm.py` all prefer the Anthropic Python SDK when
`ANTHROPIC_API_KEY` is set, and otherwise transparently fall back to the
local `claude` CLI (uses your OAuth subscription). The output is the
same structured JSON either way.

## Step 1 — Asset stage: generate the three source clips

The reaction is already locked in at idea time (see Step 1a). Body and
closer are generated here.

### 1a. Idea-time setup (one-time per row)

1. Fill in columns D–J for the new row (Topic, Hook, Question, Answer
   Prompt, Ending, Resolution, CTA). Pick a Reaction Archetype in O.
   All visuals are photorealistic; there is no per-row Style choice.
2. **Lock the reaction clip:**
   ```bash
   source .venv/bin/activate
   python scripts/trivia/pick_reactions_llm.py --row <row> --apply
   ```
   This patches the P + Q VLOOKUP formulas with a specific Clip ID picked
   from the Clips library based on the row's resolution line. After the
   run, Q resolves to a filename in `scripts/trivia/library/reactions/`.
3. Pick a slug (kebab-case from the claim, e.g.
   `australia-wider-than-moon`) and put it in the **Slug** column. The
   slug is the project-directory name for all downstream artifacts.

### 1b. Generate body + closer clips

The `manual_openart` and `automated_openart` production modes both end up
at the same place: an mp4 in `scripts/trivia/library/bodies/` or
`/closers/` and the matching filename in the row's **Body Filename** /
**Closer Filename** cells.

**Automated (default):**

```bash
source .venv/bin/activate
python scripts/trivia/openart_generate.py <row>
```

`openart_generate.py` drives the OpenArt UI via Playwright per segment.
First run is headed and pauses for manual login; subsequent runs reuse
the persistent storage state at `.playwright/openart-state.json`. The
driver intercepts the form-submission POST to pick up `resourceIds`, then
polls `/suite/api/resources/{id}` for the full-res CDN URL — no DOM
scraping. Generated clips are auto-muted (`fit_to_window` would
double-add the VO otherwise) and saved with the canonical filename.

**Manual (fallback):**

If the automated driver is broken or you want to iterate on a prompt
hands-on, copy the prompt cell (S for body, U for closer), paste it into
OpenArt's web UI, generate, and drop the resulting mp4 into the matching
library directory. Then paste the filename into T (body) or V (closer).

The hook visual is the reaction clip itself — there is no hook prompt;
the hook copy in E is what gets burned in via `--silent-hook`.

### No-reveal guardrail

If column H is `No reveal`, the Soft CTA in J must not leak the answer.
For a true/false claim, avoid the words "true" or "false" in J. Use
neutral phrasing ("Would you have guessed it", etc.).

## Step 2 — Edit / compose / publish stages

Triggered from the web UI's "Run" button (top-level pipeline) or
individually for debugging.

### 2a. Assemble

```bash
python scripts/trivia/assemble_modular.py <row> <slug> \
  --with-vo --with-music --with-sfx --silent-hook
```

What it does:

- Downloads each clip, normalizes to 1080×1920, xfade-concats into
  `projects/<slug>/assets/video/bg.mp4`.
- Generates VO segments (Piper or ElevenLabs) into the
  hook/claim/resolution/cta windows defined in `VO_WINDOWS`.
- Layers in the music bed (from `music_library/`) and SFX cues (from
  `sfx_library/`).
- Burns the hook caption (the row's E text + emphasis word from R).
- Emits `meta.json` with mode, options, option reveal times, caption
  suppress window, `cta_text`, `cta_nominal_start_ms`. The renderer uses
  this to suppress option-reveal captions and force a page break at the
  resolution→CTA boundary.

If a VO line overshoots its window, `assembly_warnings.json` is written.
The web server's render loop runs `shorten_vo.py` as a retry, which:

1. Walks the warning's field list, finding the first one with words to
   spare relative to its share of the total budget.
2. Calls Claude (SDK or CLI) to rewrite it under the target word count.
3. Saves the override to `projects/<slug>/artifacts/text_overrides.json`
   AND pushes the rewrite back to the corresponding sheet cell with a
   tracking note (timestamp, reason, before/after, word counts).
4. Re-assemble picks up the override from text_overrides.json.

### 2b. Transcribe

```bash
python scripts/common/transcribe.py <slug>
```

Outputs:

- `projects/<slug>/artifacts/words.json` — source of truth for captions.
- `remotion-composer/public/bg.mp4` + `public/words.json` +
  `public/meta.json` — staged for render.

**Brand capitalization:** Whisper never capitalizes proper nouns, so the
transcribe script post-processes the word list against `BRAND_TOKENS` at
the top of `scripts/common/transcribe.py`. It handles both single words
(`Captain`) and multi-word phrases (`Travel Crush`), preserves trailing
punctuation, and reports the fix count. Add new brands to that tuple as
they appear in scripts.

### 2c. Render

```bash
cd remotion-composer
npx remotion render src/index-trivia.tsx TriviaWithBg \
  ../projects/<slug>/renders/final_with_bg.mp4
```

The `TriviaWithBg` composition:

- Plays `public/bg.mp4` as the full-frame video layer (object-fit:
  cover).
- Reads meta.json's `suppress_captions_window_ms` and drops words whose
  audio finishes inside the window (option content). Words that extend
  past the window are kept; their display `startMs` is clamped to the
  window end so the caption appears when suppression lifts.
- Reads meta.json's `cta_text` and finds the first word in the
  transcript whose normalized text matches the CTA's first word (within
  ±1500 ms of `cta_nominal_start_ms` to absorb Whisper drift). Forces a
  caption-page break before that word so the resolution stands alone
  ("Floating duo") and doesn't get merged with the CTA ("Lock yours in
  first").
- Builds caption pages from the survivors using 3-word max and 350 ms
  pause-break rules. Orphan-rebalance pulls a word forward from the
  previous page, except across the forced CTA boundary.
- Renders TikTok-style word highlighting — Montserrat 900, white with
  dark outline, active word gets a solid green pill.
- Hides captions during silence. Clamps page end-time so adjacent pages
  never overlap.

### 2d. Publish

```bash
python scripts/trivia/publish.py <slug> <row-number>
```

The script:

- **First run for that row:** uploads a new file to the Drive folder
  and writes the link into column M.
- **Subsequent runs:** pulls the existing link from M, extracts the
  file ID, and calls `drive.files().update` to replace the content in
  place — the link stays stable.
- Sets column L = `Ready to publish`.
- Prints the local render path (`projects/<slug>/renders/final_with_bg.mp4`)
  so the human knows where to grab the file for manual TikTok upload.

Auth uses the service account at `~/.google/claude-sheets-sa.json`
(`claude-sheets-config@travel-crush.iam.gserviceaccount.com`). The Drive
folder and the sheet must be shared with this email. Override the path
via `$OPENMONTAGE_SA_PATH` if you need to.

## Step 3 — Human: review (web UI)

The trivia web app is the review surface:

```bash
source .venv/bin/activate
uvicorn scripts.trivia.web.server:app --port 8765 --reload
# then open http://127.0.0.1:8765/
```

For each row the UI shows:

- Status pill, slug, topic, hook, per-segment "ok / missing / empty"
  dots.
- Expandable variant grid: every variant in the segment's library, with
  inline `<video>` players, a "canonical" badge, and "Pick this" buttons
  to swap which variant is the canonical (filename written to T/V).
- A feedback panel: textarea + Save / Save & Re-render. The textarea
  is autosaved on Save; Re-render kicks off the full pipeline.
- A jobs sidebar that polls every 5 seconds for live status (no row
  re-render — only the sidebar updates, so playing videos aren't
  detached mid-frame).

To approve a row, flip L directly to `Approved` in the sheet or use the
"Flip to Approved" button on a successful publish job.

## Step 4 — Process feedback

When the reviewer types feedback and hits Save (or Save & Re-render),
the file lands at `projects/<slug>/artifacts/feedback.json`. The render
job (next time it runs) starts with Phase 0:

1. **`feedback_router.py`** classifies the free-text note into a
   structured `FeedbackPlan` (operations like `set_word`, `set_timing`,
   `add_brand`, `set_music_volume_db`, `regenerate_segment`,
   `allow_shorten_vo`). Writes `feedback_plan.json`.
2. **`apply_feedback_patches.py --phase pre`** materializes side-effect
   files for downstream phases (brand tokens, music volume override,
   blocker list, shorten_vo gate flag).
3. The pipeline runs normally (assemble → transcribe → render).
4. **`apply_feedback_patches.py --phase post`** edits `words.json` after
   transcribe — word substitutions match by word + nearest timestamp, so
   they survive Whisper re-runs.

After the render is approved, clear `feedback.json` (the web UI does this
automatically on a successful approve). Stale feedback files are a
common cause of "the router fired but didn't fix anything" — always
verify whether the feedback you're seeing is current.

## Caption tuning reference

`remotion-composer/src/TriviaWithBg.tsx` — grouping rules inside
`buildPages`:

| Constant | Value | Effect |
|---|---|---|
| `MAX_WORDS` | 3 | Max words per caption page |
| `PAUSE_MS` | 350 | Any transcript gap ≥ 350 ms forces a page break |
| `WHISPER_DRIFT_MS` | 1500 | Search window backward from `cta_nominal_start_ms` when matching the CTA's first word in the transcript |

Orphan 1-word pages are rebalanced by pulling a word forward from the
previous page — *unless* doing so would re-merge across the forced
resolution→CTA boundary.

Styling lives in `TikTokPage`:

- Font: Montserrat 900, 78 px, uppercase, `letterSpacing: -0.5`
- Base color: white with `2.5px #0a061e` stroke and drop shadow
- Active color: dark (`#0a061e`) on green pill (`#22E88A`)
- Padding is identical for active and inactive so the pill does not
  change layout width when a word becomes active.

Position: `paddingBottom: 180` from the bottom, so captions sit clearly
below the check / X / answer-tile art that shows up in most generated
videos.

## Troubleshooting

- **Caption page jams two VO sections together** (e.g. resolution +
  CTA on one page): check that `meta.json` has `cta_text` and
  `cta_nominal_start_ms` populated. If absent, re-assemble — both fields
  are emitted by `write_meta_json` and require `--with-vo` so the timing
  windows are known.
- **First word of resolution missing from captions** (e.g. shows "Duo
  Lock Yours" instead of "Floating Duo"): the suppress filter is
  dropping a word that ends past the window end. This was fixed by
  making the filter asymmetric (drop only words whose `endMs` is
  strictly inside `(s, e)`); confirm you're on the patched
  `TriviaWithBg.tsx`.
- **Captions and audio drift:** Whisper is not perfect. Re-run
  transcribe — results are deterministic for a given input, but rare
  transcription errors can be fixed by editing `artifacts/words.json`
  directly (or via a `set_word` patch in feedback).
- **Drive upload 403 / 404:** the service account probably isn't shared
  on the Drive folder. Share it with
  `claude-sheets-config@travel-crush.iam.gserviceaccount.com` (editor).
- **Sheet update 400 "exceeds grid limits":** the Posts tab needs
  enough columns. Expand via `appendDimension` before writing a new
  column.
- **`shorten_vo.py` skips with "already short" but the warning
  persists:** check that the warning's `WARNING_TO_FIELDS` mapping
  includes more than one field. For `choices_claim`, the mapping is
  `["question", "answer_prompt"]` — if `answer_prompt` is already short
  but the warning's `speech_s` exceeds `window_s`, the loop falls
  through to `question`. Add fields to the mapping if neither matches.
- **`feedback_router.py` returns exit 3:** neither `ANTHROPIC_API_KEY`
  nor the `claude` CLI is available. Install the Claude Code CLI or
  export an API key — either path works.

## Related docs

- `scripts/trivia/docs/setup.md` — install / first-run setup
  (Python venv, Claude CLI, Google service account, Piper voice,
  OpenArt login). Read this first on a fresh machine.
- `scripts/trivia/docs/openart-prompt-template.md` — the OpenArt prompt
  template and formula.
- `pipeline_defs/trivia-short.yaml` — the manifest (stages, external
  state, success criteria).
- `skills/pipelines/trivia-short/` — per-stage director skills.
- `remotion-composer/SCENE_TYPES.md` — other Remotion compositions
  available in this repo.
- `AGENT_GUIDE.md` — overall OpenMontage operating guide.
