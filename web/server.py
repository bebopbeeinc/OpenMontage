"""OpenMontage pipeline launcher.

A thin FastAPI app that lives at the project root and mounts per-pipeline web
apps as sub-applications. The launcher itself only serves the home page (a
list of pipelines); everything else is delegated to the mounted pipeline.

Adding a new pipeline:
  1. Build a FastAPI app in scripts/<pipeline>/web/server.py (the trivia app
     is the reference shape — see scripts/trivia/web/server.py).
  2. Mount it here:
       from scripts.<pipeline>.web import server as pipeline
       app.mount("/<pipeline>", pipeline.app)
  3. Add an entry to PIPELINES so the launcher home page lists it.
  4. In that pipeline's index.html, set `<base href="/<pipeline>/">` and use
     relative API paths (`fetch("api/...")` not `fetch("/api/...")`) so
     URLs resolve correctly under the mount prefix.

Run:
  uvicorn web.server:app --port 8765 --reload
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

# Pipeline sub-apps. Each must be a self-contained FastAPI app.
from scripts.trivia.web import server as trivia

REPO = Path(__file__).resolve().parents[1]
WEB_DIR = Path(__file__).resolve().parent


# Pipeline catalog — drives the launcher home page. Keep `path` in sync with
# the mount call below + the `<base href>` in the pipeline's index.html.
PIPELINES = [
    {
        "id": "trivia",
        "path": "/trivia/",
        "name": "Trivia Short",
        "description": (
            "Daily-trivia vertical video. Driven by the Post Calendar sheet — "
            "human writes copy, OpenArt generates 3 clips, this pipeline "
            "stitches them, transcribes, renders TikTok-style captions, "
            "publishes to Drive."
        ),
        "stability": "beta",
    },
]


app = FastAPI(title="OpenMontage pipeline launcher")

# Mount pipeline sub-apps. The trivia FastAPI app already has its own routes
# rooted at "/", so mounting at "/trivia" prefixes everything correctly.
app.mount("/trivia", trivia.app)


@app.get("/")
async def home():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/pipelines")
async def list_pipelines():
    """Used by the home page JS to render the pipeline cards."""
    return {"pipelines": PIPELINES}


@app.get("/api/health")
async def health():
    return {"ok": True, "pipelines": [p["id"] for p in PIPELINES]}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8765)
