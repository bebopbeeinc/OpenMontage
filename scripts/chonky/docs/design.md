# Where in the World Is Chonky? — image pipeline

A sibling of `scripts/trivia_images/`: reads prompts, drives OpenArt, verifies the
render against hard geometric rules, and files the result to Drive and a sheet.

## Why it exists

The game needs ~100 geography-puzzle images. Each is a real, verifiable viewpoint
with a tiny cartoon cat hidden in it. Two things make it unlike trivia-images:

1. **The output is verified, not just generated.** Chonky must land inside a
   65–105 px height band and inside an assigned zone of the frame. Both are
   measured from pixels; a render that misses either is rerolled.
2. **The frame has two regions.** The player opens on a 9:16 ViewFrame inside a
   4:5 source and pans outward, so clue value and Chonky's position are authored
   against that geometry rather than the whole picture.

An earlier build put this in ChatGPT with an Apps Script MCP backend. It failed on
things no instruction could fix: duplicate renders, file-approval stalls, no
server-side JPEG encoding, and clue text that vanished when a turn was cut short.
Everything below happens in code we control.

## Geometry (measured, not assumed)

OpenArt ignores `customWidth`/`customHeight` and returns **2048 × 2560** for 4:5.
It also ignores `outputFormat`, returning ~9 MB PNG regardless — so JPEG encoding
happens locally via `image_optimize.optimize_image_bytes_jpg`.

```
                  2048 wide
       +------+---------------+------+
       | 424  |   VIEWFRAME   | 424  |    top margin    213
       |      |   1200x2133   |      |    bottom margin 214
       | pan  |  x 424-1624   | pan  |
       |      |  y 213-2346   |      |
       +------+---------------+------+
                  2560 tall
```

Every playtest-spec envelope is met: 170.7% horizontal (spec 170–180), 120.0%
vertical (spec 120–125), 35.3% side margins, 10.0% top/bottom.

## Chonky rules

- **Height 65–105 px.** Under is unfindable, over stops being a hidden detail.
- **Zones.** `viewframe` = wholly inside the ViewFrame but clear of its central
  third (x 824–1224), so he is noticed without being the subject. `margin` =
  wholly outside, a pan reward. `centrestage` and `straddling` always fail.
- **Split is configurable**, default 70% viewframe / 30% margin, and the next
  image's zone is chosen from the running actual split so batches converge.
- **Identity is non-negotiable.** He is the character in `character_library/chonky/`,
  not "a fat ginger cat". A normal-bodied cat fails exactly like a wrong size.
- **Distance is the only real size control.** A realistic cat renders 10–20% of
  frame height in the near foreground and 3–5% at middle distance. Naming street
  furniture (a café chair, a bench) as his surface drags him forward: that
  produced 310 px against the band. Putting him on the ground among pedestrians
  at a stated distance produced 89 px first try.

## Modules

| File | Responsibility |
|---|---|
| `manual.py` | The operating manual — the authored rules, as one constant |
| `geometry.py` | Frame/ViewFrame constants, `classify_zone()`, size band |
| `measure.py` | Chonky bbox detection, height, zone verdict |
| `render.py` | Wraps `openart_image_driver.generate_image` — one call per image |
| `deliver.py` | JPEG encode to 700 KB–1.2 MB, Drive upload, sheet row |
| `ledger.py` | Used locations, zone stats, next-zone targeting, config |
| `web/server.py` | FastAPI sub-app mounted at `/chonky` |
| `web/index.html` | Review UI |

Reused unchanged: `openart_image_driver.generate_image`, `image_optimize`,
`drive_config`, `sheet_schema`, and the `web/server.py` mount contract.

## Review UI

One row per image: full frame and ViewFrame side by side; Chonky's measured
height and zone with pass/fail badges; the detected bounding box drawn over the
frame as a **draggable rectangle** — detection is a heuristic and has already
mis-measured a sunlit scene by 6×, so a human correction takes one drag and
recomputes height and zone live. Three editable clue drafts, pre-filled from the
prompt's designed clues. Per-image Reroll and Approve.

Approve is the only path that writes to Drive and the sheet.

## Open items

- `MODEL_IDS` needs `GPT Image 2.5 Sunburst -> gpt-image-2-5-sunburst`.
- The driver sends `resolution`; the sunburst form names it `resolutionTier`.
  Verify which the MCP surface accepts before the first batch.
- `character_library/chonky/` must be seeded with the model sheet.
