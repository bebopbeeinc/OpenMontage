"""Write one Chonky image prompt, to the manual's rules.

The person using this tool does not write prompts. That decision is what makes
the rules here load-bearing: every one of them was paid for with a render that
came back wrong, and a human writing prompts by hand would walk into the same
failures with no way to know why.

What the manual is for: it goes to the model as the system prompt, unedited, so
the rules travel with the request instead of being summarised into it. What
this module adds on top is a check on the way back — a couple of phrasings that
have each cost a render are rejected before anything is submitted, because a
prompt is cheap to rewrite and a render is not.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

MANUAL = Path(__file__).resolve().parent / "docs" / "manual.md"
MODEL = os.environ.get("CHONKY_PROMPT_MODEL", "claude-sonnet-5")

_REQUIRED = ("city", "country", "viewpoint", "prompt", "clues", "clue_words")

# The manual's own ranked list of clue families (section 5.3), in its order.
# An operator can ask for more of one without it costing the others: these are
# emphases, not a budget being divided up.
CLUE_FAMILIES = {
    "road_markings": "Road markings",
    "road_signs": "Road signs",
    "language": "Language and writing system",
    "driving_side": "Driving side",
    "licence_plates": "Licence plates",
    "flags": "Flags",
    "landmark": "Famous landmark or monument",
    "bollards": "Bollards and roadside posts",
    "utility_poles": "Utility poles",
    "transit": "Transit",
    "architecture": "Architecture and building materials",
    "street_furniture": "Street furniture",
    "vegetation": "Vegetation, climate, terrain",
    "infrastructure": "Distinctive local infrastructure",
}


class DraftError(RuntimeError):
    """The model's reply cannot be used as a prompt.

    Carries the offending prompt so a retry can show the writer what it wrote
    rather than only what was wrong with it.
    """

    def __init__(self, message: str, prompt: Optional[str] = None):
        super().__init__(message)
        self.prompt = prompt


def _manual_text() -> str:
    return MANUAL.read_text()


# Sections of the manual that describe a job this writer is not doing:
# rendering, verifying pixels, delivering to Drive, and what to print for a
# human operator. Sending them cost nothing but attention, and attention is
# exactly what the placement rules were losing — three drafts followed them
# zero times while being told, in the same breath, to write six prompts, run a
# whole batch without stopping, call tools this writer does not have, and
# answer as TSV.
_NOT_THE_WRITERS_JOB = (
    "1. THE BATCH WORKFLOW",
    "6. RENDERING (STEP B)",
    "7. VERIFICATION AND CLUE MESSAGES (STEP C)",
    "8. FILENAMES AND DELIVERY (STEP D)",
    "9. WHAT YOU SHOW THE OPERATOR, AND WHEN",
)

_SECTION = re.compile(r"^\d+\. [A-Z]", re.M)

# Stated to the writer because both are checked on its answer, and a check the
# writer was never told about is a trap rather than a rule.
_WRITERS_BRIEF = """
=====================================================================
YOUR JOB
=====================================================================
You write ONE image prompt and answer with JSON. You render nothing, call
no tools, and deliver nothing — another part of the pipeline does that.
Ignore any instruction below about batches, rendering, verifying pixels,
saving files, or printing for an operator; those belong to a different
job. Everything below about the LOCATION, THE FRAME, CHONKY HIMSELF and
THE CLUES is yours and is binding.

Two things are checked on your answer, and it is rejected without them:

  1. DEPTH AS AN ORDERING. Your prompt must place Chonky behind
     something already in the scene, in so many words — "every one of
     those people is between the camera and him", "he is behind the
     furthest walking tourist", "far beyond all of them". Distances in
     metres are rejected: they do not control how large he renders.

  2. HE STANDS ON THE GROUND. Your prompt must not seat him on a
     parapet, bench, ledge, step, wall, crate, case or any other piece
     of furniture. Naming a prop as his surface makes the prop the
     subject and brings both toward the camera. Put the prop in the
     scene and put him on the ground near it.

ONE REAL PLACE, NOT A PLACE LIKE IT. Section 2 below is the highest
priority in this manual and it keeps being ignored, so it is repeated
here. Every image is one exact viewpoint a person could stand at and
photograph: a named street, plaza, bridge or overlook, facing a stated
direction, with the real arrangement of what is left, centre, right,
near and far from that spot. Not a scene assembled from things the city
is known for. A render came back with laurel trees clipped into cubes,
described as "a formal geometric plaza-landscaping style" — that is a
motif, not a location, and it is what section 2.6 forbids. If you
cannot verify a viewpoint, choose a different one.

Build the geography completely first and put Chonky in last. With him
removed the picture must still be a solvable geography puzzle.

NOTHING IN THE PICTURE SPELLS THE ANSWER. No sign, banner, plate,
storefront or monument inscription may contain the name of the city,
the region or the country. A render came back with "Seattle" written on
a sign and another with "Guatemala" on a licence plate: those are not
clues, they are the answer key, and they make the level a reading test
rather than a geography one. The player should deduce the place from
script, signage conventions, plate design, road markings, architecture,
climate and terrain — never from the name itself.

NOT A CROWD SCENE. These images keep coming back packed with tourists,
and a crowd is the worst thing that can happen to this game: it hides
the clues the player is meant to read, it hides Chonky, and it dates the
photograph to a moment rather than a place. Write a quiet moment. A
handful of people at most, often none at all, and never a queue, a
throng or a busy square.

Be careful here, because rule 1 pulls the other way: people are the
easiest thing to put between the camera and Chonky, and reaching for
them is how the frame fills up. Depth can be measured against anything
already standing in the scene — "beyond the last market stall", "behind
the far lamppost", "further down the quay than the moored boats",
"deeper in than the second archway". Use those first, and people only
when the place genuinely has a few.

=====================================================================
"""


def writer_manual() -> str:
    """The manual with the other agent's job removed, and the brief on top."""
    text = _manual_text()
    starts = [m.start() for m in _SECTION.finditer(text)] + [len(text)]
    kept = []
    for start, end in zip(starts, starts[1:]):
        chunk = text[start:end]
        if not any(chunk.startswith(h) for h in _NOT_THE_WRITERS_JOB):
            kept.append(chunk)
    return _WRITERS_BRIEF + text[:starts[0]] + "".join(kept)


def _call_via_cli(system: str, user: str, *, model: Optional[str] = None) -> str:
    """Ask Claude through the CLI, which uses the OAuth subscription.

    Mirrors scripts/common/llm_shorten.py: no ANTHROPIC_API_KEY needed, and the
    `--output-format json` envelope carries the reply in `result`.
    """
    cli = shutil.which("claude")
    if not cli:
        raise DraftError("`claude` CLI not found in PATH and no ANTHROPIC_API_KEY set")
    cmd = [cli, "--print", "--model", model or MODEL,
           "--system-prompt", system, "--output-format", "json", user]
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_CODE_")}
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=300)
    if proc.returncode != 0:
        raise DraftError(f"`claude` CLI failed (exit {proc.returncode}): "
                         f"{proc.stderr.strip() or proc.stdout.strip()}")
    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise DraftError(f"`claude` CLI returned a non-JSON envelope: {exc}") from exc
    text = envelope.get("result", "")
    if not isinstance(text, str):
        raise DraftError(f"`claude` CLI envelope `result` is not a string: {text!r}")
    return text


def _call_via_sdk(system: str, user: str, *, model: Optional[str] = None) -> str:
    import anthropic

    reply = anthropic.Anthropic().messages.create(
        model=model or MODEL,
        max_tokens=2000,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in reply.content if getattr(b, "type", "") == "text")


def _default_caller(system: str, user: str, *, model: Optional[str] = None) -> str:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return _call_via_sdk(system, user, model=model)
    return _call_via_cli(system, user, model=model)


def _strip_fence(text: str) -> str:
    """Models fence JSON out of habit. That is not a malformed reply."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    body = stripped.split("\n", 1)[1] if "\n" in stripped else ""
    return body.rsplit("```", 1)[0].strip()


# Phrasings that have each cost a render. Checked on the way back rather than
# only asked for on the way out, because asking is not the same as getting.
_BANNED = (
    # (pattern, why, only_in_sentences_about_him)
    (re.compile(r"no illustration style", re.I),
     'the prompt says "no illustration style", which the model applies to the '
     "cat and reads as \"not a stylised character\" — the reference is one",
     False),
    # Distance in metres, however the number is spelled: the first version of
    # this caught only numerals and let "about fifteen metres from the camera"
    # through, which is the exact phrasing that failed. Judged only in
    # sentences about him, so "the camera looks down the 200-metre bridge"
    # stays a fact about the bridge.
    (re.compile(r"\b(?:metre|meter)s?\b[^.]*\bcamera\b|"
                r"\bcamera\b[^.]*\b(?:metre|meter)s?\b", re.I),
     "the prompt measures his distance from the camera in metres; metres do "
     "not control distance (fifteen metres returned three times the size "
     "band) — state depth as an ordering against things already in the scene",
     True),
)


# Things a prompt may name as his surface, and things it may not. Measured
# over three drafts, the writer seated him on furniture in two of them and
# stated depth as an ordering in none, with every one of these rules in its
# system prompt. Asking is not the same as getting.
_FURNITURE = (
    "parapet", "balustrade", "bench", "ledge", "step", "steps", "stair", "stairs",
    "windowsill", "sill", "railing", "rail", "wall", "table", "chair", "stool",
    "case", "box", "crate", "barrel", "plinth", "pedestal", "kerb", "curb",
    "planter", "bollard", "suitcase", "luggage", "cart", "stall", "counter",
    "bin", "post", "pillar", "column", "roof", "bonnet", "hood", "boat", "bicycle",
)
_ON_FURNITURE = re.compile(
    r"\b(?:sit|sits|sitting|sat|stand|stands|standing|stood|perch\w*|lie|lies|"
    r"lying|lay|settle[sd]?|settled|rest|rests|resting|curl\w*)\b[^.]{0,80}?"
    r"\b(?:on|upon|atop|on top of)\s+(?:the|a|an)?\s*"
    r"(?:[a-z\-]+\s+){0,3}(" + "|".join(_FURNITURE) + r")\b",
    re.I,
)

# Whether a sentence is about Chonky at all. Without this the furniture rule
# fired on "a dozen tourists sit on the steps" and on "taxis stand on the
# kerb", and the retry then told the writer to fix a placement it had never
# written — three draft cycles spent on a misdiagnosis.
_ABOUT_HIM = re.compile(r"\b(?:chonky|he|him|his)\b", re.I)


def _sentences(text: str) -> list[str]:
    return [t for t in re.split(r"(?<=[.!?])\s+|\n+", text) if t.strip()]


# Depth has to be stated as an ordering against something already in the
# scene; metres do not control it. A plain comparative says exactly that and
# was being rejected, which is worse than letting a loose phrasing through.
_ORDERING = re.compile(
    r"between the camera and (?:him|chonky)|"
    r"behind the (?:furthest|farthest|last|rearmost)|"
    r"(?:far )?beyond (?:all|them|every)|"
    r"\b(?:further|farther|deeper)\b[^.]{0,60}?\bthan\b|"
    r"no closer (?:to the camera )?than",
    re.I,
)

# A viewpoint has to name a place. These are what a generic one looks like:
# the render that prompted this described cube-clipped laurel trees as "a
# formal geometric plaza-landscaping style", which is a motif rather than a
# location, and the manual has forbidden exactly that since section 2.6.
_VAGUE_VIEWPOINT = re.compile(
    r"^(?:the\s+|a\s+|an\s+)?(?:busy\s+|old\s+|main\s+|central\s+)?"
    r"(?:plaza|square|street|road|avenue|town|old town|city cent(?:re|er)|"
    r"downtown|market|park|waterfront|promenade|neighbourhood|neighborhood|"
    r"district|quarter)"
    r"(?:\s+(?:in|of|at)\s+[\w\s]+)?$",
    re.I,
)


def _check_viewpoint(viewpoint: str) -> None:
    text = viewpoint.strip().rstrip(".")
    if len(text) < 12 or _VAGUE_VIEWPOINT.match(text):
        raise DraftError(
            f"{viewpoint!r} names a kind of place, not a place. Name the "
            "actual street, plaza, bridge or overlook and the direction the "
            "camera faces — somewhere a person could stand and be found on a "
            "map")


def _check(prompt: str) -> None:
    # The banned phrasings are judged per sentence too, so that a fact about
    # the scene — "the camera looks down the 200-metre-long bridge" — is not
    # read as a placement for him.
    his = [s for s in _sentences(prompt) if _ABOUT_HIM.search(s)]

    for pattern, why, his_only in _BANNED:
        haystack = his if his_only else [prompt]
        if any(pattern.search(t) for t in haystack):
            raise DraftError(why, prompt)

    for sentence in his:
        seated = _ON_FURNITURE.search(sentence)
        if seated:
            raise DraftError(
                f"the prompt seats him on the {seated.group(1)}; a prop named as "
                "his surface is understood as the thing being photographed and "
                "the model brings both forward — put him on the ground near it "
                "instead", prompt)

    if not _ORDERING.search(prompt):
        raise DraftError(
            "the prompt never states his depth as an ordering against something "
            "already in the scene, which is the only thing that controls it — say "
            "that the people are between the camera and him, or that he is behind "
            "the furthest one, or far beyond all of them", prompt)


def _weights_section(weights: Optional[dict]) -> list[str]:
    """Render the operator's emphases, or say nothing at all.

    Saying nothing is the default on purpose: an unset family must behave
    exactly as the manual already describes, so the absence of an opinion must
    not arrive as an opinion.
    """
    if not weights:
        return []

    lines = []
    for key, weight in weights.items():
        if key not in CLUE_FAMILIES:
            raise ValueError(
                f"unknown clue family {key!r}; expected one of "
                f"{', '.join(sorted(CLUE_FAMILIES))}")
        if not isinstance(weight, int) or isinstance(weight, bool) or not 1 <= weight <= 5:
            raise ValueError(
                f"weight for {key!r} must be a whole number from 1 to 5, got {weight!r}")
        lines.append(f"  - {CLUE_FAMILIES[key]}: {weight}")

    if not lines:
        return []
    return ["", "Emphasis requested for this image, 1 to 5 where 5 is strongest:"] + \
        sorted(lines) + [
        "",
        "These are additions, not a share-out. A high weight on one family "
        "does not reduce any other, and a family not listed here keeps exactly "
        "the weight the manual gives it — do not drop it or play it down.",
    ]


def _user_message(difficulty: int, target_zone: str, used: list[str],
                  city: Optional[str], country: Optional[str],
                  weights: Optional[dict] = None) -> str:
    lines = [
        f"Write ONE image prompt for difficulty {difficulty}.",
        "",
        f"Chonky's assigned zone for this image is: {target_zone}. "
        "The zone is assigned by the ledger and is not yours to choose.",
    ]
    if city:
        where = f"{city}, {country}" if country else city
        lines += ["", f"The location is fixed: {where}. Choose the viewpoint within it."]
    else:
        lines += ["", "Choose the location yourself."]
    if used:
        lines += ["", "These locations are already used. Do not reuse any of them, "
                      "and do not use a different viewpoint in the same city:"]
        lines += [f"  - {u}" for u in used]
    lines += _weights_section(weights)
    lines += [
        "",
        "Reply with JSON only, no prose around it:",
        '{"city": "...", "country": "...", "viewpoint": "...", '
        '"prompt": "...", "clues": ["...", "...", "..."], '
        '"clue_words": ["...", "...", "..."]}',
        "",
        "`viewpoint` names the one real, photographable camera position this "
        "image is taken from — the actual street, plaza, bridge or overlook, "
        "and the direction the camera faces. It must be somewhere a person "
        "could stand, specific enough to find on a map. \"A plaza\" is not a "
        "viewpoint. `prompt` is the complete self-contained image prompt. `clues` are the "
        "three Level Win clue messages, each naming something a player can "
        "actually see in the scene you described. `clue_words` are those same "
        "three clues as ONE lowercase word each — they become the filename, so "
        "no spaces and no punctuation.",
    ]
    return "\n".join(lines)


MAX_ATTEMPTS = 3


def draft(*, difficulty: int, target_zone: str, used: list[str],
          city: Optional[str] = None, country: Optional[str] = None,
          weights: Optional[dict] = None, verifier: Optional[Callable] = None,
          caller: Optional[Callable] = None, model: Optional[str] = None) -> dict:
    """Write one prompt, retrying with the reason when one is rejected.

    A rejection that just fails the job teaches nothing and wastes the whole
    draft; handing back what was wrong, with the prompt that was wrong, is what
    turns the check into a correction. Drafting is cheap — it is the render
    the check exists to protect.
    """
    caller = caller or _default_caller
    system = writer_manual()
    ask = _user_message(difficulty, target_zone, used, city, country, weights)

    last: Optional[DraftError] = None
    for _ in range(MAX_ATTEMPTS):
        try:
            drafted = _attempt(caller, system, ask, model)
            if verifier is not None:
                # Checked before the render, because before costs a search and
                # after costs an image — and nothing downstream can tell a
                # beautiful render of an invented street from a real one.
                finding = verifier(city=drafted["city"], country=drafted["country"],
                                   viewpoint=drafted["viewpoint"])
                if not finding.get("verified"):
                    raise DraftError(
                        f"the viewpoint could not be verified as a real place: "
                        f"{finding.get('note') or 'not found'}. Choose somewhere "
                        f"you can point to on a map",
                        drafted["prompt"])
                drafted["place_note"] = finding.get("note", "")
                # The looked-up facts are appended to the prompt the writer
                # produced. It was guessing at the signage and the plates —
                # that is where a European plate turned up in Peru — and these
                # were checked.
                from scripts.chonky.verify_place import detail_block

                block = detail_block(finding.get("details") or {})
                if block:
                    drafted["prompt"] = drafted["prompt"] + block
                    drafted["place_details"] = finding.get("details")
            return drafted
        except DraftError as exc:
            last = exc
            ask = (f"{ask}\n\n"
                   f"Your previous answer was rejected: {exc}\n\n"
                   f"The prompt that was rejected was:\n{exc.prompt or '(none)'}\n\n"
                   "Write it again, fixing exactly that. Change nothing else.")
    raise last  # type: ignore[misc]


def _attempt(caller, system: str, ask: str, model: Optional[str]) -> dict:
    raw = caller(system, ask, model=model)

    try:
        data = json.loads(_strip_fence(raw))
    except (ValueError, TypeError) as exc:
        raise DraftError(f"reply was not JSON: {exc}; got {raw[:200]!r}") from exc
    if not isinstance(data, dict):
        raise DraftError(f"reply was not a JSON object: {raw[:200]!r}")

    missing = [k for k in _REQUIRED if not data.get(k)]
    if missing:
        raise DraftError(f"reply is missing {', '.join(missing)}")
    if not isinstance(data["clues"], list) or len(data["clues"]) != 3:
        raise DraftError(f"expected exactly three clues, got {data['clues']!r}")
    _check_viewpoint(str(data["viewpoint"]))
    if not isinstance(data["clue_words"], list) or len(data["clue_words"]) != 3:
        raise DraftError(
            f"expected exactly three clue_words, got {data['clue_words']!r}")

    _check(data["prompt"])
    return {
        "city": data["city"],
        "country": data["country"],
        "prompt": data["prompt"],
        "clues": [str(c) for c in data["clues"]],
        "clue_words": [str(w) for w in data["clue_words"]],
        "viewpoint": str(data["viewpoint"]).strip(),
    }


# --------------------------------------------------------------------------
# Choosing where, before writing anything
# --------------------------------------------------------------------------

def _pick_message(slots: list[int], used: list[str]) -> str:
    lines = [
        f"Choose {len(slots)} locations, one for each of these images, in order:",
        "",
    ]
    lines += [f"  image {i + 1}: difficulty {level}" for i, level in enumerate(slots)]
    lines += [
        "",
        "Pick each one to suit its difficulty, by the definitions in section 3.",
        "They must all be different places, and none of them may repeat a city "
        "that appears below.",
    ]
    if used:
        lines += ["", "Already used:"] + [f"  - {u}" for u in used]
    lines += [
        "",
        "Reply with JSON only, no prose around it:",
        '{"locations": [{"city": "...", "country": "..."}, ...]}',
        "",
        "Nothing else — no viewpoint, no prompt, no commentary. Those are "
        "written separately, one request per image.",
    ]
    return "\n".join(lines)


def pick_locations(*, slots: list[int], used: list[str],
                   caller: Optional[Callable] = None,
                   model: Optional[str] = None) -> list[dict]:
    """Choose one location per image, all different, none already used.

    This exists to make the prompt-writing parallel. Writing was sequential
    only so that each prompt could see which places the earlier ones had
    taken; settling that here, in one short call, lets the slow part — a full
    prompt per image, unchanged — happen at the same time instead of one after
    another.
    """
    caller = caller or _default_caller
    system = writer_manual()
    ask = _pick_message(slots, used)
    taken = {u.strip().lower() for u in used}

    last: Optional[DraftError] = None
    for _ in range(MAX_ATTEMPTS):
        raw = caller(system, ask, model=model)
        try:
            chosen = _validate_locations(json.loads(_strip_fence(raw)), slots, taken)
        except DraftError as exc:
            last = exc
            ask = (f"{ask}\n\nYour previous answer was rejected: {exc}\n\n"
                   "Choose again, fixing exactly that.")
            continue
        except (ValueError, TypeError) as exc:
            last = DraftError(f"reply was not JSON: {exc}; got {raw[:200]!r}")
            continue
        return chosen
    raise last  # type: ignore[misc]


def _validate_locations(data, slots: list[int], taken: set[str]) -> list[dict]:
    if not isinstance(data, dict) or not isinstance(data.get("locations"), list):
        raise DraftError(f"expected an object with a locations list, got {data!r}")

    chosen = data["locations"]
    if len(chosen) != len(slots):
        raise DraftError(
            f"expected {len(slots)} locations, got {len(chosen)}")

    seen: set[str] = set()
    out: list[dict] = []
    for entry in chosen:
        if not isinstance(entry, dict) or not entry.get("city") or not entry.get("country"):
            raise DraftError(f"every location needs a city and a country; got {entry!r}")
        city, country = str(entry["city"]).strip(), str(entry["country"]).strip()
        key = city.lower()
        if key in seen:
            raise DraftError(
                f"{city} appears twice; every image must be a different place")
        if key in {t.split(",")[0].strip() for t in taken}:
            raise DraftError(f"{city} has already been used; choose somewhere else")
        seen.add(key)
        out.append({"city": city, "country": country})
    return out
