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

def compose_kicker(demographic: str) -> str:
    """A flexible DEFAULT kicker for ANY demographic — used only when the row's
    Kicker cell is blank. The kicker is free-form content authored per row (in
    add_row / the sheet); this is just a sensible fallback, NOT a fixed enum.
    RULE: never taunt the destination's own locals — pick an OUTSIDER group."""
    g = (demographic or "most people").strip()
    g = g[:1].upper() + g[1:]
    return f"{g} always pick the wrong one. Tag one and watch!"


def _short_label(claim: str) -> str:
    """Cheap 2-3 word fallback label from a claim (used when a label cell is blank)."""
    words = [w for w in claim.replace(",", " ").split() if w.lower() not in
             {"a", "an", "the", "of", "to", "your", "out", "has", "is", "on", "one"}]
    return " ".join(words[:3]).strip().capitalize() or claim[:18]


def build_prompt(place: str, claims: list[str], kicker: str) -> str:
    dialogue = (
        f'"Today — {place}!\n'
        f"One — {claims[0]}.\n"
        f"Two — {claims[1]}.\n"
        f"Three — {claims[2]}.\n"
        f'{kicker}"'
    )
    return f"""ACTING
A high-energy game-show host bursting with delight, leaning in toward the lens. He opens mid-grin announcing the destination, then counts off three quick claims OUT LOUD ("one… two… three…") — punchy and lively, eyebrows popping, big delighted facial energy, warm and grandfatherly but FAST. The performance lives in his FACE and VOICE — his hands stay low and OUT OF FRAME; he does NOT raise or count on his fingers. On the last line he leans in and dares the viewer with a cheeky grin.

SETTING
A bright, vibrant open-air location evoking {place}: lively, sunlit, colorful, with a festive atmosphere. No signs or text anywhere in the scene.

DIALOGUE
He says, quick and punchy:
{dialogue}

VOICE
{LOCKED_VOICE}

AUDIO
Upbeat, bouncy game-show music plays throughout — bright, playful, driving rhythm under his voice, energetic and fun.

SHOT
Tight head-and-shoulders close-up, framed so his hands stay BELOW the bottom of the frame (hands not visible), slightly low angle, photoreal, bright sunny light, lively subtle handheld energy.

EXCLUSIONS
No on-screen captions, text, signs, or graphics in the scene — those are added in post. He does not read any rules aloud."""


def build_caption(place: str, demographic: str) -> str:
    return (
        f"Two of these about {place} are true. One's a lie. "
        f"Bet {demographic} can't spot it 👀\n\n"
        f"#CaptainArchibald #TravelCrush #TwoTruthsOneLie #{place.replace(' ', '')} #travelquiz"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("slug")
    args = ap.parse_args()

    sheets = queue_row.build_sheets(write=True)
    row = queue_row.find_row_by_slug(sheets, args.slug)
    if not row:
        sys.exit(f"slug {args.slug!r} not found in Queue")
    r = queue_row.read_queue_row(sheets, row)
    place = r["place"]
    claims = [r["claim_1"], r["claim_2"], r["claim_3"]]
    demographic = r["demographic"] or "most people"
    # Kicker is per-row content: use the authored Kicker cell if present, else
    # compose a flexible default (any demographic) and persist it so the human
    # can tweak it in the sheet.
    kicker = (r.get("kicker") or "").strip() or compose_kicker(demographic)

    prompt = build_prompt(place, claims, kicker)
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
    print(f"✓ {args.slug}: prompt + caption written, status → Ready to review")
    print(f"  kicker: {kicker}")
    print(f"  labels: {labels['label_1']} / {labels['label_2']} / {labels['label_3']}")
    print("\n--- OpenArt prompt ---\n" + prompt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
