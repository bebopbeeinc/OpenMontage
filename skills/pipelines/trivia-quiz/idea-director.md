# Idea Director — Trivia Quiz Pipeline

## When To Use

Entry stage. You are turning a fixture row into a `brief.json` artifact.

## Prerequisites

| Layer | Resource | Purpose |
|---|---|---|
| Fixture | `projects/trivia-quiz/<slug>/inputs/quiz_row.yaml` | Hand-authored row (v0.1) |
| Style | `styles/trivia-quiz.yaml` | Show identity + guardrails |
| Schema | `schemas/artifacts/brief.schema.json` | Artifact validation |

## Process

### 1. Resolve Slug

User provides slug. Project dir is `projects/trivia-quiz/<slug>/`. Create it if missing.
Fixture path is `projects/trivia-quiz/<slug>/inputs/quiz_row.yaml`. Abort if missing.

### 2. Load Fixture

Parse the YAML. Required fields:

```yaml
slug: <kebab-case>
topic_mix: "<plain-language theme>"
q1:
  question: "<text, ≤80 chars>"
  difficulty: Easy
  choices:                          # list of "A) ..." / "B) ..." / "C) ..." (or omit for T/F)
    - "A) ..."
    - "B) ..."
    - "C) ..."
  answer: "<exact choice label, e.g. 'B) Sudan'>"
  surprise_fact: "<1-sentence reveal twist, ≤120 chars>"
q2: { ...same shape, difficulty: Medium }
q3:
  ...same shape, difficulty: Hard
  game_themed: true|false           # default false
game_hook_line: "<spoken line on score card>"   # required when q3.game_themed
tomorrow_tease: "Tomorrow: <topic>"
reward: ""                          # optional; empty string = disabled
captions:
  tiktok: "<post caption>"
  instagram: "<IG variant>"
  pinned_comment: "<pinned reply template>"
music_track: "<filename under music_library/>"
```

### 3. Apply No-Leak Guardrail

Compile the set of answer strings: `{q1.answer, q2.answer, q3.answer}`. Then
case-insensitive substring search across:

- `tomorrow_tease`
- `game_hook_line`
- `captions.tiktok`
- `captions.instagram`
- `captions.pinned_comment`

If any answer appears in any field, **fail review** and ask the human to
edit the fixture. Do not silently edit copy.

### 4. Validate Difficulty Ladder

- `q1.difficulty == "Easy"`
- `q2.difficulty == "Medium"`
- `q3.difficulty == "Hard"`

If wrong, fail. Don't reshuffle automatically (the human picked the questions
deliberately and we want to respect that signal).

### 5. Validate `game_themed` Coherence

If `q3.game_themed == true`:
- `game_hook_line` must be present and non-empty
- Either `q3.question` or `q3.surprise_fact` must contain a Travel Crush
  brand token (`Captain`, `Travel Crush`, `Fennec`) — otherwise the "game
  tease" is decorative and gives the viewer no actual reason to install

### 6. Write `brief.json`

Mirror the fixture into a normalized artifact. Stamp metadata: pipeline
name, version, timestamp, fixture hash (for staleness detection later).

## Output

`projects/trivia-quiz/<slug>/artifacts/brief.json` — schema-valid against
`brief.schema.json` with `metadata.quiz.*` populated.

## Checkpoint

`human_approval_default: true`. Present the resolved 3 questions + the
no-leak guardrail result + the answer ladder, and wait for approval before
the script stage runs.

## Authoring a New Riddle Pack (Batch)

When the user asks for "the next pack" / "N more riddles" / "enough to post
twice a day for a week", you are authoring multiple rounds at once. One
**post = one round = one `quiz_row.yaml` = one `Posts_Quiz` row** with three
riddles (q1 Easy, q2 Medium, q3 Hard). "Post twice a day for a week" = 14
posts = 14 rounds, not 14 individual riddles. Confirm the count math in your
reply.

### The sheet is the source of truth

`Posts_Quiz` is authoritative, not the YAML. For each new round: write
`projects/trivia-quiz/<slug>/inputs/quiz_row.yaml` **and** seed it:

```bash
python -m scripts.trivia_quiz.seed_sheet --slug riddles-round-<n>
```

`seed_sheet` is **idempotent — it appends and SKIPS any slug already on the
sheet.** It will *not* update an already-seeded row. To change a row that's
already on the sheet (fix a riddle, vary a caption), edit the fixture and push
the changed cells with `scripts.trivia_quiz.sheets.write_post_field(sheets,
slug, field, value)` (field names = `POST_FIELDS`, e.g. `q1_answer`,
`q2_question`, `caption`, `pinned_comment`). Re-running `seed_sheet` won't do it.

### Before authoring: read what already exists

Pull every existing round from the sheet and avoid repeats:

```bash
python -c "from scripts.trivia_quiz.sheets import build_sheets, read_posts_bulk; \
[print(p['slug'], p['post_date'], p['q1_answer'], p['q2_answer'], p['q3_answer']) \
for p in read_posts_bulk(build_sheets(write=False))]"
```

(Run with the repo venv: `.venv/bin/python`.)

### Quality bar for a batch

- **No duplicate riddles AND no reused answers across the *entire* catalog**,
  not just within the new batch. A riddle is defined by its answer, so two
  riddles sharing an answer is effectively a duplicate — check both the
  question text and the answer against every existing row. Also watch
  **paraphrased near-duplicates** that share a gimmick with a different answer
  (e.g. "many rings but no finger"→tree vs "a ring but no finger"→telephone);
  a quick `difflib.SequenceMatcher` pass over normalized questions (flag ≥0.80)
  catches these. Vary the wordplay templates across the pack.
- **Difficulty ladder** Easy → Medium → Hard, and **q3 `game_themed: true`**
  with a `game_hook_line` carrying a brand token (`Travel Crush`). Make q3's
  riddle travel/journey-flavored so the game pivot lands naturally.
- **No-leak guardrail** (see §3): no answer text in `game_hook_line`,
  `bottom_cta`, or any caption/pinned field. ⚠️ The coded guard in
  `scripts/trivia_quiz/build.py` only scans `captions.tiktok` /
  `captions.instagram` / `captions.pinned_comment` — but current fixtures use
  the single shared `captions.caption` field, which that guard does **not**
  check. So **manually verify the answer doesn't appear in `captions.caption`**
  (watch substrings hiding in hashtags, e.g. "rain" inside `#b`​`rain`​`teaser`).
- **Unique captions per post.** Do not clone one caption across the batch —
  each round gets its own caption + pinned comment (vary the hook, the score
  ask, emoji, and hashtag mix). Identical captions across a pack read as
  spam and is a common batch mistake.
- **Dates:** schedule `post_date` per the cadence the user asked for; two posts
  sharing a date is fine (`order` disambiguates).

Author the fixtures, run the validation checks above, seed, then present a
summary table (date · slug · the three answers) for review. Seeding leaves rows
as `final_status: Draft` — building/rendering is a separate, explicitly-
requested step.
