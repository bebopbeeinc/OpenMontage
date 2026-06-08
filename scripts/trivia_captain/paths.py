"""Path helpers for the trivia-captain pipeline.

All trivia-captain project workspaces live under
`projects/trivia-captain/<slug>/`. The pipeline-scoped subdirectory
prevents slug collisions with other pipelines (e.g. trivia-short used
the bare `projects/<slug>/` layout pre-namespacing, and several slugs
overlap between the two pipelines — notably `dutch-police-teddy-bear`).
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PIPELINE = "trivia-captain"
PROJECTS_ROOT = REPO / "projects" / PIPELINE


def project_dir(slug: str) -> Path:
    """Return the absolute path to a slug's project workspace."""
    return PROJECTS_ROOT / slug


def projects_root_rel() -> Path:
    """Path of PROJECTS_ROOT relative to REPO — handy for subprocess --root args."""
    return PROJECTS_ROOT.relative_to(REPO)
