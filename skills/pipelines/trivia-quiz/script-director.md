# Script Director — Trivia Quiz Pipeline

## When To Use

After `idea` produces `brief.json`. You are budgeting 5 VO lines against the
locked segment windows in `styles/trivia-quiz.yaml`, brand-checking the
copy, and writing `script.json`.

## VO Windows (locked)

| Line | Window (s) | Spoken when | Budget |
|---|---|---|---|
| `hook_card` | 0.0–2.8 | over the locked hook card | "Only 10% can answer all 3" (or fixture override) |
| `q1_reveal` | 7.5–10.5 | as Q1 reveal stamp lands | answer + 1-line surprise fact |
| `q2_reveal` | 16.5–19.5 | as Q2 reveal stamp lands | answer + 1-line surprise fact |
| `q3_reveal` | 25.5–28.0 | as Q3 reveal stamp lands | answer + 1-line surprise fact |
| `score_card` | 28.5–31.5 | over score CTA | score prompt + Travel Crush lockup voiceover |

**Question text is NEVER spoken.** Questions read on screen during the
countdown so the viewer reads + commits silently. Speaking them would steal
the commit beat that makes the format work.

## Process

### 1. Generate VO Text

From `brief.json.metadata.quiz`:

- `hook_card`: literal from `styles/trivia-quiz.yaml::show_identity.default_hook`
  unless the brief specifies a `show_hook_variant`.
- `qN_reveal`: phrase as `"The answer is {answer}. {surprise_fact}"`. Strip the
  choice prefix from `answer` ("B) Sudan" → "Sudan") when speaking.
- `score_card`: `"Your score? Comment 0, 1, 2, or 3 below. {game_hook_line}"`
  if `q3_game_tease` is true, otherwise `"Your score? Comment 0, 1, 2, or 3
  below. Play Travel Crush — link in bio."`

### 2. Brand-Token Check

Capitalize `Captain`, `Travel Crush`, `Fennec` in every VO line. Whisper
post-processing in `scripts/common/transcribe.py` already handles drift; this
step is for ensuring the source text is canonical.

### 3. Estimate Speech Duration

For each line, estimate `chars / 16` seconds at speed=1.05. Each line must fit
its window with ≥200ms padding on each side. If a line overflows, surface a
revision request: either shorten the `surprise_fact` (most common cause) or
flag the brief for re-write.

### 4. Write `script.json`

```json
{
  "metadata": {
    "vo": [
      { "id": "hook_card",   "text": "...", "window_s": [0.0, 2.8] },
      { "id": "q1_reveal",   "text": "...", "window_s": [7.5, 10.5] },
      { "id": "q2_reveal",   "text": "...", "window_s": [16.5, 19.5] },
      { "id": "q3_reveal",   "text": "...", "window_s": [25.5, 28.0] },
      { "id": "score_card",  "text": "...", "window_s": [28.5, 31.5] }
    ],
    "segments": [
      { "id": "hook_card",  "duration_s": 3.0, "render": "remotion_only" },
      { "id": "q1",         "duration_s": 8.0, "backdrop": "q1_bg" },
      { "id": "q2",         "duration_s": 9.0, "backdrop": "q2_bg" },
      { "id": "q3",         "duration_s": 8.0, "backdrop": "q3_bg" },
      { "id": "score_card", "duration_s": 4.0, "render": "remotion_only" }
    ],
    "brand_tokens_applied": ["Captain", "Travel Crush", "Fennec"]
  }
}
```

## Output

`projects/trivia-quiz/<slug>/artifacts/script.json` — 5 VO lines, 5 segments, brand
tokens canonicalized.

## Checkpoint

`human_approval_default: true`. Show the 5 VO lines side-by-side with their
window budgets and chars-per-second estimate. Wait for approval.
