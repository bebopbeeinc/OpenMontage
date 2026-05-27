# Edit Director — Trivia Quiz Pipeline

## When To Use

After `assets` produces `asset_manifest.json`. You are calling
`scripts/trivia_quiz/assemble_quiz.py` to produce `bg.mp4` — the
5-segment video that the Remotion composition will overlay captions and
UI on top of.

## Default Flags

```
--with-vo --with-music --with-sfx
```

Per user memory: full production is the default. Don't strip flags
unless the human asks for a silent-mix preview.

## Process

### 1. Invoke `assemble_quiz.py`

```
python -m scripts.trivia_quiz.assemble_quiz \
  --slug <slug> \
  --with-vo --with-music --with-sfx
```

The script reads `script.json` + `asset_manifest.json` + `styles/trivia-quiz.yaml`,
runs ffmpeg Ken Burns on each still (8/9/8s), inserts the locked hook card
(3s solid bg) and score-card (4s solid bg) as colored placeholder segments
(Remotion will overlay the actual UI at compose), generates ElevenLabs VO
for the 5 lines, fetches music from `music_library/`, layers SFX from
`sfx_library/`, and xfade-concats everything into
`projects/trivia-quiz/<slug>/assets/video/bg.mp4`.

### 2. Validate Output

- File exists at `projects/trivia-quiz/<slug>/assets/video/bg.mp4`
- `ffprobe` reports duration between 30.0s and 33.0s
- Audio stream present
- 1080×1920 portrait

### 3. Write `edit_decisions.json`

```json
{
  "metadata": {
    "bg_path": "projects/trivia-quiz/<slug>/assets/video/bg.mp4",
    "target_duration_s": 31.6,
    "actual_duration_s": 31.58,
    "assemble_flags": ["--with-vo", "--with-music", "--with-sfx"],
    "music_track": "<filename from brief>",
    "sfx_set": "quiz-default"
  }
}
```

## Output

`projects/trivia-quiz/<slug>/artifacts/edit_decisions.json` plus the actual `bg.mp4`
file.

## Checkpoint

`human_approval_default: false`. Auto-proceed if `bg.mp4` passes ffprobe.
