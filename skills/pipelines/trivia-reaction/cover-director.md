# Cover Director — trivia-reaction

How to make a scroll-stopping **cover image** for an ellie.travelcrush reaction reel.
Covers are an **optional** asset attached at publish (uploaded to Drive, `Cover`
column) and posted manually — no Buffer.

## The one hard rule

**The image model renders the text, not Python.** Deterministic (PIL/ffmpeg)
text looks flat and pasted-on and must never ship. Covers are generated with
**OpenArt Nano Banana Pro** editing a real peak-emotion frame, so her face stays
authentic while the model bakes in the headline, highlight, arrow, prop, and
relight as part of the lighting.

## Recipe

`scripts/trivia_reaction/cover_gen.py <slug> --headline "..." [flags] --headless`

1. **Pick the peak-emotion frame.** Auto-picks a high-energy frame; override with
   `--frame-time <s>` after you've reviewed the render (you review it anyway).
2. **Nano Banana Pro** edits that frame (aspect 9:16, res 2K) — reframes her to
   one side with clear headroom so the headline never crosses her face.
3. Writes `<slug>_cover_v{i}.png` variants + a canonical `<slug>_cover.png`.

Flags: `--highlight` (yellow word), `--emotion`, `--prop`, `--frame-time`,
`--character ellie.travelcrush`, `--variants N`, `--pick i`.

## Creative direction

- **Headline:** 2–4 words, curiosity + institutional-absurdity beats generic
  trivia (see [[project_ellie_reaction_performance_patterns]]). One yellow
  highlight word (`--highlight`).
- **Always a scroll-stopping expression — never deadpan/serious/neutral.** A flat
  face does not stop a scroll (this was the ketchup miss). Two registers only:
  - absurd / funny / "wait, what?" → **laughing, hand over mouth** (or mouth wide open)
  - shocking / unbelievable / disbelief → **wide-eyed with jaw dropped in shock**
- **The prompt's `--emotion` overrides the frame.** Nano Banana obeys the emotion
  text, so a "deadpan" prompt neutralises even a laughing frame. Two rules: (1)
  pick a `--frame-time` on an actual reaction beat that already shows the emotion,
  and (2) never write a low-energy emotion. The auto peak-pick favours open-mouth
  frames, but verify — override with `--frame-time` when it grabs a flat one.
- **Vary the text style** (`--style`, default `auto` rotates per slug across
  `condensed / marker / impact / sticker / comic`). Covers shouldn't all look
  identical; pick one that fits the joke, or let auto rotate. It's baked into the
  saved Queue!U prompt, so it's stable per row and editable.
- **Introduce a prop or character when the fact calls for it** (`--prop`): a
  no-mowing sign, a globe, an animal, or a recurring character (the husband, the
  cat). The arrow points from the headline to it. Skip it when the face carries
  the joke alone.
- **`--character ellie.travelcrush`** (saved character, workspace "R N") locks her
  identity when the scene is **reimagined** (full reframe). Not needed when
  editing the real frame in place — that's already her.
- **Brand energy:** vibrant, candy-pop, high-contrast (see
  [[feedback_scroll_stopping_assets]]) — never muted.

## Safe zones (9:16, 1080×1920 equivalent)

Keep face + headline inside the centered square; keep top ~14% and bottom ~20%
clear so IG's 3:4 grid / 4:5 feed crops and the caption/handle overlays never
clip the payload.

## From the web UI

Reaction runner → each row: **Build Cover** (or **↻ Rebuild Cover**) opens a
modal. Fill headline / highlight / emotion / prop and click **↻ Assemble from
fields** to draft the prompt, then **review + tweak it in the textarea** — the
prompt is what's sent (verbatim) and it's saved to the `Cover Prompt` column
(Queue!U) so it's reviewable/tweakable next time. Frame-time (blank = auto peak
pick), variants, and the `ellie.travelcrush` character toggle are alongside.
Needs a render first (the frame source).

Variants show under **▣ Cover** — **Use v{i}** promotes one to canonical, and
**⤓ Download (Drive)** grabs it once published. The row shows a thumbnail + a
`cover ↗` Drive link. The canonical cover uploads to Drive on **Publish** →
`Cover` column. Regenerating spends a fresh OpenArt credit (like Re-generate).

## Editable prompts (both surfaced in the web, like trivia-images)

- **Video prompt** — **✎ Video prompt** button opens the OpenArt/Seedance clip
  prompt (`Queue!J`, read by `openart_generate`). Edit → Save → **Re-generate**
  uses it.
- **Cover prompt** — the Build Cover textarea (`Queue!U`). Edit → **Rebuild
  Cover** uses it verbatim. `cover_gen --prompt "<text>"` overrides the assembled
  one; the effective prompt is always persisted to `<slug>_cover_prompt.txt` and
  Queue!U.

## Review before publish

Per [[feedback_review_renders]]: open the cover, confirm text is spelled right,
legible at thumbnail size, clear of her face and the UI-overlay zones, and that
her identity still reads as Ellie. Fix in the same pass ([[feedback_review_autofix]]).
