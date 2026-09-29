"""Check that a viewpoint is a real place before paying to render it.

Section 2 of the manual has always demanded this — "Verify it against reliable
maps, street-level imagery, official imagery, or multiple photographs" — and
the pipeline has never done it. Requiring the writer to NAME a viewpoint, which
it now does, rejects "a plaza"; a confidently worded invented street sails
straight through.

That is the one failure nothing downstream can catch. The measurement only
sees a cat, the clue pass only describes what is in the picture, and a
beautiful render of a street that does not exist looks correct to all of them
while failing the game completely — the player cannot deduce a place from
evidence that was never true of anywhere.

So it is checked before the render rather than after, because before costs a
web search and after costs an image.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Callable, Optional

MODEL = os.environ.get("CHONKY_VERIFY_MODEL", "claude-sonnet-5")

# Anything short of a confident yes is a no. The manual's own instruction for
# an uncertain feature is to omit it, and an uncertain viewpoint is worse than
# an uncertain feature: everything else in the image is built on it.
_ACCEPTED_CONFIDENCE = ("high",)


class PlaceError(RuntimeError):
    """The reply cannot be used as a verification."""


def _call_via_cli(system: str, user: str, *, model: Optional[str] = None) -> str:
    """Ask Claude to look it up, with search tools allowed.

    WebSearch and WebFetch are the whole point: without them this is the same
    model that invented the place being asked whether it invented it.
    """
    cli = shutil.which("claude")
    if not cli:
        raise PlaceError("`claude` CLI not found in PATH")
    cmd = [cli, "--print", "--model", model or MODEL,
           "--allowed-tools", "WebSearch,WebFetch",
           "--output-format", "json", f"{system}\n\n{user}"]
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_CODE_")}
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=300)
    if proc.returncode != 0:
        raise PlaceError(f"`claude` CLI failed (exit {proc.returncode}): "
                         f"{proc.stderr.strip() or proc.stdout.strip()}")
    start = proc.stdout.find('{"')
    try:
        envelope = json.loads(proc.stdout[start:] if start >= 0 else proc.stdout)
    except json.JSONDecodeError as exc:
        raise PlaceError(f"`claude` CLI returned a non-JSON envelope: {exc}") from exc
    text = envelope.get("result", "")
    if not isinstance(text, str):
        raise PlaceError(f"envelope `result` is not a string: {text!r}")
    return text


def _parse(raw: str) -> Optional[dict]:
    """The verification object, from a reply that may be wrapped in prose."""
    for candidate in (_strip_fence(raw), _embedded_object(raw)):
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict) and "real" in data:
            return data
    return None


def _embedded_object(text: str) -> Optional[str]:
    """The first {...} in a reply that prefaced its JSON with a sentence."""
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
You are checking whether a described camera viewpoint is a real, photographable
place. Search the web before answering — maps, street-level imagery, tourism
and municipal pages, photographs.

Be strict. A plausible-sounding street name is not evidence. If you cannot find
the place, or you find the name but not the arrangement described, say so. A
wrong yes here costs a rendered image and puts an invented location into a
game whose entire premise is that the places are real.
"""


_DIFFICULTY = {
    1: "2 to 5 clear clues; the destination is effectively revealed. Famous "
       "landmarks are welcome.",
    2: "country obvious, city reasonably inferable. Landmarks are welcome.",
    3: "the city should require combining several clues.",
    4: "country or region identifiable, exact city difficult. NO unmistakable "
       "landmark.",
    5: "one or two subtle independent clues; close localisation needs real "
       "expertise. NO landmark at all — infrastructure and architecture carry it.",
}


def _user_message(city: str, country: str, viewpoint: str,
                  difficulty: int = 1) -> str:
    level = _DIFFICULTY.get(int(difficulty), _DIFFICULTY[5])
    return f"""\
Location: {city}, {country}
Viewpoint: {viewpoint}
This image is for difficulty {difficulty}: {level}

Does this place exist, and could a person stand there and photograph roughly
what is described?

If it does exist, find a PHOTOGRAPH taken from roughly this viewpoint. The
photograph becomes the scene — it is redrawn, and the player solves the level
from what is in it — so it has to work as a puzzle, not merely show the place.
Judge candidates against all of this and pick the best one:

  * It must suit difficulty {difficulty}. At 4 and above, reject any
    photograph containing an unmistakable landmark: that is an instant answer
    and the level is meant to be hard.

  * NOTHING IN IT MAY SPELL THE ANSWER. Reject a photograph where the name of
    the city, the region or the country is legible on a sign, a banner, a
    shopfront or a monument. That turns the level into a reading test.

  * It must carry readable evidence — signage, script, road markings, plates,
    architecture, terrain, vegetation. A pretty view with nothing to read is
    not a geography puzzle. At least one country-level clue should be there.

  * Prefer a quiet moment. A photograph full of tourists hides the clues and
    hides the cat; a handful of people is fine, a crowd is not.

  * Prefer PORTRAIT or a squarish frame over a wide landscape one. The game
    frame is 4:5 with a tall 9:16 window inside it, and the sides are the pan
    reward, so a letterbox photograph loses most of what makes the level.

Give a direct link to an image file, not to a page containing one, and leave
it empty rather than linking something you are not confident shows this exact
spot or that fails the conditions above.

Also report what is ACTUALLY there, because an image model left to guess will
invent it: a Peruvian street came back with a European
licence plate and a street plaque with no lettering on it.

Reply with JSON only, no prose around it:

{{"real": true or false,
 "confidence": "high" or "medium" or "low",
 "note": "one sentence saying what you found, naming your evidence",
 "photo_url": "a direct link to ONE photograph taken from roughly this "
               "viewpoint — a real image file (.jpg/.png), not a page — or "
               "empty if you cannot find one you are confident shows this "
               "exact spot",
 "photo_note": "one phrase on what the photograph shows, or empty",
 "details": {{
   "street_name": "the street's real name, as written on a sign there",
   "plate_format": "what a vehicle plate in this country looks like: colour, "
                   "character pattern, any band or symbol, and what it does NOT have",
   "script": "the alphabet and language on signage here",
   "signage": "what street signs physically look like here"
 }}}}

Use "high" only when you found the place itself, not merely a city that
plausibly contains such a place. Leave any detail out rather than guess it —
a wrong detail stated as fact is worse than a missing one.
"""


def verify_viewpoint(*, city: str, country: str, viewpoint: str,
                     difficulty: int = 1,
                     caller: Optional[Callable] = None,
                     model: Optional[str] = None) -> dict:
    """Look the viewpoint up. Returns the finding and whether it counts.

    `verified` is deliberately narrower than `real`: a "yes, low confidence" is
    not a verification, and treating it as one would reproduce the failure this
    exists to stop while appearing to have checked.
    """
    caller = caller or _call_via_cli
    ask = _user_message(city, country, viewpoint, difficulty)
    raw = caller(_SYSTEM, ask, model=model)

    data = _parse(raw)
    if data is None:
        # A nuanced rejection tends to arrive as prose: asked about an invented
        # Cusco street it explained, correctly, that the real Mirador de los
        # Cóndores is a trail 92 km away. The judgement was right and only the
        # format was wrong, so ask once more for it in JSON.
        raw_retry = caller(
            _SYSTEM,
            f"{ask}\n\nYour previous answer was prose. Reply with the JSON "
            f"object and nothing else.",
            model=model)
        data = _parse(raw_retry)
        if data is None:
            # Fail closed, keeping the prose: an answer nobody could read is
            # not a place anybody found, and the prose is the most useful
            # thing in the reply — it says what is actually there.
            return {"real": False, "confidence": "low",
                    "note": raw.strip()[:400], "photo_url": "", "photo_note": "",
                    "details": {}, "verified": False}

    real = bool(data["real"])
    confidence = str(data.get("confidence", "low")).strip().lower()
    details = data.get("details")
    if not isinstance(details, dict):
        details = {}
    details = {k: str(v).strip() for k, v in details.items() if str(v or "").strip()}

    return {
        "real": real,
        "confidence": confidence,
        "note": str(data.get("note") or ""),
        "photo_url": str(data.get("photo_url") or "").strip(),
        "photo_note": str(data.get("photo_note") or "").strip(),
        "details": details,
        "verified": real and confidence in _ACCEPTED_CONFIDENCE,
    }


_DETAIL_LABELS = (
    ("street_name", "The street sign reads"),
    ("signage", "Street signs here look like"),
    ("script", "Signage script and language"),
    ("plate_format", "Vehicle plates in this country"),
)


def detail_block(details: dict) -> str:
    """The verified facts, as a block to append to a prompt.

    Appended rather than woven in, and labelled, so a reviewer reading the
    prompt can see exactly which parts were looked up and which the writer
    chose. These override the writer: it was guessing, this was checked.
    """
    if not details:
        return ""
    lines = ["", "",
             "VERIFIED LOCAL DETAIL — these were looked up for this exact "
             "place and are not to be reinterpreted. Where they contradict "
             "anything above, they win:"]
    for key, label in _DETAIL_LABELS:
        value = details.get(key)
        if value:
            lines.append(f"  - {label}: {value}")
    lines.append(
        "  - Any lettering that appears must be real, correctly spelled and "
        "legible. A blank or garbled sign is worse than no sign: leave it out "
        "of frame rather than render it empty.")
    return "\n".join(lines)
