# OpenArt Prompt Template — 15s Vertical Trivia Video

This is the canonical prompt we use in OpenArt to generate the source video for a
trivia post. The video is generated with **no on-screen text** — captions are
layered in post via the `TriviaWithBg` Remotion composition, synced to the VO
using word-level timestamps.

## Column mapping (Post Calendar sheet)

Each row in the `Posts` tab builds its prompt from these cells:

| Cell | Column | Role in prompt |
|---|---|---|
| `E<row>` | Hook | Opening VO line (0–2s) |
| `F<row>` | Question / Claim | The trivia statement (topic + spoken at 2–7s) |
| `G<row>` | Answer Prompt | Follows the claim at 2–7s (e.g. "True or false?") |
| `I<row>` | Resolution Line | VO line at 9–11s |
| `J<row>` | Soft CTA | Closing line at 11–15s (delivered by avatar) |

The per-segment OpenArt prompts live in the **Body Prompt** and **Closer
Prompt** columns. Each cell holds a `CONCATENATE` formula that references
the row's Hook / Question / Answer Prompt / Resolution / CTA cells, so any
edit to those source cells automatically updates the rendered prompt.
Copy the resolved text from the Body Prompt / Closer Prompt cell into
OpenArt — or let `scripts/trivia/openart_generate.py` do it.

## Mode-aware template

The prompt branches on column **C (Mode)**:

- `Facts` → true/false claim. Visual direction asks for a green check and
  red X button (symbolic, no text on them), plus ticking clock accents.
- `Choices` → multiple-choice question. Visual direction asks for **exactly
  N illustrated icons in a 2×2 grid**, where N is computed from G and the
  actual option names are injected into the prompt so the model has concrete
  subjects to render (not a generic example list).

### Lessons learned re: prompt hardening

Early versions of the Choices prompt failed in interesting ways on Seedance:
the model produced 6 tiles with A/B/C/D labels, duplicated options, and
ignored one of the four. The fixes:

1. **Never mention the letters A/B/C/D in the prompt** — even in a
   *prohibition* (`NEVER show A/B/C/D`), diffusion models latch onto named
   tokens and render them. The prompt now contains zero references to
   letter labels.
2. **Strip A./B./C./D. from the VO line too** — the 2–7s voice-over now
   reads as natural spoken English (`"…is illegal in Switzerland, Germany,
   USA, or Japan?"`). Some models appear to read the VO as a caption cue
   and reproduce the letter labels visually.
3. **Pin the tile count explicitly** — `"show exactly 4 illustrated icons
   in a 2×2 grid, no duplicates, no empty tiles"` beats `"in a grid"`.
4. **Front-load the text prohibition** — moved from the end of the prompt
   to a "CRITICAL CONSTRAINT:" block right after the opening line, and
   restated at the end of the Deliverable line.
5. **Specific options, not generic examples** — injecting
   `Switzerland, Germany, USA, Japan` works better than
   `country flags, food illustrations, animal silhouettes, etc.`.

All rows render **photorealistic**. Earlier iterations had a Styles tab
and a `{STYLE}` placeholder; that's deprecated. The two templates below
are the current canonical text.

### Facts template (human-readable)

```
15s vertical trivia video, photorealistic cinematic look. Fast punchy motion, clean cinematic pacing. IMPORTANT: No on-screen text, no captions, no typography, no letters, no logos anywhere in the video. ZERO text is a MUST. (Text overlays will be added in post.)

Topic: {{F}}

Voice-over script (timed):
- 0–2s: "{{E}}"
- 2–7s: "{{F}}. {{G}}"
- 7–9s: (non-verbal beat — playful suspense, ticking / whoosh sfx)
- 9–11s: "{{I}}"
- 11–15s: "{{J}}"

Visual direction: photorealistic cinematography, shallow depth of field, natural color grading, subtle motion (no cartoon springs/confetti). Symbolic green check + red X markers can appear for true/false (no text labels on them). Avatar appears for the final 4 seconds delivering the closing line.

Deliverable: 1080×1920, 15 seconds, no on-screen text at all.
```

### Choices template (human-readable)

The **specific options for that row** are extracted from column G (stripping
`A.`/`B.`/`C.`/`D.` prefixes, collapsing the double-space separator to `, `).
The formula injects them directly into the visual direction so OpenArt knows
exactly which 2–4 things to render as icons — no generic examples, no
confusion.

```
15s vertical trivia video, photorealistic cinematic look. Fast punchy motion, clean cinematic pacing. IMPORTANT: No on-screen text, no captions, no typography, no letters, no logos anywhere in the video. ZERO text is a MUST. (Text overlays will be added in post.)

Topic: {{F}}

Voice-over script (timed):
- 0–2s: "{{E}}"
- 2–7s: "{{F}} {{G}}"
- 7–9s: (non-verbal beat — playful suspense, ticking / whoosh sfx)
- 9–11s: "{{I}}"
- 11–15s: "{{J}}"

Visual direction: photorealistic cinematography, shallow depth of field, natural lighting. During 2–7s show the following options as distinct photorealistic objects, one per option, arranged in a 2×2 grid: {{OPTIONS extracted from G}}. Each must be a literal photographic depiction of its option (a country → its flag rendered cleanly; a food → the actual food; an animal → that animal; a place → a recognizable landmark). NEVER show letters, option labels (A/B/C/D), or any text near the items — each option must be recognizable by its image alone. Avatar appears for the final 4 seconds delivering the closing line.

Deliverable: 1080×1920, 15 seconds, no on-screen text at all.
```

Example — for G7 `"A. Switzerland  B. Germany  C. USA  D. Japan"`, the
visual direction resolves to:

> ...arranged in a grid: **Switzerland, Germany, USA, Japan**. Each icon must be an obvious, literal depiction...

## Sheet formula (Body Prompt + Closer Prompt columns)

The Body Prompt and Closer Prompt cells each hold an
`IF(C<row>="Choices", ..., ...)` formula that switches between the Facts
and Choices visual directions per row. The script-cell refs and a smart
punctuation joiner are shared between both branches.

To inspect or copy the formula, Ctrl-click any populated Body Prompt cell
in the sheet and pick "View formula" — there is no scripted rebuild path
at the moment.

Key shared bits:

- `Topic:` always uses `F`
- 2–7s joins `F` and `G` with `" "` if `F` ends in `.` / `?` / `!`, otherwise
  `". "` (keeps punctuation clean across Facts and Choices claims)
- For Choices rows, the options list is extracted from `G` with
  `TRIM(REGEXREPLACE(REGEXREPLACE(G, "[A-D]\.\s*", ""), "\s{2,}", ", "))` —
  strips `A.`/`B.`/`C.`/`D.` prefixes, then collapses the double-space
  separator to `, `
- Outro (avatar line + deliverable spec) is identical in both branches

## Rules / things to watch

- **No reveal strategy:** when column H is `No reveal`, make sure the Soft CTA
  (J) doesn't leak the answer. For a true/false claim, the word "true" or
  "false" in J will leak it — prefer neutral phrasing like "Would you have
  guessed it".
- **Choices mode — short options:** the avatar reads "A. X, B. Y, C. Z, D. W"
  in the 2–7s VO window. Long option names will blow the timing budget. Aim
  for ≤ 2 words per option.
- **Choices mode — iconic options:** OpenArt needs options to be things it
  can depict clearly. Countries → flags, foods → the food, animals → the
  animal. Abstract concepts ("freedom", "chance") don't icon well; rewrite
  the claim if you find yourself trying.
- **Avatar insert:** the final 4s always has an avatar delivering J. Keep J
  conversational and short (~10 words max).
- **Duration discipline:** don't let F+G exceed ~5s when read aloud. If the
  claim is long, move detail into I/J.

## After generation

Generated mp4s land in the pipeline-local clip library:

| Segment | Destination |
|---|---|
| Body   | `scripts/trivia/library/bodies/`   |
| Closer | `scripts/trivia/library/closers/`  |

The asset stage writes the filenames back to the **Body Filename** and
**Closer Filename** columns (resolved at runtime via
`post_row.cell_for`). Reaction filenames are already resolved at idea
time via the **Reaction Filename** column's VLOOKUP into the Clips
library.

Two ways to get the mp4 into the library:

- **Automated** — `python scripts/trivia/openart_generate.py <row>` drives
  OpenArt via Playwright per segment and writes the file directly to the
  canonical path. See `scripts/trivia/docs/trivia-video-workflow.md` for
  the full pipeline flow (assemble → transcribe → render → publish).
- **Manual** — paste the prompt cell (S for body, U for closer) into the
  OpenArt web UI, generate, drop the mp4 into the matching library
  directory, then paste the filename into T or V.
