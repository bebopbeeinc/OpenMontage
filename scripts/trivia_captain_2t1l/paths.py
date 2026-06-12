"""Path helpers for the trivia-captain-2t1l pipeline workspaces.

Mirrors scripts/trivia_captain/paths.py. Every per-row workspace lives under
projects/trivia-captain-2t1l/<slug>/.
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PIPELINE = "trivia-captain-2t1l"
PROJECTS_ROOT = REPO / "projects" / PIPELINE
LIBRARY_DIR = REPO / "scripts" / "trivia_captain_2t1l" / "library" / "clips"


def project_dir(slug: str) -> Path:
    return PROJECTS_ROOT / slug


def projects_root_rel() -> Path:
    return Path("projects") / PIPELINE
