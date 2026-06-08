# Script Director — Trivia Captain Pipeline

## When To Use

You are turning the brief into a three-beat script + a single OpenArt
prompt for the Captain Archibald avatar clip. The brief carries the
resolved EN copy from the daily-trivia sheet; your job is to:

1. **Pick a hook from the hook library** (rotation rules apply) — the
   hook is ALWAYS the first line of the reel. Cold open, zero setup.
2. **Write three VO beats** (hook / fact / kicker) in the late-discovery
   "and you're telling me this NOW?" register — amused disbelief, never
   anger.
3. **Choose a backdrop** from the travel-archive palette and **confirm
   the reference image** (the game splash shown on the tablet).
4. **Assemble one OpenArt prompt** in the CAPS-section format that locks
   the character reference, choreographs the tablet show-and-tell,
   embeds the spoken lines, and gives Seedance 2.0 the voice direction.
   Seedance generates the dialogue with native lip-sync — no TTS step.
5. **Write the VO lines, prompt, caption, and reference-image path back
   to the Queue** so the human can review without leaving the sheet.

The brief's `metadata.trivia_captain.correct_explanation_en` is your
*source of truth* for the fact content. Do not invent extra detail —
keep the fact verbatim or compress it; never embellish. The only
licensed inventions are Archibald's **credentials** ("Fifty years.
Six trips to Spain.") and **proximity claims** ("I was there in '87")
— those are persona fiction and may be made up freely, as long as they
are plausible for a 70-year-old lifelong traveler.

## Prerequisites

| Layer | Resource | Purpose |
|---|---|---|
| Artifact | `projects/trivia-captain/<slug>/artifacts/brief.json` | Question / CorrectAnswer / CorrectExplanation |
| Playbook | `styles/trivia-captain.yaml` | Persona register, hook library, backdrop palette, prompt template, voice spec |
| Sheet | TriviaCaptainQueue!Queue | Write VO to F/G/H, prompt to J, caption to K, reference image to M |
| Schema | `schemas/artifacts/script.schema.json` | Artifact validation |

## Process

### 1. Read The Brief

```python
brief = json.loads((Path("projects") / slug / "artifacts" / "brief.json").read_text())
trivia = brief["metadata"]["trivia_captain"]
```

Pull `question_en`, `correct_answer_en`, `correct_explanation_en`.

### 2. Pick The Hook (HOOK ALWAYS FIRST)

Read `styles/trivia-captain.yaml` → `hook_library`. Rules:

- The hook opens the reel — the first frame is Archibald already
  mid-reaction, the first words are the incredulity line. Nothing
  before it.
- Never the same hook key as the previous row (check recent Queue rows).
- The signature `now` hook ("And I'm only hearing about this NOW?") at
  most every third row.

### 3. Write The Three Beats

Proven structure (15s, calibrated on la-tomatina-tomato-fight take04,
2026-06-03):

| Beat | Duration | Content |
|---|---|---|
| hook + credentials | ~5s | cold-open incredulity line + travel credentials as evidence |
| fact (attributed to the game) | ~7s | "And this game tells me…" + the surprising detail, tablet raised |
| (short beat — Seedance produces this) | ~1s | smiling head shake, soft chuckle, no words |
| kicker | ~3s | proximity twist + the joke on himself |

**Hook beat constraints:**
- Opens with the chosen hook-library line, then credentials.
- Credentials are persona fiction: a count ("Fifty years. Six trips to
  Spain.") tied to the fact's place. 14-18 words for the whole beat.

**Fact beat constraints:**
- MUST attribute the fact to the game: "And this game tells me…" /
  "And this game is telling me…".
- Use the CorrectAnswer and the surprising detail verbatim where possible.
- 20-26 words.

**Kicker constraints:**
- One warm line, joke on himself. 9-12 words.
- Default shape: proximity + source-indignity ("I was there in '87.
  A game had to tell me.") — vary the second half across rows
  ("I had to download an app to learn this", "The game knew. I didn't.")
  so it doesn't wear out.
- Never angry. The wound is cozy.

**Word budget total: 40-46 words.** Seedance 2.0's duration slider hard-caps
at 15s; 44 words ≈ 14.7s spoken + the short beat fits with nothing
truncated. Anything over ~48 words risks a rushed or clipped kicker.

**Worked example — la-tomatina-tomato-fight (take04, format-proving row):**

| Beat | Copy |
|---|---|
| hook | "And I'm only hearing about this NOW? Fifty years. Six trips to Spain." |
| fact | "And this game tells me a town near Valencia throws 130 tons of tomatoes at each other every August." |
| kicker | "I was there in '87. A game had to tell me." |

(44 words. Hook 0-5s, fact 5-12s, beat, kicker 13-15s.)

### 4. Pick A Backdrop + Confirm The Reference Image

**Backdrop:** read `styles/trivia-captain.yaml` → `backdrops.prompt_palette`.
Stay in the travel-archive family; vary per row; nod to the fact's region
where natural (Spanish fact → souvenir plates / terracotta balcony).
Never the same backdrop two days in a row.

**Reference image (full-bleed fact + logo watermark):** what OpenArt
renders in-camera on Archibald's tablet, as a **full-bleed still of the
fact** — the fact scene fills the whole screen with the Travel Crush logo
as a top-band watermark (user feedback 2026-06-08 — the old 50/50 split
made the fact too small to read). Do NOT try to make it a "playing video":
Seedance re-hallucinates the in-camera screen each frame, so motion reads
as drift, not playback (proven on take08). Direct the screen to stay a
frozen, unchanging still instead.

```
┌──────────────┐
│ [TRAVEL CRUSH]│  top band — the logo over a dark scrim, like a video
│  la tomatina  │             player's branding watermark (same every row)
│  crowd scene  │  full-bleed — a per-row photoreal FACT image fills the
│  (full bleed) │             entire screen so it reads clearly
└──────────────┘
```

**Do NOT pre-generate the fact image.** As of 2026-06-08 the image + tablet
reference are built at **Generate time** (Phase 0 of the chain), not as a
separate authoring step. Your job here is only to **author the fact-image
prompt** and write it to **Queue!N (Fact Image Prompt)**:

- Describe the actual fact scene (the crowd, the place, the object, the
  action) photoreal unless the user asks otherwise, **portrait composition**,
  with `no on-screen text / no logos` (the logo watermark is added by the
  compositor, not the model).
- Keep it concrete and singular — one clear subject that will read at tablet
  size. Example (Iceland): "Photorealistic close-up of an old McDonald's Big
  Mac and fries preserved in a glass museum case… vertical composition. No
  text, no logos."

At Generate, `build_fact_assets.py <slug>` reads Queue!N → runs
`openart_image` (cheap model, `3:4`, `1K` — the image is reinterpreted on
the tablet, so high fidelity is wasted spend) → composites the full-bleed
`video`-layout reference (fact + Travel Crush logo watermark) → writes
`tablet_ref.png` and Queue!M. It is **idempotent**: it reuses an existing
reference on avatar re-rolls; the `↻ new image` button (or `--force`)
re-rolls a fresh fact image.

```
┌──────────────┐
│ [TRAVEL CRUSH]│  top band — the logo over a dark scrim (watermark)
│  fact scene   │  full-bleed — the per-row fact image fills the screen
│  (full bleed) │             so it reads clearly
└──────────────┘
```

(`build_tablet_ref.py --layout video` is what the helper calls; `--layout
split` is the legacy 50/50 look, rollback only.)

Record the `tablet_ref.png` repo-relative path in BOTH `script.json
metadata.openart.reference_image` AND Queue!M (the `--update-queue` flag
handles Queue!M).

### 5. Build The OpenArt Prompt (CAPS-section format)

Use the playbook's `asset_generation.openart.prompt_template`. The
prompt has seven CAPS sections in this order:

```
ACTING / SETTING / PROPS / DIALOGUE / VOICE / SHOT / EXCLUSIONS
```

**This deliberately diverges from trivia-reaction's label-free rule.**
Four takes (2026-06-03) prove Seedance does not read CAPS section
headers aloud. What it DOES read aloud — and stays forbidden — is beat
names near dialogue ("the kicker:") and timing markers ("0-2s").

Section guidance:

- **ACTING** — prose arc from the playbook (`avatar.acting_beats.arc`):
  armchair video-call hold, cold-open mid-reaction, credentials chuckle,
  tablet raised on "and this game" (show-and-tell, like a grandfather
  showing off a grandkid's drawing — NOT accusatory), smiling-head-shake
  beat, kicker with the can-you-believe-it tablet waggle. **Always-visible
  tablet (user feedback 2026-06-08):** the tablet is partly in frame for
  the ENTIRE clip — from the first frame its top edge + a sliver of the
  lit screen peek into the lower frame (resting in his lap / against his
  chest), building curiosity before the full raise; lowering after the
  reveal returns it to that partially-visible resting spot, it never
  leaves the shot. State this explicitly in the ACTING prose.
- **SETTING** — the chosen backdrop, one paragraph, with a light direction.
- **PROPS** — direct the screen as a **full-bleed still of the fact** that
  fills the screen and **stays frozen** (user feedback 2026-06-08): the
  tablet screen "displays {one-line fact-scene description} filling the
  whole screen, with the Travel Crush logo as a small watermark across the
  top… bright and slightly glossy on a backlit screen, warm lamp light
  reflecting faintly off the glass. The fact fills the screen and reads
  clearly. CRITICAL: the exact same image stays frozen on the screen for
  the entire clip — it must not change, drift, or morph." Do NOT ask for
  motion/"playing video" — Seedance re-hallucinates the screen and it reads
  as inconsistency. Also note the **always-visible** rule here: the tablet
  is partly in frame at all times — even before the raise, top edge + a
  sliver of the glowing screen show in the lower frame — and never leaves
  the shot.
- **DIALOGUE** — `He says:` then the full spoken text in quotes. The
  mid-reel beat is a parenthetical stage direction on its own line —
  `(short beat — smiling head shake, soft chuckle)` — proven not spoken.
- **VOICE** — the playbook's `avatar.voice_direction`, plus per-detail
  notes for THIS fact (which numbers land like delicious twists). Must
  end with the anchor line: "Never angry, never shouting, never deadpan
  — unbelievable but funny, and he loves it."
- **SHOT** — verbatim from the template: medium close-up, handheld
  selfie framing, slightly low angle (older-man video-call hold), warm
  lamp light, photoreal, subtle camera micro-wobble.
- **EXCLUSIONS** — no on-screen text/captions/graphics (captions in
  post); tablet shows only the referenced splash — no invented UI/text,
  no fingers covering the screen.

**Critical:** The OpenArt **character reference** carries Archibald's
identity. Never describe his face / beard / glasses / clothes from text
— competing descriptions cause drift. Refer to him as "He".

Target: 15s, 480p, 9:16, audio ON, 1 variant.

### 6. Write The Instagram Post Caption

Format locked by `styles/trivia-captain.yaml.post_caption`. Two
paragraphs separated by a blank line:

**Paragraph 1 — body** (1-2 short lines, 60-120 chars):
- Wounded dignity played for laughs; never a fact recap; no emojis.
- Patterns:
  - "[Credential count]. [Number repeated alone]. [Thing] was [proximity] the entire time."
  - "[N] trips to [place] and a puzzle game knew about [thing] before I did."
  - "Apparently everyone knew. Everyone except the man who's been there [N] times."

**Paragraph 2 — hashtags** (5 total: 2 brand + 3 niche, one line):
- **Brand tags (fixed, always first):** `#CaptainArchibald #TravelCrush`
- **Niche tags (3):** specific geography + topic, all relevant to THIS fact
- **Forbidden:** `#TIL`, `#DidYouKnow`, `#ReactionReel`, `#Reels`,
  `#Viral`, `#FunFact`

**Worked example (la-tomatina):**

```
Six trips to Spain and a puzzle game knew about the tomato war before I did.

#CaptainArchibald #TravelCrush #LaTomatina #Spain #Buñol
```

### 7. Write script.json

```jsonc
{
  "schema_version": "0.9-reel-grounded",
  "pipeline": "trivia-captain",
  "metadata": {
    "sheet_revision": "<copied from brief>",
    "slug": "<slug>",
    "playbook": "trivia-captain",
    "hook_key": "now",                  // hook-library key, for rotation checks
    "beats": {
      "hook":   "<hook line + credentials>",
      "fact":   "<'And this game tells me' + surprising detail>",
      "kicker": "<proximity + joke-on-himself line>"
    },
    "vo_words": 44,
    "emotion_direction": "amused disbelief, not anger — unbelievable but funny that he didn't know; warm, self-deprecating, grandfatherly",
    "caption": "<IG post caption per playbook post_caption rules>",
    "openart": {
      "character":  "Captain Archibald",
      "model":      "Seedance 2.0",
      "duration_s": 15,
      "aspect":     "9:16",
      "resolution": "480p",
      "variant_count": 1,
      "audio_on":   true,
      "reference_image": "projects/trivia-captain/<slug>/assets/images/<name>.jpg",
      "backdrop":   "<one palette entry, customized>",
      "spoken_lines": "<hook+credentials> <fact> (short beat — smiling head shake, soft chuckle) <kicker>",
      "prompt":     "<full assembled CAPS-section prompt>"
    }
  }
}
```

### 8. Write Back To The Queue

```python
from scripts.trivia_captain import queue_row
ws = queue_row.build_sheets(write=True)
target = queue_row.find_row_by_day(ws, day)
queue_row.update_cells(
    ws, target,
    hook_vo=beats["hook"],
    fact_vo=beats["fact"],
    kicker_vo=beats["kicker"],
    openart_prompt=prompt,            # Queue!J — the Seedance avatar prompt
    fact_image_prompt=fact_img_prompt, # Queue!N — drives Phase 0's fact image
    caption=caption,
    status=queue_row.STATUS_READY_TO_REVIEW,
)
```

Note: **do NOT write `reference_image` (Queue!M) here** — it's set at
Generate time by `build_fact_assets.py` once the fact image is built. Write
the **fact-image prompt** (Queue!N) instead; Phase 0 turns it into the image
+ reference. The Queue is the human-readable source of truth for both prompts
(J = avatar, N = fact image). Set Queue!C = `Ready to review` once written.

### 9. Self-Review + Checkpoint

`human_approval_default: true`. Present:

- The hook key chosen + why (rotation state)
- The three beats with word counts
- The chosen backdrop + the reference image (show it)
- The full OpenArt prompt
- Estimated VO duration

Wait for "go" before advancing to assets.

## What Not To Do

- Do not put ANYTHING before the hook. Cold open is the format.
- Do not let Archibald be angry. Amused disbelief only — the take-1
  "betrayed prosecutor" register was explicitly rejected by the user.
- Do not describe Archibald's physical features. The character ref owns
  his look.
- Do not skip the game attribution in the fact beat — "this game tells
  me" is the brand mechanism.
- Do not omit the reference image. A tablet with an invented screen is
  off-format (and a green screen for post-compositing was tried and
  rejected — looks fake).
- Do not embellish the fact. CorrectExplanation is the source of truth.
  (Credentials and proximity claims are the ONLY licensed fiction.)
- Do not exceed 46 words. The 15s cap is hard; the kicker gets clipped.
- Do not reuse the previous row's hook key or backdrop.
- Do not use beat names ("hook:", "kicker:") near dialogue or timing
  markers ("0-2s") anywhere — Seedance reads them aloud. CAPS section
  headers are the proven safe structure; stage directions go in
  (parentheses) on their own line inside the dialogue.
- Do not add music or SFX directions.
- Do not skip writing VO back to Queue!F/G/H. Human review happens there.
