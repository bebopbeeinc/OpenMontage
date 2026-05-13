"""Generic LLM-driven copy shortener for VO / caption rewrite.

Pipeline-agnostic. Pull this in whenever a script needs to shrink a string
to a target word count while preserving tone + named entities, optionally
steered by reviewer-supplied feedback.

Usage:
    from llm_shorten import claude_shorten

    new_text = claude_shorten(
        current="Believe it or not, Australia is wider than the moon.",
        target_words=8,
        topic="Australia size",
        situation="VO segment 'claim' overshoots its 7.3s window (8.2s of speech)",
        brand_tokens=["Captain", "Travel Crush"],
        reviewer_feedback="closer VO sounds rushed",
        tone_hints="curious, punchy",
    )

Returns the new text (already stripped of any wrapping quotes Claude may add).

`brand_tokens` are surfaced to the model so it doesn't accidentally rewrite
them. `reviewer_feedback`, when present, is elevated to "most important
constraint" — feedback like "sounds sped up" should push toward aggressive
shortening even at the cost of original beats.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

import anthropic

DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_TONE = "curious, punchy"


def _call_via_cli(system: str, user: str, model: str) -> str:
    """Plain-text fallback via the `claude` CLI (uses OAuth subscription —
    no ANTHROPIC_API_KEY required). Returns the assistant's message text.

    The `--output-format json` envelope gives us a structured wrapper whose
    `result` field is the assistant's text reply.
    """
    cli = shutil.which("claude")
    if not cli:
        raise RuntimeError("`claude` CLI not found in PATH")
    cmd = [
        cli, "--print",
        "--model", model,
        "--system-prompt", system,
        "--output-format", "json",
        user,
    ]
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_CODE_")}
    proc = subprocess.run(
        cmd, capture_output=True, text=True, env=env, timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"`claude` CLI failed (exit {proc.returncode}): "
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )
    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"`claude` CLI returned non-JSON envelope: {e}; "
            f"raw (first 300 chars): {proc.stdout[:300]!r}",
        ) from e
    text = envelope.get("result", "")
    if not isinstance(text, str):
        raise RuntimeError(f"`claude` CLI envelope `result` is not a string: {text!r}")
    return text


def claude_shorten(
    *,
    current: str,
    target_words: int,
    topic: str = "",
    situation: str = "",
    brand_tokens: list[str] | tuple[str, ...] = (),
    reviewer_feedback: str = "",
    tone_hints: str = DEFAULT_TONE,
    client: anthropic.Anthropic | None = None,
    model: str = DEFAULT_MODEL,
) -> str:
    """Length-bounded rewrite via Claude. Returns the rewritten string.

    Routing:
      - When `ANTHROPIC_API_KEY` is set, uses the Anthropic Python SDK.
      - Otherwise, falls back to the local `claude` CLI (no API key needed).
    """
    brands = ", ".join(brand_tokens) if brand_tokens else "(none)"
    feedback_block = (
        f"\nReviewer feedback on the previous render (this is the human telling "
        f"you what to fix; treat it as the most important constraint):\n"
        f"  {reviewer_feedback}\n"
    ) if reviewer_feedback else ""

    system = (
        "You are a tight copy editor for short-form video. You rewrite individual "
        "fields to be shorter without losing meaning. You preserve the central "
        "point and any named entities the caller flags as brand_tokens. You "
        "return ONLY the rewritten text — no quotes, no preamble, no explanation."
    )
    user = (
        f"{('Topic: ' + topic) if topic else ''}\n"
        f"{('Situation: ' + situation) if situation else ''}\n"
        f"Brand names to preserve verbatim: {brands}\n"
        f"{feedback_block}\n"
        f"Current value: {current!r}\n\n"
        f"Constraints:\n"
        f"- Target length: at most {target_words} words.\n"
        f"- Preserve the central point and any brand names listed above.\n"
        f"- Keep tone consistent: {tone_hints}.\n"
        f"- If reviewer feedback is present above, it overrides tone defaults.\n"
        f"- Return only the new value as plain text.\n"
    )

    if os.environ.get("ANTHROPIC_API_KEY"):
        c = client or anthropic.Anthropic()
        resp = c.messages.create(
            model=model,
            max_tokens=300,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        out = "".join(b.text for b in resp.content if hasattr(b, "text")).strip()
    else:
        out = _call_via_cli(system, user, model).strip()

    # Strip stray wrapping quotes Claude sometimes adds.
    if (out.startswith('"') and out.endswith('"')) or (out.startswith("'") and out.endswith("'")):
        out = out[1:-1].strip()
    return out
