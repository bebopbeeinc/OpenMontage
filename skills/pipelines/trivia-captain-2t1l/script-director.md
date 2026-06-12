# Trivia Captain 2T1L — Script Director

**Stage:** `script` → assembles the Seedance prompt + overlay labels + caption.
Human-approval default: **true**. Status after: **Ready to review**.

## Command
```
python scripts/trivia_captain_2t1l/build_prompt.py <slug>
```
Reads the row (place + 3 claims + demographic), assembles the validated prompt,
fills blank labels with 2-3 word fallbacks, writes the caption, flips status to
Ready to review. Writes Queue!N (prompt), Queue!O (caption), Queue J/K/L (labels).

## The Seedance prompt contract (validated)
Labeled CAPS sections `ACTING / SETTING / DIALOGUE / VOICE / AUDIO / SHOT /
EXCLUSIONS`. The DIALOGUE is **numbered**:
> "Today — <Place>! One — <claim1>. Two — <claim2>. Three — <claim3>. <demographic-taunt CTA>!"

- **Finger counting:** ACTING directs him to hold up 1 / 2 / 3 fingers as he counts
  (syncs to the bottom-stacking banners).
- **High energy:** VOICE = brisk, punchy game-show host (NOT the calm trivia-captain
  register). AUDIO requests upbeat in-prompt game-show music (continuous in one clip).
- **No in-camera sign or text** — overlays are added in post (EXCLUSIONS).
- **Brand "Travel Crush" is NOT spoken** — it lives in the Remotion banner.
- Character lock: "Captain Archibald" (never describe his face from text).
- Lie is **never** stated; the kicker is a demographic taunt, not a reveal.

## Quality bar (review_focus)
- Overlay labels (J/K/L) are 2-3 words each, read clearly, and **don't leak the lie**.
- Caption baits comments (demographic) + brand/niche hashtags; no answer leak.
- If a human hand-edited Queue!N, do not overwrite — the Generate job skips
  build_prompt when the prompt is already set.

## Checkpoint
Show the assembled prompt + labels + caption; approve before generation.
