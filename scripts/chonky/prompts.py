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

_REQUIRED = ("city", "country", "prompt", "clues", "clue_words")


class DraftError(RuntimeError):
    """The model's reply cannot be used as a prompt."""


def _manual_text() -> str:
    return MANUAL.read_text()


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
    (re.compile(r"no illustration style", re.I),
     'the prompt says "no illustration style", which the model applies to the '
     "cat and reads as \"not a stylised character\" — the reference is one"),
    # Distance in metres, however the number is spelled — the first attempt at
    # this only caught numerals and let "about fifteen metres from the camera"
    # through, which is the exact phrasing that failed. Matching a sentence
    # that mentions both metres and the camera covers every spelling and still
    # leaves "a 200-metre bridge" alone.
    (re.compile(r"[^.]*\b(?:metre|meter)s?\b[^.]*\bcamera\b|"
                r"[^.]*\bcamera\b[^.]*\b(?:metre|meter)s?\b", re.I),
     "the prompt measures his distance from the camera in metres; metres do "
     "not control distance (fifteen metres returned three times the size "
     "band) — state depth as an ordering against things already in the scene"),
)


def _check(prompt: str) -> None:
    for pattern, why in _BANNED:
        if pattern.search(prompt):
            raise DraftError(why)


def _user_message(difficulty: int, target_zone: str, used: list[str],
                  city: Optional[str], country: Optional[str]) -> str:
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
    lines += [
        "",
        "Reply with JSON only, no prose around it:",
        '{"city": "...", "country": "...", "prompt": "...", '
        '"clues": ["...", "...", "..."], '
        '"clue_words": ["...", "...", "..."]}',
        "",
        "`prompt` is the complete self-contained image prompt. `clues` are the "
        "three Level Win clue messages, each naming something a player can "
        "actually see in the scene you described. `clue_words` are those same "
        "three clues as ONE lowercase word each — they become the filename, so "
        "no spaces and no punctuation.",
    ]
    return "\n".join(lines)


def draft(*, difficulty: int, target_zone: str, used: list[str],
          city: Optional[str] = None, country: Optional[str] = None,
          caller: Optional[Callable] = None, model: Optional[str] = None) -> dict:
    """Write one prompt. Raises DraftError rather than return something unusable."""
    caller = caller or _default_caller
    raw = caller(_manual_text(),
                 _user_message(difficulty, target_zone, used, city, country),
                 model=model)

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
    }
