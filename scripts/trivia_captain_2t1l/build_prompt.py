"""Assemble the Seedance prompt + caption for a 2T1L row (the 'script' stage).

Reads the curated row (place + 3 claims + demographic), builds the validated
high-energy Seedance prompt (numbered facts "One/Two/Three" + finger-counting,
in-prompt game-show music, no in-camera sign — overlays are added in post),
fills overlay labels if blank, writes the caption, and flips status to
"Ready to review".

Usage:
    python scripts/trivia_captain_2t1l/build_prompt.py <slug>
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.trivia_captain_2t1l import paths, queue_row  # noqa: E402

# LOCKED character voice — emitted IDENTICALLY on every episode so Seedance's
# native audio converges on the same Captain Archibald voice across the series.
# (Seedance still regenerates audio per clip, so expect some drift; this is the
# strongest in-pipeline lever short of a voice-reference upload / post-VO.)
LOCKED_VOICE = (
    "The voice is ALWAYS the same character — Captain Archibald: a warm, "
    "weathered 70-year-old man, mid-to-low pitch with a gentle gravel and warm, "
    "educated diction, grandfatherly and good-humoured. Here he is in "
    "high-energy game-show-host mode — brisk and punchy, big enthusiasm, a smile "
    "audible in every word, riding the momentum but keeping every word crisp and "
    "clear. Never angry, never flat, never shrill — bright, playful, and "
    "unmistakably the same warm old storyteller in every episode."
)

# Spoken-duration budget. The Seedance clip is a FIXED 15s; if the dialogue
# runs long, Seedance crams the VO in and the voice audibly speeds up. Keep the
# whole spoken line (place + One/Two/Three claims + kicker) under this word
# count. Calibrated at a brisk-but-clear game-show pace (~2.9 words/sec) with
# headroom for the inter-claim beats Seedance inserts.
WORDS_PER_SEC = 2.9
CLIP_SECONDS = 15
MAX_DIALOGUE_WORDS = 42  # ~13.5s spoken + breathing room inside the 15s clip


# Varied closing-kicker pool. Episodes shouldn't all end on "tag one" — mix the
# CTA (tag / comment / guess) and the hook (demographic taunt vs. 1-in-10 rarity
# challenge). {G} = capitalized demographic, {g} = lowercase. Kept short so they
# don't eat the 15s spoken budget. Warm and playful, never hostile.
KICKER_TEMPLATES = [
    "{G} always pick the wrong one — tag one!",
    "Are you in the 10% that gets it right? Comment!",
    "Bet {g} can't spot the lie — drop your guess below!",
    "Only one in ten gets this right. Are you one? Comment!",
    "Think you can out-guess {g}? Comment the lie!",
    "Almost everyone falls for the lie. Did you? Tell me below!",
]


def compose_kicker(demographic: str, place: str = "") -> str:
    """A flexible DEFAULT kicker — used only when the row's Kicker cell is blank
    AND no --kicker override is passed. Rotates through KICKER_TEMPLATES so the
    series doesn't always end the same way; deterministic per `place` so re-runs
    are stable. The kicker is free-form content (author it per row in the sheet
    or via --kicker); this is just a sensible varied fallback, NOT a fixed enum.
    RULE: never taunt the destination's own locals — pick an OUTSIDER group."""
    g = (demographic or "most people").strip()
    g_cap = g[:1].upper() + g[1:]
    idx = (sum(ord(c) for c in place) % len(KICKER_TEMPLATES)) if place else 0
    return KICKER_TEMPLATES[idx].format(G=g_cap, g=g)


def _short_label(claim: str) -> str:
    """Cheap 2-3 word fallback label from a claim (used when a label cell is blank)."""
    words = [w for w in claim.replace(",", " ").split() if w.lower() not in
             {"a", "an", "the", "of", "to", "your", "out", "has", "is", "on", "one"}]
    return " ".join(words[:3]).strip().capitalize() or claim[:18]


def build_prompt(place: str, claims: list[str], kicker: str,
                 backdrop: str | None = None) -> str:
    # Facts separated by ORDINALS ("First… Second… Third…"), NOT cardinal
    # counting ("one… two… three…"). Ordinals give the viewer a clear audible
    # divider between the three claims while reading as natural narration — so
    # they don't cue the mechanical count-off that made Seedance finger-count.
    dialogue = (
        f'"Two truths and a lie about {place}!\n'
        f"First — {claims[0]}. Second — {claims[1]}. Third — {claims[2]}.\n"
        f'{kicker}"'
    )
    # SETTING background. When `backdrop` is named (per-row override), use it
    # verbatim so Seedance renders a SPECIFIC recognizable view instead of
    # guessing "the most iconic {place}" (which it often skips or generalizes).
    # Lighting is left natural in that case so e.g. a fjord/aurora scene isn't
    # forced into a generic sunny look.
    if backdrop:
        setting = (
            f"He is on location in {place}, standing right in front of {backdrop} "
            f"— an INSTANTLY RECOGNIZABLE, ICONIC view of {place}, clearly visible "
            f"and identifiable in the background behind him, well-lit and in good "
            f"focus (not blurred away). Naturally lit, vibrant and colorful, "
            f"lively atmosphere. No signs or text anywhere in the scene."
        )
        shot_bg = f"the iconic {place} backdrop ({backdrop})"
        shot_light = "natural daylight"
    else:
        setting = (
            f"He is on location in {place}, standing right in front of an "
            f"INSTANTLY RECOGNIZABLE, ICONIC {place} landmark or landscape — the "
            f"single most famous view anyone would recognize as {place} — clearly "
            f"visible and identifiable in the background behind him, well-lit and "
            f"in good focus (not blurred away). Bright, sunny, vibrant and "
            f"colorful, lively festive atmosphere. No signs or text anywhere in "
            f"the scene."
        )
        shot_bg = f"the iconic {place} backdrop"
        shot_light = "bright natural light"
    # Selfie / vlog framing — mirrors the trivia-reaction playbook. He's filming
    # HIMSELF at arm's length on location, which is what sells "he's really there".
    return f"""ACTING
A warm, high-energy Captain Archibald filming himself SELFIE-STYLE on location, talking straight down the lens like he's excitedly showing a friend where he is. He opens mid-grin by THROWING DOWN THE HOOK — "Two truths and a lie about {place}!" — then walks the viewer through the three claims, marking each one off out loud ("first… second… third…") so they're easy to tell apart, punchy and lively, eyebrows popping, big delighted facial energy, warm and grandfatherly but FAST. He gestures loosely and naturally — he does NOT count them out on his fingers. Relaxed, intimate, single-take selfie energy. On the last line he leans in and dares the viewer with a cheeky grin.

SETTING
{setting}

DIALOGUE
He says, quick and punchy:
{dialogue}

VOICE
{LOCKED_VOICE}

AUDIO
Upbeat, bouncy game-show music plays throughout — bright, playful, driving rhythm under his voice, energetic and fun.

SHOT
Medium close-up, handheld SELFIE framing — as if he is holding the camera at arm's length himself, vlog-style — eye-level, {shot_light}, photoreal, subtle camera micro-wobble. Leave room so {shot_bg} reads clearly behind him.

EXCLUSIONS
No on-screen captions, text, signs, or graphics in the scene — those are added in post. He does not read any rules aloud."""


def spoken_word_count(place: str, claims: list[str], kicker: str) -> int:
    """Words the Captain actually says: 'Two truths and a lie about <place>!
    First — <c1>. Second — <c2>. Third — <c3>.' plus the kicker. Drives the
    15s-fit warning."""
    spoken = (f"Two truths and a lie about {place} "
              f"First {claims[0]} Second {claims[1]} Third {claims[2]} {kicker}")
    return len(spoken.split())


def build_caption(place: str, demographic: str) -> str:
    return (
        f"Two of these about {place} are true. One's a lie. "
        f"Bet {demographic} can't spot it 👀\n\n"
        f"#CaptainArchibald #TravelCrush #TwoTruthsOneLie #{place.replace(' ', '')} #travelquiz"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("slug")
    ap.add_argument("--backdrop", default=None,
                    help="Named iconic backdrop (e.g. 'a dramatic Norwegian fjord'). "
                         "When set, the SETTING/SHOT use it verbatim with natural "
                         "lighting instead of guessing the most iconic view of <place>.")
    ap.add_argument("--kicker", default=None,
                    help="Override the closing kicker/CTA line for this row "
                         "(persisted to the sheet). Omit to keep the authored "
                         "cell, or fall back to a varied default.")
    args = ap.parse_args()

    sheets = queue_row.build_sheets(write=True)
    row = queue_row.find_row_by_slug(sheets, args.slug)
    if not row:
        sys.exit(f"slug {args.slug!r} not found in Queue")
    r = queue_row.read_queue_row(sheets, row)
    place = r["place"]
    claims = [r["claim_1"], r["claim_2"], r["claim_3"]]
    demographic = r["demographic"] or "most people"
    # Kicker priority: explicit --kicker override > authored Kicker cell >
    # varied default (rotated per place). Persisted to the sheet either way so
    # the human can tweak it.
    kicker = ((args.kicker or "").strip()
              or (r.get("kicker") or "").strip()
              or compose_kicker(demographic, place))

    prompt = build_prompt(place, claims, kicker, backdrop=args.backdrop)
    caption = build_caption(place, demographic)
    labels = {
        "label_1": r["label_1"] or _short_label(claims[0]),
        "label_2": r["label_2"] or _short_label(claims[1]),
        "label_3": r["label_3"] or _short_label(claims[2]),
    }

    queue_row.update_cells(
        sheets, row,
        openart_prompt=prompt, caption=caption, kicker=kicker,
        status=queue_row.STATUS_READY_TO_REVIEW, **labels,
    )
    words = spoken_word_count(place, claims, kicker)
    est = words / WORDS_PER_SEC
    print(f"✓ {args.slug}: prompt + caption written, status → Ready to review")
    print(f"  kicker: {kicker}")
    print(f"  labels: {labels['label_1']} / {labels['label_2']} / {labels['label_3']}")
    print(f"  spoken: {words} words ≈ {est:.1f}s (clip is {CLIP_SECONDS}s)")
    if words > MAX_DIALOGUE_WORDS:
        print(f"  ⚠️  OVER BUDGET: {words} > {MAX_DIALOGUE_WORDS} words — the VO will "
              f"sound sped-up. Shorten the claims (aim ≤8 words each) and re-run.")
    print("\n--- OpenArt prompt ---\n" + prompt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
