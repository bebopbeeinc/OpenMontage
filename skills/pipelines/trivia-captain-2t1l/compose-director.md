# Trivia Captain 2T1L — Compose Director

**Stage:** `compose` → render the kinetic overlay + MANDATORY frame review.
Human-approval default: **true** (always review renders).

## Command
```
python scripts/trivia_captain_2t1l/render.py <slug>
```
Stages `bg.mp4` → `remotion-composer/public/2t1l_bg.mp4` and renders the
**`TriviaTwoTruthsK3`** composition (NOT TriviaWithBg) with the per-row props
(theme, place, claim labels + reveal times):
```
npx remotion render src/index-trivia-2t1l-k3.tsx TriviaTwoTruthsK3 \
  projects/trivia-captain-2t1l/<slug>/renders/<slug>.mp4 --props='{...}'
```
Output: `renders/<slug>.mp4`. Layout = full-bleed Captain, full-width top title +
"📍 <Place>" banner, bottom-stacking fact banners (theme default `goldround`).

## MANDATORY frame review (per user memory)
Extract frames and verify before signalling done:
```
for t in 3 6 9 13; do ffmpeg -v error -ss $t -i renders/<slug>.mp4 -frames:v 1 -q:v 2 _rev_$t.jpg -y; done
```
Check each:
- Fact banners **reveal in sync** with the spoken count (one/two/three).
- The **place banner reads** and the title isn't clipped.
- **Captain's face is clear** the whole time (banners in top/bottom zones, not over him).
- Banners are **full-width edge-to-edge**; nothing clipped; music + VO audible.

## MANDATORY auto-fix (same turn)
- Reveal out of sync → re-check `props.json` reveal times (re-run assemble) and re-render.
- Title/label clipped or theme wrong → fix `props.json` / theme and re-render.
- Face covered → the clip framing is off; re-roll the clip (asset stage).

On pass, flip status → Ready to publish.
