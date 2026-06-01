"""TikTok analytics via persisted browser session (Playwright) — PROTOTYPE.

This is the "session-reuse + native CSV export" approach: you log in ONCE
manually in a real browser window, the session is saved, and every later run
reuses it so we never touch TikTok's login flow (which is where the bot
detection lives). It targets TikTok Studio's analytics page and prefers the
built-in "Download data" CSV export over scraping numbers off the DOM.

This is a prototype, not a hardened tool. TikTok Studio's DOM is obfuscated
and changes without notice, so selectors are best-effort and the script saves
debug artifacts (screenshot + HTML) whenever it can't find what it expects.
That's by design — when it breaks, you re-run with --debug and refine.

Multiple accounts: each account is its own saved session. Tag every command
with --account <name>; omit it for the "default" account. One browser app,
many sessions — no extra setup per account.

Usage:
    # 1. One-time login per account (open a window, log in by hand, save session).
    .venv/bin/python -m scripts.social_stats.tiktok_stats login --account brandA
    .venv/bin/python -m scripts.social_stats.tiktok_stats login --account brandB

    # 2. Pull analytics for one account (reuses its session, headed so you watch):
    .venv/bin/python -m scripts.social_stats.tiktok_stats fetch --account brandA

    # …or sweep every account you've logged in (headless is sensible here):
    .venv/bin/python -m scripts.social_stats.tiktok_stats fetch-all --headless

    # List which accounts have a saved session:
    .venv/bin/python -m scripts.social_stats.tiktok_stats accounts

    # Dump HTML/screenshot for selector work when something drifts:
    .venv/bin/python -m scripts.social_stats.tiktok_stats fetch --account brandA --debug

Browser: defaults to Brave. Use Chrome (or bundled Chromium) per command with
--browser, or change the default via $TIKTOK_BROWSER in .env:
    .venv/bin/python -m scripts.social_stats.tiktok_stats login --account brandA --browser chrome
Profiles are namespaced by browser, so log in and fetch with the SAME browser.
Brave note: lower Shields for tiktok.com if the dashboard misbehaves.

Sessions live in .playwright/tiktok-profile-<browser>-<account>/ — a persistent
real-browser profile per account (gitignored — they hold live auth). Outputs
land in scripts/social_stats/out/<account>/.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from playwright.sync_api import Page, Playwright, TimeoutError as PWTimeout, sync_playwright

REPO = Path(__file__).resolve().parents[2]
STATE_DIR = REPO / ".playwright"
OUT_ROOT = Path(__file__).resolve().parent / "out"

# Which browser to drive. "brave" (by binary), "chrome" (Playwright channel),
# or "chromium" (bundled). Default comes from $TIKTOK_BROWSER so you can change
# it once in .env; --browser overrides per command.
BROWSER_CHOICES = ("brave", "chrome", "chromium")
DEFAULT_BROWSER = (os.environ.get("TIKTOK_BROWSER") or "brave").lower()

# Brave isn't a Playwright channel, so we launch it by executable path. Override
# with $TIKTOK_BRAVE_PATH if yours lives somewhere non-standard.
BRAVE_PATHS = [
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",  # macOS
    "/usr/bin/brave-browser",  # Linux (deb/rpm)
    "/usr/bin/brave",
    "/snap/bin/brave",
]


def _slug(s: str) -> str:
    """Filesystem-safe tag (keeps multi-account/browser paths predictable)."""
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in s.strip())
    return safe.strip("-") or "default"


def _brave_path() -> str | None:
    env = os.environ.get("TIKTOK_BRAVE_PATH")
    if env:
        return env
    for p in BRAVE_PATHS:
        if Path(p).exists():
            return p
    return None


def _launch_target(browser: str) -> dict:
    """Map a browser choice to launch_persistent_context kwargs.

    Raises if 'brave' is requested but no binary is found — better a clear error
    than a silent fall-through to a different browser the user didn't pick.
    """
    browser = (browser or DEFAULT_BROWSER).lower()
    if browser == "brave":
        bp = _brave_path()
        if not bp:
            raise RuntimeError(
                "Brave requested but no binary found. Install Brave or set "
                "$TIKTOK_BRAVE_PATH to its executable."
            )
        return {"executable_path": bp}
    if browser == "chromium":
        return {}  # Playwright's bundled Chromium
    return {"channel": "chrome"}  # default: real installed Chrome


def _profile_dir(account: str, browser: str) -> Path:
    """Persistent user-data-dir for an (account, browser) pair.

    A real profile dir (not a JSON storage_state) keeps a consistent device
    fingerprint across runs, so the session looks like a returning device.
    Namespaced by browser too: a Chrome profile and a Brave profile are
    different engines, so mixing them would corrupt state / look signed-out.
    """
    return STATE_DIR / f"tiktok-profile-{_slug(browser)}-{_slug(account)}"


def _out_dir(account: str) -> Path:
    return OUT_ROOT / _slug(account)


def _has_profile(account: str, browser: str) -> bool:
    """A logged-in profile has been written to disk for this account+browser."""
    d = _profile_dir(account, browser)
    return d.exists() and any(d.iterdir())


def _known_profiles() -> list[tuple[str, str]]:
    """All saved (browser, account) pairs, parsed from profile dir names."""
    if not STATE_DIR.exists():
        return []
    out = []
    for d in sorted(STATE_DIR.glob("tiktok-profile-*")):
        if not d.is_dir():
            continue
        rest = d.name[len("tiktok-profile-"):]
        browser, _, account = rest.partition("-")
        if account:
            out.append((browser, account))
    return out

# TikTok Studio is the current creator analytics home (replaced creator-center).
# Override via --url if TikTok moves it again.
ANALYTICS_URL = "https://www.tiktok.com/tiktokstudio/analytics"
LOGIN_URL = "https://www.tiktok.com/login"

# How long to wait (headed) for the human to finish logging in.
LOGIN_TIMEOUT_S = 240


# ---------------------------------------------------------------------------
# Browser plumbing — mirrors scripts/common/openart_driver.py conventions.
# ---------------------------------------------------------------------------
@contextmanager
def _browser(p: Playwright, headless: bool, profile_dir: Path, browser: str = DEFAULT_BROWSER):
    """Persistent real-browser context for one account.

    Three fingerprint hardenings vs. a vanilla Playwright launch:
      1. A real consumer browser — Chrome (channel) or Brave (by binary) —
         so the engine fingerprint matches its own UA (no spoofed-UA mismatch).
         Falls back to bundled Chromium only if the requested browser fails.
      2. launch_persistent_context(user_data_dir=…) — a stable on-disk profile,
         so the device looks like a returning browser, not a fresh one each run.
      3. --disable-blink-features=AutomationControlled + an init script — removes
         navigator.webdriver, the loudest automation tell.

    We deliberately do NOT invent a user agent: a real browser already sends a
    truthful, internally-consistent UA (Brave reports a plain Chrome UA too).
    The one exception is handled in _open(): headless leaks a "HeadlessChrome"
    UA token (header + JS), so we rewrite just that token back to "Chrome" — a
    consistent edit that keeps the same real version.
    """
    profile_dir.mkdir(parents=True, exist_ok=True)
    launch_kwargs = dict(
        user_data_dir=str(profile_dir),
        headless=headless,
        accept_downloads=True,
        viewport={"width": 1440, "height": 900},
        args=["--disable-blink-features=AutomationControlled"],
    )
    target = _launch_target(browser)  # raises if brave requested but missing
    try:
        context = p.chromium.launch_persistent_context(**target, **launch_kwargs)
    except Exception as e:
        if not target:  # already bundled chromium — nothing to fall back to
            raise
        print(
            f"  · '{browser}' unavailable ({type(e).__name__}); using bundled Chromium",
            file=sys.stderr,
        )
        context = p.chromium.launch_persistent_context(**launch_kwargs)
    # Belt-and-suspenders: hide the residual webdriver flag before any page JS runs.
    context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    )
    try:
        yield context
    finally:
        # Persistent profile is flushed to disk on close — nothing else to save.
        context.close()


def _open(context) -> Page:
    """Get a page from the context and scrub the headless UA tell.

    A persistent context launches with one blank page; reuse it. If we're
    headless, Chrome stamps "HeadlessChrome" into both the User-Agent header
    and navigator.userAgent — a clear automation signal. We rewrite just that
    token to "Chrome" (same version) via CDP, which covers the HTTP header and
    JS for this page, so TikTok sees an ordinary Chrome UA.
    """
    page = context.pages[0] if context.pages else context.new_page()
    try:
        ua = page.evaluate("navigator.userAgent")
    except Exception:
        return page
    if "Headless" in ua:
        clean = ua.replace("HeadlessChrome", "Chrome")
        try:
            cdp = context.new_cdp_session(page)
            # Sets the UA on outgoing request headers AND navigator.userAgent.
            cdp.send("Network.setUserAgentOverride", {"userAgent": clean})
        except Exception as e:
            print(f"  · could not scrub headless UA ({type(e).__name__}); proceeding", file=sys.stderr)
    return page


def _has_auth_cookie(context) -> bool:
    """TikTok sets a non-empty `sessionid` cookie once you're logged in.

    Polling this (instead of re-navigating) lets us watch for login without
    reloading the page out from under the user mid-QR-scan / mid-form.
    """
    try:
        for c in context.cookies():
            if c.get("name") in ("sessionid", "sessionid_ss") and c.get("value"):
                return True
    except Exception:
        pass
    return False


def _looks_signed_out(page: Page) -> bool:
    """Heuristic: redirected to /login, or a visible 'Log in' affordance."""
    if "/login" in page.url or "/signup" in page.url:
        return True
    try:
        login_btn = page.get_by_role("button", name="Log in")
        if login_btn.count() and login_btn.first.is_visible():
            return True
        login_link = page.get_by_role("link", name="Log in")
        if login_link.count() and login_link.first.is_visible():
            return True
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# Subcommand: login
# ---------------------------------------------------------------------------
def cmd_login(account: str, browser: str) -> int:
    """Open a visible window, let the human log in, then save the session."""
    profile_dir = _profile_dir(account, browser)
    with sync_playwright() as p, _browser(p, headless=False, profile_dir=profile_dir, browser=browser) as ctx:
        page = _open(ctx)
        page.goto(LOGIN_URL, wait_until="domcontentloaded")
        print(
            f"\n  Logging in account '{account}' using {browser}.\n"
            f"  A browser window is open. Log into the TikTok account you want "
            f"to tag '{account}' (QR, Google, email…).\n"
            f"  Tip: use a fresh/incognito-like login here — this window starts "
            f"from {account}'s own saved session, so accounts stay separate.\n"
            f"  (Brave: if the page misbehaves, lower Shields for tiktok.com.)\n"
            f"  I'll wait up to {LOGIN_TIMEOUT_S}s and detect when you're in.\n",
            file=sys.stderr,
        )
        deadline = time.time() + LOGIN_TIMEOUT_S
        while time.time() < deadline:
            time.sleep(2)
            # Watch the auth cookie — do NOT re-navigate, or we'd reload the
            # login page/QR every cycle and make logging in impossible.
            if not _has_auth_cookie(ctx):
                continue
            # Cookie present → confirm by loading analytics once.
            try:
                page.goto(ANALYTICS_URL, wait_until="domcontentloaded")
            except PWTimeout:
                pass
            page.wait_for_timeout(2500)
            if not _looks_signed_out(page):
                # Persistent profile is flushed to disk on close.
                print(f"✓ login detected — profile saved to {profile_dir}", file=sys.stderr)
                return 0
        print("✗ login timed out — nothing saved. Re-run `login`.", file=sys.stderr)
        return 1


# ---------------------------------------------------------------------------
# Subcommand: fetch
# ---------------------------------------------------------------------------
# Numbers we try to read off the overview as a fallback when CSV export
# isn't reachable. Labels are matched case-insensitively against nearby text.
OVERVIEW_METRICS = ["Video views", "Profile views", "Likes", "Comments", "Shares", "Followers"]


def _save_debug(page: Page, tag: str, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    png = out_dir / f"debug_{tag}.png"
    html = out_dir / f"debug_{tag}.html"
    try:
        page.screenshot(path=str(png), full_page=True)
        html.write_text(page.content(), encoding="utf-8")
        print(f"  · debug artifacts: {png.name}, {html.name}", file=sys.stderr)
    except Exception as e:
        print(f"  · could not save debug artifacts: {e}", file=sys.stderr)


def _try_csv_export(page: Page, out_dir: Path) -> Path | None:
    """Click TikTok Studio's 'Download data' button and capture the CSV.

    Returns the saved path, or None if the button isn't found / nothing downloads.
    TikTok labels this variously ("Download data", "Download", "Export"); we
    try a few. This is the brittle part — refine via --debug HTML when it drifts.
    """
    candidates = [
        ("button", "Download data"),
        ("button", "Download"),
        ("button", "Export"),
        ("link", "Download data"),
    ]
    for role, name in candidates:
        try:
            loc = page.get_by_role(role, name=name)  # type: ignore[arg-type]
            if not loc.count() or not loc.first.is_visible():
                continue
            print(f"  · found export control: {role}='{name}'", file=sys.stderr)
            with page.expect_download(timeout=30_000) as dl_info:
                loc.first.click()
                # Some builds pop a confirm dialog with a second button.
                try:
                    page.get_by_role("button", name="Download").last.click(timeout=3_000)
                except Exception:
                    pass
            dl = dl_info.value
            out_dir.mkdir(parents=True, exist_ok=True)
            dest = out_dir / (dl.suggested_filename or "tiktok_analytics.csv")
            dl.save_as(str(dest))
            return dest
        except PWTimeout:
            print(f"  · '{name}' clicked but no download fired", file=sys.stderr)
            continue
        except Exception:
            continue
    return None


def _scrape_overview(page: Page) -> dict[str, str]:
    """Best-effort: read the big KPI numbers off the overview cards.

    Strategy: for each metric label, find the element containing that text and
    look at its container for the adjacent number. DOM-dependent and fragile —
    this is the fallback, not the happy path.
    """
    found: dict[str, str] = {}
    for label in OVERVIEW_METRICS:
        try:
            node = page.get_by_text(label, exact=False).first
            if not node.count():
                continue
            # Walk up to the card and grab its text blob, then pull the first
            # number-ish token that isn't the label itself.
            container_text = node.locator("xpath=ancestor::*[position()<=3]").last.inner_text(timeout=2_000)
            for line in container_text.splitlines():
                line = line.strip()
                if line and line.lower() != label.lower() and any(c.isdigit() for c in line):
                    found[label] = line
                    break
        except Exception:
            continue
    return found


def cmd_fetch(account: str, browser: str, headless: bool, url: str, debug: bool) -> int:
    profile_dir = _profile_dir(account, browser)
    out_dir = _out_dir(account)
    if not _has_profile(account, browser):
        print(
            f"✗ no saved {browser} profile for account '{account}' at {profile_dir} — "
            f"run `login --account {account} --browser {browser}` first.",
            file=sys.stderr,
        )
        return 1

    print(f"\n=== account '{account}' ({browser}) ===", file=sys.stderr)
    with sync_playwright() as p, _browser(p, headless=headless, profile_dir=profile_dir, browser=browser) as ctx:
        page = _open(ctx)
        print(f"→ opening {url}", file=sys.stderr)
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)  # let the SPA hydrate

        if _looks_signed_out(page):
            print(
                f"✗ session for '{account}' is stale / signed out. "
                f"Re-run `login --account {account} --browser {browser}`.",
                file=sys.stderr,
            )
            if debug:
                _save_debug(page, "signed_out", out_dir)
            return 2

        # Preferred path: native CSV export.
        csv_path = _try_csv_export(page, out_dir)
        if csv_path:
            print(f"✓ CSV exported → {csv_path}", file=sys.stderr)
            print(f"  ({csv_path.stat().st_size} bytes)", file=sys.stderr)
            if debug:
                _save_debug(page, "after_export", out_dir)
            return 0

        # Fallback: scrape the overview KPIs.
        print("  · no CSV export found — falling back to scraping overview KPIs", file=sys.stderr)
        kpis = _scrape_overview(page)
        _save_debug(page, "overview", out_dir)  # always dump so selectors can be refined
        if kpis:
            out_dir.mkdir(parents=True, exist_ok=True)
            out_json = out_dir / "overview_kpis.json"
            out_json.write_text(json.dumps(kpis, indent=2), encoding="utf-8")
            print("✓ scraped overview KPIs:", file=sys.stderr)
            print(json.dumps(kpis, indent=2))
            print(f"  saved → {out_json}", file=sys.stderr)
            return 0

        print(
            f"✗ found neither a CSV export nor recognizable KPI cards for '{account}'.\n"
            f"  Inspect the debug HTML in {out_dir} to refine selectors.",
            file=sys.stderr,
        )
        return 3


def cmd_fetch_all(headless: bool, url: str, debug: bool) -> int:
    """Run fetch across every saved profile, each with its own browser."""
    profiles = _known_profiles()
    if not profiles:
        print("✗ no saved sessions. Run `login --account <name>` first.", file=sys.stderr)
        return 1
    listing = ", ".join(f"{a} ({b})" for b, a in profiles)
    print(f"Sweeping {len(profiles)} profile(s): {listing}", file=sys.stderr)
    rc = 0
    for browser, acct in profiles:
        r = cmd_fetch(account=acct, browser=browser, headless=headless, url=url, debug=debug)
        rc = rc or r  # first non-zero wins, but keep going
    return rc


def cmd_accounts() -> int:
    """List accounts that currently have a saved session."""
    profiles = _known_profiles()
    if not profiles:
        print("No saved sessions yet. Run `login --account <name>`.", file=sys.stderr)
        return 0
    print("Saved TikTok sessions:")
    for browser, acct in profiles:
        print(f"  · {acct}  [{browser}]   ({_profile_dir(acct, browser)})")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    _br_help = f"browser to drive: {', '.join(BROWSER_CHOICES)} (default: {DEFAULT_BROWSER}, from $TIKTOK_BROWSER)"

    lg = sub.add_parser("login", help="open a window, log in by hand, save the session")
    lg.add_argument("--account", default="default", help="account tag for this session (default: 'default')")
    lg.add_argument("--browser", choices=BROWSER_CHOICES, default=DEFAULT_BROWSER, help=_br_help)

    f = sub.add_parser("fetch", help="reuse session, pull analytics (CSV export preferred)")
    f.add_argument("--account", default="default", help="which account's session to use (default: 'default')")
    f.add_argument("--browser", choices=BROWSER_CHOICES, default=DEFAULT_BROWSER, help=_br_help)
    f.add_argument("--headless", action="store_true", help="run without a visible window")
    f.add_argument("--url", default=ANALYTICS_URL, help="analytics page URL (override if TikTok moves it)")
    f.add_argument("--debug", action="store_true", help="always save screenshot + HTML for selector work")

    fa = sub.add_parser("fetch-all", help="run fetch across every saved profile (uses each one's browser)")
    fa.add_argument("--headless", action="store_true", help="run without a visible window")
    fa.add_argument("--url", default=ANALYTICS_URL, help="analytics page URL (override if TikTok moves it)")
    fa.add_argument("--debug", action="store_true", help="always save screenshot + HTML for selector work")

    sub.add_parser("accounts", help="list accounts that have a saved session")

    args = ap.parse_args()
    if args.cmd == "login":
        return cmd_login(account=args.account, browser=args.browser)
    if args.cmd == "fetch":
        return cmd_fetch(account=args.account, browser=args.browser, headless=args.headless, url=args.url, debug=args.debug)
    if args.cmd == "fetch-all":
        return cmd_fetch_all(headless=args.headless, url=args.url, debug=args.debug)
    if args.cmd == "accounts":
        return cmd_accounts()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
