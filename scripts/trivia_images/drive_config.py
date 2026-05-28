"""Shared Drive locations for the trivia-images pipeline.

Single source of truth for the staging/approved folder IDs so the web
server (scripts/trivia_images/web/server.py) and the batch optimizer
(scripts/trivia_images/optimize_drive.py) agree without either importing
the other (importing the FastAPI app spins up a prewarm thread).
"""
from __future__ import annotations

# Canonical "Question Images" Shared Drive root (reviewed/approved assets).
APPROVED_FOLDER_ID = "1wENmER7aQ6wk23jP6wOggc7mviLAB_pw"
# WIP subfolder freshly generated images land in before approval.
STAGING_FOLDER_ID = "1NMb2WeJp7HVsO-gzvOA-B0wK83uFta9k"
