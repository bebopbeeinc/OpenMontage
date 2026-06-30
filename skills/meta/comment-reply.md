# Comment Reply — Meta Skill

## When to Use

When the user wants help replying to a comment on a **published post** and gives you (in any combination):

- a **post** — by slug (`pineapple-two-years`), day number (`day 29`), or partial title,
- an **account** — e.g. `ellie.travelcrush`,
- the **comment text** they received.

Trigger phrases: "how should I reply to this comment", "got this comment on <post>", "reply to <comment> on <account>".

This is **not** a production request — do **not** route it through a pipeline, preflight, or Rule Zero. It is a copywriting task grounded in the real published post. The whole point is to reply *in the post's exact voice, referencing the post's actual content* — never from a guess about what the post said.

## Core Principle

**Never invent the post.** A comment only makes sense against what the viewer actually saw. Pull the real row from the source sheet first — the hook, fact, kicker, and caption — then reply to the *actual* joke or claim. Replying from a guess is the #1 failure mode (you will explain the wrong joke and confuse the commenter further).

## Process

### Step 1: Resolve the account → source

Map the account to its lookup module and voice sources:

| Account / Pipeline | Lookup module (`read_queue_bulk` + `slug`) | Voice & persona sources |
|---|---|---|
| `ellie.travelcrush` (trivia-reaction) | `scripts.trivia_reaction.queue_row` | memory `feedback_ellie_reaction_script_voice`, `project_ellie_reaction_performance_patterns`; `skills/pipelines/trivia-reaction/executive-producer.md` |
| trivia-captain (Captain Archibald) | `scripts.trivia_captain.queue_row` | memory `project_archibald_persona_register`, `project_trivia_captain_curated_slate` |
| trivia-captain-2t1l | `scripts.trivia_captain_2t1l.queue_row` | memory `project_trivia_captain_2t1l` |
| trivia-quiz / trivia-short | `scripts.trivia_quiz.sheets`, `scripts.trivia.post_row` (Posts_Quiz tab) | the pipeline's script-director skill |

If the account isn't in this table, ask the user which sheet/pipeline the post lives in rather than guessing. New accounts: add a row here once you've confirmed the module + voice source.

### Step 2: Pull the real post row

Use the project venv (`.venv/bin/python`) — the Google libs aren't on the system Python. Read-only; no secrets wrapper needed (service account at `~/.google/claude-sheets-sa.json`):

```bash
.venv/bin/python -c "
from scripts.trivia_reaction.queue_row import build_sheets, read_queue_bulk
s = build_sheets(write=False)
rows = read_queue_bulk(s)
KEY = 'pineapple-two-years'   # slug, or match on day / title substring
for r in rows:
    if KEY in str(r.get('slug','')) or KEY.lower() in ' '.join(str(v) for v in r.values()).lower():
        for k, v in r.items():
            if v not in (None, ''):
                print(f'{k}: {v!r}')
"
```

Swap the `import` line's module for the account's module from Step 1. The fields that matter for a reply: **`hook_vo`, `fact_vo`, `kicker_vo`, `caption`** (and `question_en` / `correct_answer_en` for quiz formats). The **kicker carries the joke** — most "I don't get it" comments are about the kicker, not the fact.

If the slug isn't found, widen the match (day number, title words) or list candidate slugs back to the user. Do not proceed on a guess.

### Step 3: Read the voice

Load the account's voice sources from Step 1 before drafting. Get the register right:

- **ellie.travelcrush** — amused, intimate, single-take selfie energy. Warm, never condescending. Light emoji. Reassure confused commenters; never make them feel slow. What lands: specific punchy buttons, "wait what" recalibrations, *specific* contrasts (not generic self-roast).
- **Captain Archibald** — amused disbelief, never anger; hook-first; dry. Stay in persona.
- Other accounts — match the script-director / persona memory for that pipeline.

### Step 4: Read the comment for intent

Classify what the commenter actually wants — it changes the reply:

| Comment type | Reply goal |
|---|---|
| Confused ("I don't get it") | Re-land the joke/fact **clearly**, kindly. Name the exact word/beat they tripped on. |
| Praise / laughing | Match energy, extend the bit, maybe tee up the next post. |
| Question | Answer it, briefly, in voice. |
| Correction / criticism | Acknowledge gracefully; don't get defensive. Only concede if they're right. |
| Pun / quoting a beat | They're playing — riff back on that beat. |

Read the comment closely — a single quoted word (e.g. `Shalf` → "shelf") often pinpoints exactly which beat landed or missed.

### Step 5: Draft 2–3 replies + recommend one

Produce 2–3 options spanning a tonal range (e.g. straight-clarify / lean-into-the-bit / shortest), each:

- in the account's voice (Step 3),
- grounded in the real post content (Step 2),
- addressing the comment's actual intent (Step 4).

Then **recommend one** with a one-line reason. Keep replies short — comment-length, not VO-length.

### Step 6: Hand off — never auto-post

Present the options and **stop**. The user posts the reply manually. Do **not** attempt to post, write to any sheet, or call any publish tool. If you spotted a line that's tighter than the original post's kicker, you may *offer* to save it (memory) or swap it into the queue for future re-cuts — but only with explicit user approval.

## Common Pitfalls

- **Guessing the post content.** Always pull the real row. If you can't find it, say so and ask — don't fabricate the joke.
- **Explaining the fact when the confusion is about the kicker.** The kicker carries the payoff; check `kicker_vo` first for "I don't get it" comments.
- **Condescending to a confused viewer.** "You're not slow" energy, not "let me explain it slowly." Confused-but-kind replies often pull *more* replies (good for the FYP).
- **Wrong register.** An ellie reply that reads like Captain Archibald (or vice versa) breaks the account. Read the voice source every time.
- **VO-length replies.** Comments are shorter than scripts. Tighten.
- **Auto-posting or writing to the sheet.** This skill is draft-only. The user posts manually.
- **Wrong Python.** Use `.venv/bin/python`; the system Python lacks `google`.

## Self-Evaluation

Before presenting, score each draft (aim 4–5 on each):

| Dimension | 1 | 3 | 5 |
|---|---|---|---|
| Grounded in real post | invented the content | vaguely related | quotes/lands the actual fact or kicker |
| Addresses the comment | ignores their point | partly | directly answers their intent (Step 4) |
| Voice match | wrong account register | close | unmistakably this account |
| Kindness/energy fit | condescending or flat | fine | warm, makes them feel good |
| Length | essay | a bit long | tight, comment-sized |

If any draft scores ≤2 on "Grounded in real post," you skipped Step 2 — go back and pull the row.
