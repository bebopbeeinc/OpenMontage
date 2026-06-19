# Script Director — Trivia Captain Reaction Pipeline

## When To Use

You are turning the brief into a three-beat script + a single OpenArt
prompt for the avatar clip. This pipeline is ellie.travelcrush's proven
"I just found out" reaction format with ONE creative change: the avatar
is **Captain Archibald** — a 70-year-old lifelong world traveler, warm
and amused that the world keeps surprising him (NOT a sea captain; the
nickname is affectionate). The brief carries the resolved EN copy from
the daily-trivia sheet; your job is to:

1. **Write three VO beats** (hook / fact / kicker) in the "I just found
   out" register, voiced as the Captain — amused disbelief, worldly,
   never angry, never lecturing.
2. **Choose a varied backdrop** from the style playbook palette,
   appropriate to the fact's vibe and to a well-traveled older man's world.
3. **Assemble one OpenArt prompt** that locks the character reference,
   describes the acting beats, names the backdrop, **embeds the three
   spoken lines, and gives Seedance 2.0 a voice direction**. Seedance
   generates the dialogue with native lip-sync — there is no separate
   TTS step.
4. **Write the three VO lines back to the Posts_Reaction queue (Hook/Fact/
   Kicker VO)** so the human can review without leaving the spreadsheet.

The brief's `metadata.trivia_captain_reaction.correct_explanation_en` is
your *source of truth* for the fact content. Do not invent extra detail —
keep the fact verbatim or compress it; never embellish.

## Prerequisites

| Layer | Resource | Purpose |
|---|---|---|
| Artifact | `projects/trivia-captain-reaction/<slug>/artifacts/brief.json` | Question / CorrectAnswer / CorrectExplanation |
| Playbook | `styles/trivia-captain-reaction.yaml` | Backdrop palette, OpenArt prompt template, voice spec |
| Sheet | Posts_Reaction tab (dailytrivia.tc Post Calendar) | Write Hook/Fact/Kicker VO back to F/G/H |
| Schema | `schemas/artifacts/script.schema.json` | Artifact validation |

## Process

### 1. Read The Brief

```python
brief = json.loads((Path("projects/trivia-captain-reaction") / slug / "artifacts" / "brief.json").read_text())
trivia = brief["metadata"]["trivia_captain_reaction"]
```

Pull `question_en`, `correct_answer_en`, `correct_explanation_en`.

### 2. Write The Three Beats (setup + laugh-break + punchline)

Reference reel structure (ported 1:1 from trivia-reaction; ~15s, 30fps):

| Beat | Duration | Content |
|---|---|---|
| hook + fact (one continuous setup sentence) | ~10s | "So I just found out that…" → builds to the absurd detail |
| (amused laugh/disbelief break — Seedance produces this) | ~2s | a warm disbelieving chuckle, no words |
| kicker (short punchline through the chuckle) | ~3s | the "can you believe it" closing line |

The brief artifact still uses three sections (hook / fact / kicker) for
caption-page bookkeeping, but **conceptually hook+fact are ONE setup
sentence**. The OpenArt prompt concatenates them with no break, and the
laugh break only fires between the joined setup and the standalone kicker.

**Hook constraints:**
- Always opens with "So I just found out" or a near variant ("Okay so
  I just found out", "After all these years, I just found out").
- 4-7 words — usually just the opener, treated as the first beat of one
  longer sentence.

**Fact constraints:**
- Use the CorrectAnswer and the surprising detail verbatim where possible.
- 17-25 words — the meat of the setup, builds the disbelief.
- Sentence flows naturally from the hook with no hard break.

**Kicker constraints:**
- One punchy line. 6-12 words.
- A number twist, an absurd consequence, or a stat-juxtaposition.
- Must NOT just repeat the CorrectAnswer.
- End with `!` to give Seedance an inflection cue.
- If the fact has no natural kicker (flat answer), flag the row as
  borderline in the brief and ask the user to either edit the
  CorrectExplanation or skip the row.

**Persona voice — the Captain, not Ellie:**
- Worldly amused disbelief, never anger, never a lecture.
- He's a well-traveled 70-year-old who's *delighted* the world still
  surprises him. Light touches like "after all these years…" land well.
- When a fact touches family or home life, he references his **WIFE**
  (he is a married older man) — **never a husband**. This is the one
  hard re-gendering rule vs the ellie scripts.
- No sea-captain / nautical puns. He's a traveler, not a sailor.

**Word budget total:** 28-35 words across all three beats. Seedance
delivers ~2.5 wps at the playbook voice, so 28-35 words + ~2s break
fills the 13-15s window cleanly.

**Worked example — Day 2 (Switzerland / guinea pigs):**

| Beat | Copy |
|---|---|
| hook | "So I just found out that" |
| fact | "in Switzerland, owning just one guinea pig is actually illegal — by law, you need to have at least two." |
| kicker | "There's even a rent-a-guinea-pig service for when one dies!" |

(31 words total. Setup runs ~0-8.8s, amused break ~8.8-10.4s,
punchline ~10.4-14.4s.)

### 3. Pick A Backdrop

Read `styles/trivia-captain-reaction.yaml` → `backdrops.prompt_palette`
and pick one appropriate to the fact's vibe — drawn from a well-traveled
older man's world:

- Animal facts → cozy living room with souvenirs / sunroom
- Food facts → sunlit kitchen / café corner
- Travel facts → balcony at golden hour / train window seat / hotel desk
- Science/history facts → study with bookshelves / den with a wall of maps
- Default → varied — rotate through the palette

**Variation rule:** Do not pick the same backdrop two days in a row.
Check recent queue rows' OpenArt prompts (or remember from the session)
before locking.

### 4. Build The OpenArt Prompt (label-free)

Use the playbook's `asset_generation.openart.prompt_template`:

```
{acting_arc_prose} {pose_prose}.
Setting: {backdrop_description}.
He says: "{setup_sentence} [laughs] [laughs] [laughs] [laughs] {punchline_sentence}"
Voice: {voice_direction}
Medium close-up, handheld selfie framing, eye-level, soft natural light,
photoreal, subtle camera micro-wobble.
No on-screen text, no captions, no graphics — captions are added in post.
```

**`{setup_sentence}`**: hook + fact concatenated with a single space, as
one continuous English sentence. No bracketing labels, no pauses encoded.

**`{punchline_sentence}`**: the kicker alone. End with `!` for an
inflection cue.

**Phonetic respelling for rare words.** Seedance reads the spoken line
literally to generate native VO, and stumbles on unfamiliar proper nouns
/ loanwords (animal names, place names, foods). Respell them
phonetically **in the spoken line / prompt ONLY**. Use a **lowercase,
natural-looking** respelling that anchors the vowel — e.g. `quokka` →
`kwocka`. **Do not use ALL-CAPS hyphenated forms** — Seedance reads those
as letters (`KWOK-uh` came out as "cougar"; `kwocka` came out clean).
Keep `beats` spelled correctly: the burned-in captions come from `beats`,
so the viewer still sees the real word while Seedance hears something it
can pronounce.

**`{acting_arc_prose}`**: a single prose paragraph telling Seedance the
emotional arc as a story — relaxed worldly start → smile builds →
genuine amused laugh/disbelief break (head shake, hand to mouth) →
recover and deliver the punchline through the chuckle. Pull the
playbook's `avatar.acting_beats.arc` and adapt it minimally. **Do not
use beat names ("hook"/"fact"/"kicker") or timing markers ("0-2s",
"12-15s") in the prompt body** — Seedance reads them aloud.

**`{pose_prose}`**: the playbook's `avatar.acting_beats.pose` —
intentionally open ("relaxed, intimate, worldly selfie") rather than
locking a specific hand position.

**`{backdrop_description}`**: one of the palette entries, customized to
the fact's geographic / topical vibe. Example:

> a warm wood-paneled study lined with bookshelves and framed photos
> from decades of travel, a globe on the desk, soft tungsten light

**Voice direction.** Use the playbook's `avatar.voice_direction` string
verbatim. It explicitly explains that `[laughs]` cues are an audible
amused chuckle, not a silent gap, and that the Captain references his
wife (never a husband).

**Critical:** The OpenArt **character reference** carries Archibald's
identity. Do not describe his face / hair / clothes from text — that's
what the saved character is for. Refer to him as "the Captain" or
"Archibald" but never describe physical features. **No reference image /
no tablet splash** — this pipeline does not attach one.

Target duration: 15s (style playbook locks 13-15s window). 1 variant
per row.

### 5. Write The Instagram Post Caption

Separate from the on-video warm-purple-pill caption — this is the text
that goes in the Instagram post body when the video is uploaded.

Format locked by `styles/trivia-captain-reaction.yaml.post_caption`. Two
paragraphs separated by a blank line:

**Paragraph 1 — body** (1-2 short lines, 60-130 chars):
- Dry, self-aware, worldly — amused that the world still surprises him.
  Not a fact recap.
- No emojis. The warmth is in the voice.
- Common patterns:
  - "Been to [place] and nobody warned me about [specific Y]."
  - "Sixty years of travel and [absurd detail] is news to me."
  - "[Punchy observation]. I have follow-up questions."
  - Optional disclaimer: "No idea if this is true, but I'm delighted either way."
- **No sea-captain / nautical puns.**

**Paragraph 2 — hashtags** (5 total: 2 brand + 3 niche, on one line):
- **Brand tags (fixed, always first):** `#DailyTrivia #IJustFoundOut`
  (confirm/adjust the account anchor tag with the user if dailytrivia.tc
  uses a different one)
- **Niche tags (3, varied per row):** mix specific geography + topic
  (e.g. `#Switzerland #GuineaPigs #SwissLaws`)
- All 3 niche tags relevant to THIS fact — no boilerplate niche block
- **Forbidden:** `#TIL`, `#DidYouKnow`, `#ReactionReel`, `#Reels`, `#Viral`,
  `#FunFact` — overused generics with low reach ROI

**Worked examples:**

```
Been to Switzerland twice and nobody mentioned the guinea-pig law.

#DailyTrivia #IJustFoundOut #Switzerland #GuineaPigs #SwissLaws
```

```
Sixty years of travel and I'm only now hearing about the tomato war.

#DailyTrivia #IJustFoundOut #LaTomatina #Spain #Buñol
```

The full caption (body + blank line + hashtags) goes into `script.json`
at `metadata.caption`, AND into the queue Caption column (K) in the next
step.

### 6. Write script.json

```jsonc
{
  "schema_version": "0.1",
  "pipeline": "trivia-captain-reaction",
  "metadata": {
    "sheet_revision": "<copied from brief>",
    "slug": "<slug>",
    "playbook": "trivia-captain-reaction",
    "beats": {
      "hook":   "So I just found out that",
      "fact":   "<the surprising-detail sentence>",
      "kicker": "<short punchline ending in !>"
    },
    "vo_words": 31,
    "caption": "<1-2 line IG post caption, see playbook post_caption rules>",
    "openart": {
      "character":  "Captain Archibald",
      "model":      "Seedance 2.0",
      "duration_s": 15,
      "aspect":     "9:16",
      "resolution": "480p",
      "variant_count": 1,
      "audio_on":   true,
      "reference_image": null,
      "backdrop":   "<one palette entry, customized>",
      "acting":     "<prose acting arc — see playbook avatar.acting_beats.arc>",
      "voice_direction": "<playbook avatar.voice_direction verbatim>",
      "spoken_lines": "<hook+fact> [laughs] [laughs] [laughs] [laughs] <kicker>",
      "prompt":     "<full assembled OpenArt prompt, label-free>"
    }
  }
}
```

### 7. Write VO Back To The Queue

```python
from scripts.trivia_captain_reaction import queue_row
ws = queue_row.build_sheets(write=True)
target = queue_row.find_row_by_day(ws, day)
queue_row.update_cells(
    ws, target,
    hook_vo=beats["hook"],
    fact_vo=beats["fact"],
    kicker_vo=beats["kicker"],
    openart_prompt=prompt,
    caption=caption,
    status=queue_row.STATUS_READY_TO_REVIEW,
)
```

The OpenArt prompt also lands in the queue OpenArt Prompt column (J) in
the same `update_cells` call. The queue is now the human-readable source
of truth for the prompt that will be sent to Seedance.

### 8. Self-Review + Checkpoint

`human_approval_default: true`. Present:

- The three beats with word counts
- The chosen backdrop
- The full OpenArt prompt (so the human can spot character/backdrop drift)
- Estimated VO duration

Wait for "go" before advancing to assets.

## What Not To Do

- Do not describe Archibald's physical features. Use the OpenArt character ref.
- Do not attach a reference image / tablet splash — this pipeline has none.
- Do not have the Captain reference a husband. He references his WIFE.
- Do not use sea-captain / nautical puns — he's a world traveler.
- Do not pick the same backdrop two days in a row.
- Do not embellish the fact. CorrectExplanation is the source of truth.
- Do not write more than 35 words total.
- Do not add music or SFX directions — reference uses neither.
- Do not skip writing the VO lines back to the queue (Hook/Fact/Kicker VO).
- Do not use beat names ("hook", "fact", "kicker", "punchline", "setup")
  or timing markers ("0-2s", "12-15s") as labels in the OpenArt prompt
  body. Seedance reads them aloud.
- Do not leave rare proper nouns / loanwords spelled normally in the
  spoken line if Seedance mispronounces them. Respell phonetically in the
  spoken line / prompt only; keep `beats` correct for the captions.
- Do not lock a specific hand position in the pose prose. Describe the
  energy, let the character ref handle physical specifics.
