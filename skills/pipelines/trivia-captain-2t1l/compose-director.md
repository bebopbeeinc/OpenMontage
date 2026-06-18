# Trivia Captain 2T1L — Compose Director

**Stage:** `compose` → render the kinetic overlay + MANDATORY frame review.
Human-approval default: **true** (always review renders).

## Command
```
python scripts/trivia_captain_2t1l/render.py <slug>
```
Stages `bg.mp4` → `remotion-composer/public/2t1l_bg.mp4` and renders the
**`TriviaTwoTruthsK3`** composition with the per-row props (theme, place) plus the
word-level caption `words` from `artifacts/words.json`:
```
npx remotion render src/index-trivia-2t1l-k3.tsx TriviaTwoTruthsK3 \
  projects/trivia-captain-2t1l/<slug>/renders/<slug>.mp4 --props='{...}'
```
Output: `renders/<slug>.mp4`. Layout = full-bleed Captain, centered top header
lockup (TC logo + "📍 <Place>" pill) + **bottom word-by-word karaoke captions** —
the same caption renderer (`buildPages` + `TikTokPage`) as the ellie.travelcrush /
trivia-reaction videos. No claim pills / fact banners (user decision 2026-06-11).

## MANDATORY frame review (per user memory)
Extract frames and verify before signalling done:
```
for t in 3 6 9 13; do ffmpeg -v error -ss $t -i renders/<slug>.mp4 -frames:v 1 -q:v 2 _rev_$t.jpg -y; done
```
Check each:
- Captions **track the VO** (the highlighted word matches what's being spoken).
- The **place header reads** and the logo isn't clipped.
- **Captain's face is clear** the whole time (captions sit at the very bottom).
- Music + VO audible.

## MANDATORY auto-fix (same turn)
- Captions out of sync / wrong words → re-run assemble (re-transcribes `words.json`) and re-render.
- Place header / theme wrong → fix `props.json` / theme and re-render.
- Face covered → the clip framing is off; re-roll the clip (asset stage).

On pass, flip status → Ready to publish.
