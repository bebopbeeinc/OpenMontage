# Executive Producer — Trivia Quiz Pipeline

## When To Use

The user references the daily-trivia QUIZ workflow. Triggers:

- "render the quiz for <slug>" / "process quiz <slug>"
- "make a 3-question post about <topic>"
- A reference to `dailytrivia.tc` paired with a 3-question / quiz / multi-question shape
- Feedback on an already-rendered quiz post

If the user wants a single-fact short, route to `trivia-short`. If they want a
reaction reel, route to `trivia-reaction`. Quiz format = three questions + a
score CTA, ~30s vertical, faceless.

## Philosophy

Trivia-quiz is **fixture-authoritative in v0.1** (no Sheets integration yet).
The source of truth is `projects/trivia-quiz/<slug>/inputs/quiz_row.yaml` — a hand-authored
file that mirrors the future `Posts_Quiz` sheet row. The pipeline owns the
renders + transient artifacts under `projects/trivia-quiz/<slug>/artifacts/`. Once the
format is validated on ~5 posts, v0.2 will swap the fixture for a Sheets read.

Your job:

1. **Read the fixture first.** `projects/trivia-quiz/<slug>/inputs/quiz_row.yaml` is the
   contract. If it's missing or schema-invalid, fail at idea and ask the human
   to populate it. Do not invent questions.
2. **Treat `styles/trivia-quiz.yaml` as the show identity.** Title, default
   hook, score CTA, lockup, segment durations, brand tokens — all live there.
   Don't re-litigate them per row.
3. **Run user defaults.** Per memory: full production flags
   (`--with-vo --with-music --with-sfx`), frame-review every render,
   never auto-publish.
4. **Three viewer-commit beats matter most.** The countdown bars at Q1/Q2/Q3
   are the format's reason for being. If a render drops a countdown or its
   timing drifts > 100ms, that's a CRITICAL frame-review defect — auto-fix it.

## Stage Loop

```
idea → script → assets → edit → compose → publish
```

Each stage reads its required artifacts from `projects/trivia-quiz/<slug>/artifacts/`
and writes one canonical artifact back. See `pipeline_defs/trivia-quiz.yaml`
for the per-stage contract.

## Frame Review (mandatory at compose)

Extract these 7 frames from `renders/final_quiz.mp4`:

| Frame at | Verify |
|---|---|
| 1.5s | Locked hook card centered, brand title visible |
| 5.0s | Q1 question fully on-screen + countdown bar visible |
| 9.0s | Q1 reveal stamp (green ✓) on the correct choice |
| 13.0s | Q2 countdown bar visible |
| 18.0s | Q2 reveal stamp on correct choice |
| 25.0s | Q3 reveal stamp on correct choice |
| 29.0s | Score card with "0/1/2/3 👇" prompt + Travel Crush lockup |

Any defect → auto-fix in the same turn (re-patch `meta.json`, re-render).
Don't list defects and ask. See `feedback_review_autofix` user memory.

## What This Pipeline Doesn't Do (v0.1)

- No Google Sheets read/write — fixture YAML instead
- No OpenArt backdrop generation — stills + Ken Burns instead
- No per-row smart link — `styles/trivia-quiz.yaml` placeholder URL on the lockup
- No reward-loop comment bot — separate feature

All four land in v0.2 once the format is proven.
