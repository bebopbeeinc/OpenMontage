#!/usr/bin/env python
"""OAuth 2.1 + JSON-RPC client for OpenArt's remote MCP server.

This is the transport the OpenArt drivers run on. It replaces the Playwright
browser session with the same API surface the `openart` MCP server exposes to
agents, so a headless launchd process can call it with no Chromium and no DOM
selectors.

Auth is OAuth 2.1 against https://openart.ai, which supports dynamic client
registration and PKCE for public clients (no client secret to manage):

    registration_endpoint  .../suite/api/auth/oauth/register
    token_endpoint_auth    none          (public client)
    code_challenge_methods S256
    grant_types            authorization_code, refresh_token

One interactive consent per machine (`--login`, opens a browser and catches the
redirect on a loopback port), then the refresh token on disk keeps every later
run silent. This mirrors how `.playwright/openart-state.json` used to work, but
a refresh token survives far longer than a scraped cookie jar.

Token store: .openart/mcp-token.json (chmod 600, gitignored).

CLI:
    python scripts/common/openart_mcp.py --login      # one-time consent
    python scripts/common/openart_mcp.py --status     # who am I / credits
    python scripts/common/openart_mcp.py --call openart_workspace_list
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import socket
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Optional

import requests

REPO = Path(__file__).resolve().parents[2]

MCP_URL = "https://mcp.openart.ai/mcp"
# RFC 8707 resource indicator. The AS binds the token to this exact resource;
# omitting it gets a token the MCP server rejects.
MCP_RESOURCE = MCP_URL
ISSUER = "https://openart.ai"
SCOPE = "full_access"

TOKEN_FILE = REPO / ".openart" / "mcp-token.json"

# Fixed loopback port so the registered redirect_uri stays stable across
# re-auth. If it's busy we register a fresh client on a free port instead.
DEFAULT_CALLBACK_PORT = 47823
CALLBACK_PATH = "/callback"

CLIENT_NAME = "OpenMontage"
HTTP_TIMEOUT_S = 60
LOGIN_TIMEOUT_S = 300
# Long-poll tools (openart_creation_wait) hold the connection open for as long
# as their own timeoutSeconds. The transport timeout must clear that, or the
# client hangs up while the server is still legitimately waiting — so callers
# pass an explicit read_timeout and this is the slack added on top.
LONG_POLL_MARGIN_S = 30


class OpenArtMCPError(RuntimeError):
    """The MCP server returned an error, or the transport failed."""


class OpenArtAuthError(OpenArtMCPError):
    """No usable token and no way to get one without a human.

    Raised in headless contexts (the launchd trivia server, cron) where the
    refresh token is missing or rejected. The fix is always the same: run
    `python scripts/common/openart_mcp.py --login` once as the desktop user.
    """


# --------------------------------------------------------------------------
# Token store
# --------------------------------------------------------------------------

def _read_tokens() -> dict:
    try:
        return json.loads(TOKEN_FILE.read_text())
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as e:
        raise OpenArtAuthError(f"{TOKEN_FILE} is corrupt ({e}); re-run --login") from e


def _write_tokens(tok: dict) -> None:
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Write then chmod before the secret is readable by anyone else.
    fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(tok, fh, indent=2)


# --------------------------------------------------------------------------
# OAuth
# --------------------------------------------------------------------------

_AS_META: Optional[dict] = None


def _as_metadata() -> dict:
    """Authorization-server metadata, fetched once per process."""
    global _AS_META
    if _AS_META is None:
        r = requests.get(
            f"{ISSUER}/.well-known/oauth-authorization-server", timeout=HTTP_TIMEOUT_S,
        )
        r.raise_for_status()
        _AS_META = r.json()
    return _AS_META


def _register_client(redirect_uri: str) -> str:
    meta = _as_metadata()
    r = requests.post(
        meta["registration_endpoint"],
        json={
            "client_name": CLIENT_NAME,
            "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "scope": SCOPE,
        },
        timeout=HTTP_TIMEOUT_S,
    )
    if not r.ok:
        raise OpenArtAuthError(f"client registration failed: {r.status_code} {r.text[:300]}")
    client_id = r.json().get("client_id")
    if not client_id:
        raise OpenArtAuthError(f"registration returned no client_id: {r.text[:300]}")
    return client_id


def _port_free(port: int) -> bool:
    """True when both loopback stacks can take this port."""
    for family, addr in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        with socket.socket(family, socket.SOCK_STREAM) as s:
            try:
                s.bind((addr, port))
            except OSError:
                return False
    return True


def _free_port(preferred: int) -> int:
    if _port_free(preferred):
        return preferred
    for _ in range(50):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        if _port_free(port):
            return port
    raise OpenArtAuthError("could not find a loopback port free on both IPv4 and IPv6")


class _V6Server(HTTPServer):
    address_family = socket.AF_INET6


def _serve_callback(port: int) -> list[HTTPServer]:
    """Listen on the callback port on both loopback stacks.

    OpenArt normalizes the redirect_uri host to `localhost`, which resolves to
    ::1 before 127.0.0.1 on macOS — an IPv4-only listener never sees the
    redirect and the login hangs until it times out. Two loopback listeners
    cover both without exposing the port off-box the way binding :: would.
    """
    servers: list[HTTPServer] = []
    for cls, addr in ((HTTPServer, "127.0.0.1"), (_V6Server, "::1")):
        try:
            servers.append(cls((addr, port), _CallbackHandler))
        except OSError:
            continue  # one stack is enough if the other is unavailable
    if not servers:
        raise OpenArtAuthError(f"could not listen on loopback port {port}")
    for server in servers:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    return servers


class _CallbackHandler(BaseHTTPRequestHandler):
    """Catches the one redirect and hands the code back to the main thread."""

    result: dict = {}

    def do_GET(self):  # noqa: N802 - stdlib naming
        parsed = urllib.parse.urlparse(self.path)
        params = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        # Browsers also fetch /favicon.ico and sometimes /. Only the callback
        # path carries the grant, and the first answer wins — otherwise a
        # favicon request landing a moment later wipes the code we just got.
        if parsed.path.rstrip("/") == CALLBACK_PATH and not type(self).result:
            type(self).result = params
        ok = "code" in params
        body = (
            b"<h2>OpenArt connected.</h2><p>You can close this tab.</p>"
            if ok else
            b"<h2>Authorization failed.</h2><p>Check the terminal.</p>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):  # silence the default stderr spam
        pass


def login(open_browser: bool = True) -> dict:
    """Run the interactive PKCE flow and persist the tokens. Returns the store."""
    port = _free_port(DEFAULT_CALLBACK_PORT)
    # `localhost`, not 127.0.0.1: OpenArt rewrites the host to localhost, and
    # the token exchange must send back the same value the authorize used.
    redirect_uri = f"http://localhost:{port}{CALLBACK_PATH}"

    stored = _read_tokens()
    # Reuse the registered client only if it was registered for this same port.
    client_id = stored.get("client_id") if stored.get("redirect_uri") == redirect_uri else None
    if not client_id:
        client_id = _register_client(redirect_uri)

    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).decode().rstrip("=")
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest(),
    ).decode().rstrip("=")
    state = secrets.token_urlsafe(24)

    meta = _as_metadata()
    auth_url = meta["authorization_endpoint"] + "?" + urllib.parse.urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": SCOPE,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "resource": MCP_RESOURCE,
    })

    _CallbackHandler.result = {}
    servers = _serve_callback(port)

    print(f"Opening OpenArt consent in your browser:\n  {auth_url}\n", file=sys.stderr)
    if open_browser:
        webbrowser.open(auth_url)

    deadline = time.time() + LOGIN_TIMEOUT_S
    while not _CallbackHandler.result and time.time() < deadline:
        time.sleep(0.25)
    for server in servers:
        server.shutdown()

    result = _CallbackHandler.result
    if not result:
        raise OpenArtAuthError(f"no redirect received within {LOGIN_TIMEOUT_S}s")
    if "code" not in result:
        raise OpenArtAuthError(
            f"authorization denied: {result.get('error', '?')} "
            f"{result.get('error_description', '')}".strip(),
        )
    if result.get("state") != state:
        raise OpenArtAuthError("state mismatch on the OAuth redirect — aborting")

    r = requests.post(
        meta["token_endpoint"],
        data={
            "grant_type": "authorization_code",
            "code": result["code"],
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": verifier,
            "resource": MCP_RESOURCE,
        },
        timeout=HTTP_TIMEOUT_S,
    )
    if not r.ok:
        raise OpenArtAuthError(f"token exchange failed: {r.status_code} {r.text[:300]}")

    tok = _store_token_response(r.json(), client_id, redirect_uri)
    print("OpenArt MCP connected. Token stored at "
          f"{TOKEN_FILE.relative_to(REPO)}", file=sys.stderr)
    return tok


def _store_token_response(payload: dict, client_id: str, redirect_uri: str) -> dict:
    prior = _read_tokens()
    tok = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "access_token": payload["access_token"],
        # A refresh response may omit refresh_token, meaning "keep the old one".
        "refresh_token": payload.get("refresh_token") or prior.get("refresh_token"),
        "expires_at": time.time() + float(payload.get("expires_in", 3600)),
    }
    _write_tokens(tok)
    return tok


def _refresh(tok: dict) -> dict:
    if not tok.get("refresh_token"):
        raise OpenArtAuthError(
            "no refresh token stored — run:\n"
            "  python scripts/common/openart_mcp.py --login",
        )
    meta = _as_metadata()
    r = requests.post(
        meta["token_endpoint"],
        data={
            "grant_type": "refresh_token",
            "refresh_token": tok["refresh_token"],
            "client_id": tok["client_id"],
            "resource": MCP_RESOURCE,
        },
        timeout=HTTP_TIMEOUT_S,
    )
    if not r.ok:
        raise OpenArtAuthError(
            f"refresh failed ({r.status_code}) — the grant was probably revoked. Run:\n"
            "  python scripts/common/openart_mcp.py --login",
        )
    return _store_token_response(r.json(), tok["client_id"], tok["redirect_uri"])


def access_token(force_refresh: bool = False) -> str:
    """A valid bearer token, refreshing 60s before expiry."""
    tok = _read_tokens()
    if not tok.get("access_token"):
        raise OpenArtAuthError(
            "OpenArt MCP is not connected on this machine. Run once:\n"
            "  python scripts/common/openart_mcp.py --login",
        )
    if force_refresh or time.time() >= tok.get("expires_at", 0) - 60:
        tok = _refresh(tok)
    return tok["access_token"]


# --------------------------------------------------------------------------
# JSON-RPC over streamable HTTP
# --------------------------------------------------------------------------

PROTOCOL_VERSION = "2025-06-18"


def _parse_response(resp: requests.Response, want_id: int) -> dict:
    """Pull one JSON-RPC response out of either a JSON or SSE body."""
    ctype = resp.headers.get("Content-Type", "")
    if "text/event-stream" in ctype:
        for line in resp.text.splitlines():
            if not line.startswith("data:"):
                continue
            try:
                msg = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            if msg.get("id") == want_id:
                return msg
        raise OpenArtMCPError(f"no JSON-RPC response for id={want_id} in SSE stream")
    try:
        return resp.json()
    except json.JSONDecodeError as e:
        raise OpenArtMCPError(f"non-JSON response: {resp.text[:300]}") from e


class MCPSession:
    """One initialized MCP session. Thread-safe via a single lock."""

    def __init__(self) -> None:
        self._http = requests.Session()
        self._session_id: Optional[str] = None
        self._next_id = 0
        self._lock = threading.Lock()

    def _headers(self, token: str) -> dict:
        h = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if self._session_id:
            h["Mcp-Session-Id"] = self._session_id
        return h

    def _post(self, payload: dict, token: str,
              read_timeout: Optional[float] = None) -> requests.Response:
        return self._http.post(
            MCP_URL, json=payload, headers=self._headers(token),
            timeout=read_timeout or HTTP_TIMEOUT_S,
        )

    def _initialize(self, token: str) -> None:
        self._next_id += 1
        rid = self._next_id
        resp = self._post({
            "jsonrpc": "2.0",
            "id": rid,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": CLIENT_NAME, "version": "1.0"},
            },
        }, token)
        if resp.status_code == 401:
            raise _Unauthorized()
        resp.raise_for_status()
        _parse_response(resp, rid)
        self._session_id = resp.headers.get("Mcp-Session-Id") or self._session_id
        # The spec requires this notification before any tools/call.
        self._http.post(
            MCP_URL,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers=self._headers(token),
            timeout=HTTP_TIMEOUT_S,
        )

    def call(self, name: str, arguments: Optional[dict] = None,
             read_timeout: Optional[float] = None) -> Any:
        """Call an MCP tool and return its parsed JSON payload.

        The OpenArt tools all answer with a single JSON text block, so we parse
        it rather than handing back the MCP content envelope.

        `read_timeout` must exceed the server-side wait for any long-polling
        tool; see LONG_POLL_MARGIN_S.
        """
        with self._lock:
            return self._call_locked(name, arguments or {}, read_timeout=read_timeout)

    def _call_locked(self, name: str, arguments: dict, _retried: bool = False,
                     read_timeout: Optional[float] = None) -> Any:
        try:
            token = access_token(force_refresh=_retried)
            if self._session_id is None:
                self._initialize(token)
            self._next_id += 1
            rid = self._next_id
            resp = self._post({
                "jsonrpc": "2.0",
                "id": rid,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }, token, read_timeout=read_timeout)
            if resp.status_code == 401:
                raise _Unauthorized()
            # An expired session id is recoverable: re-initialize and retry.
            if resp.status_code == 404 and self._session_id:
                self._session_id = None
                if not _retried:
                    return self._call_locked(name, arguments, _retried=True,
                                             read_timeout=read_timeout)
            resp.raise_for_status()
            msg = _parse_response(resp, rid)
        except _Unauthorized:
            if _retried:
                raise OpenArtAuthError(
                    "OpenArt rejected the token even after a refresh. Run:\n"
                    "  python scripts/common/openart_mcp.py --login",
                ) from None
            self._session_id = None
            return self._call_locked(name, arguments, _retried=True,
                                     read_timeout=read_timeout)

        if "error" in msg:
            err = msg["error"]
            raise OpenArtMCPError(
                f"{name}: {err.get('message', err)} ({err.get('code', '?')})",
            )
        return _unwrap(name, msg.get("result", {}))


class _Unauthorized(Exception):
    """Internal: the MCP server answered 401."""


def _unwrap(name: str, result: dict) -> Any:
    """Turn an MCP tool result into the payload the caller wants."""
    if result.get("isError"):
        raise OpenArtMCPError(f"{name}: {_result_text(result) or 'tool reported an error'}")
    if isinstance(result.get("structuredContent"), dict):
        return result["structuredContent"]
    text = _result_text(result)
    if text is None:
        return result
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"text": text}


def _result_text(result: dict) -> Optional[str]:
    parts = [
        block.get("text", "")
        for block in result.get("content", [])
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    return "\n".join(parts) if parts else None


_SESSION: Optional[MCPSession] = None
_SESSION_LOCK = threading.Lock()


def session() -> MCPSession:
    """The process-wide MCP session."""
    global _SESSION
    with _SESSION_LOCK:
        if _SESSION is None:
            _SESSION = MCPSession()
        return _SESSION


def call_tool(name: str, arguments: Optional[dict] = None,
              read_timeout: Optional[float] = None) -> Any:
    """Call one OpenArt MCP tool. The single entry point the drivers use."""
    return session().call(name, arguments, read_timeout=read_timeout)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--login", action="store_true", help="run the one-time OAuth consent")
    ap.add_argument("--no-browser", action="store_true", help="print the URL instead of opening it")
    ap.add_argument("--status", action="store_true", help="show the connected account and credits")
    ap.add_argument("--call", metavar="TOOL", help="call one MCP tool and print its JSON")
    ap.add_argument("--args", default="{}", help="JSON arguments for --call")
    args = ap.parse_args()

    if args.login:
        login(open_browser=not args.no_browser)
        return 0
    if args.status:
        print(json.dumps(call_tool("openart_account_get"), indent=2))
        return 0
    if args.call:
        print(json.dumps(call_tool(args.call, json.loads(args.args)), indent=2))
        return 0
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(_main())
