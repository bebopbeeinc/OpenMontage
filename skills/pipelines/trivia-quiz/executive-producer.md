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

Trivia-quiz is **Sheets-authoritative.** The source of truth is the
`Posts_Quiz` tab (see `scripts/trivia_quiz/sheets.py`); the per-row fixture
`projects/trivia-quiz/<slug>/inputs/quiz_row.yaml` is the hand-authored input
that gets seeded into the sheet via `python -m scripts.trivia_quiz.seed_sheet
--slug <slug>`. Build reads from the sheet with `build --from-sheet`. The
pipeline owns the renders + transient artifacts under
`projects/trivia-quiz/<slug>/artifacts/`.

To **author a new pack of rounds**, follow the "Authoring a New Riddle Pack"
section in `idea-director.md` — it covers the fixture→seed flow, the
no-duplicate / no-reused-answer rule across the whole catalog, unique captions
per post, and the `seed_sheet` idempotency gotcha (it won't update an existing
row — use `sheets.write_post_field` for edits).

Your job:

1. **Read the row first.** The `Posts_Quiz` row (or its `quiz_row.yaml` source)
   is the contract. If it's missing or schema-invalid, fail at idea and ask the
   human to populate it. Do not invent questions.
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

## What This Pipeline Doesn't Do

- No per-row smart link — `styles/trivia-quiz.yaml` placeholder URL on the lockup
- No reward-loop comment bot — separate feature
- No auto-publish — render + frame-review, then stop for local approval
  (see `feedback_trivia_local_approval` user memory)
