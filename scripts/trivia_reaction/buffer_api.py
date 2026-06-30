"""Buffer GraphQL API client — schedule posts to ellie.travelcrush's
TikTok + Instagram channels via Buffer (https://api.buffer.com).

This is the sanctioned path: you create a Personal Key in Buffer (Settings →
API → Personal Keys), we store it, and from then on we call the documented
GraphQL endpoint. No scraping, no browser automation.

Buffer does NOT host media: a video post references a PUBLIC, DIRECT, STABLE
HTTPS URL to the .mp4 and Buffer fetches it. For this pipeline that URL is a
Google-Drive direct-download link to the already-published render (see
buffer_push.py).

Prerequisites (one-time, in the browser):
    1. In Buffer, connect ellie.travelcrush's TikTok + Instagram channels.
    2. Settings → API → Personal Keys → "+ New Key" → name it (e.g.
       "OpenMontage"), pick the longest expiry. Copy the token.
    3. Store it (gitignored):
           echo '{"access_token": "PASTE_TOKEN_HERE"}' > .secrets/buffer-api.json
       (or export BUFFER_ACCESS_TOKEN=...)

Usage:
    # Verify the token + list organizations:
    .venv/bin/python -m scripts.trivia_reaction.buffer_api orgs

    # List connected channels and cache TikTok/IG ids:
    .venv/bin/python -m scripts.trivia_reaction.buffer_api channels

    # Dump the CreatePostInput type + scheduling enums (schema sanity check):
    .venv/bin/python -m scripts.trivia_reaction.buffer_api introspect

Tokens live in .secrets/buffer-api.json (gitignored). Channel ids are cached to
.secrets/buffer-channels.json so buffer_push.py needn't re-query every run.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SECRETS_DIR = REPO / ".secrets"
TOKEN_PATH = SECRETS_DIR / "buffer-api.json"
CHANNELS_CACHE = SECRETS_DIR / "buffer-channels.json"

API_URL = "https://api.buffer.com"

# Buffer's `service` strings for the channels we post to. Matched
# case-insensitively against the channel list.
SERVICE_TIKTOK = "tiktok"
SERVICE_INSTAGRAM = "instagram"

# Load .env so BUFFER_ACCESS_TOKEN is available if used.
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(REPO / ".env")
except ImportError:
    pass
import os  # noqa: E402  (after load_dotenv so values are present)


# --- token -----------------------------------------------------------------
def load_token() -> str:
    """Return the Buffer access token from .secrets/buffer-api.json or env."""
    env = os.environ.get("BUFFER_ACCESS_TOKEN")
    if env:
        return env.strip()
    if TOKEN_PATH.exists():
        data = json.loads(TOKEN_PATH.read_text(encoding="utf-8"))
        tok = (data.get("access_token") or "").strip()
        if tok:
            return tok
    raise SystemExit(
        "✗ no Buffer token. Create a Personal Key (Buffer → Settings → API → "
        "Personal Keys) and store it:\n"
        f"    echo '{{\"access_token\": \"PASTE\"}}' > {TOKEN_PATH.relative_to(REPO)}\n"
        "  (or export BUFFER_ACCESS_TOKEN=...)"
    )


# --- low-level GraphQL -----------------------------------------------------
def gql(query: str, variables: dict | None = None, token: str | None = None) -> dict:
    """POST a GraphQL request; return the `data` object. Raises SystemExit on
    transport errors or top-level `errors`. Enum values must be passed inside
    `variables` (JSON strings the server coerces) so caption text with quotes,
    emoji, and newlines never has to be embedded in the query string."""
    token = token or load_token()
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        API_URL, data=body, method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            payload = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            raise SystemExit(
                f"✗ HTTP {e.code} from Buffer\n  response body: {raw or '(empty)'}"
            ) from e
    if payload.get("errors"):
        raise SystemExit(
            "✗ Buffer GraphQL error:\n"
            + json.dumps(payload["errors"], indent=2)
        )
    return payload.get("data") or {}


# --- discovery -------------------------------------------------------------
def get_organizations(token: str | None = None) -> list[dict]:
    data = gql(
        "query { account { organizations { id name } } }", token=token,
    )
    return ((data.get("account") or {}).get("organizations")) or []


def get_organization_id(token: str | None = None) -> str:
    orgs = get_organizations(token)
    if not orgs:
        raise SystemExit("✗ no organizations on this Buffer account.")
    # Single-org accounts are the norm here; if multi-org, take the first and
    # surface the choice in the log.
    if len(orgs) > 1:
        print(
            "  · multiple Buffer orgs; using the first: "
            + ", ".join(f"{o['name']}({o['id']})" for o in orgs),
            file=sys.stderr,
        )
    return orgs[0]["id"]


def list_channels(org_id: str | None = None, token: str | None = None) -> list[dict]:
    org_id = org_id or get_organization_id(token)
    data = gql(
        "query($input: ChannelsInput!) { "
        "channels(input: $input) { id name service displayName } }",
        {"input": {"organizationId": org_id}},
        token=token,
    )
    return data.get("channels") or []


def resolve_channels(
    services: list[str] | None = None,
    org_id: str | None = None,
    token: str | None = None,
    refresh: bool = False,
) -> dict[str, dict]:
    """Map service name → channel dict for the requested services. Caches the
    full channel list to .secrets/buffer-channels.json. Pass refresh=True to
    re-query."""
    services = [s.lower() for s in (services or [SERVICE_TIKTOK, SERVICE_INSTAGRAM])]
    channels: list[dict] | None = None
    if not refresh and CHANNELS_CACHE.exists():
        try:
            channels = json.loads(CHANNELS_CACHE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            channels = None
    if channels is None:
        channels = list_channels(org_id=org_id, token=token)
        SECRETS_DIR.mkdir(parents=True, exist_ok=True)
        CHANNELS_CACHE.write_text(json.dumps(channels, indent=2), encoding="utf-8")
    out: dict[str, dict] = {}
    for svc in services:
        match = next(
            (c for c in channels if (c.get("service") or "").lower() == svc),
            None,
        )
        if match:
            out[svc] = match
    return out


# --- posting ---------------------------------------------------------------
CREATE_POST = """
mutation CreatePost($input: CreatePostInput!) {
  createPost(input: $input) {
    ... on PostActionSuccess { post { id status dueAt } }
    ... on MutationError { message }
  }
}
""".strip()


def create_video_post(
    channel_id: str,
    text: str,
    video_url: str,
    service: str | None = None,
    due_at: str | None = None,
    thumbnail_url: str | None = None,
    draft: bool = False,
    scheduling_type: str = "automatic",
    token: str | None = None,
) -> dict:
    """Create a single video post on one channel.

    service: the channel's Buffer service ("instagram", "tiktok", …). Instagram
    requires a post type — we post these vertical reels as `reel` and also share
    them to the main feed. Other services don't need extra metadata.

    scheduling_type: 'automatic' = Buffer publishes directly at the scheduled
    time. 'notification' = Buffer pushes a phone reminder and you finish the
    post in the native app (the only path where TikTok's in-app auto-captions
    can be added — Direct Post never adds them).

    due_at: ISO-8601 UTC (e.g. "2026-06-25T15:00:00.000Z"). When given, the
    post is scheduled for that time (mode=customScheduled). When None, the post
    is added to the channel's next available queue slot (mode=addToQueue).

    draft: when True the post is saved as a Buffer draft — it will NOT
    auto-publish; you approve/send it from the Buffer app. Safe for testing.

    Returns {"ok": bool, "post": {...}} or {"ok": False, "error": "..."}.
    """
    video: dict = {"url": video_url}
    if thumbnail_url:
        video["thumbnailUrl"] = thumbnail_url
    post_input: dict = {
        "channelId": channel_id,
        "text": text,
        "assets": [{"video": video}],
        # Required by the schema. 'automatic' = Buffer direct-publishes;
        # 'notification' = Buffer reminds you to finish the post in-app.
        "schedulingType": scheduling_type,
    }
    if due_at:
        post_input["mode"] = "customScheduled"
        post_input["dueAt"] = due_at
    else:
        post_input["mode"] = "addToQueue"
    if draft:
        post_input["saveToDraft"] = True
    if (service or "").lower() == SERVICE_INSTAGRAM:
        # IG requires an explicit post type; vertical reels go out as `reel` and
        # shouldShareToFeed surfaces them in the main feed too.
        post_input["metadata"] = {
            "instagram": {"type": "reel", "shouldShareToFeed": True},
        }

    data = gql(CREATE_POST, {"input": post_input}, token=token)
    result = data.get("createPost") or {}
    if result.get("message"):  # MutationError branch
        return {"ok": False, "error": result["message"]}
    return {"ok": True, "post": result.get("post") or {}}


# --- introspection (schema sanity check) -----------------------------------
def introspect_create_post(token: str | None = None) -> dict:
    """Return the field/enum shape of CreatePostInput so we can verify the
    exact argument names + scheduling enum values against the live schema."""
    q = """
    query {
      input: __type(name: "CreatePostInput") {
        inputFields { name type { name kind ofType { name kind } } }
      }
      modes: __type(name: "ShareMode") { enumValues { name } }
      sched: __type(name: "SchedulingType") { enumValues { name } }
    }
    """
    return gql(q, token=token)


# --- CLI -------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("orgs", help="list organizations (verifies the token)")
    pc = sub.add_parser("channels", help="list + cache connected channels")
    pc.add_argument("--refresh", action="store_true", help="re-query, ignore cache")
    sub.add_parser("introspect", help="dump CreatePostInput fields + scheduling enums")
    args = ap.parse_args()

    if args.cmd == "orgs":
        for o in get_organizations():
            print(f"  {o['id']}  {o['name']}")
        return 0
    if args.cmd == "channels":
        chans = resolve_channels(
            services=[SERVICE_TIKTOK, SERVICE_INSTAGRAM], refresh=args.refresh,
        )
        # Show the full list too, so missing/misnamed services are obvious.
        org_id = get_organization_id()
        for c in list_channels(org_id=org_id):
            marker = "→" if (c.get("service") or "").lower() in chans else " "
            print(f" {marker} {c.get('service'):12} {c.get('id'):26} "
                  f"{c.get('name')} ({c.get('displayName')})")
        missing = [s for s in (SERVICE_TIKTOK, SERVICE_INSTAGRAM) if s not in chans]
        if missing:
            print(f"\n  ⚠ not connected in Buffer: {', '.join(missing)}", file=sys.stderr)
        else:
            print(f"\n  ✓ cached → {CHANNELS_CACHE.relative_to(REPO)}", file=sys.stderr)
        return 0
    if args.cmd == "introspect":
        print(json.dumps(introspect_create_post(), indent=2))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
