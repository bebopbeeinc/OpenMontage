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

Write the three Level Win messages, each naming one thing you can genuinely
see. They are shown to a player in a casual mobile game, so the manual's
section 7 governs how they read:

  * ONE simple factual sentence each, MAXIMUM 12 WORDS. Count them.
  * Fun-fact tone, internationally readable, understandable by a 10 year old.
  * Put the important nouns and proper nouns in [square brackets].
  * Open with the clue's category, then a colon. For example:
      Road marking: The pavement contains [Hangul], the Korean alphabet.
      Flag: A blue-white-red [French flag] flies from the pole.

  * clue 1 is the single most definitive visible clue.
  * clue 2 is the second most definitive, and DISTINCT from clue 1.
  * clue 3 is one less obvious, semi-hidden clue.

Rules, and they are the whole point of this step:

  * ONLY what is in the picture. If the scene was supposed to contain a
    licence plate, a road sign or a flag and it did not come out, do not
    mention it. A clue describing something absent makes the level unfair.

  * ONLY what is LEGIBLE. If text or a detail is too small, blurred or
    low-contrast to read at the size it appears, it is not a clue. Do not
    write "the plate reads Jalisco" unless you can actually read Jalisco on
    it. Say what a player could genuinely make out instead.

  * Describe what the thing looks like, not what you infer it means. "The
    plate has a blue strip on the left with yellow stars" is a clue. "The
    plate confirms Estonia" is a conclusion, and if the strip is not there
    the clue is simply false.

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


MAX_CLUE_WORDS = 12


def check_clues(clues: list[str]) -> None:
    """Hold the clues to section 7.5, because asking has not been enough.

    A clue is a line in a casual game, read on a phone in a second. The pass
    was returning eighteen words of scene description, which is a caption.
    """
    for clue in clues:
        body = clue.split(":", 1)[1] if ":" in clue else clue
        words = [w for w in body.split() if w.strip()]
        if len(words) > MAX_CLUE_WORDS:
            raise InspectError(
                f"clue is {len(words)} words, over the {MAX_CLUE_WORDS}-word "
                f"limit in section 7.5: {clue!r}")


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
