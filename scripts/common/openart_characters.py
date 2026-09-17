#!/usr/bin/env python
"""Local reference stills that stand in for OpenArt's saved characters.

The Playwright drivers picked characters out of OpenArt's library by name
("Captain Archibald", "ellie.travelcrush"). The MCP surface has no tool that
resolves a saved character by name — and even its `element` reference form
requires the image URL — so the identity reference has to be addressed from
something we hold ourselves.

Two ways to hold it, per character. Either is fine; a character may use one or
the other, not both.

1. **Stills** — the exported images themselves:

       character_library/captain-archibald/01-face.png
                                           02-outfit.png

   Seed by opening the character in the OpenArt UI and downloading its
   reference images. What you exported is unambiguously what gets used.

2. **Pointers** — `refs.json`, naming media already in OpenArt:

       character_library/ellie-travelcrush/refs.json

       ["https://cdn.openart.ai/.../ellie-face.png"]

   Entries may be bare URL strings, `{"url": ..., "label": ...}` objects, or
   whole visualReference objects (used as-is). Nothing is uploaded. The
   tradeoff: edit the character in OpenArt and the URL goes stale silently —
   generations keep rendering the old look with no error. Stills fail loudly
   instead, which is why they are the default recommendation.

`refs.json` wins when both are present.

Either way the resolved references are cached per workspace in
`character_library/.uploads.json` — otherwise every single row would re-upload
or re-resolve the same assets.
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Optional

import openart_api as api

REPO = Path(__file__).resolve().parents[2]
LIBRARY = REPO / "character_library"
CACHE_FILE = LIBRARY / ".uploads.json"

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")
POINTER_FILE = "refs.json"
METADATA_FILE = "character.json"

# One character's stills can be uploaded by several worker threads at once.
_CACHE_LOCK = threading.Lock()


def slug(name: str) -> str:
    """"Captain Archibald" -> "captain-archibald"; "ellie.travelcrush" -> "ellie-travelcrush"."""
    return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")


def directory(name: str) -> Path:
    return LIBRARY / slug(name)


def stills(name: str) -> list[Path]:
    """The character's reference stills, in filename order.

    Filenames sort, so prefix them (`01-face.png`, `02-outfit.png`) when the
    order matters — the models weight earlier references more heavily.
    """
    root = directory(name)
    if not root.is_dir():
        return []
    return sorted(
        p for p in root.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES
    )


def pointers(name: str) -> list:
    """Entries from the character's `refs.json`, or [] when there is none."""
    path = directory(name) / POINTER_FILE
    try:
        loaded = json.loads(path.read_text())
    except FileNotFoundError:
        return []
    except json.JSONDecodeError as e:
        raise ValueError(f"{path.relative_to(REPO)} is not valid JSON: {e}") from e
    if not isinstance(loaded, list):
        raise ValueError(
            f"{path.relative_to(REPO)} must hold a JSON list of references, "
            f"got {type(loaded).__name__}",
        )
    return loaded


def _missing_message(name: str, root: Path) -> str:
    return (
        f"no reference for character {name!r}.\n"
        f"OpenArt's MCP API cannot resolve a saved character by name, so the "
        f"identity reference has to be addressed from this repo. Give {name} "
        f"either of these in:\n"
        f"  {root.relative_to(REPO)}/\n"
        f"  - one or more {'/'.join(IMAGE_SUFFIXES)} stills exported from the "
        f"OpenArt character library, or\n"
        f"  - {POINTER_FILE}, a JSON list of that character's OpenArt media "
        f"URLs.\n"
        f"See character_library/README.md."
    )


def owning_workspace(name: str) -> Optional[str]:
    """The workspace that owns this character's assets, per character.json."""
    try:
        meta = json.loads((directory(name) / METADATA_FILE).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    return meta.get("workspace")


def _pointer_workspace_error(name: str, url: str, err: Exception) -> Exception:
    """Explain the one failure mode pointers have and stills don't.

    An OpenArt asset belongs to the workspace it was uploaded into, so a
    refs.json pointer resolves only while generating there. The raw API message
    ("Upload is unavailable in the current Workspace") never names the
    character or the workspace that would work.
    """
    if "unavailable in the current workspace" not in str(err).lower():
        return err
    owner = owning_workspace(name)
    active = (api.last_known_workspace() or {}).get("name", "the active workspace")
    return RuntimeError(
        f"character {name!r} is referenced by URL ({POINTER_FILE}), and that "
        f"asset belongs to {owner or 'another'} workspace — it cannot be "
        f"resolved while generating in {active!r}.\n"
        f"  {url}\n"
        f"Either generate in {owner or 'the owning'} workspace (pass "
        f"workspace=...), or re-export this character as stills, which upload "
        f"into whichever workspace runs the job:\n"
        f"  python scripts/common/openart_character_export.py --write "
        f"--only {name!r}",
    )


def _load_cache() -> dict:
    try:
        return json.loads(CACHE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_cache(cache: dict) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, indent=2, sort_keys=True))


def _still_key(workspace_id: str, path: Path) -> str:
    stat = path.stat()
    # Size + mtime is enough to notice a re-exported still without hashing
    # megabytes on every call.
    return f"{workspace_id}|{path.relative_to(LIBRARY)}|{stat.st_size}|{stat.st_mtime_ns}"


def _pointer_key(workspace_id: str, url: str) -> str:
    return f"{workspace_id}|url|{url}"


def _pointer_url(entry) -> str:
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict) and isinstance(entry.get("url"), str):
        return entry["url"]
    raise ValueError(
        f"{POINTER_FILE} entries must be a URL string or an object with a "
        f"'url' field, got {entry!r}",
    )


def _is_full_reference(entry) -> bool:
    """True when the entry is already a visualReference the form will accept."""
    return (
        isinstance(entry, dict)
        and all(isinstance(entry.get(k), str) for k in ("type", "id", "url", "label"))
    )


def visual_references(
    name: str,
    purpose: str = "create-video",
    limit: Optional[int] = None,
) -> list[dict]:
    """Resolve a character to visualReferences, from refs.json or local stills.

    `refs.json` wins when a character has both. References are workspace-scoped,
    so switch to the target workspace *before* calling this.
    """
    entries = pointers(name)
    paths = [] if entries else stills(name)
    if not entries and not paths:
        raise FileNotFoundError(_missing_message(name, directory(name)))
    if limit:
        entries, paths = entries[:limit], paths[:limit]

    # Resolved only when something actually needs caching: a refs.json of
    # complete references costs no network call, and must not need one.
    cached_workspace_id: list[str] = []

    def workspace_id() -> str:
        if not cached_workspace_id:
            # Cache-key only — ensure_workspace has just run, so this reuses
            # what it fetched rather than asking again on every row.
            ws = api.last_known_workspace() or {}
            cached_workspace_id.append(ws.get("id", "default"))
        return cached_workspace_id[0]

    with _CACHE_LOCK:
        cache = _load_cache()
        refs: list[dict] = []
        dirty = False

        for entry in entries:
            # A hand-written full reference is taken at face value — no call.
            if _is_full_reference(entry):
                refs.append(entry)
                continue
            url = _pointer_url(entry)
            key = _pointer_key(workspace_id(), url)
            ref = cache.get(key)
            if not ref:
                label = entry.get("label") if isinstance(entry, dict) else None
                try:
                    ref = api.reference_for_url(url, label=label)
                except api.OpenArtMCPError as e:
                    raise _pointer_workspace_error(name, url, e) from e
                cache[key] = ref
                dirty = True
            refs.append(ref)

        for path in paths:
            key = _still_key(workspace_id(), path)
            ref = cache.get(key)
            if not ref:
                ref = api.upload_reference(
                    path,
                    media_type="image",
                    purpose=purpose,
                    label=f"{slug(name)}/{path.name}",
                )
                cache[key] = ref
                dirty = True
            refs.append(ref)

        if dirty:
            _save_cache(cache)
    return refs


def available() -> dict[str, str]:
    """Character slugs that can be resolved, mapped to how: stills or refs.json."""
    if not LIBRARY.is_dir():
        return {}
    found: dict[str, str] = {}
    for d in sorted(LIBRARY.iterdir()):
        if not d.is_dir():
            continue
        if (d / POINTER_FILE).is_file():
            found[d.name] = POINTER_FILE
        elif any(p.suffix.lower() in IMAGE_SUFFIXES for p in d.iterdir() if p.is_file()):
            found[d.name] = "stills"
    return found


if __name__ == "__main__":
    print(f"character_library: {LIBRARY}")
    found = available()
    if not found:
        print("no characters resolvable yet — see character_library/README.md")
    for name, how in found.items():
        print(f"  {name:28} {how}")
