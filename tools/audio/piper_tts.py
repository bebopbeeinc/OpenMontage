"""Piper local text-to-speech provider tool."""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    RetryPolicy,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolStatus,
    ToolTier,
)

# Repo-local voice cache. Gitignored: the .onnx files are ~30-115MB each and
# come from upstream HuggingFace, so we fetch on demand rather than commit.
REPO_ROOT = Path(__file__).resolve().parents[2]
VOICE_DIR = REPO_ROOT / ".piper_voices"

# HuggingFace base for the rhasspy/piper-voices model index. The repo lays
# voices out as <lang>/<locale>/<voice>/<quality>/<file>; we derive the URL
# from the model name pattern <locale>-<voice>-<quality>.
HF_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"


def _parse_voice_name(name: str) -> tuple[str, str, str, str]:
    """Split a Piper model name into its HuggingFace path components.

    `en_US-lessac-medium` -> ('en', 'en_US', 'lessac', 'medium')

    The first directory in the upstream layout is the 2-letter language code,
    not the full locale — derived from the locale's prefix before the
    underscore. Raises ValueError on names that don't match the convention.
    """
    parts = name.split("-")
    if len(parts) != 3:
        raise ValueError(
            f"piper voice name {name!r} doesn't match <locale>-<voice>-<quality> "
            f"(e.g. en_US-lessac-medium)"
        )
    locale, voice, quality = parts
    if "_" not in locale:
        raise ValueError(
            f"piper voice name {name!r}: locale {locale!r} must contain '_' "
            f"(e.g. en_US)"
        )
    lang = locale.split("_", 1)[0]
    return lang, locale, voice, quality


def voice_files_present(name: str, voice_dir: Path = VOICE_DIR) -> bool:
    """True iff both the .onnx and .onnx.json files for `name` exist locally."""
    onnx = voice_dir / f"{name}.onnx"
    cfg = voice_dir / f"{name}.onnx.json"
    return onnx.is_file() and cfg.is_file() and onnx.stat().st_size > 0


def fetch_voice(
    name: str,
    voice_dir: Path = VOICE_DIR,
    *,
    progress: bool = True,
) -> tuple[Path, Path]:
    """Download `name`'s .onnx + .onnx.json into `voice_dir` if missing.

    Returns the (onnx_path, config_path) pair. Idempotent: existing files
    are kept as-is. Raises on network failure or unknown voice.
    """
    voice_dir.mkdir(parents=True, exist_ok=True)
    lang, locale, voice, quality = _parse_voice_name(name)
    base = f"{HF_BASE}/{lang}/{locale}/{voice}/{quality}"
    onnx_url = f"{base}/{name}.onnx"
    cfg_url = f"{base}/{name}.onnx.json"
    onnx_path = voice_dir / f"{name}.onnx"
    cfg_path = voice_dir / f"{name}.onnx.json"

    for url, dest in ((onnx_url, onnx_path), (cfg_url, cfg_path)):
        if dest.is_file() and dest.stat().st_size > 0:
            continue
        if progress:
            print(f"  → fetching {dest.name} from huggingface…", file=sys.stderr)
        tmp = dest.with_suffix(dest.suffix + ".part")
        try:
            urllib.request.urlretrieve(url, tmp)  # noqa: S310 - controlled URL
            tmp.replace(dest)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        if progress:
            mb = dest.stat().st_size / (1024 * 1024)
            print(f"  ✓ saved {dest.name} ({mb:.1f} MB)", file=sys.stderr)

    return onnx_path, cfg_path


class PiperTTS(BaseTool):
    name = "piper_tts"
    version = "0.2.0"
    tier = ToolTier.VOICE
    capability = "tts"
    provider = "piper"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.LOCAL

    dependencies = ["python:piper"]
    install_instructions = (
        "Install Piper TTS:\n"
        "  pip install piper-tts\n"
        "Voice models are auto-fetched on first use from\n"
        "  https://huggingface.co/rhasspy/piper-voices\n"
        "into .piper_voices/ at the repo root. To pre-fetch (e.g. on a new\n"
        "machine without network at runtime):\n"
        "  python scripts/piper_voices/fetch.py en_US-lessac-medium"
    )
    agent_skills = ["text-to-speech"]

    capabilities = [
        "text_to_speech",
        "offline_generation",
    ]
    supports = {
        "voice_cloning": False,
        "multilingual": False,
        "offline": True,
        "native_audio": True,
    }
    best_for = [
        "offline narration fallback",
        "privacy-sensitive local-only workflows",
    ]
    not_good_for = [
        "best-in-class expressive voice quality",
        "voice clone matching",
    ]

    input_schema = {
        "type": "object",
        "required": ["text"],
        "properties": {
            "text": {"type": "string"},
            "model": {
                "type": "string",
                "default": "en_US-lessac-medium",
            },
            "speaker_id": {
                "type": "integer",
                "default": 0,
            },
            "length_scale": {
                "type": "number",
                "default": 1.0,
            },
            "sentence_silence": {
                "type": "number",
                "default": 0.3,
            },
            "output_path": {"type": "string"},
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=2, ram_mb=512, vram_mb=0, disk_mb=200, network_required=False
    )
    retry_policy = RetryPolicy(max_retries=1, retryable_errors=[])
    idempotency_key_fields = ["text", "model", "speaker_id", "length_scale"]
    side_effects = ["writes audio file to output_path"]
    user_visible_verification = ["Listen to generated audio for intelligibility"]

    def get_status(self) -> ToolStatus:
        try:
            import piper  # noqa: F401
            return ToolStatus.AVAILABLE
        except ImportError:
            pass
        if shutil.which("piper"):
            return ToolStatus.AVAILABLE
        return ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        if self.get_status() != ToolStatus.AVAILABLE:
            return ToolResult(success=False, error="Piper TTS not available. " + self.install_instructions)

        start = time.time()
        try:
            model_name = inputs.get("model", "en_US-lessac-medium")
            if not voice_files_present(model_name):
                try:
                    fetch_voice(model_name)
                except Exception as exc:
                    return ToolResult(
                        success=False,
                        error=(
                            f"Failed to fetch piper voice {model_name!r}: {exc}. "
                            f"Pre-fetch manually: python scripts/piper_voices/fetch.py {model_name}"
                        ),
                    )
            result = self._generate(inputs)
        except Exception as exc:
            return ToolResult(success=False, error=f"Local TTS generation failed: {exc}")

        result.duration_seconds = round(time.time() - start, 2)
        return result

    def _piper_cmd(self) -> list[str]:
        """Choose between the `piper` CLI and `python -m piper`. The pip-
        installed version of piper-tts on macOS does not always create a
        `piper` shim on PATH, so falling back to `python -m piper` is robust.
        """
        if shutil.which("piper"):
            return ["piper"]
        return [sys.executable, "-m", "piper"]

    def _generate(self, inputs: dict[str, Any]) -> ToolResult:
        output_path = Path(inputs.get("output_path", "tts_output.wav"))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        model = inputs.get("model", "en_US-lessac-medium")

        cmd = [
            *self._piper_cmd(),
            "--model", model,
            "--data-dir", str(VOICE_DIR),
            "--speaker", str(inputs.get("speaker_id", 0)),
            "--length-scale", str(inputs.get("length_scale", 1.0)),
            "--sentence-silence", str(inputs.get("sentence_silence", 0.3)),
            "--output-file", str(output_path),
        ]
        proc = subprocess.run(
            cmd,
            input=inputs["text"],
            capture_output=True,
            text=True,
            timeout=300,
        )

        if proc.returncode != 0:
            return ToolResult(success=False, error=f"Piper failed (exit {proc.returncode}): {proc.stderr}")
        if not output_path.exists():
            return ToolResult(success=False, error=f"Piper output file missing: {output_path}")

        return ToolResult(
            success=True,
            data={
                "provider": self.provider,
                "model": model,
                "speaker_id": inputs.get("speaker_id", 0),
                "text_length": len(inputs["text"]),
                "output": str(output_path),
                "format": "wav",
            },
            artifacts=[str(output_path)],
            model=model,
        )
