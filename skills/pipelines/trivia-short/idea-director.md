# Idea Director - Trivia Short Pipeline

## When To Use

You are turning a Post Calendar row into a brief artifact. The user has
provided a slug + row, OR the slug alone with enough context to find the row
(usually the row was the most recent Draft in Posts).

For trivia-short, the brief is mostly **structured copy from the sheet** plus
a few derived fields. There is no creative ideation here — the human did that
in the Posts row. Your job is to read it accurately, apply the no-reveal
guardrail, stamp the row hash, and write the brief.

**The sheet is the source of truth.** `brief.json` is a derived view — it is
regenerated from the row on every fresh run, and it always carries a
`metadata.sheet_revision` hash so downstream stages can detect staleness.
See `executive-producer.md` "Sheet Contract" and "Sheet Sync At Stage Entry".

## Prerequisites

| Layer | Resource | Purpose |
|---|---|---|
| Schema | `schemas/artifacts/brief.schema.json` | Artifact validation |
| Sheet | Posts tab, columns A-Z | Trivia row source data |
| Sheet | CLIPS_SHEET (`1E19Pv9ur0KsgHxny65rX_CXsT-yHPkbyhqjZTEvJG_E`), tabs `Personas`, `Clips` | Reaction-clip catalog |
| Script | `scripts/trivia/pick_reactions_llm.py` | LLM-driven reaction picker |
| Docs | `scripts/trivia/docs/trivia-video-workflow.md` | Column map + full workflow narrative |
| Auth | `~/.google/claude-sheets-sa.json` | Sheets read access |
| Env  | `ANTHROPIC_API_KEY` (or `claude` CLI) | Required by `pick_reactions_llm.py` |

## Process

### 1. Resolve Slug + Row

If the user gave both, use them. If only a slug, find the row whose Slug
column matches; abort if zero or >1 matches.

If only a row number, read the Slug column for that row.

If neither, ask. Do not invent a row.

### 2. Read The Row

`read_post_row(sheets, row)` returns the full row as a dict keyed by the
field names below. Resolution uses the live header row, so the column
letters here are reference only.

| Field | Header label | Required |
|---|---|---|
| `order` | Order | no |
| `mode` | Mode | YES — must be `Facts` or `Choices` |
| `topic` | Topic | YES |
| `hook` | Hook | YES |
| `question` | Question | YES |
| `answer_prompt` | Answer Prompt | YES (Choices mode) |
| `ending` | Ending | YES — drives no-reveal guardrail |
| `resolution` | Resolution | YES |
| `cta` | CTA | YES |
| `reaction_archetype` | Reaction Archetype | YES (drives the picker) |
| `reaction_prompt` | Reaction Prompt (VLOOKUP) | written by picker |
| `reaction_filename` | Reaction Filename (VLOOKUP) | written by picker |
| `slug` | Slug | YES |

### 3. Apply The No-Reveal Guardrail

If `H` (Ending) is `No reveal`:

- For a true/false claim: `J` (CTA) MUST NOT contain the words `true` or
  `false` (case-insensitive). If it does, fail review and ask the human to
  edit J in the sheet. Do not silently edit copy.
- For a choices claim: `J` MUST NOT name the correct option.

Record the guardrail outcome in `decision_log` whether it passed or required
a fix.

### 4. Pick The Reaction Clip (Upfront, From The Clips Library)

The reaction clip is **chosen at idea time** so it makes narrative sense with
the row's closer/resolution line. The catalog lives in the Clips library
spreadsheet (`CLIPS_SHEET`), not in `scripts/trivia/library/reactions/`.

Run the LLM-driven picker:

```bash
python scripts/trivia/pick_reactions_llm.py --row <row> --dry-run
# inspect the suggested Clip ID + persona + reasoning
python scripts/trivia/pick_reactions_llm.py --row <row> --apply
```

What it does (read the script's docstring before running):

1. Reads the Reaction Archetype cell and the row's full context — topic,
   hook, question, **resolution**, CTA.
2. Filters `CLIPS_SHEET!Clips!A:M` to clips whose archetype matches.
3. Asks Claude (Opus 4.7) to pick the persona/clip whose ethnicity, age,
   gender, and vibe best fit the trivia context — especially the resolution
   line. Country-specific topics get culture-matched personas; topic-neutral
   content (animals, science) gets a free pick optimized for energy.
4. With `--apply`: patches the VLOOKUP formulas in the Reaction Prompt and
   Reaction Filename cells to hardcode the chosen Clip ID. After this, both
   cells resolve to stable text for the downstream stages.

Announce the call before running (paid Claude API + sheet write). Show the
dry-run pick to the user before applying when this is the first time picking
for the row.

If the picker fails (no candidates for that archetype, model error), do not
silently fall back — surface the failure and ask. Either the archetype is
wrong (human edits the Reaction Archetype cell) or the catalog is empty
for that archetype (human adds clips).

### 5. Compute Derived Brief Fields

- `target_duration_seconds`: `14.4` (3 + 8 + 4 - 2 * 0.3 xfade)
- `target_platform`: `tiktok_short` (1080x1920, <=15s)
- `segments`: list of three with name + nominal duration
- `assembly_defaults`: per user memory — `["--with-vo", "--with-music", "--with-sfx", "--silent-hook"]`

### 6. Compute The Sheet Revision Hash

Hash the cells the pipeline reads so downstream stages can detect "the
human edited the row since we last ran". Use the field names from
`post_row.ROW_KEYS` (resolution to column letters happens at read time):

```python
import hashlib, json
# row is the dict returned by read_post_row(sheets, n).
hashed_fields = (
    "mode", "topic", "hook", "question", "answer_prompt", "ending",
    "resolution", "cta", "reaction_archetype",
    "reaction_prompt", "reaction_filename",
    "body_prompt", "body_filename", "closer_prompt", "closer_filename",
    "slug",
)
cells = [row[f] for f in hashed_fields]
sheet_revision = hashlib.sha1(json.dumps(cells).encode()).hexdigest()[:12]
```

Note: `reaction_prompt` and `reaction_filename` are read AFTER the
reaction picker runs, so the hash reflects the picked Clip ID. If you
re-run idea with a different reaction pick, the hash changes and
downstream artifacts invalidate (as intended).

### 7. Write The Brief

Write `projects/<slug>/artifacts/brief.json` per `schemas/artifacts/brief.schema.json`.

Trivia-specific data goes in `metadata.trivia`:

```jsonc
{
  "metadata": {
    "sheet_revision": "a1b2c3d4e5f6",
    "trivia": {
      "row": 5,
      "slug": "australia-wider-than-moon",
      "mode": "facts",
      "topic": "...",
      "hook": "...",
      "question": "...",
      "ending": "No reveal",
      "resolution": "...",
      "cta": "...",
      "reaction_archetype": "Smug nod",
      "reaction_clip_id": "R077",
      "reaction_clip_status": "Generated",
      "reaction_persona": "Captain — overconfident sea-dog",
      "reaction_filename": "captain_smug_03.mp4",
      "reaction_pick_reasoning": "Captain's wry register pairs with the closer-line beat 'and that's why...'",
      "assembly_defaults": ["--with-vo", "--with-music", "--with-sfx", "--silent-hook"],
      "no_reveal_guardrail": "passed"   // or "fixed_by_human"
    }
  }
}
```

### 9. Self-Review

Run the meta reviewer against this stage's `review_focus`. Fix critical
findings; note suggestions in `decision_log`.

### 10. Checkpoint With Human Approval

Per the manifest, this stage is `human_approval_default: true`. Present:

- Slug, row, mode
- The four pieces of copy (hook / question / resolution / CTA)
- Reaction pick: archetype + Clip ID + persona + one-line reasoning
- No-reveal guardrail result
- Assembly defaults that will be applied at edit stage

Wait for "go" before advancing to script.

## What Not To Do

- Do not edit sheet content from this stage EXCEPT via `pick_reactions_llm.py
  --apply` (which patches the Reaction Prompt + Reaction Filename VLOOKUP
  formulas). Everything else is human-owned.
- Do not pick a reaction clip by hand. Use the picker — it has the persona
  catalog, the cultural matching rules, and the resolution-line context.
- Do not assume mode from the topic — read it from the Mode column.
- Do not skip the no-reveal check when Ending = `No reveal`.
- Do not advance to script if `pick_reactions_llm.py` failed. Surface the
  failure and ask.
