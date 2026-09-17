#!/usr/bin/env python
"""Export OpenArt saved characters into `character_library/`.

A one-time bootstrap, not a runtime dependency. OpenArt's MCP API has no tool
that lists saved characters, but its web app has an authenticated endpoint that
does — `/suite/api/character/list` — so this borrows the Playwright session the
old drivers left behind (`.playwright/openart-state.json`) to read it, and
writes the result into the layout `openart_characters.py` expects.

It is the only place Playwright is still used for OpenArt, and it runs only
when you ask it to. If the saved session has expired, log in once with the
archived driver's probe and re-run:

    python scripts/common/openart_driver_playwright.py --probe

Usage:
    python scripts/common/openart_character_export.py                      # list only
    python scripts/common/openart_character_export.py --write              # download stills
    python scripts/common/openart_character_export.py --write --pointers   # refs.json only
    python scripts/common/openart_character_export.py --write --only "Captain Archibald"
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "common"))

import openart_api as api  # noqa: E402
import openart_characters as characters  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

STATE_FILE = REPO / ".playwright" / "openart-state.json"
CHARACTER_LIST = "https://openart.ai/suite/api/character/list"
SUITE_URL = "https://openart.ai/suite/create-video/byte-plus-seedance-2"
PAGE_LIMIT = 50


def _scopes() -> list[dict]:
    """Every (workspace, project) pair, via MCP — which supports both listings.

    Only the character listing itself needs the browser session; everything
    else the API answers properly, so we ask it there.
    """
    restore = (api.current_workspace() or {}).get("name")
    found: list[dict] = []
    try:
        for ws in api.workspaces():
            api.ensure_workspace(ws["name"])
            for proj in api.call_tool("openart_project_list").get("items", []):
                found.append({"workspace": ws["name"], "project": proj.get("name"),
                              "project_id": proj.get("id")})
    finally:
        if restore:
            api.ensure_workspace(restore)
    return found


def _enter_workspace(page, workspace: str) -> None:
    """Point the browser session at `workspace` before listing its characters.

    `/suite/api/character/list` filters by the session's active workspace, not
    by the projectId alone — querying a project while the browser sits in the
    other workspace returns nothing, which reads exactly like "this character
    doesn't exist". Switching via MCP moves the account-level setting; the page
    has to be reloaded for the session to pick it up.
    """
    api.ensure_workspace(workspace)
    page.goto(SUITE_URL, wait_until="domcontentloaded", timeout=90_000)
    page.wait_for_timeout(3_000)


def _characters_in(request, project_id: str) -> list[dict]:
    out, cursor = [], None
    while True:
        url = f"{CHARACTER_LIST}?featureTypes=character&projectId={project_id}&limit={PAGE_LIMIT}"
        if cursor:
            url += f"&cursor={cursor}"
        r = request.get(url)
        if not r.ok:
            return out
        try:
            payload = r.json()
        except Exception:
            # An HTML body here means the session no longer satisfies this
            # endpoint — treat it as "nothing readable", not a crash.
            return out
        out.extend(payload.get("characters", []))
        if not payload.get("hasMore"):
            return out
        cursor = payload.get("nextCursor")
        if not cursor:
            return out


def _reference_urls(char: dict) -> list[str]:
    """Primary reference images first, then any other stills, deduped."""
    urls: list[str] = []
    for ref in char.get("referenceImages") or []:
        if ref.get("type") == "primary" and ref.get("url"):
            urls.append(ref["url"])
    for ref in char.get("referenceImages") or []:
        if ref.get("url") and ref["url"] not in urls:
            urls.append(ref["url"])
    for url in char.get("imageUrls") or []:
        if url not in urls:
            urls.append(url)
    return urls


def _write_metadata(target: Path, char: dict, urls: list[str],
                    workspace: str | None = None) -> None:
    """OpenArt's own description of the character, kept as provenance.

    Useful as prompt material too: it is the look in words, which reinforces
    the image reference rather than competing with it. `workspace` records who
    owns the assets — refs.json pointers only resolve there.
    """
    (target / "character.json").write_text(json.dumps({
        "characterName": char["characterName"],
        "openartId": char.get("id"),
        "workspace": workspace,
        "description": (char.get("analysis") or {}).get("characterDescription", ""),
        "analysis": char.get("analysis") or {},
        "sourceUrls": urls,
    }, indent=2))


def _save_stills(request, char: dict, urls: list[str],
                 workspace: str | None = None) -> Path:
    name = char["characterName"]
    target = characters.directory(name)
    target.mkdir(parents=True, exist_ok=True)
    for i, url in enumerate(urls, start=1):
        ext = Path(url.split("?", 1)[0]).suffix.lower() or ".png"
        dest = target / f"{i:02d}-{characters.slug(name)}{ext}"
        resp = request.get(url)
        if not resp.ok:
            print(f"    ! {resp.status} {url}", file=sys.stderr)
            continue
        dest.write_bytes(resp.body())
        print(f"    {dest.relative_to(REPO)}  ({len(resp.body()):,} bytes)")
    _write_metadata(target, char, urls, workspace)
    return target


def _save_pointers(char: dict, urls: list[str],
                   workspace: str | None = None) -> Path:
    """Write refs.json naming the OpenArt URLs, downloading nothing.

    Removes any stills already exported for this character: refs.json wins at
    resolve time, so leaving them would be dead weight that still reads like
    the source of truth.
    """
    name = char["characterName"]
    target = characters.directory(name)
    target.mkdir(parents=True, exist_ok=True)
    (target / characters.POINTER_FILE).write_text(json.dumps(urls, indent=2))
    print(f"    {(target / characters.POINTER_FILE).relative_to(REPO)}"
          f"  ({len(urls)} url(s))")
    for stale in sorted(target.iterdir()):
        if stale.is_file() and stale.suffix.lower() in characters.IMAGE_SUFFIXES:
            stale.unlink()
            print(f"    removed still {stale.relative_to(REPO)}")
    _write_metadata(target, char, urls, workspace)
    return target


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true", help="download; otherwise just list")
    ap.add_argument("--pointers", action="store_true",
                    help="with --write, record OpenArt URLs in refs.json instead "
                         "of downloading the images")
    ap.add_argument("--only", action="append", default=[],
                    help="character name to export (repeatable; default all)")
    args = ap.parse_args()

    if not STATE_FILE.exists():
        print(f"no saved OpenArt session at {STATE_FILE.relative_to(REPO)}.\n"
              f"Log in once:  python scripts/common/openart_driver_playwright.py --probe",
              file=sys.stderr)
        return 2

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(storage_state=str(STATE_FILE))
        page = ctx.new_page()
        # Establish the session before hitting the JSON endpoints.
        page.goto(SUITE_URL, wait_until="domcontentloaded", timeout=90_000)
        if page.locator("text=/Sign up to create for FREE/").count() > 0:
            print("saved OpenArt session has expired — log in again:\n"
                  "  python scripts/common/openart_driver_playwright.py --probe",
                  file=sys.stderr)
            browser.close()
            return 2

        request = ctx.request
        started_in = (api.current_workspace() or {}).get("name")
        seen: set[str] = set()
        exported = 0
        scopes = _scopes()
        for scope in scopes:
            _enter_workspace(page, scope["workspace"])
            chars = _characters_in(request, scope["project_id"])
            print(f"\n{scope['workspace']} / {scope['project']}:"
                  f"{'' if chars else '  (no characters)'}")
            for char in chars:
                name = char.get("characterName")
                if not name or name in seen:
                    continue
                seen.add(name)
                urls = _reference_urls(char)
                mark = "" if urls else "   (no reference images)"
                print(f"  {name}  [{len(urls)} image(s)]{mark}")
                if args.only and name not in args.only:
                    continue
                if args.write and urls:
                    if args.pointers:
                        _save_pointers(char, urls, scope["workspace"])
                    else:
                        _save_stills(request, char, urls, scope["workspace"])
                    exported += 1
        if started_in:
            api.ensure_workspace(started_in)
        browser.close()

    if not args.write:
        print("\nlisting only — re-run with --write to download into character_library/")
    else:
        print(f"\nexported {exported} character(s)")
        for slug_name, how in characters.available().items():
            print(f"  {slug_name:28} {how}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
