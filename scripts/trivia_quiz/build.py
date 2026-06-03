"""Build a trivia-quiz post end-to-end from a fixture row.

v0.1 minimal path:
    1. Read projects/trivia-quiz/<slug>/inputs/quiz_row.yaml
    2. Validate (no-leak guardrail, difficulty ladder, game-themed coherence)
    3. Write brief.json + script.json + quiz_meta.json under artifacts/
    4. Generate backdrops (solid color by default; FLUX with --with-flux)
    5. Build bg.mp4 via ffmpeg (32s, silent v0.1 — VO comes in v0.2)
    6. Stage bg.mp4 + quiz_meta.json into remotion-composer/public/
    7. Print the Remotion render command (caller runs it; we don't background it)

Usage:
    python -m scripts.trivia_quiz.build --slug scotland-turkey-bahamas
    python -m scripts.trivia_quiz.build --slug scotland-turkey-bahamas --with-flux
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


REPO = Path(__file__).resolve().parents[2]
STYLES_PATH = REPO / "styles" / "trivia-quiz.yaml"

# Auto-load .env so ELEVENLABS_API_KEY etc are available without requiring
# the caller to `source .env`. Silent if python-dotenv isn't installed.
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(REPO / ".env")
except ImportError:
    # Fallback: minimal manual parse of .env so we still pick up KEY=value lines
    _env_path = REPO / ".env"
    if _env_path.exists():
        for line in _env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v

# Show-identity assets live here, shared across all posts. The hook + score
# backdrops are generated ONCE (per the locked show_backdrop_prompt) and
# reused on every subsequent post. Regenerate with --regen-show-assets.
SHOW_LIBRARY = REPO / "scripts" / "trivia_quiz" / "library"
SHOW_BG_LIBRARY_PATH = SHOW_LIBRARY / "show_bg.jpg"

# Locked segment timings (must match styles/trivia-quiz.yaml::segments).
SEG_DURATIONS = {
    # Hook intro removed — viewer goes straight into Q1. Saves ~3s of runtime
    # and keeps the show feed-quick (sub-30s).
    "q1": 8.0,
    "q2": 9.0,
    "q3": 8.0,
    "score_card": 4.0,
}
XFADE_S = 0.3

# Reveal lands at this offset within each question segment.
REVEAL_OFFSET_S = {"q1": 4.5, "q2": 5.0, "q3": 4.5}
COUNTDOWN_OFFSET_S = 0.5      # countdown bar appears 0.5s into segment
COUNTDOWN_DURATION_S = 3.0


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

SLUG_RE = re.compile(r"^[a-z0-9-]+$")
ANSWER_RE = re.compile(r"^([A-Z]\)\s+.+|TRUE|FALSE)$")


def _fail(msg: str) -> "None":
    print(f"✗ {msg}", file=sys.stderr)
    sys.exit(2)


def _load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        _fail(f"missing file: {path}")
    return yaml.safe_load(path.read_text())


# Backdrop spoiler guardrail — the backdrop plays through the pre-reveal
# countdown, so depicting the answer object confirms the answer before the
# viewer guesses. Convention (rounds 1–6): evoke the SETTING and negate the
# answer object ("...no compass, no people..."). This check is negation-aware
# so the correct "no <answer>" phrasing passes while a bare mention fails.
_ARTICLES = {"a", "an", "the", "your", "my", "his", "her", "their", "its", "our"}
_NEGATIONS = {"no", "not", "without", "zero", "never", "sans", "minus", "absent"}


def _answer_core_noun(answer: str) -> str:
    """'B) The North Star' -> 'north star'. Strips the choice label and any
    leading article/possessive so we match the bare object name."""
    core = answer.split(")", 1)[-1].strip().lower()
    toks = core.split()
    while toks and toks[0] in _ARTICLES:
        toks = toks[1:]
    return " ".join(toks)


def _backdrop_spoils_answer(answer: str, backdrop: str) -> bool:
    """True if the answer's core noun appears in the backdrop prompt WITHOUT a
    preceding negation. Matches the full noun phrase (allowing a trailing
    plural 's' on the final word) so 'tennis court' never trips 'tennis ball'."""
    core_toks = _answer_core_noun(answer).split()
    if not core_toks:
        return False
    btoks = re.findall(r"[a-z']+", backdrop.lower())
    n = len(core_toks)
    for i in range(len(btoks) - n + 1):
        window = btoks[i:i + n]
        last_ok = window[-1] == core_toks[-1] or window[-1] == core_toks[-1] + "s"
        if window[:-1] == core_toks[:-1] and last_ok:
            preceding = btoks[max(0, i - 2):i]  # negation cue within 2 tokens
            if not any(t in _NEGATIONS for t in preceding):
                return True
    return False


def validate_fixture(row: Dict[str, Any]) -> None:
    """Apply idea-director guardrails to the fixture before doing any work."""
    slug = row.get("slug")
    if not slug or not SLUG_RE.match(str(slug)):
        _fail(f"invalid slug {slug!r} (must match {SLUG_RE.pattern})")

    # Difficulty ladder
    expected_diff = {"q1": "Easy", "q2": "Medium", "q3": "Hard"}
    for qid, want in expected_diff.items():
        got = row.get(qid, {}).get("difficulty")
        if got != want:
            _fail(f"{qid}.difficulty must be {want!r}, got {got!r}")

    # Each question has the required fields
    for qid in ("q1", "q2", "q3"):
        q = row.get(qid, {})
        for field in ("question", "answer", "surprise_fact"):
            if not q.get(field):
                _fail(f"{qid}.{field} is required")
        if q.get("choices") is not None and not isinstance(q["choices"], list):
            _fail(f"{qid}.choices must be a list (or omitted)")
        if not ANSWER_RE.match(str(q["answer"])):
            _fail(f"{qid}.answer must be a labeled choice 'X) ...' or 'TRUE'/'FALSE', got {q['answer']!r}")
        if q.get("choices"):
            labels = [c.strip() for c in q["choices"]]
            if q["answer"].strip() not in labels:
                _fail(f"{qid}.answer {q['answer']!r} not in choices {labels}")

    # Game-themed Q3 coherence — the brand tease must live SOMEWHERE that
    # connects Q3 to Travel Crush. Cleanest place is the game_hook_line on
    # the score card; the Q3 question text itself often deliberately avoids
    # naming the game so the trivia reads as universal. So: warn only when
    # NEITHER game_hook_line nor any Q3 field contains a brand token.
    if row["q3"].get("game_themed") is True:
        if not row.get("game_hook_line"):
            _fail("q3.game_themed=true requires game_hook_line at the top level")
        brand_tokens = ("Captain", "Travel Crush", "Fennec")
        check_text = " ".join([
            row["q3"]["question"],
            row["q3"]["surprise_fact"],
            row.get("game_hook_line", ""),
        ]).lower()
        if not any(tok.lower() in check_text for tok in brand_tokens):
            print(
                "⚠ q3.game_themed=true but no brand token appears in q3.question, "
                "q3.surprise_fact, or game_hook_line. The 'game tease' will feel "
                "decorative.",
                file=sys.stderr,
            )

    # No-leak guardrail
    answers = [row[qid]["answer"].split(")", 1)[-1].strip().lower() for qid in ("q1", "q2", "q3")]
    leak_fields = {
        "bottom_cta": row.get("bottom_cta", ""),
        "game_hook_line": row.get("game_hook_line", ""),
        "captions.tiktok": row.get("captions", {}).get("tiktok", ""),
        "captions.instagram": row.get("captions", {}).get("instagram", ""),
        "captions.pinned_comment": row.get("captions", {}).get("pinned_comment", ""),
    }
    for field_name, text in leak_fields.items():
        haystack = text.lower()
        for i, ans in enumerate(answers, start=1):
            if ans and ans in haystack:
                _fail(
                    f"no-leak guardrail: '{ans}' (q{i} answer) appears in {field_name}. "
                    "Reword to avoid spoiling the reveal in TikTok/IG captions or "
                    "the score card."
                )

    # Backdrop spoiler guardrail — the backdrop is on screen during the
    # pre-reveal countdown, so it must never depict the answer object.
    for qid in ("q1", "q2", "q3"):
        q = row.get(qid, {})
        backdrop = q.get("backdrop_hint") or ""
        if backdrop and _backdrop_spoils_answer(q["answer"], backdrop):
            core = _answer_core_noun(q["answer"])
            _fail(
                f"backdrop spoiler guardrail: {qid} backdrop_hint names the answer "
                f"('{core}') without a negation. The backdrop plays during the "
                f"countdown, so it would spoil the reveal. Either remove it from the "
                f"scene or negate it explicitly (e.g. 'no {core}')."
            )


# ---------------------------------------------------------------------------
# Artifact construction
# ---------------------------------------------------------------------------

def _json_default(o: Any) -> Any:
    """JSON encoder hook for YAML-native types we don't want to coerce upstream
    (e.g. PyYAML returns `date`/`datetime` for ISO-shaped strings)."""
    from datetime import date, datetime
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    raise TypeError(f"Object of type {o.__class__.__name__} is not JSON serializable")


def _row_hash(row: Dict[str, Any]) -> str:
    canonical = json.dumps(row, sort_keys=True, ensure_ascii=False, default=_json_default)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def build_brief(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "metadata": {
            "pipeline": "trivia-quiz",
            "pipeline_version": "0.1",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "fixture_hash": _row_hash(row),
            "quiz": {
                "slug": row["slug"],
                "topic_mix": row.get("topic_mix", ""),
                "post_date": row.get("post_date"),
                "q1": row["q1"],
                "q2": row["q2"],
                "q3": row["q3"],
                "game_hook_line": row.get("game_hook_line", ""),
                # Row override takes precedence; if blank, fall through to the
                # locked style-config default ("Follow for daily trivia").
                "bottom_cta": row.get("bottom_cta") or "",
                # Hook + closer pairing (resolved later in build_quiz_meta).
                # Passed through here so quiz_meta build can apply the
                # row → style default precedence.
                "hook": row.get("hook") or "",
                "hook_variant": row.get("hook_variant") or "",
                "closer": row.get("closer") or {},
                "reward": row.get("reward", ""),
                "captions": row.get("captions", {}),
                "music_track": row.get("music_track", ""),
            },
        }
    }


def build_script(brief: Dict[str, Any], style: Dict[str, Any]) -> Dict[str, Any]:
    """Generate the 5 VO lines + segment list against locked windows."""
    quiz = brief["metadata"]["quiz"]
    show = style["show_identity"]

    def spoken_answer(ans: str) -> str:
        # "B) Sudan" -> "Sudan"
        if ")" in ans:
            return ans.split(")", 1)[-1].strip()
        return ans

    # Per the @factualquiz reference: VO only on the questions and answers.
    # The host READS each question aloud as it appears, then announces JUST
    # the answer word at reveal (no surprise_fact explanation — that text
    # stays on screen but isn't spoken). Hook + score have NO VO — the
    # locked typography + SFX carry those beats.
    def spoken_q(text: str) -> str:
        return text.strip().rstrip("?") + "?"  # ensure rising-question intonation

    q1_question = spoken_q(quiz["q1"]["question"])
    q2_question = spoken_q(quiz["q2"]["question"])
    q3_question = spoken_q(quiz["q3"]["question"])

    # Phrase each answer as a short sentence so Piper has enough surrounding
    # context to land natural intonation. Rotation across Q1/Q2/Q3 adds
    # variety AND avoids grammatical mismatches with plural answers
    # ("And it's Pigs!" sounded off — exclamation + plural noun didn't land).
    #   Q1 → "The answer is X."         (sets the cadence)
    #   Q2 → "It's X!"                  (punchier middle)
    #   Q3 → "The correct answer is X." (declarative kicker — works for
    #                                    singular AND plural answers)
    a1 = spoken_answer(quiz["q1"]["answer"])
    a2 = spoken_answer(quiz["q2"]["answer"])
    a3 = spoken_answer(quiz["q3"]["answer"])
    q1_answer = f"The answer is {a1}."
    q2_answer = f"It's {a2}!"
    q3_answer = f"The correct answer is {a3}."

    # Score-card VO — reads the bottom CTA aloud. "→" doesn't speak well, so
    # we transform it ("Follow → never miss a round" → "Follow so you never
    # miss a round" — reads conversationally). The CTA itself rotates across
    # videos via _resolve_bottom_cta (slug-keyed pick from the style pool) so
    # the spoken line matches whatever lands on screen.
    bottom_cta = _resolve_bottom_cta(quiz, show)
    score_text = bottom_cta.replace(" → ", " so you ").replace("→", "so you").strip()

    # Segment timings WITHOUT the intro — Q1 starts at 0.0s. Saves ~3s.
    #   q1 starts at 0.00s, reveal at 4.50s, ends ~8.00s
    #   q2 starts at 7.70s (xfade -0.3), reveal at 12.70s, ends ~16.40s
    #   q3 starts at 16.10s, reveal at 20.60s, ends ~23.80s
    #   score_card starts at 23.50s, runs 4s → ~27.20s total
    return {
        "metadata": {
            "vo": [
                {"id": "q1_question", "text": q1_question, "window_s": [0.10, 2.80]},
                {"id": "q1_answer",   "text": q1_answer,   "window_s": [4.50, 6.30]},
                {"id": "q2_question", "text": q2_question, "window_s": [7.80, 10.50]},
                {"id": "q2_answer",   "text": q2_answer,   "window_s": [12.70, 14.50]},
                {"id": "q3_question", "text": q3_question, "window_s": [16.20, 18.90]},
                {"id": "q3_answer",   "text": q3_answer,   "window_s": [20.60, 22.40]},
                {"id": "score_card",  "text": score_text,  "window_s": [24.00, 26.80]},
            ],
            "segments": [
                {"id": "q1",         "duration_s": SEG_DURATIONS["q1"],         "backdrop": "q1_bg"},
                {"id": "q2",         "duration_s": SEG_DURATIONS["q2"],         "backdrop": "q2_bg"},
                {"id": "q3",         "duration_s": SEG_DURATIONS["q3"],         "backdrop": "q3_bg"},
                {"id": "score_card", "duration_s": SEG_DURATIONS["score_card"], "render": "remotion_only"},
            ],
            "brand_tokens_applied": style.get("brand_tokens", []),
        }
    }


def _resolve_bottom_cta(row: Dict[str, Any], show: Dict[str, Any]) -> str:
    """Pick the score-card bottom CTA so it varies across videos.

    Precedence:
      1. row.bottom_cta (explicit per-row override always wins)
      2. Slug-keyed deterministic pick from (bottom_cta_default + bottom_cta_variants)
         — same slug always lands the same line (reproducible), different slugs
         spread across the pool (no two consecutive posts share a CTA unless
         the pool is small). Keeps the feed from repeating itself.
      3. Hardcoded "Follow" fallback if styles are completely empty.
    """
    override = (row.get("bottom_cta") or "").strip()
    if override:
        return override

    default = (show.get("bottom_cta_default") or "").strip()
    variants = [v.strip() for v in (show.get("bottom_cta_variants") or []) if v and v.strip()]
    pool: List[str] = []
    if default:
        pool.append(default)
    pool.extend(variants)
    pool = list(dict.fromkeys(pool))  # de-dupe, preserve order
    if not pool:
        return "Follow"

    slug = (row.get("slug") or "").strip() or "default"
    idx = int(hashlib.sha256(slug.encode("utf-8")).hexdigest(), 16) % len(pool)
    return pool[idx]


def _resolve_hook_and_closer(row: Dict[str, Any], show: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve the hook + paired closer from the fixture and style defaults.

    Precedence (per field):
      1. Explicit row.hook / row.closer
      2. Named variant via row.hook_variant (key into show.hook_closer_variants)
      3. show.default_hook_variant
    """
    variants = show.get("hook_closer_variants", {}) or {}
    default_key = show.get("default_hook_variant") or "ten_percent_callback"
    default_variant = variants.get(default_key) or {}

    # Pick the active variant
    row_variant_key = (row.get("hook_variant") or "").strip()
    if row_variant_key:
        active = variants.get(row_variant_key)
        if active is None:
            _fail(f"hook_variant {row_variant_key!r} not found in styles/trivia-quiz.yaml::hook_closer_variants")
    else:
        active = default_variant

    hook = row.get("hook") or active.get("hook") or ""
    closer = row.get("closer") or active.get("closer") or {}
    return {
        "hook": hook,
        "closer": {
            "intro":    closer.get("intro", "Are you in the"),
            "emphasis": closer.get("emphasis", "10%?"),
            "cta":      closer.get("cta", "Comment below if you are 👇"),
        },
    }


def build_quiz_meta(brief: Dict[str, Any], style: Dict[str, Any]) -> Dict[str, Any]:
    """The structured timeline Remotion's TriviaQuiz composition reads."""
    quiz = brief["metadata"]["quiz"]
    show = style["show_identity"]

    # Absolute starts in the bg.mp4 timeline (xfade overlap = XFADE_S between segments).
    # No hook segment — Q1 starts at 0.
    t = 0.0
    starts = {}
    durations = {}
    for seg in ("q1", "q2", "q3", "score_card"):
        starts[seg] = t
        durations[seg] = SEG_DURATIONS[seg]
        t += SEG_DURATIONS[seg] - XFADE_S
    # Compensate the last overlap subtracted (no segment follows score_card).
    score_start = starts["score_card"]

    def find_answer_index(q: Dict[str, Any]) -> int:
        if not q.get("choices"):
            return 0 if str(q["answer"]).upper().startswith("TRUE") else 1
        labels = [c.strip() for c in q["choices"]]
        return labels.index(q["answer"].strip())

    def spoken_answer(ans: str) -> str:
        return ans.split(")", 1)[-1].strip() if ")" in ans else ans

    questions = []
    for qid in ("q1", "q2", "q3"):
        q = quiz[qid]
        questions.append({
            "id": qid,
            "start_s": starts[qid],
            "duration_s": durations[qid],
            "question": q["question"],
            "choices": q.get("choices") or ["TRUE", "FALSE"],
            "answer_index": find_answer_index(q),
            "answer_label": spoken_answer(q["answer"]),
            "countdown_start_s": COUNTDOWN_OFFSET_S,
            "countdown_duration_s": COUNTDOWN_DURATION_S,
            "reveal_at_s": REVEAL_OFFSET_S[qid],
            "surprise_fact": q["surprise_fact"],
            "difficulty": q["difficulty"],
        })

    # Bottom-CTA resolution: per-row override wins, otherwise slug-keyed pick
    # across (default + variants) so the feed doesn't repeat the same line.
    bottom_cta = _resolve_bottom_cta(quiz, show)

    # Hook + closer resolution — paired unit, see _resolve_hook_and_closer.
    # We pull from the original row fields (brief's quiz dict carries them).
    pair = _resolve_hook_and_closer(quiz, show)
    return {
        "show": {
            "title": show["title"],
            "hook": pair["hook"],
            "closer": pair["closer"],
            "lockup_text": show["game_lockup"]["text"],
            "lockup_brand": show["game_lockup"]["brand_label"],
            "placeholder_url": show["game_lockup"]["placeholder_url"],
        },
        "questions": questions,
        "score_card": {
            "start_s": score_start,
            "bottom_cta": bottom_cta,
            "reward": quiz.get("reward", "") or "",
            "game_hook_line": quiz.get("game_hook_line", ""),
        },
    }


# ---------------------------------------------------------------------------
# Backdrop generation
# ---------------------------------------------------------------------------

def generate_backdrops_solid(project_dir: Path) -> Dict[str, Path]:
    """v0.1 default: write 5 solid-color JPGs (one per segment).

    1080x1920 dark navy fills with slight per-segment color variation.
    Mostly hidden by Remotion's UI cards anyway. Use --with-openart or
    --with-flux for real backdrops.

    IMPORTANT: writes to `solid_<seg>_bg.jpg` paths (not `<seg>_bg.jpg`) so
    a stray run without --with-openart can't silently clobber high-quality
    OpenArt/FLUX images. This was a real bug — solid placeholders overwrote
    real backdrops and --reuse-question-assets locked them in.
    """
    images = {}
    palette = {
        "hook":  "0x0a1228",
        "q1":    "0x101a2c",
        "q2":    "0x1a1530",
        "q3":    "0x0a2030",
        "score": "0x0a1228",
    }
    for seg, bg in palette.items():
        out = project_dir / "assets" / "images" / f"solid_{seg}_bg.jpg"
        out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"color=c={bg}:s=1080x1920:d=1",
            "-frames:v", "1", str(out),
        ], check=True, capture_output=True)
        images[seg] = out
    return images


def generate_backdrops_openart(
    project_dir: Path,
    brief: Dict[str, Any],
    style: Dict[str, Any],
    *,
    headless: bool = False,
    reuse_question_assets: bool = False,
) -> Dict[str, Path]:
    """Drive OpenArt to generate 5 backdrop STILLS (hook + Q1/Q2/Q3 + score).

    Stills with Ken Burns animation in post (not Seedance video) — gives us
    deterministic motion, lighter files, and consistent show identity. Hook
    and score prompts are locked in styles/trivia-quiz.yaml so they read as
    bookend show-identity moments across every post; per-question prompts
    come from the fixture's backdrop_hint fields.

    Default model: Nano Banana Pro (highest-quality Google Gemini variant on
    OpenArt). Aspect 9:16 vertical so we don't have to scale up.

    Login is persisted at .playwright/openart-state.json from earlier pipeline
    runs; first call may need a visible browser if the session has expired.
    """
    from tools.tool_registry import registry  # local import — playwright cost
    registry.discover()
    openart = registry._tools["openart_image"]

    images_dir = project_dir / "assets" / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    SHOW_LIBRARY.mkdir(parents=True, exist_ok=True)

    quiz = brief["metadata"]["quiz"]
    show = style["show_identity"]

    # SHOW-IDENTITY backdrop: one image, reused on hook + score across every
    # post in the series. Cached at SHOW_BG_LIBRARY_PATH. Regenerate by
    # passing --regen-show-assets at the CLI (handled in main()).
    if not SHOW_BG_LIBRARY_PATH.exists():
        show_prompt = show.get("show_backdrop_prompt", "").strip()
        print(f"  openart → show_bg (locked, library cache): {show_prompt[:90]}…")
        result = openart.execute({
            "prompt": show_prompt,
            "model": "Nano Banana Pro",
            "aspect": "9:16",
            "resolution": "2K",
            "output_path": str(SHOW_BG_LIBRARY_PATH),
            "headless": headless,
            "keep_source_ext": False,
        })
        if not result.success:
            print(f"  ✗ openart_image failed on show backdrop: {result.error}", file=sys.stderr)
            sys.exit(3)
        cached = Path(result.data.get("output_path", SHOW_BG_LIBRARY_PATH))
        if cached != SHOW_BG_LIBRARY_PATH and cached.exists():
            shutil.copy2(cached, SHOW_BG_LIBRARY_PATH)
    else:
        print(f"  openart → show_bg cached at {SHOW_BG_LIBRARY_PATH.relative_to(REPO)} (reused; --regen-show-assets to refresh)")

    # Copy the cached show backdrop into the per-project assets for both
    # hook and score. Same source image, different Ken Burns curves in
    # build_bg_mp4 will make them feel like bookends.
    images: Dict[str, Path] = {}
    for seg in ("hook", "score"):
        dst = images_dir / f"{seg}_bg.jpg"
        shutil.copy2(SHOW_BG_LIBRARY_PATH, dst)
        images[seg] = dst

    # Per-question topical backdrops — generated fresh per post unless
    # reuse_question_assets is set AND the file is already on disk (lets the
    # user iterate the SHOW backdrop without paying for Q1/Q2/Q3 each time).
    for qid in ("q1", "q2", "q3"):
        out = images_dir / f"{qid}_bg.jpg"
        if reuse_question_assets and out.exists():
            print(f"  openart → {qid}: reusing existing {out.relative_to(REPO)}")
            images[qid] = out
            continue
        hint = quiz[qid].get("backdrop_hint") or quiz[qid]["question"]
        prompt = (
            f"{hint}. Photorealistic, cinematic, professional cinematography, "
            "no people, no on-screen text, magazine-quality lighting, "
            "9:16 vertical."
        )
        print(f"  openart → {qid}: {prompt[:90]}…")
        result = openart.execute({
            "prompt": prompt,
            "model": "Nano Banana Pro",
            "aspect": "9:16",
            "resolution": "2K",
            "output_path": str(out),
            "headless": headless,
            "keep_source_ext": False,
        })
        if not result.success:
            print(f"  ✗ openart_image failed on {qid}: {result.error}", file=sys.stderr)
            sys.exit(3)
        saved_path = Path(result.data.get("output_path", out))
        if not saved_path.exists():
            saved_path = out
        images[qid] = saved_path
    return images


def generate_backdrops_flux(project_dir: Path, brief: Dict[str, Any], style: Dict[str, Any]) -> Dict[str, Path]:
    """Call flux_image to render 5 themed backdrops (hook + Q1/Q2/Q3 + score).
    Same image used for hook + score (single show_backdrop_prompt — show
    identity bookends) — distinct Ken Burns curves in assembly. Q1/Q2/Q3
    prompts come from the fixture's backdrop_hint."""
    from tools.tool_registry import registry  # local import; not all envs have FLUX configured
    registry.discover()
    flux = registry._tools["flux_image"]

    images = {}
    quiz = brief["metadata"]["quiz"]
    show = style["show_identity"]
    show_prompt = show.get("show_backdrop_prompt", "").strip()
    prompts = {
        "hook":  show_prompt,                                          # same source as score
        "q1":    quiz["q1"].get("backdrop_hint") or quiz["q1"]["question"],
        "q2":    quiz["q2"].get("backdrop_hint") or quiz["q2"]["question"],
        "q3":    quiz["q3"].get("backdrop_hint") or quiz["q3"]["question"],
        "score": show_prompt,                                          # same source as hook
    }

    for seg, hint in prompts.items():
        if seg in ("q1", "q2", "q3"):
            prompt = (
                f"{hint}. Photorealistic, cinematic, professional photography, "
                "no people, no on-screen text, magazine quality. 9:16 vertical."
            )
        else:
            prompt = hint
        out = project_dir / "assets" / "images" / f"{seg}_bg.jpg"
        out.parent.mkdir(parents=True, exist_ok=True)
        print(f"  flux_image → {seg}: {prompt[:90]}…")
        result = flux.execute({
            "prompt": prompt,
            "width": 1024,
            "height": 1820,
            "model": "flux-pro/v1.1",
            "output_path": str(out),
        })
        if not result.success:
            print(f"  ⚠ FLUX failed on {seg}: {result.error} — falling back to solid bg")
            palette = {"hook": "0x0a1228", "q1": "0x101a2c", "q2": "0x1a1530", "q3": "0x0a2030", "score": "0x0a1228"}
            subprocess.run([
                "ffmpeg", "-y", "-f", "lavfi",
                "-i", f"color=c={palette[seg]}:s=1080x1920:d=1",
                "-frames:v", "1", str(out),
            ], check=True, capture_output=True)
        images[seg] = out
    return images


# ---------------------------------------------------------------------------
# bg.mp4 assembly
# ---------------------------------------------------------------------------

def build_bg_mp4(
    project_dir: Path,
    backdrops: Dict[str, Path],
    style: Dict[str, Any],
    *,
    with_vo: bool = False,
    with_music: bool = False,
    with_sfx: bool = False,
) -> Path:
    """Assemble the 5-segment bg.mp4 with Ken Burns on each backdrop.
    Audio layers (VO/music/SFX) get muxed in separately in main() — keep
    this function focused on the silent video assembly."""

    video_dir = project_dir / "assets" / "video"
    video_dir.mkdir(parents=True, exist_ok=True)

    # 4 segments now (hook removed). Score still uses the show backdrop
    # (library cache) with a distinct zoom-out drift curve to feel like an
    # ending bookend; Q1/Q2/Q3 are per-row topical with rotating pan directions.
    # zoom_from, zoom_to, x-expression-template, y-expression-template
    pan_patterns = {
        "q1":    (1.00, 1.08, "iw*0.05*on/{n}",  "ih*0.00"),         # left→right
        "q2":    (1.00, 1.10, "iw*0.00",         "ih*0.04*on/{n}"),  # top→bottom
        "q3":    (1.00, 1.08, "iw*-0.05*on/{n}", "ih*0.00"),         # right→left
        "score": (1.06, 1.00, "iw*-0.025*on/{n}","ih*0.00"),         # drift OUT, slight left pan
    }
    seg_order = ("q1", "q2", "q3", "score")
    seg_to_duration_key = {"q1": "q1", "q2": "q2", "q3": "q3", "score": "score_card"}

    parts: List[Path] = []
    for seg in seg_order:
        dur = SEG_DURATIONS[seg_to_duration_key[seg]]
        src = backdrops[seg]
        clip = video_dir / f"seg_{seg}.mp4"
        is_video = src.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv"}

        if is_video:
            # Legacy: OpenArt video clips (deprecated path — kept for back-compat).
            subprocess.run([
                "ffmpeg", "-y",
                "-stream_loop", "-1", "-i", str(src),
                "-t", str(dur),
                "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1",
                "-an",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
                "-r", "30",
                str(clip),
            ], check=True, capture_output=True)
        else:
            # Still + Ken Burns. Five segments, five distinct pan/zoom curves.
            nframes = int(dur * 30)
            zf, zt, x_expr_t, y_expr_t = pan_patterns[seg]
            x_expr = x_expr_t.format(n=nframes)
            y_expr = y_expr_t.format(n=nframes)
            zoom_expr = f"{zf}+({zt}-{zf})*on/{nframes}"
            # IMPORTANT: pre-scale the source 4x before zoompan to fix jitter.
            # zoompan quantizes x/y to integer pixels each frame; at the
            # ~1152px source resolution the per-frame pan delta is sub-pixel,
            # producing a visible 0→0→1→0→1 shake. Working at 4x resolution
            # (4320x7680) means the same integer rounding is sub-pixel at the
            # 1080 output and the motion reads smooth.
            vf = (
                "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
                "scale=4320:7680:flags=lanczos,"
                f"zoompan=z='{zoom_expr}':x='{x_expr}':y='{y_expr}':d=1:s=1080x1920:fps=30"
            )
            subprocess.run([
                "ffmpeg", "-y",
                "-loop", "1", "-framerate", "30", "-t", str(dur),
                "-i", str(src),
                "-vf", vf,
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
                "-r", "30",
                str(clip),
            ], check=True, capture_output=True)
        parts.append(clip)
    bg = video_dir / "bg.mp4"

    # Build the xfade filter chain. With 4 inputs (q1, q2, q3, score),
    # we chain 3 xfades. Hook intro was removed — viewer goes straight to Q1.
    durations_list = [
        SEG_DURATIONS["q1"],
        SEG_DURATIONS["q2"],
        SEG_DURATIONS["q3"],
        SEG_DURATIONS["score_card"],
    ]
    inputs_args: List[str] = []
    for p in parts:
        inputs_args += ["-i", str(p)]

    # offsets[i] = where the i-th xfade begins on the merged timeline
    offsets = []
    cumulative = 0.0
    for i in range(len(parts) - 1):
        cumulative += durations_list[i] - XFADE_S
        offsets.append(cumulative)
    # Reset and recompute the actual offsets (xfade offset = previous combined - xfade)
    # combined_after_i = sum(d[0..i+1]) - (i+1)*XFADE_S
    # xfade at step i begins at: combined_after_i - duration[i+1]
    offsets = []
    combined = durations_list[0]
    for i in range(1, len(parts)):
        # The xfade between segment (i-1) and i begins at:
        # (combined so far without this segment) - XFADE_S
        offsets.append(combined - XFADE_S)
        combined = combined + durations_list[i] - XFADE_S

    # Build a label chain: [0:v][1:v]xfade=transition=fade:duration=X:offset=O1[v01];
    # [v01][2:v]xfade=...:offset=O2[v02]; [v02][3:v]xfade=...:offset=O3[v03];
    # [v03][4:v]xfade=...:offset=O4[vout]
    transitions = ["fade", "fade", "fade", "fade"]
    chain_parts = []
    last_label = "[0:v]"
    for i in range(len(parts) - 1):
        out_label = f"[v{i:02d}]" if i < len(parts) - 2 else "[vout]"
        chain_parts.append(
            f"{last_label}[{i+1}:v]xfade=transition={transitions[i]}:"
            f"duration={XFADE_S}:offset={offsets[i]}{out_label}"
        )
        last_label = out_label
    filtergraph = ";".join(chain_parts)

    subprocess.run([
        "ffmpeg", "-y",
        *inputs_args,
        "-filter_complex", filtergraph,
        "-map", "[vout]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
        "-r", "30",
        str(bg),
    ], check=True, capture_output=True)

    # Cleanup intermediates
    for p in parts:
        p.unlink(missing_ok=True)

    return bg


# ---------------------------------------------------------------------------
# Stage for Remotion
# ---------------------------------------------------------------------------

def stage_for_remotion(project_dir: Path, bg_mp4: Path, quiz_meta: Dict[str, Any]) -> Path:
    """Copy bg.mp4 + quiz_meta.json into remotion-composer/public/ so the
    composition's calculateMetadata can fetch them via staticFile()."""
    public = REPO / "remotion-composer" / "public"
    public.mkdir(parents=True, exist_ok=True)
    shutil.copy2(bg_mp4, public / "bg.mp4")
    (public / "quiz_meta.json").write_text(json.dumps(quiz_meta, indent=2, ensure_ascii=False, default=_json_default))
    # words.json is unused by TriviaQuiz in v0.1 (no VO yet), but write a stub
    # to keep parallel pipelines from getting confused if both are in play.
    if not (public / "words.json").exists():
        (public / "words.json").write_text("[]")
    return public


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True, help="kebab-case project slug")
    ap.add_argument("--from-sheet", action="store_true", help="read inputs from the Posts_Quiz Google Sheet instead of the YAML fixture (v0.2 authoring path)")
    ap.add_argument("--with-flux", action="store_true", help="generate FLUX still backdrops (mid-fidelity, ~$0.09/post)")
    ap.add_argument("--with-openart", action="store_true", help="drive OpenArt to generate Nano Banana Pro stills (5 backdrops; show backdrop is library-cached and reused across posts)")
    ap.add_argument("--openart-headless", action="store_true", help="run OpenArt playwright headless (only if login session is fresh)")
    ap.add_argument("--regen-show-assets", action="store_true", help="force regeneration of the shared show backdrop in scripts/trivia_quiz/library/")
    ap.add_argument("--reuse-question-assets", action="store_true", help="skip OpenArt regeneration for Q1/Q2/Q3 if the JPGs already exist in the project (useful when iterating on the show backdrop without burning credits on questions)")
    ap.add_argument("--with-vo", action="store_true", help="generate ElevenLabs VO for the 5 script lines (requires ELEVENLABS_API_KEY)")
    ap.add_argument("--with-music", action="store_true", help="layer a music bed from music_library/ (track selectable per-row or via style default)")
    ap.add_argument("--with-sfx", action="store_true", help="layer SFX cues (countdown ticks, reveal stings, score-card ding)")
    ap.add_argument("--tts-provider", default="piper", choices=["piper", "elevenlabs"], help="TTS engine (default: piper — local, same model as trivia-short)")
    ap.add_argument("--voice-id", default="", help="ElevenLabs voice_id override (only with --tts-provider=elevenlabs)")
    ap.add_argument("--piper-model", default="", help="Path to a .onnx Piper model (defaults to .piper_voices/en_US-ryan-high.onnx, same as trivia-short)")
    ap.add_argument("--no-render", action="store_true", help="prepare artifacts but don't print the render command")
    args = ap.parse_args()

    # Per project convention, each pipeline gets its own folder under projects/.
    # trivia-reaction nests slugs at projects/trivia-reaction/<slug>/, and we
    # follow the same shape: projects/trivia-quiz/<slug>/.
    project_dir = REPO / "projects" / "trivia-quiz" / args.slug
    fixture_path = project_dir / "inputs" / "quiz_row.yaml"
    artifacts_dir = project_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    # 1. Read + validate fixture — from sheet or YAML, same dict shape downstream.
    if args.from_sheet:
        from scripts.trivia_quiz.sheets import build_sheets, resolve_post_row_to_fixture
        print(f"→ Loading fixture from Sheets: slug={args.slug!r}")
        sheets = build_sheets(write=False)
        row = resolve_post_row_to_fixture(sheets, slug=args.slug)
        print(f"  ✓ fixture resolved from Posts_Quiz tab")
        # Optionally persist for posterity so the artifacts/ directory still
        # carries a snapshot of what was read.
        project_dir.joinpath("inputs").mkdir(parents=True, exist_ok=True)
        (project_dir / "inputs" / "quiz_row_from_sheet.yaml").write_text(
            yaml.safe_dump(row, sort_keys=False, allow_unicode=True)
        )
    else:
        print(f"→ Loading fixture: {fixture_path.relative_to(REPO)}")
        row = _load_yaml(fixture_path)
    validate_fixture(row)
    print("  ✓ fixture passed validation")

    # 2. Read style config
    style = _load_yaml(STYLES_PATH)

    # 3. Build artifacts
    brief = build_brief(row)
    script = build_script(brief, style)
    quiz_meta = build_quiz_meta(brief, style)

    (artifacts_dir / "brief.json").write_text(json.dumps(brief, indent=2, ensure_ascii=False, default=_json_default))
    (artifacts_dir / "script.json").write_text(json.dumps(script, indent=2, ensure_ascii=False, default=_json_default))
    (artifacts_dir / "quiz_meta.json").write_text(json.dumps(quiz_meta, indent=2, ensure_ascii=False, default=_json_default))
    print(f"  ✓ wrote brief.json, script.json, quiz_meta.json under {artifacts_dir.relative_to(REPO)}")

    # 4. Generate backdrops — three fidelity tiers in priority order.
    # All three modes now produce 5 stills (hook + Q1/Q2/Q3 + score), animated
    # by Ken Burns in the assembly step.
    if args.with_openart:
        if args.regen_show_assets and SHOW_BG_LIBRARY_PATH.exists():
            print(f"→ --regen-show-assets: removing cached {SHOW_BG_LIBRARY_PATH.relative_to(REPO)}")
            SHOW_BG_LIBRARY_PATH.unlink()
        print("→ Generating OpenArt stills (3 fresh per-question + 1 shared show backdrop, reused if cached)")
        backdrops = generate_backdrops_openart(
            project_dir, brief, style,
            headless=args.openart_headless,
            reuse_question_assets=args.reuse_question_assets,
        )
    elif args.with_flux:
        print("→ Generating FLUX stills (5 images, ~$0.15 total)")
        backdrops = generate_backdrops_flux(project_dir, brief, style)
    else:
        print("→ Generating solid-color stills (v0.1 default — covers 5 segments)")
        backdrops = generate_backdrops_solid(project_dir)
    for qid, p in backdrops.items():
        print(f"  ✓ {qid}: {p.relative_to(REPO)}")

    # 5. Assemble bg.mp4 (silent video) — duration computed here, audio added
    # in step 5b if any audio flag is set.
    print("→ Assembling bg.mp4 (silent video)")
    bg_mp4 = build_bg_mp4(project_dir, backdrops, style)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(bg_mp4)],
        capture_output=True, text=True, check=True,
    )
    actual = float(probe.stdout.strip())
    print(f"  ✓ bg.mp4 written ({actual:.2f}s)")

    # 5b. Audio layers — VO + music + SFX, mixed and muxed into bg.mp4.
    if args.with_vo or args.with_music or args.with_sfx:
        from scripts.trivia_quiz.audio import mix_and_attach_audio
        print("→ Mixing audio layers (VO / music / SFX)")
        mix_and_attach_audio(
            bg_mp4=bg_mp4,
            script=script,
            quiz_meta=quiz_meta,
            music_track=brief["metadata"]["quiz"].get("music_track") or "",
            total_duration=actual,
            audio_dir=project_dir / "assets" / "audio",
            with_vo=args.with_vo,
            with_music=args.with_music,
            with_sfx=args.with_sfx,
            tts_provider=args.tts_provider,
            voice_id=args.voice_id or None,
            piper_model=Path(args.piper_model) if args.piper_model else None,
        )
        # Re-probe — muxing with -shortest can trim by a few ms
        probe2 = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(bg_mp4)],
            capture_output=True, text=True, check=True,
        )
        actual = float(probe2.stdout.strip())
        print(f"  ✓ bg.mp4 with audio ({actual:.2f}s)")

    # 6. Edit decisions + render report stubs
    edit_decisions = {
        "metadata": {
            "bg_path": str(bg_mp4.relative_to(REPO)),
            "target_duration_s": sum(SEG_DURATIONS.values()) - 4 * XFADE_S,
            "actual_duration_s": actual,
            "assemble_flags": [
                f"--with-flux={args.with_flux}",
                f"--with-vo={args.with_vo}",
                f"--with-music={args.with_music}",
                f"--with-sfx={args.with_sfx}",
            ],
        }
    }
    (artifacts_dir / "edit_decisions.json").write_text(json.dumps(edit_decisions, indent=2, default=_json_default))

    # 7. Stage for Remotion
    public = stage_for_remotion(project_dir, bg_mp4, quiz_meta)
    print(f"  ✓ staged bg.mp4 + quiz_meta.json -> {public.relative_to(REPO)}")

    # 8. Print render command
    if not args.no_render:
        renders_dir = project_dir / "renders"
        renders_dir.mkdir(parents=True, exist_ok=True)
        out_mp4 = renders_dir / "final_quiz.mp4"
        cmd = (
            f"cd {REPO / 'remotion-composer'} && "
            f"npx remotion render src/index-trivia-quiz.tsx TriviaQuiz "
            f"{out_mp4}"
        )
        print("\n→ Run the Remotion render with:\n")
        print(f"  {cmd}\n")
        print(f"  Output will land at: {out_mp4.relative_to(REPO)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
