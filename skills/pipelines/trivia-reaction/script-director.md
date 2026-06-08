# Script Director — Trivia Reaction Pipeline

## When To Use

You are turning the brief into a three-beat script + a single OpenArt
prompt for the avatar clip. The brief carries the resolved EN copy from
the daily-trivia sheet; your job is to:

1. **Write three VO beats** (hook / fact / kicker) in the "I just found
   out" register.
2. **Choose a varied backdrop** from the style playbook palette,
   appropriate to the fact's vibe.
3. **Assemble one OpenArt prompt** that locks the character reference,
   describes the acting beats, names the backdrop, **embeds the three
   spoken lines, and gives Seedance 2.0 a voice direction**. Seedance
   generates the dialogue with native lip-sync — there is no separate
   TTS step.
4. **Write the three VO lines back to Queue!G/H/I** so the human can
   review without leaving the spreadsheet.

The brief's `metadata.trivia_reaction.correct_explanation_en` is your
*source of truth* for the fact content. Do not invent extra detail —
keep the fact verbatim or compress it; never embellish.

## Prerequisites

| Layer | Resource | Purpose |
|---|---|---|
| Artifact | `projects/trivia-reaction/<slug>/artifacts/brief.json` | Question / CorrectAnswer / CorrectExplanation |
| Playbook | `styles/trivia-reaction.yaml` | Backdrop palette, OpenArt prompt template, voice spec |
| Sheet | TriviaReactionQueue!Queue | Write Hook/Fact/Kicker VO back to G/H/I |
| Schema | `schemas/artifacts/script.schema.json` | Artifact validation |

## Process

### 1. Read The Brief

```python
brief = json.loads((Path("projects") / slug / "artifacts" / "brief.json").read_text())
trivia = brief["metadata"]["trivia_reaction"]
```

Pull `question_en`, `correct_answer_en`, `correct_explanation_en`.

### 2. Write The Three Beats (v9: setup + laugh-break + punchline)

Reference reel structure (16.6s, 30fps), verified by direct frame+audio
analysis on 2026-05-19 (see `styles/trivia-reaction.yaml.identity.reference_evidence`):

| Beat | Duration | Content |
|---|---|---|
| hook + fact (one continuous setup sentence) | ~10s | "So I just found out that…" → builds to the absurd detail |
| (audible laugh break — Seedance produces this) | ~2s | exhale-giggles, no words |
| kicker (short punchline through laughter) | ~3s | the "wait, what?" closing line |

The brief artifact still uses three sections (hook / fact / kicker) for
caption-page bookkeeping, but **conceptually hook+fact are ONE setup
sentence**. The OpenArt prompt concatenates them with no break, and the
laugh break only fires between the joined setup and the standalone kicker.

**Hook constraints:**
- Always opens with "So I just found out" or a near variant ("Okay so
  I just found out", "Wait, I just found out")
- 4-7 words — usually just the opener, treated as the first beat of one
  longer sentence rather than a standalone clause.

**Fact constraints:**
- Use the CorrectAnswer and the surprising detail verbatim where possible.
- 17-25 words — the meat of the setup, builds amusement.
- Sentence flows naturally from the hook with no hard break.

**Kicker constraints:**
- One punchy line. 6-12 words.
- A number twist, an absurd consequence, or a stat-juxtaposition.
- Must NOT just repeat the CorrectAnswer.
- End with `!` to give Seedance an inflection cue.
- If the fact has no natural kicker (flat answer), flag the row as
  borderline in the brief and ask the user to either edit the
  CorrectExplanation or skip the row.

**Word budget total:** 28-35 words across all three beats. Seedance
delivers ~2.5 wps at the playbook voice, so 28-35 words + ~2s break
fills the 13-15s window cleanly.

**Worked example — Day 2 (Switzerland / guinea pigs), shipped take13:**

| Beat | Copy |
|---|---|
| hook | "So I just found out that" |
| fact | "in Switzerland, owning just one guinea pig is actually illegal — by law, you need to have at least two." |
| kicker | "There's even a rent-a-guinea-pig service for when one dies!" |

(31 words total. Setup runs 0-8.8s, audible laugh break 8.8-10.4s,
punchline 10.4-14.4s. Hits the reel arc exactly.)

### 3. Pick A Backdrop

Read `styles/trivia-reaction.yaml` → `backdrops.prompt_palette` and pick
one appropriate to the fact's vibe:

- Animal facts → cozy living room / plant corner
- Food facts → kitchen / cafe
- Travel facts → balcony at golden hour / train window seat
- Science facts → office nook / bookshop
- Default → varied — rotate through the palette

**Variation rule:** Do not pick the same backdrop two days in a row.
Check the recent Queue rows' OpenArt prompts (or just remember from the
agent's session) before locking.

### 4. Build The OpenArt Prompt (v9 — label-free)

Use the playbook's `asset_generation.openart.prompt_template`:

```
{acting_arc_prose} {pose_prose}.
Setting: {backdrop_description}.
She says: "{setup_sentence} [laughs] [laughs] [laughs] [laughs] {punchline_sentence}"
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
`kwocka` (the `-ocka` forces the short-o, like "rock"). **Do not use
ALL-CAPS hyphenated forms** — Seedance reads those as letters: `KWOK-uh`
came out as "cougar", `kwocka` came out clean (verified on quokka-selfies
take01 vs take02, 2026-06-05). Keep `beats` spelled correctly: the
burned-in captions come from `beats`, so the viewer still sees the real
word while Seedance hears something it can pronounce.

**`{acting_arc_prose}`**: a single prose paragraph telling Seedance the
emotional arc as a story — relaxed start → smile builds → real audible
laugh break → recover and deliver punchline through laughter. Pull the
playbook's `avatar.acting_beats.arc` and adapt it minimally. **Do not
use beat names ("hook"/"fact"/"kicker") or timing markers ("0-2s",
"12-15s") in the prompt body** — Seedance reads them aloud. (Take09
literally said "The kicker?" after the prompt used `"12-15s kicker:"`.
This is documented in the playbook's `prompt_authoring_rules`.)

**`{pose_prose}`**: the playbook's `avatar.acting_beats.pose` —
intentionally open ("relaxed intimate selfie") rather than locking a
specific hand position. Earlier "one hand behind her head" pinned the
pose too tightly and produced unnatural-looking takes.

**`{backdrop_description}`**: one of the palette entries, customized to
the fact's geographic / topical vibe. Example:

> a cozy wood-paneled Swiss alpine chalet living room with a large
> window behind her showing a snow-dusted village street and pine
> trees, soft afternoon light, warm tones

**Voice direction.** Use the playbook's `avatar.voice_direction` string
verbatim. It explicitly explains that `[laughs]` cues are an audible
break, not a silent gap, which is what Seedance needs to hear.

**Critical:** The OpenArt **character reference** carries Ellie's
identity. Do not describe her face / hair / clothes from text — that's
what the saved character is for. Refer to her as "the character" or
"Ellie" but never describe physical features.

Target duration: 15s (style playbook locks 13-15s window). 1 variant
per row.

### 5. Write The Instagram Post Caption

Separate from the on-video orange-pill caption — this is the text that
goes in the Instagram post body when the video is uploaded.

Format locked by `styles/trivia-reaction.yaml.post_caption`. Two
paragraphs separated by a blank line:

**Paragraph 1 — body** (1-2 short lines, 60-120 chars):
- Dry, self-aware, meta about the recording — not a fact recap
- No emojis. The warmth is in the voice.
- Common patterns:
  - "Plan was to [X]. Plan failed at [specific Y from the fact]."
  - "Tried to be cool about this. [What broke me]."
  - "[Punchy observation]. I don't have notes." / "I have follow-up questions."
  - Optional disclaimer: "No idea if this is true, but no care."

**Paragraph 2 — hashtags** (5 total: 2 brand + 3 niche, on one line):
- **Brand tags (fixed, always first):** `#TravelCrush #IJustFoundOut`
- **Niche tags (3, varied per row):** mix specific geography + topic
  (e.g. `#Switzerland #GuineaPigs #SwissLaws`)
- All 3 niche tags relevant to THIS fact — no boilerplate niche block
  reused across rows
- **Forbidden:** `#TIL`, `#DidYouKnow`, `#ReactionReel`, `#Reels`, `#Viral`,
  `#FunFact` — overused generics with low reach ROI

The fact itself is on the video — the caption body's job is to make
people *want* to watch. Don't tell them what happens.

**Worked examples (shipped):**

```
Plan was to deliver this calmly. Plan failed at 'rent-a-guinea-pig.'

#TravelCrush #IJustFoundOut #Switzerland #GuineaPigs #SwissLaws
```

```
Spain decided once a year is for tomato warfare. I don't have notes.

#TravelCrush #IJustFoundOut #LaTomatina #Spain #Buñol
```

```
Every nugget I've ever eaten is technically a dinosaur. Sitting with this.

#TravelCrush #IJustFoundOut #Dinosaurs #TRex #PaleontologyFacts
```

The full caption (body + blank line + hashtags) goes into
`script.json` at `metadata.caption`, AND into Queue!K via
`update_cells(... caption=...)` in the next step.

### 6. Write script.json

```jsonc
{
  "schema_version": "0.9-reel-grounded",
  "pipeline": "trivia-reaction",
  "metadata": {
    "sheet_revision": "<copied from brief>",
    "slug": "<slug>",
    "playbook": "trivia-reaction",
    "beats": {
      "hook":   "So I just found out that",
      "fact":   "<the surprising-detail sentence>",
      "kicker": "<short punchline ending in !>"
    },
    "vo_words": 31,
    "caption": "<1-2 line IG post caption, see playbook post_caption rules>",
    "openart": {
      "character":  "ellie.travelcrush",
      "model":      "Seedance 2.0",
      "duration_s": 15,
      "aspect":     "9:16",
      "resolution": "480p",
      "variant_count": 1,
      "audio_on":   true,
      "backdrop":   "<one palette entry, customized>",
      "acting":     "<prose acting arc — see playbook avatar.acting_beats.arc>",
      "voice_direction": "<playbook avatar.voice_direction verbatim>",
      "spoken_lines": "<hook+fact> [laughs] [laughs] [laughs] [laughs] <kicker>",
      "prompt":     "<full assembled OpenArt prompt, label-free>"
    }
  }
}
```

### 6. Write VO Back To The Queue

```python
from scripts.trivia_reaction import queue_row
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

Also write the assembled OpenArt prompt to Queue!J (`openart_prompt`)
in the same `update_cells` call. The Queue is now the human-readable
source of truth for the prompt that will be sent to Seedance.

Set Queue!C = `Ready to review` once the human has signed off.

### 7. Self-Review + Checkpoint

`human_approval_default: true`. Present:

- The three beats with word counts
- The chosen backdrop
- The full OpenArt prompt (so the human can spot character/backdrop drift)
- Estimated VO duration

Wait for "go" before advancing to assets.

## What Not To Do

- Do not describe Ellie's physical features. Use the OpenArt character ref.
- Do not pick the same backdrop two days in a row.
- Do not embellish the fact. CorrectExplanation is the source of truth.
- Do not write more than 35 words total. The reference reel is 16.6s and
  the laugh break absorbs ~2s of that.
- Do not add music or SFX directions — reference uses neither.
- Do not skip writing the VO lines back to Queue!G/H/I. Human review uses them.
- Do not use beat names ("hook", "fact", "kicker", "punchline", "setup")
  or timing markers ("0-2s", "12-15s") as labels in the OpenArt prompt
  body. Seedance reads them aloud (verified — take10 said "The kicker?"
  out loud). The playbook's `prompt_authoring_rules` makes this binding.
- Do not leave rare proper nouns / loanwords (animals, place names,
  foods) spelled normally in the spoken line if Seedance mispronounces
  them. Respell phonetically in the spoken line / prompt only; keep
  `beats` correct for the captions.
- Do not lock a specific hand position in the pose prose ("one hand
  behind her head" etc). Describe the energy, let the character ref
  handle physical specifics.
