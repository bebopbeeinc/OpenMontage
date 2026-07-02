# Cover Director — trivia-captain-reaction

How to make a scroll-stopping **cover image** for a Captain Archibald reaction reel
(dailytrivia.tc A/B test). Covers are an **optional** asset attached at publish
(uploaded to Drive, `Cover` column) and posted manually — no Buffer.

Captain Archibald is a warm, worldly 70-year-old traveler (NOT a sea captain —
the nickname is affectionate). His register is **amused disbelief / wide-eyed
delight** — see [[project_archibald_persona_register]] and
[[project_trivia_captain_reaction_abtest]]. The cover carries that same energy.

## The one hard rule

**The image model renders the text, not Python.** Deterministic (PIL/ffmpeg)
text looks flat and pasted-on and must never ship. Covers are generated with
**OpenArt Nano Banana Pro** editing a real peak-emotion frame, so his face stays
authentic while the model bakes in the headline, highlight, arrow, prop, and
relight as part of the lighting.

## Recipe

`scripts/trivia_captain_reaction/cover_gen.py <slug> --headline "..." [flags] --headless`

1. **Pick the peak-emotion frame.** Auto-picks a high-energy frame; override with
   `--frame-time <s>` after you've reviewed the render (you review it anyway).
2. **Nano Banana Pro** edits that frame (aspect 9:16, res 2K) — reframes him to
   one side with clear headroom so the headline never crosses his face.
3. Writes `<slug>_cover_v{i}.png` variants + a canonical `<slug>_cover.png`.

Flags: `--highlight` (yellow word), `--emotion`, `--prop`, `--frame-time`,
`--character "Captain Archibald"`, `--variants N`, `--pick i`.

## Creative direction

- **Headline:** 2–4 words, curiosity + institutional-absurdity beats generic
  trivia. One yellow highlight word (`--highlight`).
- **Always a scroll-stopping expression — never deadpan/serious/neutral.** A flat
  face does not stop a scroll. The Captain never registers anger — his two
  scroll-stopping registers are:
  - absurd / funny / "after all these years?!" → **laughing, delighted, a hand
    to his forehead** (or mouth open mid-chuckle)
  - shocking / unbelievable / disbelief → **wide-eyed, brows up, jaw dropped in
    delighted shock**
- **The prompt's `--emotion` overrides the frame.** Nano Banana obeys the emotion
  text, so a "deadpan" prompt neutralises even a laughing frame. Two rules: (1)
  pick a `--frame-time` on an actual reaction beat that already shows the emotion,
  and (2) never write a low-energy emotion. The auto peak-pick favours open-mouth
  frames, but verify — override with `--frame-time` when it grabs a flat one.
- **Vary the text style** (`--style`, default `auto` rotates per slug across
  `condensed / marker / impact / sticker / comic`). Covers shouldn't all look
  identical; pick one that fits the joke, or let auto rotate. It's baked into the
  saved Queue!N prompt, so it's stable per row and editable.
- **Introduce a prop or character when the fact calls for it** (`--prop`): a
  no-mowing sign, a globe, an animal. When a fact touches home life he references
  his **WIFE** (never a husband) — the one hard re-gendering rule vs the ellie
  scripts. The arrow points from the headline to the prop. Skip it when the face
  carries the joke alone.
- **`--character "Captain Archibald"`** (saved character, workspace "R N") locks
  his identity when the scene is **reimagined** (full reframe). Not needed when
  editing the real frame in place — that's already him.
- **Brand energy:** vibrant, candy-pop, high-contrast (see
  [[feedback_scroll_stopping_assets]]) — never muted. No sea-captain / nautical
  motifs — he's a world traveler, not a sailor.

## Safe zones (9:16, 1080×1920 equivalent)

Keep face + headline inside the centered square; keep top ~14% and bottom ~20%
clear so IG's 3:4 grid / 4:5 feed crops and the caption/handle overlays never
clip the payload.

## From the web UI

Reaction runner → each row, **Cover** column: **★ Build cover** (or **↻ Rebuild
cover**) is **one click** — it uses the saved cover prompt (Queue!N) verbatim,
auto-picks the peak frame, generates 2 variants, and uploads the canonical cover
to Drive immediately (`Cover` column) so a shareable link is available without
Publishing the video first. Rebuild confirms first (it overwrites the variants
and spends a fresh OpenArt credit, like Re-generate). If the render isn't local
it's synced from Drive automatically — no manual Pull needed.

Edit the prompt with **✎ Cover prompt** before building. **▣ Review cover** opens
the variant picker — **Use v{i}** promotes one to canonical, **⤓ Download
(Drive)** grabs it, and **↻ Rebuild** regenerates. The row shows a `cover ↗`
Drive link in the Drive column.

## Editable prompts (both surfaced in the web, like trivia-images)

- **Video prompt** — **✎ Video prompt** button opens the OpenArt/Seedance clip
  prompt (`Queue!J`, read by `openart_generate`). Edit → Save → **Re-generate**
  uses it.
- **Cover prompt** — **✎ Cover prompt** opens the cover prompt (`Queue!N`). Edit
  → Save → **Build/Rebuild cover** uses it verbatim. `cover_gen --prompt "<text>"`
  overrides the assembled one; the effective prompt is always persisted to
  `<slug>_cover_prompt.txt` and Queue!N.

## Review before publish

Per [[feedback_review_renders]]: open the cover, confirm text is spelled right,
legible at thumbnail size, clear of his face and the UI-overlay zones, and that
his identity still reads as Captain Archibald. Fix in the same pass
([[feedback_review_autofix]]).
