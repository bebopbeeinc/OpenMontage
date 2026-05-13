#!/usr/bin/env python
"""LLM-driven reaction-clip picker for the Posts sheet.

For each row, looks at the trivia context (topic, hook, question) and the
available reaction clips with that row's archetype, then asks Claude to pick
the persona that best fits — preferring culturally-matching personas for
country-specific topics, and treating topic-neutral content (animals, science,
weird facts) as free choice.

When `--apply` is set, updates BOTH the Q (reaction_prompt) and R
(reaction_filename) formulas in lockstep — they hardcode the same clip ID.

Usage:
    # Single row, dry-run
    python scripts/trivia/pick_reactions_llm.py --row 8 --dry-run

    # Single row, apply
    python scripts/trivia/pick_reactions_llm.py --row 8 --apply

    # All data rows
    python scripts/trivia/pick_reactions_llm.py --all --dry-run
    python scripts/trivia/pick_reactions_llm.py --all --apply

Auth:
    Tries the Anthropic SDK first (using ANTHROPIC_API_KEY).
    When that env var is missing, falls back to the local `claude` CLI
    (Claude Code), which uses your OAuth subscription — no API key required.

Environment:
    Requires the service-account JSON at ~/.google/claude-sheets-sa.json
    for the Sheets API.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import anthropic
from pydantic import BaseModel, Field, ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent))
from post_row import (  # noqa: E402
    CLIPS_SHEET, POST_SHEET, build_sheets as _build_sheets, cell_for,
    read_post_row,
)

DATA_START_ROW = 5
LAST_ROW = 26
MODEL = "claude-opus-4-7"


class PickResponse(BaseModel):
    clip_id: str = Field(description='The chosen Clip ID, e.g. "R077". Must exactly match one of the candidate IDs.')
    reasoning: str = Field(description="One sentence explaining why this persona fits the trivia context.")


def build_sheets():
    return _build_sheets(write=True)   # this script patches Q+R formulas


def get_personas(sheets) -> dict[str, str]:
    r = sheets.spreadsheets().values().get(
        spreadsheetId=CLIPS_SHEET, range="Personas!A2:C30",
    ).execute().get("values", [])
    out: dict[str, str] = {}
    for row in r:
        row = list(row) + [""] * (3 - len(row))
        pid, label, desc = row[:3]
        if pid:
            out[pid.strip()] = f"{label.strip()} — {desc.strip()}"
    return out


def get_clips(sheets) -> list[dict]:
    """Read full Clips catalog once; reused across all rows."""
    r = sheets.spreadsheets().values().get(
        spreadsheetId=CLIPS_SHEET, range="Clips!A2:M200",
    ).execute().get("values", [])
    out: list[dict] = []
    for c in r:
        c = list(c) + [""] * (13 - len(c))
        cid, status, arch, _, pid, persona_short, _, _, _, _, _, _, fname = c[:13]
        if cid.strip():
            out.append({
                "id": cid.strip(),
                "status": status.strip(),
                "archetype": arch.strip(),
                "persona_id": pid.strip(),
                "persona_short": persona_short.strip(),
                "filename": fname.strip(),
            })
    return out


def get_post_row(sheets, row: int) -> dict:
    """Project the canonical row dict down to the fields this picker needs."""
    d = read_post_row(sheets, row)
    return {
        "row": row,
        "post": d["post"],
        "mode": d["mode"],
        "topic": d["topic"],
        "hook": d["hook"],
        "question": d["question"],
        "answer_prompt": d["answer_prompt"],
        "ending": d["ending"],
        "resolution": d["resolution"],
        "cta": d["cta"],
        "archetype": d["reaction_archetype"],
    }


def candidates_for(archetype: str, all_clips: list[dict], personas: dict[str, str]) -> list[dict]:
    out = []
    for c in all_clips:
        if c["archetype"] == archetype.strip():
            persona_full = personas.get(c["persona_id"], c["persona_short"] or "<unknown>")
            out.append({**c, "persona": persona_full})
    return out


SYSTEM_PROMPT = """You are picking a reaction-clip persona for a trivia social-media post.

Your job: given a trivia post and a list of available reaction clips (each with the same gesture/emotion archetype but a different persona), choose the single persona whose ethnicity, age, gender, and overall vibe best fits the trivia context.

Selection rules:
1. If the topic is country-specific or culturally specific, STRONGLY prefer a persona whose appearance plausibly matches that culture:
   - Japan → East Asian persona
   - China, Korea, Vietnam → East Asian persona
   - India, Pakistan → South Asian persona
   - France, Germany, Italy, UK, Netherlands, Greece → white European persona
   - Mexico, Spain, Argentina, Brazil → Latina/Latino persona
   - Nigeria, Ghana, Kenya, Ethiopia → Black persona
   - Middle East / North Africa → Middle Eastern persona
2. For "Generated" status clips, prefer them if available (a real video already exists).
3. For topic-neutral content (animals, nature, science, weird facts, body biology), pick whichever persona feels most natural for the energy of the hook/question — diversity and freshness matter, but don't force a stereotype where none applies.
4. Don't repeat the same persona across many adjacent posts if alternatives exist.

Return the chosen Clip ID (e.g. "R077") and a one-sentence rationale."""


def _call_via_sdk(client: anthropic.Anthropic, user_prompt: str) -> PickResponse:
    """Pick via the Anthropic SDK with adaptive thinking. Requires ANTHROPIC_API_KEY."""
    response = client.messages.parse(
        model=MODEL,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        thinking={"type": "adaptive"},
        messages=[{"role": "user", "content": user_prompt}],
        output_format=PickResponse,
    )
    return response.parsed_output  # type: ignore[no-any-return]


def _call_via_cli(user_prompt: str) -> PickResponse:
    """Pick via the local `claude` CLI (OAuth subscription, no API key).

    Note: the CLI does not currently expose an adaptive-thinking flag, so
    extended reasoning is unavailable on this path. The structured-output
    schema still enforces the response shape via `--json-schema`.
    """
    cli = shutil.which("claude")
    if not cli:
        raise RuntimeError("`claude` CLI not found in PATH")
    schema_json = json.dumps(PickResponse.model_json_schema())
    cmd = [
        cli, "--print",
        "--model", MODEL,
        "--system-prompt", SYSTEM_PROMPT,
        "--output-format", "json",
        "--json-schema", schema_json,
        user_prompt,
    ]
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
            f"`claude` CLI returned non-JSON envelope: {e}; "
            f"raw (first 300 chars): {proc.stdout[:300]!r}",
        ) from e
    payload = envelope.get("structured_output")
    if payload is None:
        raise RuntimeError(
            f"`claude` CLI envelope missing `structured_output`. "
            f"keys: {list(envelope.keys())}",
        )
    try:
        return PickResponse.model_validate(payload)
    except ValidationError as e:
        raise RuntimeError(
            f"`claude` CLI structured_output does not match PickResponse schema: {e}\n"
            f"raw payload: {json.dumps(payload)[:500]!r}",
        ) from e


def pick_for_row(client: anthropic.Anthropic | None, post: dict, candidates: list[dict]) -> PickResponse | None:
    if not candidates:
        return None
    candidates_text = "\n".join(
        f"  - {c['id']}  [status: {c['status']}]  persona: {c['persona']}"
        for c in candidates
    )
    user_prompt = f"""Trivia context:
- Post title: {post['post']}
- Topic: {post['topic']}
- Mode: {post['mode']}
- Hook (first thing the reactor reads/sees): {post['hook']}
- Question/Claim: {post['question']}
- Answer/Options: {post['answer_prompt']}
- Resolution line: {post['resolution']}
- Reaction archetype (the gesture all candidates share): {post['archetype']}

Candidate reaction clips (one will be picked):
{candidates_text}

Choose one Clip ID."""

    if client is not None:
        return _call_via_sdk(client, user_prompt)
    return _call_via_cli(user_prompt)


def update_reaction_formulas(sheets, row: int, new_clip_id: str) -> None:
    """Replace the hardcoded clip ID in the reaction-prompt + reaction-filename
    VLOOKUP formulas. Cell positions are resolved at runtime via cell_for so
    column reorderings in the sheet don't require code changes."""
    prompt_cell = cell_for(sheets, row, "reaction_prompt")
    fname_cell = cell_for(sheets, row, "reaction_filename")
    r = sheets.spreadsheets().values().batchGet(
        spreadsheetId=POST_SHEET,
        ranges=[prompt_cell, fname_cell],
        valueRenderOption="FORMULA",
    ).execute()["valueRanges"]
    old_prompt = r[0]["values"][0][0] if r[0].get("values") else ""
    old_fname = r[1]["values"][0][0] if r[1].get("values") else ""
    new_prompt = re.sub(r'"R\d+"', f'"{new_clip_id}"', old_prompt, count=1)
    new_fname = re.sub(r'"R\d+"', f'"{new_clip_id}"', old_fname, count=1)
    if new_prompt == old_prompt or new_fname == old_fname:
        raise RuntimeError(
            f"Could not patch clip ID into reaction-formula cells at row {row}. "
            f"{prompt_cell} was: {old_prompt!r}  {fname_cell} was: {old_fname!r}"
        )
    sheets.spreadsheets().values().batchUpdate(
        spreadsheetId=POST_SHEET,
        body={
            "valueInputOption": "USER_ENTERED",
            "data": [
                {"range": prompt_cell, "values": [[new_prompt]]},
                {"range": fname_cell, "values": [[new_fname]]},
            ],
        },
    ).execute()


def current_clip_id(sheets, row: int) -> str | None:
    """Extract the currently-hardcoded clip ID from the row's reaction-prompt
    VLOOKUP formula."""
    r = sheets.spreadsheets().values().get(
        spreadsheetId=POST_SHEET, range=cell_for(sheets, row, "reaction_prompt"),
        valueRenderOption="FORMULA",
    ).execute().get("values", [[]])
    if not r or not r[0]:
        return None
    m = re.search(r'VLOOKUP\(\s*"([^"]+)"', r[0][0], re.IGNORECASE)
    return m.group(1) if m else None


def main() -> int:
    ap = argparse.ArgumentParser()
    g1 = ap.add_mutually_exclusive_group(required=True)
    g1.add_argument("--row", type=int, help="Single row number to process")
    g1.add_argument("--all", action="store_true", help="Process all data rows 5-26")
    g2 = ap.add_mutually_exclusive_group(required=True)
    g2.add_argument("--dry-run", action="store_true", help="Show picks, do not write")
    g2.add_argument("--apply", action="store_true", help="Write updated formulas to the sheet")
    args = ap.parse_args()

    use_sdk = bool(os.environ.get("ANTHROPIC_API_KEY"))
    use_cli = bool(shutil.which("claude")) if not use_sdk else False
    if not (use_sdk or use_cli):
        print(
            "ERROR: no Claude auth available — set ANTHROPIC_API_KEY or install the `claude` CLI",
            file=sys.stderr,
        )
        return 2
    print(f"(using Claude via {'SDK' if use_sdk else 'CLI'})")

    sheets = build_sheets()
    client = anthropic.Anthropic() if use_sdk else None

    print("Loading personas + clips catalog from Clips sheet...")
    personas = get_personas(sheets)
    all_clips = get_clips(sheets)
    print(f"  {len(personas)} personas, {len(all_clips)} clips total "
          f"({sum(1 for c in all_clips if c['status'].lower() == 'generated')} generated)")

    rows = [args.row] if args.row else list(range(DATA_START_ROW, LAST_ROW + 1))

    swaps = []  # (row, old_id, new_id, reasoning)
    for row in rows:
        post = get_post_row(sheets, row)
        if not post["post"] or not post["archetype"]:
            print(f"\nrow {row}: skipped (no post title or archetype)")
            continue
        cands = candidates_for(post["archetype"], all_clips, personas)
        if not cands:
            print(f"\nrow {row} ({post['post']!r}): no candidates with archetype {post['archetype']!r}")
            continue
        old_id = current_clip_id(sheets, row)
        print(f"\nrow {row}: {post['post']!r}")
        print(f"  archetype:  {post['archetype']!r}")
        print(f"  candidates: {len(cands)}  ({sum(1 for c in cands if c['status'].lower() == 'generated')} generated)")
        print(f"  current:    {old_id}")
        try:
            pick = pick_for_row(client, post, cands)
        except anthropic.APIStatusError as e:
            print(f"  ERROR: API call failed ({e.status_code}): {e.message}", file=sys.stderr)
            continue
        if pick is None:
            continue
        valid_ids = {c["id"] for c in cands}
        if pick.clip_id not in valid_ids:
            print(f"  WARNING: model returned {pick.clip_id!r} which isn't in candidates "
                  f"{sorted(valid_ids)} — skipping write")
            continue
        chosen = next(c for c in cands if c["id"] == pick.clip_id)
        print(f"  → pick:     {pick.clip_id}  [{chosen['status']}]")
        print(f"    persona:  {chosen['persona']}")
        print(f"    reason:   {pick.reasoning}")
        if pick.clip_id == old_id:
            print(f"    (no change — already on {old_id})")
            continue
        swaps.append((row, old_id, pick.clip_id, pick.reasoning))

    print(f"\n=== Summary ===")
    print(f"  picks differing from current: {len(swaps)}")
    if not swaps:
        print("  Nothing to write.")
        return 0
    for row, old_id, new_id, reasoning in swaps:
        print(f"    row {row}: {old_id} → {new_id}")

    if args.dry_run:
        print("\nDRY-RUN — no changes written.")
        return 0

    print(f"\nApplying {len(swaps)} swap(s)...")
    for row, old_id, new_id, _ in swaps:
        update_reaction_formulas(sheets, row, new_id)
        print(f"  ✓ row {row}: Q + R updated to {new_id}")
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
