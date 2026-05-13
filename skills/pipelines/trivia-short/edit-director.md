# Edit Director - Trivia Short Pipeline

## When To Use

The asset manifest is ready (three clips in `projects/<slug>/assets/video/`)
and you need to produce the stitched `bg.mp4` that the compose stage will
caption. This stage is a thin wrapper around `scripts/trivia/assemble_modular.py`.

## Prerequisites

| Layer | Resource | Purpose |
|---|---|---|
| Artifact | `projects/<slug>/artifacts/asset_manifest.json` | 3 clip paths |
| Artifact | `projects/<slug>/artifacts/script.json` | VO copy + windows |
| Artifact | `projects/<slug>/artifacts/brief.json` | Mode (drives SFX cue set) |
| Script | `scripts/trivia/assemble_modular.py` | The workhorse |
| Schema | `schemas/artifacts/edit_decisions.schema.json` | Artifact validation |
| Memory | `feedback_trivia_assembly_flags` | House defaults |

## The Default Flags (Mandatory)

Per user memory: trivia assembly is always full production. The four flags
ALWAYS apply unless the user explicitly opts one out in the current turn:

```
--with-vo       # ElevenLabs VO at the 4 VO_WINDOWS (hook/claim/resolution/cta)
--with-music    # background music bed under the timeline
--with-sfx      # mode-specific SFX cue set (FACTS or CHOICES)
--silent-hook   # mute the source clip's hook segment; burn the hook caption
```

If you are about to skip any of these, **announce it** to the user with the
reason before running. Silent deviation is a contract violation.

## Process

### 1. Verify Inputs

- All three clip paths in `asset_manifest.json` exist on disk.
- The brief has `metadata.trivia.mode` set (drives `SFX_CUES_FACTS` vs
  `SFX_CUES_CHOICES` in `assemble_modular.py`).
- The script has VO copy for hook / claim / resolution / cta with text that
  fits the VO_WINDOWS budgets (0.3-2.7 / 3.0-10.3 / 10.6-11.7 / 11.9-13.3).

### 2. Resolve Hook Emphasis Override (Sheet Read + Write)

The **Emphasis Override** column holds the per-row emphasis word for the
burned hook caption. The sheet is the source of truth here too —
`assemble_modular.py` reads it on entry and writes back the resolved word
so the human can see what landed.

Read order (handled by the script):

1. The row's `emphasis_override` value if non-empty
2. Hooks library tab lookup
3. Auto-pick (`assemble_modular.py`'s heuristic)

Write back: `assemble_modular.py` writes the resolved word to the
Emphasis Override cell after the run (via `cell_for(sheets, row,
"emphasis_override")`). Record the same value in
`edit_decisions.metadata.hook_emphasis` so the artifact and the sheet
agree. This is the only sheet write this stage performs.

### 3. Run Assemble

```bash
source .venv/bin/activate
python scripts/trivia/assemble_modular.py <row> <slug> \
  --with-vo --with-music --with-sfx --silent-hook
```

Announce the call first. Expected wall time: 60-120s (VO synthesis is the
bottleneck).

Outputs:

- `projects/<slug>/assets/video/bg.mp4` — the stitched silent-hook background
- `projects/<slug>/artifacts/edit_decisions.json` (you write this; the script
  writes intermediate stage files)

If the script exits non-zero, do not retry blindly. Read the error:

- **`reaction_filename` empty or file missing in `scripts/trivia/library/reactions/`**: asset stage didn't deliver — re-enter assets.
- **VO synthesis failed (ElevenLabs auth/quota)**: announce + ask user. Do
  NOT fall back to silent VO unless approved.
- **ffmpeg xfade failed**: usually a duration mismatch — check ffprobe on
  each clip against the asset manifest.

### 4. Validate The Output

- `bg.mp4` exists at the expected path.
- ffprobe duration is 14.4s ± 0.5s.
- Audio stream is present (assuming `--with-vo`).
- Resolution is 1080x1920.

### 5. Write Edit Decisions

Per `schemas/artifacts/edit_decisions.schema.json`. Trivia-specific shape:

```jsonc
{
  "metadata": {
    "sheet_revision": "a1b2c3d4e5f6",
    "assemble_flags": ["--with-vo", "--with-music", "--with-sfx", "--silent-hook"],
    "hook_emphasis": "moon",
    "sfx_cue_set": "facts",   // or "choices"
    "vo_windows": {
      "hook":       [0.3, 2.7],
      "claim":      [3.0, 10.3],
      "resolution": [10.6, 11.7],
      "cta":        [11.9, 13.3]
    },
    "output_path": "projects/<slug>/assets/video/bg.mp4",
    "duration_s": 14.4
  }
}
```

### 6. Self-Review + Checkpoint

`human_approval_default: false`. Auto-proceed to compose if validation passes.
If anything's off (duration drift, missing audio, etc.), pause and ask.

## What Not To Do

- Do not skip the four default flags without announcing it.
- Do not patch VO copy here — that's script's job. If VO doesn't fit the
  windows, re-enter script.
- Do not edit the source clips here. If a clip is the wrong duration,
  re-enter assets.
- Do not re-run assemble in a loop hoping for different output. The script is
  deterministic given fixed inputs; same input = same output.
