"""OpenArt image generation via Playwright browser automation.

Wraps the Playwright driver at scripts/trivia_images/openart_image_driver.py
as a registered BaseTool so it is discoverable through the registry and
routable through image_selector.

Models exposed as a parameter (no provider lock-in): Nano Banana Pro,
Nano Banana 2, Nano Banana, GPT Image 2, Seedream 4.5.

reference_image_path attaches a local image file to OpenArt's hidden file
input before submitting. The model then uses it as a visual source for a
"same scene" remix — output keeps the reference's environment while taking
the new prompt's content. Used by the trivia-images answer-image stage.
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
DRIVER_DIR = REPO / "scripts" / "trivia_images"
STATE_FILE = REPO / ".playwright" / "openart-state.json"


class OpenArtImage(BaseTool):
    name = "openart_image"
    version = "0.1.0"
    tier = ToolTier.GENERATE
    capability = "image_generation"
    provider = "openart"
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.HYBRID

    dependencies = ["python:playwright"]
    install_instructions = (
        "OpenArt is browser-automated, not API-based.\n"
        "  1. pip install playwright && playwright install chromium\n"
        "  2. First run is headed (headless=False) so you can log in at\n"
        "     openart.ai. The session persists at .playwright/openart-state.json\n"
        "     and subsequent runs can be headless."
    )
    agent_skills: list[str] = []

    capabilities = [
        "generate_image",
        "text_to_image",
        "photorealistic",
        "illustration",
    ]

    # Model display name -> URL slug. Source of truth for which models the
    # driver knows how to drive. Keep in sync with MODEL_SLUGS in
    # scripts/trivia_images/openart_image_driver.py.
    KNOWN_MODELS: list[str] = [
        "Nano Banana Pro",
        "Nano Banana 2",
        "Nano Banana",
        "GPT Image 2",
        "Seedream 4.5",
    ]

    supports = {
        "negative_prompt": False,
        "seed": False,
        "custom_size": False,        # aspect/resolution are radio-picked
        "reference_image": True,
        "variants": True,
    }
    best_for = [
        "fast iteration via web UI under an existing OpenArt subscription",
        "models exclusive to OpenArt (e.g. Seedream 4.5)",
        "free-tier generation when API budgets are tight",
    ]
    not_good_for = [
        "headless servers without a persisted login",
        "fully deterministic / seedable generation",
        "high-volume batch jobs (driver runs one prompt at a time)",
    ]

    input_schema = {
        "type": "object",
        "required": ["prompt", "output_path"],
        "properties": {
            "prompt": {"type": "string"},
            "model": {
                "type": "string",
                "enum": KNOWN_MODELS,
                "default": "Nano Banana Pro",
            },
            "output_path": {
                "type": "string",
                "description": (
                    "Destination path for variant 1. Extension may be "
                    "rewritten to match the CDN's served format unless "
                    "keep_source_ext=False."
                ),
            },
            "output_paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Optional explicit per-variant paths. If provided, "
                    "overrides output_path + variants."
                ),
            },
            "variants": {"type": "integer", "default": 1, "minimum": 1},
            "aspect": {"type": "string", "default": "4:3"},
            "resolution": {"type": "string", "default": "2K"},
            "headless": {"type": "boolean", "default": False},
            "keep_source_ext": {"type": "boolean", "default": True},
            "reference_image_path": {
                "type": "string",
                "description": (
                    "Local path to an image to attach as a same-scene "
                    "reference. The driver uploads it to OpenArt's hidden "
                    "file input before submitting, so the model uses it as "
                    "a visual source. Used by the trivia-images answer-"
                    "image stage."
                ),
            },
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=2, ram_mb=2048, vram_mb=0, disk_mb=200, network_required=True
    )
    retry_policy = RetryPolicy(max_retries=0)
    resume_support = ResumeSupport.NONE
    idempotency_key_fields = ["prompt", "model", "aspect", "resolution"]
    side_effects = [
        "opens a Chromium browser (headed on first run)",
        "writes image file(s) to output_path(s)",
        "may prompt for manual OpenArt login on first run",
        "persists session state at .playwright/openart-state.json",
    ]
    user_visible_verification = [
        "inspect saved image(s) under output_path",
        "verify model + aspect + resolution match the request",
    ]

    def get_status(self) -> ToolStatus:
        try:
            import playwright  # noqa: F401
        except ImportError:
            return ToolStatus.UNAVAILABLE
        # Driver can run headed and prompt for login on first use, so absence
        # of saved state is degraded (not unavailable) — the user can still
        # complete the flow interactively.
        if STATE_FILE.exists():
            return ToolStatus.AVAILABLE
        return ToolStatus.DEGRADED

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        # OpenArt billing is subscription-based, not per-call from this side.
        return 0.0

    def estimate_runtime(self, inputs: dict[str, Any]) -> float:
        variants = max(1, int(inputs.get("variants", 1)))
        return 60.0 * variants

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
        stem, suf = base_path.stem, base_path.suffix or ".jpg"
        return [base_path.with_name(f"{stem}_v{i+1}{suf}") for i in range(variants)]

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        try:
            self.check_dependencies()
        except Exception as e:
            return ToolResult(success=False, error=str(e))

        reference_image_path = inputs.get("reference_image_path")
        if reference_image_path:
            ref = Path(reference_image_path).expanduser().resolve()
            if not ref.exists():
                return ToolResult(
                    success=False,
                    error=f"reference_image_path does not exist: {ref}",
                )
            reference_image_path = ref

        # Import the driver lazily so registry discovery doesn't pay the
        # cost of importing playwright at startup.
        if str(DRIVER_DIR) not in sys.path:
            sys.path.insert(0, str(DRIVER_DIR))
        try:
            from openart_image_driver import generate_image  # type: ignore
        except ImportError as e:
            return ToolResult(
                success=False,
                error=f"failed to import openart_image_driver: {e}",
            )

        try:
            output_paths = self._resolve_output_paths(inputs)
        except ValueError as e:
            return ToolResult(success=False, error=str(e))

        prompt = inputs["prompt"]
        model = inputs.get("model", "Nano Banana Pro")
        if model not in self.KNOWN_MODELS:
            return ToolResult(
                success=False,
                error=f"unknown model {model!r}; known: {self.KNOWN_MODELS}",
            )

        start = time.time()
        try:
            saved = generate_image(
                prompt=prompt,
                model=model,
                output_paths=output_paths,
                headless=bool(inputs.get("headless", False)),
                aspect=inputs.get("aspect", "4:3"),
                resolution=inputs.get("resolution", "2K"),
                keep_source_ext=bool(inputs.get("keep_source_ext", True)),
                reference_image_path=reference_image_path,
            )
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"openart_image_driver raised: {e}",
                duration_seconds=time.time() - start,
                model=model,
            )

        return ToolResult(
            success=bool(saved),
            data={
                "saved_paths": [str(p) for p in saved],
                "model": model,
                "aspect": inputs.get("aspect", "4:3"),
                "resolution": inputs.get("resolution", "2K"),
                "reference_image_path": (
                    str(reference_image_path) if reference_image_path else None
                ),
            },
            artifacts=[str(p) for p in saved],
            duration_seconds=time.time() - start,
            model=model,
            error=None if saved else "driver returned no saved paths",
        )
