"""Feedback router for the trivia-short pipeline.

Reads `projects/<slug>/artifacts/feedback.json` (the reviewer's free-text note
saved by the web UI), classifies what the reviewer is complaining about, and
emits `projects/<slug>/artifacts/feedback_plan.json` — a structured plan with
concrete patches plus the list of pipeline phases that need to re-run.

The plan is consumed by `apply_feedback_patches.py`, which lives next door.
The web server's render loop runs router + applier(pre) before assemble and
applier(post) between reconcile and render.

Classifications (the router's `kind` enum):
  vo_length        — VO copy overruns its window; route through shorten_vo
  caption_word     — a specific caption word is wrong; patch words.json
  caption_timing   — a caption's start_ms / end_ms is off; patch words.json
  brand_token      — a brand name shows up wrong; add to brand_tokens_extra
  music_volume     — music too loud / too quiet; set music_volume_db override
  clip_change      — the source clip itself is wrong; surface, do not auto-fix
  unclear          — ambiguous; surface to human

Usage:
    python scripts/trivia/feedback_router.py <row> <slug>

Auth:
    Tries the Anthropic SDK first (using ANTHROPIC_API_KEY).
    When that env var is missing, falls back to the local `claude` CLI
    (Claude Code), which uses your OAuth subscription — no API key
    required. The CLI fallback honors the same FeedbackPlan JSON schema
    via `--json-schema`, so callers get identical output either way.

Exits:
    0  plan written (may be empty/no-op if nothing actionable)
    2  no feedback.json or feedback text is empty (caller should skip patcher)
    3  no auth available (no ANTHROPIC_API_KEY and no `claude` CLI in PATH)
    4  sheet read failed
    5  Claude call failed (SDK or CLI)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Literal, Union

import anthropic
from pydantic import BaseModel, Field, ValidationError

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from post_row import build_sheets, read_post_row  # noqa: E402

MODEL = "claude-opus-4-7"


# ---------------------------- Plan schema ----------------------------

class PatchSetWord(BaseModel):
    op: Literal["set_word"]
    target_word: str = Field(description="The word currently shown in the caption that the reviewer wants changed (as it appears in words.json — preserve trailing punctuation if present)")
    near_time_ms: int = Field(description="Approximate startMs of the target word. Used to disambiguate when the word appears multiple times. The applier picks the closest match.")
    new_word: str = Field(description="The corrected word text (preserve trailing punctuation if any)")
    reason: str = Field(description="One short sentence: why this word, why this time")


class PatchSetTiming(BaseModel):
    op: Literal["set_timing"]
    target_word: str = Field(description="The word whose timing needs to change (as it appears in words.json)")
    near_time_ms: int = Field(description="Approximate startMs of the target word for disambiguation")
    new_start_ms: int | None = Field(default=None, description="New start_ms, or null to leave unchanged")
    new_end_ms: int | None = Field(default=None, description="New end_ms, or null to leave unchanged")
    reason: str


class PatchAddBrand(BaseModel):
    op: Literal["add_brand"]
    token: str = Field(description='Canonical capitalization, e.g. "Fennec" or "Travel Crush"')
    reason: str


class PatchMusicVolume(BaseModel):
    op: Literal["set_music_volume_db"]
    value_db: float = Field(description="Music volume in dB. Negative reduces, positive boosts. Typical range -20 to +6.")
    reason: str


class PatchRegenerateSegment(BaseModel):
    op: Literal["regenerate_segment"]
    segment: Literal["hook", "body", "closer"]
    reason: str


class PatchShortenVoLine(BaseModel):
    """Hint that shorten_vo.py should re-run on the next assemble warning.
    Doesn't itself modify anything — just records intent so the assembler's
    retry path is allowed to fire."""
    op: Literal["allow_shorten_vo"]
    fields: list[Literal["hook", "claim", "resolution", "cta"]] = Field(
        description="Which VO lines the reviewer is complaining about"
    )
    reason: str


Patch = Union[
    PatchSetWord,
    PatchSetTiming,
    PatchAddBrand,
    PatchMusicVolume,
    PatchRegenerateSegment,
    PatchShortenVoLine,
]


class FeedbackPlan(BaseModel):
    summary: str = Field(description="One human-readable sentence describing what the reviewer is complaining about")
    classifications: list[Literal[
        "vo_length", "caption_word", "caption_timing", "brand_token",
        "music_volume", "clip_change", "unclear",
    ]]
    patches: list[Patch] = Field(default_factory=list)
    unresolved: list[str] = Field(
        default_factory=list,
        description="Free-text descriptions of feedback aspects that can't be auto-patched (e.g. 'closer clip vibe wrong')",
    )
    confidence: float = Field(
        default=0.5,
        ge=0.0, le=1.0,
        description="Overall confidence (0-1) that the patches accurately address the feedback",
    )


# ---------------------------- Prompt ----------------------------

SYSTEM_PROMPT = """You are the feedback router for a daily-trivia short-form video pipeline. Your job:

1. Read the reviewer's free-text feedback note about the most recent render.
2. Read the current captions (words.json — array of {word, startMs, endMs}).
3. Read the canonical VO script copy from the sheet row (so you know what was *supposed* to be said).
4. Decide which subsystem(s) need to act, and produce concrete patch operations.

Classification rules:

- **vo_length** — feedback like "VO is rushed", "sounds sped up", "audio cut off", "talking too fast". This means the VO script was too long for its time window. Action: emit `allow_shorten_vo` listing which fields, AND the assembler will run shorten_vo.py with this feedback as context.
- **caption_word** — feedback like "caption says X but should say Y", "wrong word in caption at time T". Action: emit `set_word` with the target word and its approximate startMs (look at words.json to find the matching entry). The applier will use word+time to find the right index, so include the trailing punctuation if any (e.g. `"true,"` not just `"true"`).
- **caption_timing** — feedback like "caption shows TRUE too late", "highlight is one word behind audio". Action: emit `set_timing` with the target word and approximate startMs, plus the new ms values.
- **brand_token** — feedback like "Captain is shown lowercase", "Fennec is missing capitalization". Action: emit `add_brand` with the canonical capitalization.
- **music_volume** — feedback like "music too loud", "music is drowning out the VO", "music too quiet". Action: emit `set_music_volume_db`. Default music is at -18 dB. A "too loud" complaint usually means -22 to -26 dB; "too quiet" means -12 to -14 dB.
- **clip_change** — feedback like "body clip looks off-brand", "wrong reaction archetype", "closer doesn't fit". This needs human action (regenerate the clip in OpenArt). Emit `regenerate_segment` and let the human handle it.
- **unclear** — feedback that's ambiguous or you can't confidently route. Add a description to `unresolved` so the human can see what wasn't auto-handled.

Multi-classification is fine — one feedback note can mention several issues. Emit patches for all that you can confidently handle and put the rest in `unresolved`.

Be conservative: if the target word for a caption_word patch is ambiguous (multiple matches and you can't tell which one the reviewer means), prefer `unresolved` over a wrong guess. The applier searches by word+nearest-time and will refuse if it can't find a unique match within ~2 seconds of `near_time_ms`.

Return ONLY the structured FeedbackPlan."""


def _load_feedback(slug: str) -> str:
    path = REPO / "projects" / slug / "artifacts" / "feedback.json"
    if not path.exists():
        return ""
    try:
        return str(json.loads(path.read_text()).get("feedback", "") or "").strip()
    except (json.JSONDecodeError, OSError):
        return ""


def _load_words(slug: str) -> list[dict]:
    path = REPO / "projects" / slug / "artifacts" / "words.json"
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text()) or []
    except (json.JSONDecodeError, OSError):
        return []


def _format_words_for_prompt(words: list[dict]) -> str:
    """Compact representation: 'i  startMs..endMs  "word"' per line."""
    lines = []
    for i, w in enumerate(words):
        text = w.get("word", "")
        s = w.get("startMs", 0)
        e = w.get("endMs", 0)
        lines.append(f"  {i:>3}  {s:>5}..{e:<5}  {text!r}")
    return "\n".join(lines)


def _call_via_sdk(system_prompt: str, user_prompt: str) -> FeedbackPlan:
    """Route via the Anthropic Python SDK. Requires ANTHROPIC_API_KEY."""
    client = anthropic.Anthropic()
    resp = client.messages.parse(
        model=MODEL,
        max_tokens=4096,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
        output_format=FeedbackPlan,
    )
    return resp.parsed_output  # type: ignore[no-any-return]


def _call_via_cli(system_prompt: str, user_prompt: str) -> FeedbackPlan:
    """Route via the local `claude` CLI. Uses the user's OAuth subscription —
    no API key required.

    The CLI emits an outer JSON envelope when invoked with
    `--output-format json`. When `--json-schema` is also set, the
    schema-conformant payload lives under the envelope's `structured_output`
    field (rather than in `result`, which holds the assistant's prose for
    text-mode runs).
    """
    cli = shutil.which("claude")
    if not cli:
        raise RuntimeError("`claude` CLI not found in PATH")
    schema_json = json.dumps(FeedbackPlan.model_json_schema())
    cmd = [
        cli, "--print",
        "--model", MODEL,
        "--system-prompt", system_prompt,
        "--output-format", "json",
        "--json-schema", schema_json,
        user_prompt,
    ]
    # Detach from any CLAUDE_CODE_* env that might confuse the child session —
    # this is a pure structured-output call, not a tool-using agent.
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_CODE_")}
    proc = subprocess.run(
        cmd, capture_output=True, text=True, env=env, timeout=300,
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
            f"`claude` CLI returned non-JSON envelope: {e}\n"
            f"raw output (first 500 chars): {proc.stdout[:500]!r}",
        ) from e
    payload = envelope.get("structured_output")
    if payload is None:
        raise RuntimeError(
            f"`claude` CLI envelope missing `structured_output` field. "
            f"envelope keys: {list(envelope.keys())}; "
            f"result (first 300 chars): {str(envelope.get('result', ''))[:300]!r}",
        )
    try:
        return FeedbackPlan.model_validate(payload)
    except ValidationError as e:
        raise RuntimeError(
            f"`claude` CLI structured_output does not match FeedbackPlan schema: {e}\n"
            f"raw payload: {json.dumps(payload)[:500]!r}",
        ) from e


def main(row_num: int, slug: str) -> int:
    artifacts = REPO / "projects" / slug / "artifacts"
    plan_path = artifacts / "feedback_plan.json"

    feedback = _load_feedback(slug)
    if not feedback:
        print(f"no feedback to route at {artifacts / 'feedback.json'}")
        # Clear stale plan so downstream phases don't act on yesterday's plan.
        plan_path.unlink(missing_ok=True)
        return 2

    use_sdk = bool(os.environ.get("ANTHROPIC_API_KEY"))
    use_cli = bool(shutil.which("claude")) if not use_sdk else False
    if not (use_sdk or use_cli):
        print(
            "ERROR: no Claude auth available — set ANTHROPIC_API_KEY or install the `claude` CLI",
            file=sys.stderr,
        )
        return 3

    try:
        row = read_post_row(build_sheets(), row_num)
    except Exception as e:
        print(f"ERROR: sheet read failed: {e}", file=sys.stderr)
        return 4

    words = _load_words(slug)
    words_block = _format_words_for_prompt(words) if words else "(no words.json yet — first render)"

    user_prompt = f"""Reviewer feedback on the current render:

{feedback}

Canonical VO copy (what was supposed to be said, by mode):
  mode:           {row.get('mode')!r}
  hook (E):       {row.get('hook')!r}
  question (F):   {row.get('question')!r}
  answer_prompt:  {row.get('answer_prompt')!r}
  resolution:     {row.get('resolution')!r}
  cta:            {row.get('cta')!r}

Current captions (words.json — index, startMs..endMs, "word"):
{words_block}

Produce the FeedbackPlan."""

    backend = "SDK" if use_sdk else "CLI"
    print(f"  (using Claude via {backend})")
    try:
        if use_sdk:
            plan = _call_via_sdk(SYSTEM_PROMPT, user_prompt)
        else:
            plan = _call_via_cli(SYSTEM_PROMPT, user_prompt)
    except anthropic.APIStatusError as e:
        print(f"ERROR: Claude API call failed ({e.status_code}): {e.message}", file=sys.stderr)
        return 5
    except Exception as e:
        print(f"ERROR: Claude call failed: {e}", file=sys.stderr)
        return 5

    artifacts.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(plan.model_dump_json(indent=2) + "\n")

    print(f"✓ wrote {plan_path.relative_to(REPO)}")
    print(f"  summary:        {plan.summary}")
    print(f"  classifications: {plan.classifications}")
    print(f"  patches:        {len(plan.patches)}")
    print(f"  unresolved:     {len(plan.unresolved)}")
    print(f"  confidence:     {plan.confidence}")
    for p in plan.patches:
        print(f"    - {p.op}: {p.reason}")
    for u in plan.unresolved:
        print(f"    unresolved: {u}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: feedback_router.py <row> <slug>")
    sys.exit(main(int(sys.argv[1]), sys.argv[2]))
