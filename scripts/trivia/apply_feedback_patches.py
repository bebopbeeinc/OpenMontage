"""Apply patches from feedback_plan.json to the project's artifacts.

The patcher runs in two phases driven by --phase:

  --phase pre   (before assemble)
    Writes side-effect files that downstream scripts already know how to read:
      - brand_tokens_extra.json       (consumed by transcribe.py)
      - assemble_overrides.json       (consumed by server.py's assemble flags)
      - shorten_vo_enabled.flag       (gate so assembler's retry path can fire)

  --phase post  (after reconcile, before Remotion render)
    Applies word/timing edits directly to words.json. Validates each patch's
    `expected_old_word` against the live cell before writing — refuses any
    mismatch to avoid silently corrupting captions.

The plan itself is produced by feedback_router.py. This script does no LLM
work — it's pure mechanical translation of plan -> file edits.

Usage:
    python scripts/trivia/apply_feedback_patches.py <slug> --phase pre
    python scripts/trivia/apply_feedback_patches.py <slug> --phase post

Exits:
    0  patches applied (may be zero if plan was empty for this phase)
    2  no feedback_plan.json (nothing to do — clean skip)
    3  plan is malformed / unreadable
    4  one or more patches refused (expected_old_word mismatch); see stderr
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]


def _read_plan(slug: str) -> dict | None:
    path = REPO / "projects" / slug / "artifacts" / "feedback_plan.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as e:
        print(f"ERROR: feedback_plan.json malformed: {e}", file=sys.stderr)
        sys.exit(3)


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2) + "\n")


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return default


# ---------------------------- Phase: pre ----------------------------

def apply_pre(slug: str, plan: dict) -> int:
    """Pre-assemble patches: brand tokens, music volume, shorten_vo gate."""
    artifacts = REPO / "projects" / slug / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)

    brands: list[str] = []
    music_db: float | None = None
    shorten_fields: list[str] = []
    blockers: list[dict] = []
    applied = 0

    for p in plan.get("patches", []):
        op = p.get("op")
        if op == "add_brand":
            tok = (p.get("token") or "").strip()
            if tok:
                brands.append(tok)
                applied += 1
        elif op == "set_music_volume_db":
            music_db = float(p.get("value_db"))
            applied += 1
        elif op == "allow_shorten_vo":
            shorten_fields.extend(p.get("fields") or [])
            applied += 1
        elif op == "regenerate_segment":
            blockers.append({
                "kind": "regenerate_segment",
                "segment": p.get("segment"),
                "reason": p.get("reason", ""),
            })

    if brands:
        # Union with existing tokens — brand additions accumulate across runs.
        # A brand added in a previous reviewer pass should still apply today
        # even if today's feedback doesn't mention it.
        brands_path = artifacts / "brand_tokens_extra.json"
        existing_brands = _read_json(brands_path, default=[])
        if not isinstance(existing_brands, list):
            existing_brands = []
        merged = sorted(set(str(t).strip() for t in (existing_brands + brands) if str(t).strip()))
        _write_json(brands_path, merged)
        new_count = len(set(brands) - set(existing_brands))
        print(f"  brand_tokens_extra.json: +{new_count} new, {len(merged)} total")
    if music_db is not None:
        # Merge with existing overrides so future override keys aren't dropped
        # when only music_volume_db changes (and vice versa).
        overrides_path = artifacts / "assemble_overrides.json"
        existing_overrides = _read_json(overrides_path, default={})
        if not isinstance(existing_overrides, dict):
            existing_overrides = {}
        existing_overrides["music_volume_db"] = str(music_db)
        _write_json(overrides_path, existing_overrides)
        print(f"  assemble_overrides.json: music_volume_db={music_db}")
    if shorten_fields:
        (artifacts / "shorten_vo_enabled.flag").write_text(
            ",".join(sorted(set(shorten_fields))) + "\n"
        )
        print(f"  wrote shorten_vo_enabled.flag: fields={sorted(set(shorten_fields))}")
    if blockers:
        _write_json(artifacts / "feedback_blockers.json", blockers)
        print(f"  wrote feedback_blockers.json with {len(blockers)} blocker(s) — human action needed:")
        for b in blockers:
            print(f"    - regenerate {b['segment']}: {b['reason']}")

    print(f"phase=pre: applied {applied} patch(es)")
    return 0


# ---------------------------- Phase: post ----------------------------

_PUNCT_TAIL = ",.!?;:'\""

# Max distance between near_time_ms and a candidate's startMs to consider a
# match. 2 seconds is generous — a trivia short is ~14s so 2s = ~15% of total.
_MAX_TIME_DRIFT_MS = 2000


def _normalize(w: str) -> str:
    """Strip trailing punctuation + lower-case for fuzzy word matching."""
    return (w or "").rstrip(_PUNCT_TAIL).lower()


def _find_index(words: list[dict], target_word: str, near_time_ms: int) -> int | None:
    """Find the index of the words.json entry whose word matches target_word
    (case-insensitive, ignoring trailing punctuation) and whose startMs is
    closest to near_time_ms. Returns None if no match within _MAX_TIME_DRIFT_MS."""
    tgt = _normalize(target_word)
    if not tgt:
        return None
    candidates: list[tuple[int, int]] = []  # (abs_dt, idx)
    for i, w in enumerate(words):
        if _normalize(w.get("word", "")) == tgt:
            dt = abs(int(w.get("startMs", 0)) - int(near_time_ms))
            candidates.append((dt, i))
    if not candidates:
        return None
    candidates.sort()
    best_dt, best_idx = candidates[0]
    if best_dt > _MAX_TIME_DRIFT_MS:
        return None
    return best_idx


def apply_post(slug: str, plan: dict) -> int:
    """Post-reconcile patches: words.json word + timing edits, matched by
    (target_word, near_time_ms) so the patch survives transcribe re-runs that
    shift indices."""
    artifacts = REPO / "projects" / slug / "artifacts"
    words_path = artifacts / "words.json"
    if not words_path.exists():
        print(f"  no words.json at {words_path} — nothing to patch")
        return 0

    words: list[dict] = json.loads(words_path.read_text())
    if not isinstance(words, list):
        print(f"ERROR: words.json is not a list", file=sys.stderr)
        return 3

    word_patches = [p for p in plan.get("patches", []) if p.get("op") in {"set_word", "set_timing"}]
    if not word_patches:
        print(f"phase=post: no word/timing patches in plan")
        return 0

    refused: list[str] = []
    applied = 0

    # Backup the pre-patch words.json once per run for trace.
    backup = artifacts / "words.pre_feedback_patch.json"
    if not backup.exists():
        shutil.copy(words_path, backup)

    # Two-pass: resolve all target indices against the ORIGINAL words.json
    # before any mutations. Otherwise a set_word patch on the same target
    # as a later set_timing patch would shadow the second lookup ("true" ->
    # "MAYBE" means a subsequent target_word="true" search returns nothing).
    resolved: list[tuple[dict, int]] = []
    for p in word_patches:
        target = (p.get("target_word") or "").strip()
        near = int(p.get("near_time_ms", 0))
        idx = _find_index(words, target, near)
        if idx is None:
            refused.append(
                f"{p.get('op')}: no words.json entry matching target_word={target!r} "
                f"within {_MAX_TIME_DRIFT_MS}ms of {near}ms"
            )
            continue
        resolved.append((p, idx))

    for p, idx in resolved:
        op = p["op"]
        near = int(p.get("near_time_ms", 0))
        if op == "set_word":
            new_word = (p.get("new_word") or "").strip()
            if not new_word:
                refused.append(f"set_word @ idx {idx}: empty new_word")
                continue
            old = words[idx].get("word", "")
            words[idx]["word"] = new_word
            applied += 1
            print(f"  set_word @ {idx} (near {near}ms): {old!r} -> {new_word!r}  ({p.get('reason', '')})")
        elif op == "set_timing":
            if p.get("new_start_ms") is not None:
                words[idx]["startMs"] = int(p["new_start_ms"])
            if p.get("new_end_ms") is not None:
                words[idx]["endMs"] = int(p["new_end_ms"])
            applied += 1
            print(f"  set_timing @ {idx} (near {near}ms): start={words[idx].get('startMs')}, end={words[idx].get('endMs')}  ({p.get('reason', '')})")

    _write_json(words_path, words)

    # Stage to Remotion's public dir so the next render picks up edits.
    public = REPO / "remotion-composer" / "public" / "words.json"
    if public.parent.exists():
        shutil.copy(words_path, public)
        print(f"  staged to {public.relative_to(REPO)}")

    print(f"phase=post: applied {applied} patch(es), refused {len(refused)}")
    for r in refused:
        print(f"  REFUSED: {r}", file=sys.stderr)
    return 4 if refused else 0


# ---------------------------- Main ----------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("slug")
    ap.add_argument("--phase", choices=["pre", "post"], required=True)
    args = ap.parse_args()

    plan = _read_plan(args.slug)
    if plan is None:
        print(f"no feedback_plan.json for {args.slug} — skipping")
        return 2

    if args.phase == "pre":
        return apply_pre(args.slug, plan)
    return apply_post(args.slug, plan)


if __name__ == "__main__":
    sys.exit(main())
