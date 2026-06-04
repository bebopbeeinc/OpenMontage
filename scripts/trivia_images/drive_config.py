"""Shared Drive locations for the trivia-images pipeline.

Single source of truth for the Drive root so the web server
(scripts/trivia_images/web/server.py), the batch optimizer
(scripts/trivia_images/optimize_drive.py), and the one-time migration
(scripts/trivia_images/migrate_to_country_folders.py) agree without any of
them importing the others (importing the FastAPI app spins up a prewarm
thread).

Layout (post country-folder migration):
    Question Images/            <- QUESTION_IMAGES_ROOT_ID (the Shared Drive root)
      <CODE>/                   <- one folder per COUNTRY code (US, IN, FR, ...)
        {N}{Q|A}.png            <- full-res originals
        Resized/{N}{Q|A}.png    <- 512x384 game copies

There is no longer a "WIP"/staging folder: an image lives in exactly one
country folder, and "approved vs WIP" is a STATUS read from / written to the
question tab in the sheet (the `Q Image Approved` / `A Image Approved`
columns), not a folder location.
"""
from __future__ import annotations

# Canonical "Question Images" Shared Drive root. Country code subfolders live
# directly under it. (This is the folder that used to be APPROVED_FOLDER_ID.)
QUESTION_IMAGES_ROOT_ID = "1wENmER7aQ6wk23jP6wOggc7mviLAB_pw"
