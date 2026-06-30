# Select Director — Trivia Captain Reaction Pipeline

## When To Use

You are turning one daily-trivia Day into a `brief` artifact. The user
provided a Day number (or said "process Day 2"). Your job is to:

1. Read the DailyTriviaConfig row for that Day.
2. Resolve the three Uids (Question / CorrectAnswer / CorrectExplanation)
   via LocalizedTextConfig.
3. Stamp a sheet-revision hash and write `brief.json`.
4. Upsert the Posts_Reaction row with status `Draft`.

There is no creative ideation here — the trivia content is owned by the
DailyTriviaConfig sheet. Your output is a structured, schema-valid
derived view.

The fact-fit auto-classifier was removed on 2026-05-19. Humans curate
which Days make good reaction-reel candidates; the heuristic was
redundant. If a Day doesn't fit (vocab quiz, flat answer), just don't
queue it.

## Prerequisites

| Layer | Resource | Purpose |
|---|---|---|
| Sheet | DailyTriviaConfig (DEV) | Trivia content source |
| Sheet | LocalizedTextConfig (DEV) | Uid → EN dictionary |
| Sheet | @archibald.travelcrush Post Calendar — Posts_Reaction tab | Workflow-state SoT |
| Script | `scripts/trivia_captain_reaction/select_row.py` | The CLI that does all of this |
| Auth | `~/.google/claude-sheets-sa.json` | Sheets read+write access |
| Schema | `schemas/artifacts/brief.schema.json` | Artifact validation |

## Process

### 1. Resolve The Day

If the user gave a Day number, use it. If only a slug (existing queue
row), look up the Day from `Queue!A` for that slug.

If neither, ask. Do not invent.

### 2. Run select_row.py

```bash
python scripts/trivia_captain_reaction/select_row.py --day <N>
```

The CLI does the four-step process (read row → resolve Uids →
write brief → upsert queue with status `Draft`).

If the user wants a custom slug:

```bash
python scripts/trivia_captain_reaction/select_row.py --day <N> --slug <custom-slug>
```

To inspect without writing:

```bash
python scripts/trivia_captain_reaction/select_row.py --day <N> --dry-run
```

### 3. Read Back The Brief

The CLI writes `projects/trivia-captain-reaction/<slug>/artifacts/brief.json`. Read it before
checkpointing — the trivia_captain_reaction sub-object inside `metadata` is what
the script director will consume.

### 4. Self-Review

Run the meta reviewer against this stage's `review_focus`:

- Queue row found / appended for the Day
- Slug is kebab-case
- All three Uids resolve to non-empty EN strings
- Sheet revision hash present in `metadata.sheet_revision`

Fix critical findings; note suggestions in `decision_log`.

### 5. Checkpoint With Human Approval

`human_approval_default: true`. Present:

- Day + slug + Queue row number
- Question (EN), Correct Answer (EN), CorrectExplanation (EN)
- The hash that locks downstream artifacts to this row state

Wait for "go" before advancing to script.

## What Not To Do

- Do not write to DailyTriviaConfig or LocalizedTextConfig.
- Do not invent translations — if the EN cell is empty in
  LocalizedTextConfig, surface the gap and ask.
- Do not overwrite an existing queue row's Slug without confirming with
  the user — slugs are the project directory key and renaming breaks artifacts.
