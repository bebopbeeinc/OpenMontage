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
