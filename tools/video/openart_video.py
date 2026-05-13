"""OpenArt video generation via Playwright browser automation.

Wraps the Playwright driver at scripts/common/openart_driver.py as a
registered BaseTool so it is discoverable through the registry and routable
through video_selector.

Models exposed as a parameter (no provider lock-in): Seedance 2.0, HappyHorse.
Add new entries by extending KNOWN_MODELS here AND MODEL_SLUGS in the driver.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    ResumeSupport,
    RetryPolicy,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolStatus,
    ToolTier,
)

REPO = Path(__file__).resolve().parents[2]
DRIVER_DIR = REPO / "scripts" / "common"
STATE_FILE = REPO / ".playwright" / "openart-state.json"


class OpenArtVideo(BaseTool):
    name = "openart_video"
    version = "0.1.0"
    tier = ToolTier.GENERATE
    capability = "video_generation"
    provider = "openart"
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.HYBRID

    dependencies = ["python:playwright", "cmd:ffmpeg"]
    install_instructions = (
        "OpenArt is browser-automated, not API-based.\n"
        "  1. pip install playwright && playwright install chromium\n"
        "  2. brew install ffmpeg (used to strip audio when audio_on=False)\n"
        "  3. First run is headed (headless=False) so you can log in at\n"
        "     openart.ai. The session persists at .playwright/openart-state.json\n"
        "     and subsequent runs can be headless."
    )
    agent_skills: list[str] = []

    capabilities = [
        "generate_video",
        "text_to_video",
        "short_clip",
    ]

    # Model display name -> URL slug. Source of truth for which models the
    # driver knows how to drive. Keep in sync with MODEL_SLUGS in
    # scripts/common/openart_driver.py.
    KNOWN_MODELS: list[str] = [
        "Seedance 2.0",
        "HappyHorse",
    ]

    supports = {
        "duration_seconds": True,
        "audio": True,
        "variants": True,
        "character_reference": True,   # via the OpenArt "Characters" library
        "reference_image": False,      # custom-upload references not exposed yet
    }
    best_for = [
        "vertical short-form clips under an existing OpenArt subscription",
        "Seedance trailer-style cinematics",
        "HappyHorse cartoon-style clips",
    ]
    not_good_for = [
        "headless servers without a persisted login",
        "fully deterministic / seedable generation",
        "long clips (driver targets ≤10s)",
    ]

    input_schema = {
        "type": "object",
        "required": ["prompt", "output_path"],
        "properties": {
            "prompt": {"type": "string"},
            "model": {
                "type": "string",
                "enum": KNOWN_MODELS,
                "default": "Seedance 2.0",
            },
            "duration_s": {"type": "integer", "default": 8, "minimum": 1},
            "output_path": {
                "type": "string",
                "description": "Destination .mp4 path for variant 1.",
            },
            "output_paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional explicit per-variant paths.",
            },
            "variants": {"type": "integer", "default": 1, "minimum": 1},
            "audio_on": {
                "type": "boolean",
                "default": False,
                "description": (
                    "Leave audio enabled when True. Default off; the driver "
                    "strips the audio track post-download for muted mode."
                ),
            },
            "character": {
                "type": "string",
                "description": (
                    "Optional saved-character name from the OpenArt "
                    "'Characters' library to attach as a reference."
                ),
            },
            "headless": {"type": "boolean", "default": False},
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=2, ram_mb=2048, vram_mb=0, disk_mb=500, network_required=True
    )
    retry_policy = RetryPolicy(max_retries=0)
    resume_support = ResumeSupport.NONE
    idempotency_key_fields = ["prompt", "model", "duration_s", "character"]
    side_effects = [
        "opens a Chromium browser (headed on first run)",
        "writes video file(s) to output_path(s)",
        "may prompt for manual OpenArt login on first run",
        "persists session state at .playwright/openart-state.json",
        "remuxes downloaded mp4 to strip audio when audio_on=False",
    ]
    user_visible_verification = [
        "inspect saved mp4(s) under output_path",
        "verify duration + aspect via ffprobe",
    ]

    def get_status(self) -> ToolStatus:
        try:
            import playwright  # noqa: F401
        except ImportError:
            return ToolStatus.UNAVAILABLE
        import shutil
        if shutil.which("ffmpeg") is None:
            return ToolStatus.UNAVAILABLE
        if STATE_FILE.exists():
            return ToolStatus.AVAILABLE
        return ToolStatus.DEGRADED

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def estimate_runtime(self, inputs: dict[str, Any]) -> float:
        variants = max(1, int(inputs.get("variants", 1)))
        duration = int(inputs.get("duration_s", 8))
        # Rough: ~15s per generated second per variant, plus base overhead.
        return 30.0 + 15.0 * duration * variants

    def _resolve_output_paths(self, inputs: dict[str, Any]) -> list[Path]:
        explicit = inputs.get("output_paths")
        if explicit:
            return [Path(p).expanduser().resolve() for p in explicit]
        base = inputs.get("output_path")
        if not base:
            raise ValueError("output_path (or output_paths) is required")
        variants = max(1, int(inputs.get("variants", 1)))
        base_path = Path(base).expanduser().resolve()
        if variants == 1:
            return [base_path]
        stem, suf = base_path.stem, base_path.suffix or ".mp4"
        return [base_path.with_name(f"{stem}_v{i+1}{suf}") for i in range(variants)]

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        try:
            self.check_dependencies()
        except Exception as e:
            return ToolResult(success=False, error=str(e))

        if str(DRIVER_DIR) not in sys.path:
            sys.path.insert(0, str(DRIVER_DIR))
        try:
            from openart_driver import generate_clip  # type: ignore
        except ImportError as e:
            return ToolResult(
                success=False,
                error=f"failed to import openart_driver: {e}",
            )

        try:
            output_paths = self._resolve_output_paths(inputs)
        except ValueError as e:
            return ToolResult(success=False, error=str(e))

        prompt = inputs["prompt"]
        model = inputs.get("model", "Seedance 2.0")
        if model not in self.KNOWN_MODELS:
            return ToolResult(
                success=False,
                error=f"unknown model {model!r}; known: {self.KNOWN_MODELS}",
            )

        duration_s = int(inputs.get("duration_s", 8))
        start = time.time()
        try:
            saved = generate_clip(
                prompt=prompt,
                model=model,
                duration_s=duration_s,
                output_paths=output_paths,
                headless=bool(inputs.get("headless", False)),
                audio_on=bool(inputs.get("audio_on", False)),
                character=inputs.get("character"),
            )
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"openart_driver raised: {e}",
                duration_seconds=time.time() - start,
                model=model,
            )

        return ToolResult(
            success=bool(saved),
            data={
                "saved_paths": [str(p) for p in saved],
                "model": model,
                "duration_s": duration_s,
                "audio_on": bool(inputs.get("audio_on", False)),
            },
            artifacts=[str(p) for p in saved],
            duration_seconds=time.time() - start,
            model=model,
            error=None if saved else "driver returned no saved paths",
        )
