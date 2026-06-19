# Asset Director — Trivia Captain Reaction Pipeline

## When To Use

You are producing the OpenArt avatar talking-head clip that feeds the
edit stage. The script artifact specifies the prompt + duration + variant
count; you choose between two production modes declared in the manifest:

- `manual_openart` — the user generates the clip in OpenArt's web UI
  using the saved `Captain Archibald` character, drops the mp4 into
  `scripts/trivia_captain_reaction/library/clips/<slug>.mp4`, and tells you go.
- `automated_openart` — `scripts/trivia_captain_reaction/openart_generate.py`
  drives the OpenArt UI via Playwright and downloads the variants
  directly.

## Prerequisites

| Layer | Resource | Purpose |
|---|---|---|
| Artifact | `projects/trivia-captain-reaction/<slug>/artifacts/brief.json` | Slug, Day |
| Artifact | `projects/trivia-captain-reaction/<slug>/artifacts/script.json` | OpenArt prompt, character, duration |
| Script | `scripts/trivia_captain_reaction/openart_generate.py` | Playwright driver wrapper |
| Driver | `scripts/common/openart_driver.py` | Shared OpenArt Playwright driver |
| Library | `scripts/trivia_captain_reaction/library/clips/` | Gitignored — final mp4s land here |
| Schema | `schemas/artifacts/asset_manifest.schema.json` | Artifact validation |

## Process

### 1. Pick The Production Mode

Default to `manual_openart` unless the user explicitly opted in to automation
("drive OpenArt for me", "run the batch"). Reasons to stay manual:

- First-time render for a new slug — prompt iteration is hands-on
- The user is at the keyboard and wants control over the variant pick
- Automated driver is broken / rate-limited / OpenArt UI changed

Announce the chosen mode before doing any work.

### 2. Verify The OpenArt Character Exists

The user maintains the `Captain Archibald` character in OpenArt's
"My Library → Characters" page. This is a one-time setup; we don't
verify it programmatically — but if the driver's
`_select_character(page, "Captain Archibald")` fails (character not
found in the picker), STOP and ask the user to create it before
retrying. Do NOT silently fall back to a different character or to
a text-only describe-him prompt.

### 3a. Manual OpenArt Branch

Print the OpenArt config from `script.json` so the user can copy it
into the OpenArt UI:

```
Model:        Seedance 2.0
Character:    Captain Archibald      (saved character in your library)
Aspect:       9:16
Resolution:   1080p
Duration:     15s
Audio:        ON                     (Seedance generates the dialogue lip-synced)
Variants:     2

Prompt:
  <full prompt from script.metadata.openart.prompt — includes spoken
   lines + voice direction; Seedance reads these as the character's VO>
```

Pause and tell the user: "Generate this in OpenArt, download the
mp4 to `scripts/trivia_captain_reaction/library/clips/<slug>.mp4`, then say go.
If you rendered two variants, pick your favorite and rename it to the
canonical filename above (drop the `_v1`/`_v2` suffix)."

When the user says go, verify the file exists and proceed.

### 3b. Automated OpenArt Branch

Run the driver:

```bash
python scripts/trivia_captain_reaction/openart_generate.py <slug>
# Optional flags:
#   --variants 1     # save one credit
#   --headless       # no visible browser window
#   --force          # regenerate even if file exists
```

If the driver fails (login expired, character not found, page changed),
fall back to manual and tell the user exactly what failed and what they
need to do.

Announce the call before running: model name + variant count + character
+ cost class + expected wall time. OpenArt credits are real money.

### 4. Verify The Clip

Once a clip exists at `scripts/trivia_captain_reaction/library/clips/<slug>.mp4`,
quick sanity:

- `ffprobe` says ~15s duration (within ±2s of script.json's `duration_s`)
- Aspect is 1080x1920 (9:16). If the OpenArt export is 2160 or 720,
  the assemble step will rescale.
- **Audio track MUST be present** (Seedance 2.0 native synced voice). If
  the audio track is missing, the clip was generated with `audio_on=false`
  by mistake — regenerate.
- Spoken dialogue audibly matches the three beats in `script.json`. If
  Seedance improvised, regenerate with a more constrained prompt.

If the clip is dramatically off-spec, surface it and ask whether to
regenerate or proceed.

### 5. Write Asset Manifest

`projects/trivia-captain-reaction/<slug>/artifacts/asset_manifest.json`:

```jsonc
{
  "schema_version": "0.1",
  "pipeline": "trivia-captain-reaction",
  "metadata": {
    "sheet_revision": "<copied from script>",
    "slug": "<slug>"
  },
  "assets": [
    {
      "type": "video",
      "role": "avatar_clip",
      "path": "scripts/trivia_captain_reaction/library/clips/<slug>.mp4",
      "duration_s": 15.2,
      "aspect": "1080x1920",
      "fps": 30,
      "generation_summary": {
        "provider": "openart",
        "model": "Seedance 2.0",
        "character": "Captain Archibald",
        "duration_target_s": 15,
        "variant_count": 2,
        "audio_on": true,
        "audio_source": "seedance_native",
        "prompt": "<full prompt — includes spoken lines + voice direction>",
        "mode": "manual_openart" | "automated_openart"
      }
    }
  ]
}
```

VO is NOT a separate asset — it's part of the avatar clip (Seedance 2.0
generates native synced voice). The asset manifest only carries the
single video.

### 6. Self-Review + Checkpoint

`human_approval_default: false` — auto-proceed to edit if all checks pass.
Review focus:

- Clip exists at the canonical path
- Duration within ±2s of target
- Aspect 9:16
- Asset manifest is schema-valid

## What Not To Do

- Do not silently swap the character to a different saved character or
  to a text-only describe-him prompt. Captain Archibald is the lock.
- Do not regenerate clips without `--force` if the canonical file already exists.
- Do not pre-resolve VO or audio paths — that's edit's job.
- Do not concat / trim / transcode here — that's edit's job.
- Do not write Drive URLs to the Queue from this stage.
