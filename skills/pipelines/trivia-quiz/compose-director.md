# Compose Director — Trivia Quiz Pipeline

## When To Use

After `edit` produces `bg.mp4`. You are running Whisper to get word-level
timings, post-processing brand tokens, then rendering the final video via
the Remotion `TriviaQuiz` composition.

## Process

### 1. Transcribe `bg.mp4`

```
python -m scripts.common.transcribe \
  --video projects/trivia-quiz/<slug>/assets/video/bg.mp4 \
  --out projects/trivia-quiz/<slug>/artifacts/words.json \
  --brand-tokens Captain "Travel Crush" Fennec
```

Brand-token post-processing fixes Whisper drift on "Travel Crush" ↔ "travel
crash" / "travel krush" — already wired in `transcribe.py`.

### 2. Write `quiz_meta.json`

Remotion needs a structured per-segment timeline so it knows when to draw
the hook card, the question cards, the countdown bars, the reveal stamps,
and the score card. Build this from `script.json` + `brief.json`:

```json
{
  "show": {
    "title": "Trivia by Travel Crush",
    "hook": "Only 10% can answer all 3",
    "score_cta": "Your score? 0 / 1 / 2 / 3 👇",
    "lockup_text": "Play in bio 👇",
    "lockup_brand": "TRAVEL CRUSH",
    "placeholder_url": "play.travelcrush.com"
  },
  "questions": [
    {
      "id": "q1",
      "start_s": 3.0,
      "duration_s": 8.0,
      "question": "What is Scotland's official national animal?",
      "choices": ["A) Highland cow", "B) Stag", "C) Unicorn"],
      "answer_index": 2,
      "answer_label": "Unicorn",
      "countdown_start_s": 0.0,         // relative to segment start
      "countdown_duration_s": 3.0,
      "reveal_at_s": 4.5,
      "surprise_fact": "It's been Scotland's heraldic animal since the 1100s.",
      "difficulty": "Easy"
    },
    { "id": "q2", ... },
    { "id": "q3", ... }
  ],
  "score_card": {
    "start_s": 28.0,
    "tomorrow_tease": "Tomorrow: World Capitals",
    "reward": "",
    "game_hook_line": "Sail to the Bahamas in Travel Crush."
  }
}
```

Write to `projects/trivia-quiz/<slug>/artifacts/quiz_meta.json`.

### 3. Render via Remotion `TriviaQuiz`

```
cd remotion-composer
npx remotion render src/index-trivia-quiz.tsx TriviaQuiz \
  ../projects/trivia-quiz/<slug>/renders/final_quiz.mp4 \
  --props='{"slug":"<slug>"}'
```

`TriviaQuiz` loads `bg.mp4`, `words.json`, and `quiz_meta.json` from the
project dir via `calculateMetadata` (same pattern as `TriviaWithBg`).

### 4. MANDATORY Frame Review

Per user memory (`feedback_review_renders`). Extract 7 frames:

| Frame at | Verify |
|---|---|
| 1.5s | Locked hook card centered |
| 5.0s | Q1 question + countdown bar |
| 9.0s | Q1 reveal stamp on correct choice |
| 13.0s | Q2 countdown visible |
| 18.0s | Q2 reveal stamp |
| 25.0s | Q3 reveal stamp |
| 29.0s | Score card + Travel Crush lockup |

Extract via:
```
ffmpeg -i renders/final_quiz.mp4 -ss <t> -frames:v 1 review/frame_<t>.jpg
```

### 5. Auto-Fix Defects

Per user memory (`feedback_review_autofix`). If a defect is found:
- Caption drift > 200ms → patch `words.json` and re-render
- Countdown timing wrong → patch `quiz_meta.json::questions[N].countdown_*`
  and re-render
- Reveal stamp on wrong choice → check `answer_index`, fix, re-render

Don't list defects and ask. Fix in the same turn.

### 6. Write `render_report.json`

```json
{
  "metadata": {
    "output_path": "projects/trivia-quiz/<slug>/renders/final_quiz.mp4",
    "duration_s": 31.6,
    "frame_review_passed": true,
    "frames_extracted": ["1.5s", "5.0s", "9.0s", "13.0s", "18.0s", "25.0s", "29.0s"],
    "auto_fixes_applied": []
  }
}
```

## Output

`projects/trivia-quiz/<slug>/artifacts/render_report.json` plus the final mp4.

## Checkpoint

`human_approval_default: true`. Present the 7 frames + the auto-fix log
to the human. Wait for approval before publish.
