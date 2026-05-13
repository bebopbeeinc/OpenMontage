#!/usr/bin/env python
"""Replace Posts!S (body_prompt) formula with hand-crafted, fact-specific
prompts per row. Each prompt is tailored to its row's claim/options + style
so OpenArt produces a focused, coherent body clip instead of a generic
template-substituted output.

Usage:
    python scripts/trivia/rewrite_body_prompts.py --dry-run
    python scripts/trivia/rewrite_body_prompts.py --apply

The script is idempotent — only writes rows whose current value is a
formula or differs from the planned text.
"""
from __future__ import annotations

import argparse
import sys
import sys
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build

sys.path.insert(0, str(Path(__file__).resolve().parent))
from post_row import POST_SHEET, SA_PATH, column_letter_for  # noqa: E402

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


# --- Per-row tailored body prompts ---------------------------------------
# Each entry: row number -> body prompt text.
# Designed for OpenArt-style image/video gen models. Style notes are kept
# tight; the visual-detail block is fact-specific.

PROMPTS: dict[int, str] = {
    5: """8s vertical photo-realistic video, 1080x1920. Real-world documentary B-roll feel, natural lighting, active camera at 24fps.

BODY of a 3-part trivia: "Australia is wider than the moon."

Subject:
- Sweeping aerial drone shot over the vast red-earth Australian outback at golden hour — endless flat horizon, scattered scrub, deep ochre sand, long shadows. The scale of the land must read as enormous and uninterrupted.
- A real, large full moon (or nearly full) is visible low in the sky on the horizon line, slightly hazy in the warm dusk atmosphere. Camera framing places the moon clearly within the same horizon as the land beneath it, so the visual comparison of width reads instantly.
- Camera: slow forward dolly/drone push, with a gentle reveal of the moon as the foreground land continues to extend past it.
- No people, no vehicles, no captions or text overlays.

Silent track. Deliverable: 1080x1920, exactly 8s, MP4.""",

    6: """8s vertical photo-realistic video, 1080x1920. Real-world documentary B-roll feel, natural lighting, handheld camera at 24fps.

BODY of a 3-part trivia.

Subject:
- A freshwater turtle (painted turtle or similar) submerged in a clear shallow pond, visible through clean slightly green-tinted water.
- Camera angle: low side-on or three-quarter rear, slight handheld settle.
- Action: small clusters of air bubbles rise from the rear of the turtle and drift up to the surface in soft trails. The turtle remains calmly still; one slow paddle stroke around 4s.
- Lighting: dappled sunlight filtering through water, gentle caustics on the pond floor.
- Background: aquatic plants, a few smooth pebbles. No people, no overlays.

No on-screen text, no captions, no logos, no SFX, no voice — silent track.
Deliverable: 1080x1920, exactly 8s, silent MP4.""",

    7: """8s vertical photo-realistic video, 1080x1920. Real-world documentary B-roll feel, natural lighting, handheld camera at 24fps.

BODY of a 3-part trivia about which country has an "owning just 1 guinea pig is illegal" law. SPLIT-SCREEN 2x2 layout — the 9:16 frame is divided into a 2x2 grid (each quadrant 540x960). All 4 quadrants play simultaneously for the full 8 seconds. Thin 2-3px white separators between quadrants.

- TOP-LEFT (SWITZERLAND): alpine village, wood chalet with red shutters and geraniums in window boxes, snowy mountain peak in background. A guinea pig in a small enclosure in the foreground.
- TOP-RIGHT (GERMANY): sunny Bavarian beer-garden table OR a cobblestone Munich street with a half-timbered Fachwerk house. Guinea pig nibbling a leaf.
- BOTTOM-LEFT (USA): suburban American backyard with white picket fence and a flag visible in the corner. Guinea pig on green grass with a small chew toy.
- BOTTOM-RIGHT (JAPAN): traditional Japanese garden with stone lantern and koi pond. Guinea pig nestled near a small bonsai or on tatami.

Each quadrant is continuously moving footage (not stills): handheld feel, natural daylight, no people on camera, no captions or text overlays.

Silent track. Deliverable: 1080x1920, exactly 8s, MP4.""",

    8: """8s vertical photo-realistic video, 1080x1920. Real-world documentary B-roll feel, natural lighting, handheld camera at 24fps.

BODY of a 3-part trivia: "What is the national sport of Japan?" — 2 options: Sumo, Soccer. SPLIT-SCREEN layout — the 9:16 frame is divided horizontally into two equal halves (each 1080x960). BOTH halves play simultaneously for the full 8 seconds. A subtle 2-3px white separator runs across the middle.

- TOP HALF (SUMO): inside a Japanese sumo arena (dohyō ring on a packed dirt floor, shrine-style canopy roof above, audience faintly visible in soft-focus). Two sumo wrestlers in black mawashi belts crouched in the pre-bout stance, then one initial charge. Warm overhead arena lighting.
- BOTTOM HALF (SOCCER): a youth soccer match on a green grass pitch in Japan — a kid in a navy and red Japanese national-team-colored kit dribbles a ball mid-frame, golden-hour natural light. Crowd or empty bleachers in soft-focus background.

Both halves are continuously moving footage (not stills). No on-screen text, no captions, no logos. Silent track.
Deliverable: 1080x1920, exactly 8s, MP4.""",

    9: """8s vertical photo-realistic video, 1080x1920. Real-world documentary B-roll feel, natural lighting, handheld camera at 24fps.

BODY of a 3-part trivia: "Which of these foods never really goes bad?" — 2 options: Honey, Peanut Butter. SPLIT-SCREEN layout — the 9:16 frame is divided horizontally into two equal halves (each 1080x960). BOTH halves play simultaneously for the full 8 seconds. A subtle 2-3px white separator runs across the middle.

- TOP HALF (HONEY): a clear glass jar of golden honey on a rustic wooden countertop, warm kitchen light. A wooden honey dipper slowly lifts out of the jar with thick honey trailing back down in a continuous golden ribbon. Soft macro focus, beautiful caramel highlights.
- BOTTOM HALF (PEANUT BUTTER): an open jar of fresh peanut butter on the same wooden countertop. A spoon or knife slowly drags through the surface, pulling up a thick swirl of creamy peanut butter. Soft macro focus, warm tones.

Both halves are continuously moving footage (not stills): handheld feel, natural daylight, no people on camera, no captions or text overlays. Silent track.
Deliverable: 1080x1920, exactly 8s, MP4.""",

    10: """8s vertical photo-realistic video, 1080x1920. Real-world documentary B-roll feel, natural lighting, handheld camera at 24fps.

BODY of a 3-part trivia: "Dutch police cars carry teddy bears."

Subject:
- A real Dutch police car (Politie) parked on a typical Dutch street — blue-and-yellow Battenburg checkerboard livery, red-and-blue light bar on top. Cobblestone road, classic Amsterdam-style canal-house facades softly out of focus in the background.
- A well-loved tan/brown teddy bear sits prominently on the dashboard or in the passenger seat, visible through the windshield/passenger window.
- Camera: slow handheld push-in toward the car, then a subtle settle on the bear behind the glass with a faint reflection of the street on the windshield.
- Soft overcast daylight or warm late-afternoon light. No officers, no people in shot, no captions or text overlays.

Silent track. Deliverable: 1080x1920, exactly 8s, MP4.""",

    11: """8s vertical photo-realistic video, 1080x1920. Real-world documentary B-roll feel, natural lighting, active camera at 24fps.

BODY of a 3-part trivia: "Which Paris museum is home to the Mona Lisa?" — 2 options: Louvre Museum, Vatican. SPLIT-SCREEN layout — the 9:16 frame is divided horizontally into two equal halves (each 1080x960). BOTH halves play simultaneously for the full 8 seconds. A subtle 2-3px white separator runs across the middle.

- TOP HALF (LOUVRE): the Louvre's iconic glass pyramid in the central courtyard at golden hour, classical Renaissance facades surrounding it. A few tourists silhouetted in soft focus walking through the courtyard. Slow forward dolly toward the pyramid with warm Parisian light reflecting off the glass.
- BOTTOM HALF (VATICAN): the colonnaded plaza of St. Peter's Basilica in Vatican City, the great dome rising in the background. Slow drone-like camera drift across the colonnade at golden hour, warm Roman light, tourists tiny in the distance.

Both halves are continuously moving footage (not stills): cinematic feel, natural daylight, no captions or text overlays. Silent track.
Deliverable: 1080x1920, exactly 8s, MP4.""",

    12: """8s vertical photo-realistic video, 1080x1920. Real-world documentary B-roll feel, natural lighting, handheld camera at 24fps.

BODY of a 3-part trivia: "You can sneeze while you're asleep."

Subject:
- A real adult person asleep in bed, side-on or three-quarter close-up. Soft, peaceful expression — eyes gently closed, mouth slightly parted, head resting on a pillow under a soft cotton blanket.
- Soft early-morning natural light filtering through curtains, dust motes catching the light. Calm, intimate bedroom atmosphere.
- Action: subtle slow rise and fall of breathing; one slow stir mid-clip (a small head turn or gentle shoulder shift) but no waking.
- Camera: slow push-in toward the face with very slight handheld drift.
- No captions or text overlays.

Silent track. Deliverable: 1080x1920, exactly 8s, MP4.""",

    13: """8s vertical photo-realistic video, 1080x1920. Real-world documentary B-roll feel, natural lighting, active camera at 24fps.

BODY of a 3-part trivia: "What is the longest train ride in the world?" — 4 options: Trans-Siberian Railway, Reading Railroad, Polar Express, Dollywood Express. SPLIT-SCREEN 2x2 layout — the 9:16 frame is divided into a 2x2 grid (each quadrant 540x960). All 4 quadrants play simultaneously for the full 8 seconds. Thin 2-3px white separators between quadrants.

- TOP-LEFT (TRANS-SIBERIAN RAILWAY): a long modern Russian passenger train cutting through a vast snowy Siberian taiga landscape under a pale wide sky. Slow tracking shot from the side, locomotive and carriages snaking past.
- TOP-RIGHT (READING RAILROAD): a top-down close-up of a real Monopoly board, framed on the Reading Railroad space — vintage train token nearby, dice and a few houses around. Macro slow drift over the board.
- BOTTOM-LEFT (POLAR EXPRESS): a vintage black steam locomotive at night in deep winter, golden warm light glowing from its windows, snow falling heavily, plumes of white steam under cold blue moonlight.
- BOTTOM-RIGHT (DOLLYWOOD EXPRESS): a colorful theme-park steam train pulling open-air carriages through Tennessee Smoky Mountain greenery on a sunny day, warm summer light, faint crowd in the distance.

Each quadrant is continuously moving footage (not stills): documentary-style camera moves, natural daylight or scene-appropriate light, no captions or text overlays.

Silent track. Deliverable: 1080x1920, exactly 8s, MP4.""",

    14: """8s vertical photo-realistic video, 1080x1920. Real-world underwater documentary B-roll feel, natural light filtering through water at 24fps.

BODY of a 3-part trivia: "Seahorses start every day with a dance ritual."

Subject:
- Two real seahorses face-to-face in a clear shallow tropical reef. One slightly pink/orange, the other golden-yellow. Their prehensile tails curl and almost touch, slowly intertwining as they sway.
- Action: a slow underwater dance — they drift in a gentle circle around each other, fins fluttering rapidly, occasionally locking tails. Tiny bubbles rise around them.
- Background: soft-focus coral and swaying seagrass, dappled caustics on the sandy floor, gentle currents.
- Camera: macro close-up with slow handheld drift, slight push-in around 4s.
- No people, no captions, no text overlays.

Silent track. Deliverable: 1080x1920, exactly 8s, MP4.""",

    15: """8s vertical photo-realistic video, 1080x1920. Real-world wildlife documentary B-roll feel, natural lighting, active camera at 24fps.

BODY of a 3-part trivia: "Which animal is the only mammal that can truly fly?" — 4 options: Bat, Elephant, Turtle, Platypus. SPLIT-SCREEN 2x2 layout — the 9:16 frame is divided into a 2x2 grid (each quadrant 540x960). All 4 quadrants play simultaneously for the full 8 seconds. Thin 2-3px white separators between quadrants.

- TOP-LEFT (BAT): a real bat in flight at dusk against a deep blue sky, wings spread mid-flap, captured in a slow-motion-ish shot with sharp wing detail.
- TOP-RIGHT (ELEPHANT): a real adult African elephant walking slowly across golden savannah grass at sunset, trunk swinging, dust catching the warm light.
- BOTTOM-LEFT (TURTLE): a real green sea turtle gliding gracefully through clear blue tropical water, sunbeams piercing the surface, viewed from a low side angle.
- BOTTOM-RIGHT (PLATYPUS): a real platypus paddling along a calm Australian creek, smooth water surface ripples behind it, soft eucalyptus leaves on the bank.

Each quadrant is continuously moving footage (not stills): cinematic nature-channel quality, natural light, no captions or text overlays.

Silent track. Deliverable: 1080x1920, exactly 8s, MP4.""",

    16: """8s vertical photo-realistic video, 1080x1920. Real-world documentary B-roll feel, warm natural lighting, macro camera at 24fps.

BODY of a 3-part trivia: "Cocoa beans used to be money."

Subject:
- A close-up scene on a dark stone or weathered wood Mesoamerican-style surface. A pair of hands (sleeves of woven natural cloth, no faces) slowly pours a cascade of dark roasted cocoa beans from a small clay bowl into a pile on the surface.
- Beside the pile of beans, a small heap of antique gold coins glints under warm flickering torchlight.
- Faint background hint of carved stone Aztec/Mayan relief or terracotta pottery in soft shadowy focus, evoking an ancient marketplace.
- Camera: slow macro push-in over the beans, then a gentle drift toward the coins around 4-5s. Dust motes catch the warm light.
- No captions, no text overlays.

Silent track. Deliverable: 1080x1920, exactly 8s, MP4.""",

    17: """8s vertical photo-realistic video, 1080x1920. Real-world wildlife documentary B-roll feel, natural lighting, active camera at 24fps.

BODY of a 3-part trivia: "Which animals hold hands while they sleep so they don't drift apart?" — 4 options: Sea otters, Beavers, Fish, Oysters. SPLIT-SCREEN 2x2 layout — the 9:16 frame is divided into a 2x2 grid (each quadrant 540x960). All 4 quadrants play simultaneously for the full 8 seconds. Thin 2-3px white separators between quadrants.

- TOP-LEFT (SEA OTTERS): two real sea otters floating on their backs in calm coastal kelp water, paws linked, eyes closed, gentle bobbing on the swell. Soft golden hour light.
- TOP-RIGHT (BEAVERS): a real beaver in a quiet woodland stream, gnawing on a freshly cut stick beside its log dam, dappled forest light, water rippling softly.
- BOTTOM-LEFT (FISH): a small school of bright clownfish weaving among the tentacles of a sea anemone in clear tropical water, sunbeams flickering through the surface.
- BOTTOM-RIGHT (OYSTERS): a cluster of real oysters on a wet rocky tidal flat, water gently lapping over them, shells slightly opening and closing.

Each quadrant is continuously moving footage (not stills): nature-channel quality, natural light, no captions or text overlays.

Silent track. Deliverable: 1080x1920, exactly 8s, MP4.""",

    18: """8s vertical photo-realistic video, 1080x1920. Real-world wildlife documentary B-roll feel, natural lighting, active camera at 24fps.

BODY of a 3-part trivia: "Kangaroos can't hop backwards."

Subject:
- A real adult red or grey kangaroo in the Australian outback at golden hour. Vast red-earth landscape, scattered dry grasses and low scrub, big sky.
- Action: the kangaroo hops powerfully FORWARD across the frame, side-on profile, thick tail counter-balancing each leap. Several full hops captured cleanly with motion blur on the legs.
- Camera: side tracking shot moving with the kangaroo, slight handheld feel, dust kicking up from the landing.
- Warm dusk light, long shadows. No people, no captions, no text overlays.

Silent track. Deliverable: 1080x1920, exactly 8s, MP4.""",

    19: """8s vertical photo-realistic video, 1080x1920. Real-world documentary B-roll feel, natural lighting, active camera at 24fps.

BODY of a 3-part trivia: "What was the wheel first used for?" — 4 options: Pottery, Chariots, Siege warfare, Construction. SPLIT-SCREEN 2x2 layout — the 9:16 frame is divided into a 2x2 grid (each quadrant 540x960). All 4 quadrants play simultaneously for the full 8 seconds. Thin 2-3px white separators between quadrants.

- TOP-LEFT (POTTERY): close-up of real hands shaping a wet clay vessel on a spinning pottery wheel. Slick clay walls rise as the potter's fingers smooth the form. Warm workshop light.
- TOP-RIGHT (CHARIOTS): a historical-reenactment shot of a wooden two-wheeled chariot pulled by a galloping horse across a dusty field, low side angle, dust trailing behind.
- BOTTOM-LEFT (SIEGE WARFARE): a real wooden trebuchet at full pull on a medieval-reenactment field, counterweight dropping and the throwing arm swinging up to release a boulder, overcast light.
- BOTTOM-RIGHT (CONSTRUCTION): two workers in rough tunics pushing a heavy wooden cart loaded with hewn stone blocks alongside a partially-built ancient stone wall, dust and exertion.

Each quadrant is continuously moving footage (not stills): documentary or historical-reenactment quality, no captions or text overlays.

Silent track. Deliverable: 1080x1920, exactly 8s, MP4.""",

    20: """8s vertical photo-realistic video, 1080x1920. Real-world nature documentary B-roll feel, natural sunny lighting, macro camera at 24fps.

BODY of a 3-part trivia: "Bees dance to show each other where the best flowers are."

Subject:
- Macro close-up of real honeybees on a hive's honeycomb surface — golden hexagonal wax cells filling the frame. Several bees crawl across the comb, antennae twitching, wings flickering.
- Action: one focal bee performs a small waggle/figure-8 motion, body shaking, while neighboring bees respond and shift around her. Mid-clip, cut or push-out reveals more bees streaming past on the comb; one bee lifts off with pollen-laden legs.
- Camera: extreme macro with shallow depth of field, slow drift across the comb and a subtle pull-back near the end.
- Warm summer sun catching the wings and pollen. No captions or text overlays.

Silent track. Deliverable: 1080x1920, exactly 8s, MP4.""",

    21: """8s vertical photo-realistic video, 1080x1920. Real-world documentary B-roll feel, natural lighting, active camera at 24fps.

BODY of a 3-part trivia: "What is the French word for hello?" — 4 options: Bonjour, Oui, Baguette, Adios. SPLIT-SCREEN 2x2 layout — the 9:16 frame is divided into a 2x2 grid (each quadrant 540x960). All 4 quadrants play simultaneously for the full 8 seconds. Thin 2-3px white separators between quadrants. CRITICAL: NO text, signs, captions, or letters in any quadrant — pure visuals only.

- TOP-LEFT (BONJOUR): a real Parisian street cafe at golden hour — small round tables with espresso cups, the Eiffel Tower visible in the soft-focus background. Slow camera drift across the terrace, no visible signage.
- TOP-RIGHT (OUI): close-up of a French person's hand giving a clear thumbs-up gesture against a softly blurred Parisian boulevard background. Warm afternoon light.
- BOTTOM-LEFT (BAGUETTE): macro shot of a fresh, crusty French baguette being lifted from a wooden bakery counter, golden crust crackling, soft flour dust catching the light.
- BOTTOM-RIGHT (ADIOS): a Spanish/Mexican sun-drenched plaza with a real woven sombrero on a wooden table, warm terracotta walls, late-afternoon golden light. Subtle drift of the camera, no signage visible.

Each quadrant is continuously moving footage (not stills): documentary travel-channel quality, natural light, no captions or text overlays anywhere.

Silent track. Deliverable: 1080x1920, exactly 8s, MP4.""",

    22: """8s vertical photo-realistic video, 1080x1920. Real-world documentary B-roll feel, natural lighting, active handheld camera at 24fps.

BODY of a 3-part trivia: "Spain has a festival where people throw tomatoes at each other." (La Tomatina, Buñol, Spain.)

Subject:
- A real Spanish town square (Buñol-style), narrow streets between cream-walled, terracotta-roofed buildings. Cobblestones underfoot, balconies with green shutters above.
- A joyful crowd in white t-shirts and goggles in the streets, drenched and stained bright red, mid-tomato-fight. Whole and crushed tomatoes flying through the air, arcs of red pulp.
- Action: tomatoes splatter against walls and people; a wave of red liquid sloshes underfoot; people laughing, arms raised, throwing.
- Camera: handheld in the thick of it, capturing splashes and motion blur, tomato pulp catching the camera occasionally.
- Warm midday Spanish sunlight. No captions or text overlays.

Silent track. Deliverable: 1080x1920, exactly 8s, MP4.""",

    23: """8s vertical photo-realistic video, 1080x1920. Real-world documentary B-roll feel, natural lighting, active camera at 24fps.

BODY of a 3-part trivia: "What was the first pizzeria in the United States called?" — 4 options: Lombardi's, Papa's Pizza, Pizza Casa, Pie Place. SPLIT-SCREEN 2x2 layout — the 9:16 frame is divided into a 2x2 grid (each quadrant 540x960). All 4 quadrants play simultaneously for the full 8 seconds. Thin 2-3px white separators between quadrants. CRITICAL: NO storefront signage, captions, or letters readable in any quadrant — pure visuals only.

- TOP-LEFT (LOMBARDI'S): a vintage Little Italy NYC pizzeria storefront with a red-and-white striped awning (no readable signage), a fresh pizza visible in the window, a classic black iron fire escape on the brick facade. Slow handheld push toward the door at dusk, warm window glow.
- TOP-RIGHT (PAPA'S PIZZA): an older Italian-American chef in a white apron and chef's hat, gray mustache, smiling as he tosses pizza dough in the air in a warm pizzeria kitchen. Warm tungsten light.
- BOTTOM-LEFT (PIZZA CASA): a sun-drenched Mediterranean stone house with a terracotta roof, an outdoor table set with a fresh pizza on a wooden board, olive trees in soft focus, warm afternoon light.
- BOTTOM-RIGHT (PIE PLACE): close-up of a stone wood-fired pizza oven with bright orange flames inside, a margherita pizza on a long wooden peel sliding into the heat, embers glowing.

Each quadrant is continuously moving footage (not stills): documentary food-channel quality, no captions or text overlays anywhere.

Silent track. Deliverable: 1080x1920, exactly 8s, MP4.""",

    24: """8s vertical photo-realistic video, 1080x1920. Real-world museum-quality B-roll feel, dramatic gallery lighting, slow camera moves at 24fps.

BODY of a 3-part trivia: "Who was the Greek goddess of the rainbow?" — 4 options: Iris, Hades, Artemis, Aphrodite. SPLIT-SCREEN 2x2 layout — the 9:16 frame is divided into a 2x2 grid (each quadrant 540x960). All 4 quadrants play simultaneously for the full 8 seconds. Thin 2-3px white separators between quadrants. Each quadrant shows a real classical sculpture or painting depicting that deity, treated as filmed museum B-roll. NO captions or labels.

- TOP-LEFT (IRIS): slow camera push around a real classical marble sculpture or fresco of the goddess Iris in flowing robes, a faint rainbow streak visible in the painted background. Soft museum spotlight.
- TOP-RIGHT (HADES): slow drift across a real classical sculpture of Hades on his throne, with the three-headed dog Cerberus at his feet. Dim moody gallery light, deep shadows.
- BOTTOM-LEFT (ARTEMIS): slow camera move around a real classical marble of Artemis with her bow drawn, a deer or stag at her side. Cool gallery lighting.
- BOTTOM-RIGHT (APHRODITE): slow push toward a real "Birth of Venus"-style classical work — Aphrodite rising from a seashell on calm waters. Warm soft light.

Each quadrant is continuously moving footage (not stills): museum-documentary quality camera moves, no captions or text overlays.

Silent track. Deliverable: 1080x1920, exactly 8s, MP4.""",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true", help="Show plan, no writes.")
    g.add_argument("--apply", action="store_true", help="Apply to live sheet.")
    args = ap.parse_args()

    creds = service_account.Credentials.from_service_account_file(str(SA_PATH), scopes=SCOPES)
    sheets = build("sheets", "v4", credentials=creds)

    # Resolve the live body-prompt column from the sheet's header row so
    # this keeps working across column reorderings.
    body_col = column_letter_for(sheets, "body_prompt")
    body_range = f"Posts!{body_col}5:{body_col}26"

    # Read current values (formula-rendered) so we can detect already-replaced rows.
    cur = sheets.spreadsheets().values().get(
        spreadsheetId=POST_SHEET, range=body_range, valueRenderOption="FORMULA",
    ).execute().get("values", [])
    cur = [(c[0] if c else "") for c in cur] + [""] * (22 - len(cur))

    print(f"--- Plan ({len(PROMPTS)} rows) ---")
    plan = []
    for i, (row, new_text) in enumerate(sorted(PROMPTS.items())):
        existing = cur[row - 5]
        is_formula = isinstance(existing, str) and existing.startswith("=")
        is_same = existing == new_text
        action = "WRITE" if not is_same else "skip"
        size = len(new_text)
        print(f"  row {row:>2}: {action}  (current: {'formula' if is_formula else 'static'}, "
              f"new prompt: {size} chars)")
        if not is_same:
            plan.append({"range": f"Posts!{body_col}{row}", "values": [[new_text]]})

    if not plan:
        print("\nNothing to write — sheet already has these prompts.")
        return 0

    # Show one Facts + one Choices sample so the user can sanity-check shape
    print("\n--- Sample: row 5 (Facts, Australia vs Moon) ---")
    print(PROMPTS[5])
    print("\n--- Sample: row 8 (Choices, Japan National Sport, 2 options) ---")
    print(PROMPTS[8])

    if args.dry_run:
        print(f"\nDRY-RUN — would write {len(plan)} cells. Re-run with --apply to commit.")
        return 0

    print(f"\nWriting {len(plan)} cells to Posts!{body_col} with USER_ENTERED ...")
    sheets.spreadsheets().values().batchUpdate(
        spreadsheetId=POST_SHEET,
        body={"valueInputOption": "USER_ENTERED", "data": plan},
    ).execute()
    print("Done.")

    # Verify a couple rows
    v = sheets.spreadsheets().values().get(
        spreadsheetId=POST_SHEET, range=f"Posts!{body_col}5:{body_col}8",
    ).execute().get("values", [])
    print("\n--- Verify rows 5-8 first 100 chars each ---")
    for i, row in enumerate(v):
        s = row[0] if row else ""
        print(f"  X{5+i}: {s[:100]!r}{' …' if len(s) > 100 else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
