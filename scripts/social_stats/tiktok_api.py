"""TikTok analytics via the OFFICIAL Display API (Login Kit OAuth) — no scraping.

This is the sanctioned path: you authorize a TikTok developer app once via the
real TikTok OAuth screen, we store the resulting tokens, and from then on we
call documented JSON endpoints. No browser automation, no fingerprinting, no
login throttle — the access TikTok actually intends for reading your own data.

Prerequisites (one-time, in the browser — see the walkthrough):
    1. Create an app at https://developers.tiktok.com/, choose the DESKTOP
       platform, and add Login Kit with scopes:
       user.info.basic, user.info.stats, video.list
    2. Register the redirect URI EXACTLY (trailing slash matters):
           http://localhost:8723/callback/
    3. Create a Sandbox and add your TikTok account as a target user (so you
       can use it without full app review).
    4. Put the credentials in .env:
           TIKTOK_CLIENT_KEY=...
           TIKTOK_CLIENT_SECRET=...

Desktop apps require PKCE; TikTok uses a hex-encoded SHA256 challenge (not the
usual base64url) — handled below.

Usage:
    # One-time: open TikTok's OAuth screen, authorize, store tokens.
    .venv/bin/python -m scripts.social_stats.tiktok_api auth --account dailytrivia

    # Pull stats (auto-refreshes the access token when stale):
    .venv/bin/python -m scripts.social_stats.tiktok_api fetch --account dailytrivia

Tokens live in .secrets/tiktok-api-<account>.json (gitignored — they're
credentials). Outputs land in scripts/social_stats/out/<account>/.
"""
from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import secrets
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SECRETS_DIR = REPO / ".secrets"
OUT_ROOT = Path(__file__).resolve().parent / "out"

# Load .env so TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET are available.
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(REPO / ".env")
except ImportError:
    pass
import os  # noqa: E402  (after load_dotenv so values are present)

# --- OAuth + API endpoints (Display API v2) --------------------------------
AUTHORIZE_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
USERINFO_URL = "https://open.tiktokapis.com/v2/user/info/"
VIDEOLIST_URL = "https://open.tiktokapis.com/v2/video/list/"

SCOPES = "user.info.basic,user.info.stats,video.list"

# Must match the redirect URI registered in the TikTok app settings EXACTLY,
# including the trailing slash. TikTok requires https in general, but DESKTOP
# apps are explicitly allowed http localhost / loopback — so register the app
# as a "Desktop" platform and add exactly this URI.
REDIRECT_PORT = 8723
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/callback/"

USER_FIELDS = [
    "open_id", "union_id", "display_name", "follower_count",
    "following_count", "likes_count", "video_count",
]
VIDEO_FIELDS = [
    "id", "title", "create_time", "view_count", "like_count",
    "comment_count", "share_count",
]


def _slug(s: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in s.strip())
    return safe.strip("-") or "default"


def _token_path(account: str) -> Path:
    return SECRETS_DIR / f"tiktok-api-{_slug(account)}.json"


def _out_dir(account: str) -> Path:
    return OUT_ROOT / _slug(account)


def _creds() -> tuple[str, str]:
    key = os.environ.get("TIKTOK_CLIENT_KEY")
    secret = os.environ.get("TIKTOK_CLIENT_SECRET")
    if not key or not secret:
        raise SystemExit(
            "✗ TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET not set. Add them to .env "
            "(get them from your app at https://developers.tiktok.com/)."
        )
    return key, secret


# --- low-level HTTP --------------------------------------------------------
def _post_form(url: str, data: dict) -> dict:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def _get_json(url: str, token: str, params: dict) -> dict:
    full = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(full, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def _post_json(url: str, token: str, params: dict, body: dict) -> dict:
    full = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        full, data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


# --- token storage / refresh ----------------------------------------------
def _save_tokens(account: str, tok: dict) -> None:
    SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    # Stamp an absolute expiry so fetch knows when to refresh.
    tok = dict(tok)
    if "expires_in" in tok:
        tok["_access_expires_at"] = int(time.time()) + int(tok["expires_in"]) - 60
    _token_path(account).write_text(json.dumps(tok, indent=2), encoding="utf-8")


def _load_tokens(account: str) -> dict | None:
    p = _token_path(account)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _valid_access_token(account: str) -> str:
    """Return a live access token, refreshing it if expired."""
    tok = _load_tokens(account)
    if not tok:
        raise SystemExit(f"✗ no tokens for '{account}'. Run `auth --account {account}` first.")
    if int(tok.get("_access_expires_at", 0)) > int(time.time()):
        return tok["access_token"]
    # Refresh.
    key, secret = _creds()
    print("  · access token expired — refreshing", file=sys.stderr)
    resp = _post_form(TOKEN_URL, {
        "client_key": key,
        "client_secret": secret,
        "grant_type": "refresh_token",
        "refresh_token": tok["refresh_token"],
    })
    if "access_token" not in resp:
        raise SystemExit(f"✗ refresh failed: {json.dumps(resp)}\n  Re-run `auth --account {account}`.")
    # Carry the (possibly rotated) refresh token forward.
    _save_tokens(account, resp)
    return resp["access_token"]


# --- subcommand: auth ------------------------------------------------------
class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    captured: dict = {}

    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.rstrip("/") != "/callback":
            self.send_response(404); self.end_headers(); return
        _CallbackHandler.captured = dict(urllib.parse.parse_qsl(parsed.query))
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<h2>TikTok authorization received. You can close this tab.</h2>")

    def log_message(self, *a):  # silence the default stderr logging
        pass


def cmd_auth(account: str) -> int:
    key, secret = _creds()
    state = secrets.token_urlsafe(16)
    # PKCE — desktop apps require it. TikTok's challenge is HEX-encoded SHA256
    # of the verifier (not the standard base64url), with method "S256".
    code_verifier = secrets.token_urlsafe(64)  # 43-128 unreserved chars
    code_challenge = hashlib.sha256(code_verifier.encode()).hexdigest()
    auth_url = AUTHORIZE_URL + "?" + urllib.parse.urlencode({
        "client_key": key,
        "scope": SCOPES,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    })

    server = http.server.HTTPServer(("localhost", REDIRECT_PORT), _CallbackHandler)
    threading.Thread(target=server.handle_request, daemon=True).start()

    print(f"\n  Opening TikTok's authorization page for '{account}'.", file=sys.stderr)
    print(f"  Log in as the account you want to tag '{account}' and approve.", file=sys.stderr)
    print(f"  If the browser doesn't open, paste this URL:\n\n  {auth_url}\n", file=sys.stderr)
    webbrowser.open(auth_url)

    # Wait for the callback (up to 5 min).
    deadline = time.time() + 300
    while not _CallbackHandler.captured and time.time() < deadline:
        time.sleep(1)
    server.server_close()
    cap = _CallbackHandler.captured
    if not cap:
        print("✗ no callback received (timed out).", file=sys.stderr)
        return 1
    if cap.get("state") != state:
        print("✗ state mismatch — aborting (possible CSRF).", file=sys.stderr)
        return 1
    if "code" not in cap:
        print(f"✗ authorization failed: {cap}", file=sys.stderr)
        return 1

    print("  · exchanging authorization code for tokens", file=sys.stderr)
    resp = _post_form(TOKEN_URL, {
        "client_key": key,
        "client_secret": secret,
        "code": cap["code"],
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI,
        "code_verifier": code_verifier,
    })
    if "access_token" not in resp:
        print(f"✗ token exchange failed: {json.dumps(resp, indent=2)}", file=sys.stderr)
        return 1
    _save_tokens(account, resp)
    print(f"✓ authorized — tokens saved to {_token_path(account)}", file=sys.stderr)
    return 0


# --- subcommand: fetch -----------------------------------------------------
def cmd_fetch(account: str, max_videos: int) -> int:
    token = _valid_access_token(account)
    out_dir = _out_dir(account)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n=== account '{account}' (Display API) ===", file=sys.stderr)
    user = _get_json(USERINFO_URL, token, {"fields": ",".join(USER_FIELDS)})
    udata = (user.get("data") or {}).get("user", {})
    if not udata:
        print(f"✗ user/info returned no data: {json.dumps(user, indent=2)}", file=sys.stderr)
        return 2

    videos = _post_json(
        VIDEOLIST_URL, token,
        {"fields": ",".join(VIDEO_FIELDS)},
        {"max_count": max(1, min(max_videos, 20))},
    )
    vlist = (videos.get("data") or {}).get("videos", [])

    result = {
        "fetched_at": int(time.time()),
        "account": account,
        "profile": udata,
        "videos": vlist,
    }
    out_json = out_dir / "tiktok_api_stats.json"
    out_json.write_text(json.dumps(result, indent=2), encoding="utf-8")

    # Human-readable summary to stdout.
    print(json.dumps({
        "display_name": udata.get("display_name"),
        "followers": udata.get("follower_count"),
        "likes": udata.get("likes_count"),
        "videos": udata.get("video_count"),
        "recent_pulled": len(vlist),
    }, indent=2))
    print(f"  full data saved → {out_json}", file=sys.stderr)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("auth", help="one-time OAuth: authorize and store tokens")
    a.add_argument("--account", default="default", help="account tag for these tokens")

    f = sub.add_parser("fetch", help="pull profile + recent video stats (refreshes token as needed)")
    f.add_argument("--account", default="default", help="which account's tokens to use")
    f.add_argument("--max-videos", type=int, default=20, help="recent videos to pull (1-20, API cap is 20/page)")

    args = ap.parse_args()
    if args.cmd == "auth":
        return cmd_auth(account=args.account)
    if args.cmd == "fetch":
        return cmd_fetch(account=args.account, max_videos=args.max_videos)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
