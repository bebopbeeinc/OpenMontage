# Asset Director — Trivia Captain Pipeline

## When To Use

You are producing the OpenArt avatar talking-head clip that feeds the
edit stage. The script artifact specifies the prompt + duration + variant
count + reference image; you choose between two production modes declared
in the manifest:

- `manual_openart` — the user generates the clip in OpenArt's web UI
  using the saved `Captain Archibald` character, drops the mp4 into
  `scripts/trivia_captain/library/clips/<slug>.mp4`, and tells you go.
- `automated_openart` — `scripts/trivia_captain/openart_generate.py`
  drives the OpenArt UI via Playwright and downloads the variants
  directly.

## Prerequisites

| Layer | Resource | Purpose |
|---|---|---|
| Artifact | `projects/trivia-captain/<slug>/artifacts/brief.json` | Slug, Day |
| Artifact | `projects/trivia-captain/<slug>/artifacts/script.json` | OpenArt prompt, character, duration, reference image |
| Script | `scripts/trivia_captain/openart_generate.py` | Playwright driver wrapper |
| Driver | `scripts/common/openart_driver.py` | Shared OpenArt Playwright driver |
| Library | `scripts/trivia_captain/library/clips/` | Gitignored — final mp4s land here |
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

### 3. The Reference Image (Split-Screen Tablet)

`scripts/trivia_captain/openart_generate.py` reads `reference_image` from
Queue!M (live; the sheet is the SoT) and passes it to
`scripts/common/openart_driver.py` `generate_clip(reference_image=...)`.
The driver clicks OpenArt's **"Upload Media"** pill and attaches the file
**BEFORE** selecting the character. The tablet that Archibald holds up in
the generated clip then shows that image rendered in-camera (proven
2026-06-03, take04 of la-tomatina-tomato-fight).

The reference is a **full-bleed fact still** (fact image filling the whole
tablet screen + Travel Crush logo watermark across the top), built at
**Generate time by Phase 0** (`scripts/trivia_captain/build_fact_assets.py`),
not pre-generated. Phase 0 reads the fact-image prompt from **Queue!N**,
runs `openart_image` (cheap model, `3:4`, `1K`), composites the
`video`-layout `tablet_ref.png`, and writes Queue!M. It is **idempotent** —
reuses an existing reference on avatar re-rolls; `--force` (the `↻ new
image` button) re-rolls a fresh fact image. So Queue!M is populated by the
chain itself; if Queue!N is empty, send the row back to the script director
(it can't build the tablet without a fact-image prompt). The old 50/50
split is `build_tablet_ref.py --layout split` (rollback only).

**Images only.** Video references DO NOT work — the OpenArt submit
endpoint returns `400 "Video metadata is required"`. The reference must
be a still image.

### 4a. Manual OpenArt Branch

Print the OpenArt config from `script.json` so the user can copy it
into the OpenArt UI:

```
Model:        Seedance 2.0
Character:    Captain Archibald     (saved character in your library)
Reference:    <metadata.openart.reference_image>  (Upload Media → tablet splash)
Aspect:       9:16
Resolution:   1080p
Duration:     15s
Audio:        ON                     (Seedance generates the dialogue lip-synced)
Variants:     2

Prompt:
  <full prompt from script.metadata.openart.prompt — includes spoken
   lines + voice direction; Seedance reads these as the character's VO>
```

Pause and tell the user: "Upload the reference image via Upload Media,
then generate this in OpenArt, download the mp4 to
`scripts/trivia_captain/library/clips/<slug>.mp4`, then say go.
If you rendered two variants, pick your favorite and rename it to the
canonical filename above (drop the `_v1`/`_v2` suffix)."

When the user says go, verify the file exists and proceed.

### 4b. Automated OpenArt Branch

Run the driver:

```bash
python scripts/trivia_captain/openart_generate.py <slug>
# Optional flags:
#   --variants 1     # save one credit
#   --headless       # no visible browser window
#   --force          # regenerate even if file exists
```

If the driver fails (login expired, character not found, page changed),
fall back to manual and tell the user exactly what failed and what they
need to do.

Announce the call before running: model name + variant count + character
+ reference image + cost class + expected wall time. OpenArt credits are
real money.

### 5. Verify The Clip

Once a clip exists at `scripts/trivia_captain/library/clips/<slug>.mp4`,
quick sanity:

- `ffprobe` says ~15s duration (within ±2s of script.json's `duration_s`)
- Aspect is 1080x1920 (9:16). If the OpenArt export is 2160 or 720,
  the assemble step will rescale.
- **Audio track MUST be present** (Seedance 2.0 native synced voice). If
  the audio track is missing, the clip was generated with `audio_on=false`
  by mistake — regenerate.
- Spoken dialogue audibly matches the three beats in `script.json`. If
  Seedance improvised, regenerate with a more constrained prompt.
- **Tablet-reveal frame.** Extract a frame at the tablet-reveal beat and
  verify the fact is on the screen, fills it, **reads clearly** (not shrunk
  to a corner), and the Travel Crush logo watermark is legible. If the
  tablet is blank or shows a garbled image, the reference upload didn't
  take — regenerate after confirming the Upload Media step.
- **Screen image stays consistent (user feedback 2026-06-08).** Extract
  two frames a second apart during the fact beat and confirm the screen
  shows the SAME image — same composition, same logo position. Seedance
  re-hallucinates in-camera screens, so the fact can drift/morph between
  frames; if the screen content changes noticeably, re-roll (a frozen,
  identical screen is the bar — do NOT ask for motion).
- **First frame — tablet partly visible (user feedback 2026-06-08).**
  Extract frame 0 and verify the tablet's top edge + a sliver of its lit
  screen are already in the lower frame, before the raise. The always-
  visible tablet is what makes viewers curious and wait for the reveal.
  If frame 0 has the tablet fully out of shot, it's a re-roll — regenerate
  (the rule is in the prompt, but like the orientation it's non-determin-
  istic).

If the clip is dramatically off-spec, surface it and ask whether to
regenerate or proceed.

### 6. Write Asset Manifest

`projects/trivia-captain/<slug>/artifacts/asset_manifest.json`:

```jsonc
{
  "schema_version": "0.1",
  "pipeline": "trivia-captain",
  "metadata": {
    "sheet_revision": "<copied from script>",
    "slug": "<slug>"
  },
  "assets": [
    {
      "type": "video",
      "role": "avatar_clip",
      "path": "scripts/trivia_captain/library/clips/<slug>.mp4",
      "duration_s": 15.2,
      "aspect": "1080x1920",
      "fps": 30,
      "generation_summary": {
        "provider": "openart",
        "model": "Seedance 2.0",
        "character": "Captain Archibald",
        "reference_image": "<repo-relative splash path>",
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

### 7. Self-Review + Checkpoint

`human_approval_default: false` — auto-proceed to edit if all checks pass.
Review focus:

- Clip exists at the canonical path
- Duration within ±2s of target
- Aspect 9:16
- Tablet splash legible at the reveal beat
- Asset manifest is schema-valid

## What Not To Do

- Do not silently swap the character to a different saved character or
  to a text-only describe-him prompt. Captain Archibald is the lock.
- Do not pass a video as the reference image — images only (the submit
  endpoint rejects video metadata).
- Do not regenerate clips without `--force` if the canonical file already exists.
- Do not pre-resolve VO or audio paths — that's edit's job.
- Do not concat / trim / transcode here — that's edit's job.
- Do not write Drive URLs to the Queue from this stage.
