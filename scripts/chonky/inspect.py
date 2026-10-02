"""STEP C: look at the render, then write the clues from what is actually there.

The manual has demanded this from the beginning — "Clue messages must never be
written from the prompt alone: the prompt says what SHOULD be there; STEP C
catches when the render did not deliver it" — and the pipeline never did it.
Clues arrived in the same reply as the prompt, written before any image
existed, and nothing ever compared them to the pixels.

Two reported failures come straight from that gap. A Tallinn clue described an
EU blue strip lettered EST on a plate showing nothing of the kind. A
Guadalajara clue cited a Jalisco plate too blurry to read. Both were true of
the prompt and false of the picture.

So the clues are written here instead, by a pass that is handed the render. It
is also the only place that can answer two questions nothing else can: whether
a detail is actually legible at the size a player will see it, and whether the
picture has spelled its own answer somewhere in its signage.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Optional

MODEL = os.environ.get("CHONKY_INSPECT_MODEL", "claude-sonnet-5")

_REQUIRED = ("clues", "clue_words")


class InspectError(RuntimeError):
    """The reply cannot be used as a set of clues."""


def _call_via_cli(system: str, user: str, *, model: Optional[str] = None,
                  image: Optional[Path] = None) -> str:
    """Ask Claude to read the image, through the CLI's own Read tool.

    The CLI is used rather than the SDK for the same reason the prompt writer
    uses it: it authenticates with the machine's OAuth session, so the server
    needs no API key. `--allowed-tools Read` is what lets it open the file;
    without it the model can only say the path exists.
    """
    cli = shutil.which("claude")
    if not cli:
        raise InspectError("`claude` CLI not found in PATH")
    prompt = f"{system}\n\n{user}"
    if image is not None:
        prompt = f"Read the image at {image}, then:\n\n{prompt}"
    cmd = [cli, "--print", "--model", model or MODEL,
           "--allowed-tools", "Read", "--output-format", "json", prompt]
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_CODE_")}
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=300)
    if proc.returncode != 0:
        raise InspectError(f"`claude` CLI failed (exit {proc.returncode}): "
                           f"{proc.stderr.strip() or proc.stdout.strip()}")
    # The CLI may print a warning line before its JSON envelope.
    start = proc.stdout.find('{"')
    try:
        envelope = json.loads(proc.stdout[start:] if start >= 0 else proc.stdout)
    except json.JSONDecodeError as exc:
        raise InspectError(f"`claude` CLI returned a non-JSON envelope: {exc}") from exc
    text = envelope.get("result", "")
    if not isinstance(text, str):
        raise InspectError(f"envelope `result` is not a string: {text!r}")
    return text


def _parse(raw: str):
    """The reply object, from a reply that may be wrapped in prose.

    The CLI prefaced one Venice reply with "Apologies — that tool call was a
    mistake. Here is the requested JSON:", and the render lost its clues over
    it. verify_place.py already recovers from exactly this shape; this pass did
    not, and threw away a complete, correct answer for a sentence standing in
    front of it.
    """
    for candidate in (_strip_fence(raw), _embedded_object(raw)):
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict) and "clues" in data:
            return data
    return None


def _embedded_object(text: str):
    """The outermost {...} in a reply that prefaced its JSON with prose."""
    start = text.find("{")
    end = text.rfind("}")
    return text[start:end + 1] if 0 <= start < end else None


def _strip_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    body = stripped.split("\n", 1)[1] if "\n" in stripped else ""
    return body.rsplit("```", 1)[0].strip()


_SYSTEM = """\
You are checking a finished image for a geography puzzle game. The player sees
the photograph and has to work out where it was taken.

You write the three clue messages. They are shown to the player after they
solve the level, so each one must name something that is ACTUALLY VISIBLE in
this image — not something the image was supposed to contain.
"""


def _user_message(city: str, country: str, difficulty: int) -> str:
    from scripts.chonky.prompts import CLUE_FAMILIES

    families = ", ".join(sorted(CLUE_FAMILIES))
    return f"""\
This image is meant to be {city}, {country}, at difficulty {difficulty}.

Write the three Level Win hints. A hint is shown to the player in a casual
mobile game, and its job is to HELP THEM GUESS WHERE THIS IS.

MAXIMUM 50 CHARACTERS EACH. Not words — characters, counted including spaces.
This is the shape and the whole length of it:

    that church was baroque architecture

Say it the way a person would say it to a 10-year-old standing next to them:
point at the thing, name what it is, stop. No preamble, no second clause, no
explaining why it matters. Plain words a child would already know, or one
worth learning — "baroque", "Hangul", "cobblestones" — but never a sentence
that needs a second sentence.

  * POINT AT SOMETHING YOU CAN ACTUALLY SEE and name it: the alphabet on the
    signs, the shape of the plates, the colour of the kerb, the style of the
    church, the brand on the bikes, the kind of tree.
  * DO NOT NAME THE PLACE. Never the city, region or country the player is
    being asked to find. "Those signs are in Hangul" is a hint; "you are in
    Korea" is the answer.
  * NO formatting of any kind — no brackets, no category labels, no quotes
    unless the sign itself is being quoted.

Only what is ACTUALLY in the picture, and only what is LEGIBLE at the size it
appears. If the scene was supposed to contain a plate or a sign and it did not
come out, do not mention it. A hint describing something absent makes the
level unfair, and one describing something unreadable makes it impossible.

  * hint 1 is the strongest: the thing that most narrows down where this is.
  * hint 2 is the second strongest, and about something DIFFERENT.
  * hint 3 is a smaller, less obvious detail a sharp player would spot.

Chonky — the orange cat — is never a hint. He is what the player hunts, not
evidence of where they are.

IS THIS ACTUALLY CHONKY? He is a specific character, not "a cat": a
near-spherical body far rounder than any ordinary cat, very short legs almost
lost under it, a large white belly bib from chin down the underside, four
white paws like socks, a thick fluffy tail with darker bands, round cheeks and
a small head against a huge body, ginger tabby striping over the orange.

An ordinary ginger tabby in the right place at the right size is a FAILED
image, exactly as a wrongly-sized one is — so say so plainly when that is what
you see.

Answer `is_chonky` with exactly one of three words:

  "yes"     you can see the silhouette and it is his
  "no"      you can see the silhouette and it is an ordinary cat
  "unsure"  you cannot see the silhouette well enough to say

"unsure" IS A REAL ANSWER AND IS OFTEN THE RIGHT ONE. If he is too small, too
distant, turned away, or blurred, the honest answer is that you cannot tell.
A 16-pixel cat at the far end of a promenade cannot be confirmed as Chonky by
anyone — guessing "yes" there is worse than useless, because it reports a
check that was never actually made. Do not reason from the prompt, from the
reference sheet, or from what the picture was supposed to contain: judge only
what you can actually resolve in these pixels.

Give `identity_note` whenever the answer is not "yes", saying in a few words
what is wrong or what stopped you ("ordinary tabby, no bib", "legs too long",
"a speck at this distance, no bib resolvable").

HOW MANY CLUES ARE REALLY THERE? Count the independent, concrete, human-made
pieces of evidence a player could actually reason from — signage, script,
licence plates, road markings, driving side, flags, bollards, utility poles,
transit livery, building construction, a landmark. Count only what is genuinely
visible and legible at the size it appears, and count two things of the same
kind once. The floor for a usable image is TWO, at every difficulty: a
beautiful picture with nothing to reason from is a failed puzzle, not a hard
one.

Report `clue_count`, and `clue_families` naming which of these they belong to:
{families}

Also report `clue_in_viewframe`: whether at least one clue strong enough to
place the country or region sits inside the VIEWFRAME — the centre of the
picture, from about 21% to 79% of the width and 8% to 92% of the height. That
is all the player sees before panning, and a puzzle whose only evidence is out
in the side margins opens on nothing.

Also report whether any lettering in the picture failed to come out: a sign,
plaque, banner or plate that is BLANK where text belongs, or GARBLED into
shapes that are not real writing. A render came back with a proper ceramic
street plaque containing no name at all — the frame without the word. That is
a broken prop, not a subtle clue, and the reviewer has to know.

Also report whether anything in the picture spells the answer: a sign,
banner, plate, storefront or inscription containing the name of the city, the
region or the country. That makes the level a reading test rather than a
geography one.

Reply with JSON only, no prose around it:

{{"clues": ["...", "...", "..."],
 "clue_words": ["...", "...", "..."],
 "is_chonky": "yes" or "no" or "unsure",
 "identity_note": "what is wrong with the cat, or empty",
 "clue_count": a whole number,
 "clue_families": ["...", "..."],
 "clue_in_viewframe": true or false,
 "names_the_place": true or false,
 "names_the_place_detail": "what it says, or empty",
 "broken_text": true or false,
 "broken_text_detail": "which sign is blank or garbled, or empty"}}

`clue_words` are those same three clues as ONE lowercase word each; they
become the filename, so no spaces and no punctuation.
"""


# Caglar's own example runs to 25 words. The old 12-word cap is what forced
# the clue pass into captions: there is no room in twelve words to name a
# thing AND say what it tells you, so it dropped the half that helps.
# Fifty characters, counted with spaces (Caglar, 2026-10-02). The shape is
# "that church was baroque architecture" — 36 characters, said the way a person
# says it to a ten-year-old standing next to them.
#
# This is the third format. The first was twelve bracketed words and read like
# a database field; the second ran to about 130 characters and carried both the
# visible thing and what it narrowed the place down to. Fifty fits only the
# first half, which is the deliberate trade: the hint points and names, and the
# player makes the inference.
MAX_CLUE_CHARS = 50


_IDENTITY = ("yes", "no", "unsure")


def _identity(value):
    """Three-valued, because a boolean had nowhere to put "I cannot tell".

    On the live batch of 2026-10-02 a Riga render whose cat is an unresolvable
    speck — 16 px, YOLO could not find it at all — came back True, and a
    Mexico City cat at 91 px where the bib does not resolve came back True as
    well. The pass had been told to say so in the note when it could not tell,
    but the field it answered in was a boolean, so uncertainty rounded to a
    confident answer.

    None still means "never asked". Anything unrecognised becomes "unsure",
    because a word nobody defined is not evidence of anything. Booleans are
    accepted for sidecars written before this existed.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return "yes" if value else "no"
    text = str(value).strip().lower()
    return text if text in _IDENTITY else "unsure"


def check_clues(clues: list[str]) -> None:
    """Hold the hints to fifty characters. Nothing else is enforced here.

    Two earlier rules were tried and removed. A word cap produced captions,
    because twelve words has no room to name a thing and say anything about
    it. A "must contain a proper noun" rule rejected a Sydney hint that
    described the Harbour Bridge precisely and deliberately without naming it,
    which was the format working — avoiding the answer and naming something
    identifiable pull in opposite directions, and no regex tells them apart.

    A length in characters is the one thing about a hint that is not a matter
    of judgement, so it is the one thing checked.
    """
    for clue in clues:
        text = clue.strip()
        if len(text) > MAX_CLUE_CHARS:
            raise InspectError(
                f"hint is {len(text)} characters, over the {MAX_CLUE_CHARS}-"
                f"character limit — say it the way you would say it to a "
                f"ten-year-old and then stop: {clue!r}")


def inspect_render(image_path, *, city: str, country: str, difficulty: int,
                   caller: Optional[Callable] = None,
                   model: Optional[str] = None) -> dict:
    """Write the clues for a finished render, from the render.

    Raises rather than guessing: a clue nobody checked is exactly what this
    step exists to stop producing.
    """
    image_path = Path(image_path)
    if not image_path.exists():
        raise FileNotFoundError(f"no render at {image_path}")

    caller = caller or _call_via_cli
    raw = caller(_SYSTEM, _user_message(city, country, difficulty),
                 model=model, image=image_path)

    data = _parse(raw)
    if data is None:
        raise InspectError(f"reply was not JSON; got {raw[:200]!r}")
    if not isinstance(data, dict):
        raise InspectError(f"reply was not a JSON object: {raw[:200]!r}")

    for key in _REQUIRED:
        value = data.get(key)
        if not isinstance(value, list) or len(value) != 3:
            raise InspectError(f"expected exactly three {key}, got {value!r}")

    check_clues([str(c) for c in data["clues"]])

    # Reported, never enforced. These are a vision model's judgement rather
    # than a measurement like the pixel box, so they can be wrong in both
    # directions — they belong beside the size verdict for a person to read,
    # not in front of a gate. A reply that omits them reports None rather than
    # a confident False, because "not asked" and "no" are different answers.
    from scripts.chonky.prompts import CLUE_FAMILIES

    families = [str(f) for f in (data.get("clue_families") or [])
                if str(f) in CLUE_FAMILIES]

    count = data.get("clue_count")
    try:
        count = int(count) if count is not None else None
    except (TypeError, ValueError):
        count = None

    return {
        "clues": [str(c) for c in data["clues"]],
        "clue_words": [str(w) for w in data["clue_words"]],
        "is_chonky": _identity(data.get("is_chonky")),
        "identity_note": str(data.get("identity_note") or ""),
        "clue_count": count,
        "clue_families": families,
        "clue_in_viewframe": (None if data.get("clue_in_viewframe") is None
                              else bool(data["clue_in_viewframe"])),
        "names_the_place": bool(data.get("names_the_place")),
        "names_the_place_detail": str(data.get("names_the_place_detail") or ""),
        "broken_text": bool(data.get("broken_text")),
        "broken_text_detail": str(data.get("broken_text_detail") or ""),
    }
