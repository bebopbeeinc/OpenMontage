"""Path helpers for the trivia-captain-reaction pipeline.

All trivia-captain-reaction project workspaces live under
`projects/trivia-captain-reaction/<slug>/`. The pipeline-scoped subdirectory
prevents slug collisions with the sister pipelines (trivia-reaction,
trivia-captain) — several Day-N slugs overlap across them.
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PIPELINE = "trivia-captain-reaction"
PROJECTS_ROOT = REPO / "projects" / PIPELINE


def project_dir(slug: str) -> Path:
    """Return the absolute path to a slug's project workspace."""
    return PROJECTS_ROOT / slug


def projects_root_rel() -> Path:
    """Path of PROJECTS_ROOT relative to REPO — handy for subprocess --root args."""
    return PROJECTS_ROOT.relative_to(REPO)
