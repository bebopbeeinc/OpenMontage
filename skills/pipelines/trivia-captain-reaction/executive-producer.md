# Executive Producer — Trivia Captain Reaction Pipeline

## When To Use

User references the trivia-captain-reaction workflow. Triggers:

- "render Day <N>" / "make a reaction reel for Day <N>"
- "process <slug>" (slug like `guinea-pigs-switzerland`)
- "publish <slug>" / "the avatar clip is in the library"
- A bare Day number after recent context establishes this workflow

For a one-off short, the daily trivia mobile game, or any non-reaction
format, pick a different pipeline. This pipeline is specific to the
"I just found out" reaction-reel format for the dailytrivia.tc account.

## Philosophy

Trivia-captain-reaction is **sheet-authoritative on two surfaces**:

1. **DailyTriviaConfig (DEV)** + **LocalizedTextConfig (DEV)** own the
   *content* — Question / CorrectAnswer / CorrectExplanation. Pipeline
   never writes here. Resolved every run via `daily_trivia.read_daily_trivia_row`.
2. **The Posts_Reaction tab on the dailytrivia.tc Post Calendar** owns the
   *workflow state* — Slug, Status, the three VO lines, Drive Link, and the
   assembled OpenArt Prompt. Pipeline writes here at select / script /
   compose / publish boundaries.

   Status is a 4-state enum: `Draft` → `Ready to review` → `Ready to publish`
   → `Published`. See `queue_row.STATUS_*` constants.

Artifacts under `projects/trivia-captain-reaction/<slug>/artifacts/` are derived views — they
carry `metadata.sheet_revision` so downstream stages can detect "the
human edited the content sheets since we last ran".

Your job:

1. **Always re-read both content sheets at stage entry.** If the sheet
   revision hash differs, downstream artifacts are stale — re-enter at select.
2. **Run the user's house defaults.** No music; warm purple-pill captions;
   `showFactsOverlay=false`. Always review renders before signaling
   "ready" (per user memory). Never auto-run `publish.py` (per user memory).
3. **OpenArt character lock.** Every video gen call uses
   `character="Captain Archibald"`. Never describe the Captain's face from text
   in the per-row prompt — the OpenArt saved character carries his identity.
4. **Backdrops vary per row.** The script-director picks one from the
   playbook palette appropriate to the fact's vibe; do not silently fall
   back to a default jungle backdrop.

## Sheet Contract

The three sheets, what each stage reads and writes:

| Sheet | Tab | Read by | Written by |
|---|---|---|---|
| DailyTriviaConfig | DailyTriviaConfig (DEV) | all stages (via brief) | nothing |
| LocalizedTextConfig | LocalizedTextConfig (DEV) | select (Uid→EN) | nothing |
| dailytrivia.tc Post Calendar | Posts_Reaction | all stages | select, script, compose, publish |

Within the Posts_Reaction tab on the dailytrivia.tc Post Calendar:

| Col | Field | Read | Written by |
|---|---|---|---|
| A | Day | select | select (append only) |
| B | Slug | all | select |
| C | Status | executive-producer | select, script, compose, publish, mark-as-published |
| D | Question (EN) | (denormalized for humans) | select |
| E | Correct Answer (EN) | (denormalized for humans) | select |
| F | Hook VO | edit | script |
| G | Fact VO | edit | script |
| H | Kicker VO | edit | script |
| I | Drive Link | publish (re-publish detect of captioned final) | publish |
| J | OpenArt Prompt | asset-director (copy into OpenArt for manual mode) | script-director |
| K | Caption | publish (human copies into Instagram post body) | script-director |
| L | Drive Clip | publish (re-publish detect of raw avatar clip) | publish |

## Sheet Sync At Stage Entry

Before any director starts work:

1. Re-read the DailyTriviaConfig row for the slug's Day.
2. Compute the sheet revision hash via `select_row.compute_revision_hash`.
3. Compare against `metadata.sheet_revision` on the most recent cached
   artifact for this slug.
4. If hash differs → the human edited content. Invalidate downstream
   artifacts and re-enter at `select`.
5. If hash matches → reuse cached artifacts, run only the requested stage.

Idea stage is the canonical hash-writer. Every subsequent artifact copies
the same hash forward.

## Operating Loop

```
  select → script → assets → edit → compose → publish
                                                 |
                                                 v
                                      (human reviews render)
                                                 |
                  +----------+------------+--------+
                  |          |            |
                approved   feedback     skip
                  |          |            |
                  v          v            v
            publish     classify and    Queue!C = Skip
                       re-enter stage
```

## Feedback Re-entry Decision Tree

When the user gives in-conversation feedback on a slug (the sheet no
longer has a Feedback column — feedback flows through the chat):

1. Classify the feedback axis:

| Pattern | Re-enter at | Why |
|---|---|---|
| Caption text/timing fix | `compose` | Patch words.json, re-render |
| VO copy change (any beat) | `script` → `edit` → `compose` | Beat copy lives in script.json |
| TTS voice change | `edit` → `compose` | Re-run assemble.py with new --voice-id |
| Avatar clip change ("the Captain's wrong"/"backdrop wrong") | `script` → `assets` → `edit` → `compose` | OpenArt prompt edit + regen |
| Fact-fit reconsideration | `select` | Re-classify and possibly Skip |

2. If feedback is ambiguous, ask the user which axis before re-entering.
3. Approval phrases ("ship it", "good", "publish") move Queue!C →
   `Ready to publish` and hand off to publish.

## Budget & Time

- `budget_default_usd: 0.40` — one OpenArt avatar clip + one ElevenLabs
  TTS run + one Remotion render. Anything over this is unusual — flag and ask.
- `max_wall_time_minutes: 25` — OpenArt is the slowest step (~2-8 min for
  a 15s clip with 2 variants). Remotion render ~3-5 min. If a single
  stage exceeds 10 min wall clock, abort and ask the user.

## Decision Communication Contract

Per AGENT_GUIDE.md, announce before paid calls:

- **Asset stage (automated_openart):** announce model + variant count +
  character lock + duration before driving OpenArt. Credits are real money.
- **Edit stage:** announce TTS provider + voice_id before generating VO.
  ElevenLabs is paid; Piper is free local.
- **Compose stage:** announce the Remotion composition and target output path.
- **Publish stage:** announce the slug + Drive folder, then **wait for
  human OK**. Never auto-run publish.py — hard rule from user memory.

## Related Skills

- `pipelines/trivia-captain-reaction/select-director` — Day → brief
- `pipelines/trivia-captain-reaction/script-director` — brief → three beats + OpenArt prompt
- `pipelines/trivia-captain-reaction/asset-director` — manual vs automated OpenArt
- `pipelines/trivia-captain-reaction/edit-director` — assemble.py orchestration
- `pipelines/trivia-captain-reaction/compose-director` — transcribe + Remotion + frame review
- `pipelines/trivia-captain-reaction/publish-director` — Drive + Queue write-back
- `meta/reviewer`
- `meta/checkpoint-protocol`

## What Not To Do

- Do not auto-publish. Publish always waits for explicit human approval.
- Do not skip frame-review at compose. Not "ready" until visually verified.
- Do not write to DailyTriviaConfig or LocalizedTextConfig. Read-only.
- Do not describe the Captain's face in OpenArt prompts. Use the character ref.
- Do not add music or SFX — reference uses neither.
- Do not silently fall back to a default backdrop. Pick varied per row.
