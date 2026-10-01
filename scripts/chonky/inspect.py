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
    return f"""\
This image is meant to be {city}, {country}, at difficulty {difficulty}.

Write the three Level Win hints. A hint is shown to the player in a casual
mobile game, and its job is to HELP THEM GUESS WHERE THIS IS.

This is the shape to copy:

    Those teal rental bikes on the sidewalk belong to the "MiBici" network,
    which is only found in Mexico's second-largest city.

Why that one works, and what to do:

  * POINT AT SOMETHING SPECIFIC YOU CAN SEE. "Those teal rental bikes on the
    sidewalk" — a thing in the picture, said the way a person would say it.
    Not "a curved bay is lined with high-rises", which describes the view the
    player is already looking at and narrows nothing down.

  * NAME IT. The hint is only useful if it names the identifiable thing: the
    MiBici network, the Hangul alphabet, a plate format, a bollard style, a
    tree species, a brand of bus. "A sign" helps nobody; "a blue sign in
    Hangul" is a hint.

  * SAY WHAT IT NARROWS DOWN, WITHOUT HANDING OVER THE ANSWER. "only found in
    Mexico's second-largest city" — the player still gets to make the last
    step themselves. Do NOT write "which means this is Guadalajara". Name the
    country, the region, the kind of place, or a fact that points at one city
    without naming it.

  * BE CONVERSATIONAL AND TEACH SOMETHING. Write like a person telling you a
    fun fact, not like a label on a diagram. A good hint is worth knowing even
    after the level is over. One or two sentences, up to about thirty words.

  * NO category prefixes, no square brackets, no formatting of any kind. Plain
    friendly sentences.

Only what is ACTUALLY in the picture, and only what is LEGIBLE at the size it
appears. If the scene was supposed to contain a plate or a sign and it did not
come out, do not mention it. Do not write "the plate reads Jalisco" unless you
can genuinely read Jalisco on it — a hint describing something absent makes
the level unfair, and one describing something unreadable makes it impossible.

  * hint 1 is the strongest: the thing that most narrows down where this is.
  * hint 2 is the second strongest, and about something DIFFERENT.
  * hint 3 is a smaller, less obvious detail a sharp player would spot.

Chonky — the orange cat — is never a hint. He is what the player hunts, not
evidence of where they are.

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
MAX_CLUE_WORDS = 32


def check_clues(clues: list[str]) -> None:
    """Refuse a caption. Asking has not been enough anywhere else here.

    The test is whether the sentence could only have been written about this
    place. "A curved bay is lined with white high-rises" could be fifty
    cities; "the MiBici network" could only be one.
    """
    for clue in clues:
        words = [w for w in clue.split() if w.strip()]
        if len(words) > MAX_CLUE_WORDS:
            raise InspectError(
                f"hint is {len(words)} words, over the {MAX_CLUE_WORDS}-word "
                f"limit — keep it to a sentence or two: {clue!r}")

        # A proper noun, a quoted name or a capitalised term is what makes a
        # hint actionable: MiBici, Hangul, Jalisco. Without one it is a
        # description of the picture.
        named = [w for w in clue.replace('"', " ").split()[1:]
                 if w[:1].isupper() or w[:1].isdigit()]
        if not named:
            raise InspectError(
                f"hint names nothing a player could look up or recognise, so "
                f"it narrows nothing down — name the network, the alphabet, "
                f"the plate format, the species: {clue!r}")


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

    try:
        data = json.loads(_strip_fence(raw))
    except (ValueError, TypeError) as exc:
        raise InspectError(f"reply was not JSON: {exc}; got {raw[:200]!r}") from exc
    if not isinstance(data, dict):
        raise InspectError(f"reply was not a JSON object: {raw[:200]!r}")

    for key in _REQUIRED:
        value = data.get(key)
        if not isinstance(value, list) or len(value) != 3:
            raise InspectError(f"expected exactly three {key}, got {value!r}")

    check_clues([str(c) for c in data["clues"]])

    return {
        "clues": [str(c) for c in data["clues"]],
        "clue_words": [str(w) for w in data["clue_words"]],
        "names_the_place": bool(data.get("names_the_place")),
        "names_the_place_detail": str(data.get("names_the_place_detail") or ""),
        "broken_text": bool(data.get("broken_text")),
        "broken_text_detail": str(data.get("broken_text_detail") or ""),
    }
