#!/usr/bin/env python
"""Playwright driver for openart.ai image generation.

Drives the OpenArt Create-Image UI to generate one or more image variants from
a prompt with a chosen model, then downloads the result.

Mirrors `scripts/common/openart_driver.py` (the video driver) but targets the
image suite. Key differences:
  - URL base: /suite/create-image/<slug>
  - Variant count aria-label: "Increase/Decrease image count"
  - Gallery uses <img src="cdn.openart.ai/..."> instead of <video>
  - No duration slider, no audio toggle, no mode selector, no character picker

Auth: reuses the persistent storage state at .playwright/openart-state.json
created by the video driver. First run is headed and waits up to 5 minutes for
manual login.

Public API:
    generate_image(prompt, model, output_paths, headless=False, ...) -> list[Path]
      Raises OpenArtGenerationError when zero variants come back (e.g. a
      content-policy block); the message carries the failure reason.
      Raises OpenArtOutOfCreditsError when every candidate workspace is out of
      credits. Credits are per-workspace, so an exhausted workspace is retried
      in `fallback_workspaces` before that error surfaces.

Smoke test:
    python scripts/trivia_images/openart_image_driver.py \
      --prompt "a tiny mosquito on a podium with a giant crown, Pixar style" \
      --model "Nano Banana Pro" --out scripts/trivia_images/library/_smoketest.jpg
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from playwright.sync_api import (
    Page,
    Playwright,
    TimeoutError as PWTimeout,
    sync_playwright,
)

REPO = Path(__file__).resolve().parents[2]
STATE_FILE = REPO / ".playwright" / "openart-state.json"

OPENART_SUITE_BASE = "https://openart.ai/suite/create-image"

# The OpenArt account belongs to more than one team, and OpenArt persists the
# active team per session — it can silently swap the active workspace between
# runs. The trivia-images question/answer art is owned by the "BebopBee Art
# Team" workspace (NOT the personal "R N" team that owns the saved video
# characters), so we re-assert it on every run before generating. Set to ""
# to skip the switch and use whatever team is currently active.
OPENART_WORKSPACE = "BebopBee Art Team"

# Workspaces to fall back to, in order, when the primary one is out of credits.
# Each OpenArt workspace has its own credit pool: the "BebopBee Art Team" team
# plan refills monthly and runs dry mid-batch (a 4:3 2K Nano Banana Pro image
# costs ~70 credits), while the personal "R N" workspace carries the much
# larger Wonder-plan balance. Falling back keeps a batch running instead of
# failing every remaining row. Pass `fallback_workspaces=()` to disable.
OPENART_FALLBACK_WORKSPACES: tuple[str, ...] = ("R N",)

# Once a workspace has been seen out of credits we skip it for this long rather
# than re-paying the switch + click + paywall round trip on every subsequent row
# of a batch. Process-local and time-boxed, so topping the plan up self-heals
# without a restart. Long-lived callers (the trivia-images server) benefit most.
CREDIT_EXHAUSTED_TTL_S = 1800

# workspace name -> monotonic time when it was last seen out of credits.
_credit_exhausted: dict[str, float] = {}

# Model display name -> URL slug. The slug is the source of truth; landing on
# the slug URL preselects the model in the Model card.
MODEL_SLUGS: dict[str, str] = {
    "Nano Banana Pro": "nano-banana-pro",
    "Nano Banana 2":   "nano-banana-2",
    "Nano Banana":     "nano-banana",
    "GPT Image 2":     "gpt-image-2",
    "Seedream 4.5":    "seedream-4-5",
}


def _model_url(model: str) -> str:
    try:
        slug = MODEL_SLUGS[model]
    except KeyError as e:
        raise ValueError(
            f"unknown model {model!r}. known: {list(MODEL_SLUGS)}",
        ) from e
    return f"{OPENART_SUITE_BASE}/{slug}"


GENERATION_TIMEOUT_S = 240   # Nano Banana Pro typically returns in 20-90s per image
POLL_INTERVAL_S = 3
LOGIN_TIMEOUT_S = 300


@dataclass(frozen=True)
class Selectors:
    # Signed-out signals. OpenArt's anonymous CTA labels drift over time, so we
    # OR several stable affordances rather than trust one button label. A single
    # marker ("Sign up to create for FREE") silently stopped matching when the
    # copy changed, false-positiving login detection. Any present => not authed.
    signed_out_markers: tuple[str, ...] = (
        "button:has-text('Sign up to create for FREE')",
        "button:has-text('Get for free')",
        "a:has-text('Login')",
        "button:has-text('Login')",
    )
    prompt_editor: str = "div.tiptap.ProseMirror[contenteditable='true']"
    setting_card: str = "div.group:has-text('Setting'):has-text('Output')"
    model_card: str = "div.group:has-text('Model'):has(svg[aria-label*='Model'])"

    aspect_radio_template: str = "[role='dialog'] [role='radio']:has-text('{label}')"
    resolution_radio_template: str = "[role='dialog'] [role='radio']:has-text('{label}')"

    count_decrease: str = "button[aria-label='Decrease image count']"
    count_increase: str = "button[aria-label='Increase image count']"

    generate_button: str = "button[data-generate-btn='true']"

    # Reference-image attach: the hidden <input type='file'> on the page accepts
    # image MIMEs and uploads via the standard React file picker. After upload,
    # a preview img with alt="Reference" appears in the form. The CDN URL of
    # that img (cdn.openart.ai/openart-uploads/...) is the signal the server
    # accepted the file. The bare blob: URL only confirms client-side preview.
    file_input: str = "input[type='file']"
    reference_preview: str = "img[alt='Reference']"
    reference_cdn_preview: str = (
        "img[alt='Reference'][src*='cdn.openart.ai/openart-uploads/']"
    )

    # Out-of-credits paywall. CONFIRMED (2026-07-29) against the exhausted
    # "BebopBee Art Team" workspace: clicking Generate with an insufficient
    # balance fires NO creation POST at all — it opens a Radix dialog reading
    # "Add more credits for your team to continue creating." That silence is
    # why the batch stalled: the old code sat in expect_response until its 30s
    # timeout and reported a bare Playwright TimeoutError with no reason.
    modal: str = "[role='dialog'], [role='alertdialog']"


SEL = Selectors()


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
def _is_signed_out(page: Page) -> bool:
    return any(page.locator(sel).count() > 0 for sel in SEL.signed_out_markers)


def _goto_suite(page: Page, target_url: str) -> None:
    page.goto(target_url, wait_until="domcontentloaded", timeout=60_000)
    settle = ", ".join((*SEL.signed_out_markers, SEL.prompt_editor, SEL.generate_button))
    try:
        page.wait_for_selector(settle, timeout=15_000)
    except PWTimeout:
        pass


def _ensure_logged_in(page: Page, target_url: str, headless: bool = False) -> None:
    """Navigate to target_url and ensure we're authenticated.

    Headless runs can't show a login window, so manual re-auth is impossible —
    fail fast with the headed re-auth command instead of blocking for
    LOGIN_TIMEOUT_S on a login that can never complete.
    """
    _goto_suite(page, target_url)
    if not _is_signed_out(page):
        return
    if headless:
        raise RuntimeError(
            "OpenArt session is logged out and this is a headless run — "
            "can't log in without a browser window. Refresh auth with:\n"
            "  python scripts/common/openart_driver.py --probe\n"
            "log in, confirm the suite loads authenticated, then re-run.",
        )
    print(
        f"\n⚠ Not logged in. Please log in manually. I'll wait up to {LOGIN_TIMEOUT_S}s.\n",
        file=sys.stderr,
    )
    deadline = time.time() + LOGIN_TIMEOUT_S
    while time.time() < deadline:
        time.sleep(2)
        if not _is_signed_out(page):
            print("✓ login detected", file=sys.stderr)
            _goto_suite(page, target_url)
            return
    raise RuntimeError("login timed out")


# ---------------------------------------------------------------------------
# Workspace switcher
# ---------------------------------------------------------------------------
def _select_workspace(page: Page, workspace: str) -> None:
    """Ensure the named OpenArt team/workspace is the active one.

    The workspace switcher is a Radix popover in the header: the trigger is a
    `button[aria-haspopup='dialog']` showing an avatar + the current workspace
    name + an ArrowDownBold chevron; clicking it opens a `[role='dialog']`
    titled "Workspaces" with one `<button>` per workspace.

    Idempotent: if the switcher already shows `workspace`, do nothing. Diagnoses
    each failure with a screenshot under .playwright/. Mirrors
    `scripts/common/openart_driver.py::_select_workspace` — the header UI is
    shared between the Create-Image and Animate-Video suites.
    """
    out_dir = REPO / ".playwright"
    out_dir.mkdir(parents=True, exist_ok=True)

    # There are a couple of chevron-dialog buttons in the header (workspace and
    # project switchers); match them all and disambiguate by behaviour.
    triggers = page.locator(
        "button[aria-haspopup='dialog']:has(svg[aria-label='ArrowDownBold'])"
    )
    try:
        triggers.first.wait_for(timeout=15_000)
    except PWTimeout:
        page.screenshot(path=str(out_dir / "img_ws_fail_no_trigger.png"), full_page=True)
        raise RuntimeError(
            "workspace switcher not found — OpenArt header may have changed. "
            "See .playwright/img_ws_fail_no_trigger.png",
        )

    # Fast path: the active workspace name is rendered inside its trigger label
    # (with the avatar initial prepended, e.g. "BBebopBee Art Team"), so a
    # containment test against any switcher trigger tells us we're already there.
    n = triggers.count()
    for i in range(n):
        if workspace in (triggers.nth(i).text_content() or ""):
            return

    # Open the Workspaces popover. The first chevron trigger is the workspace
    # one, but fall back to the others if the "Workspaces" dialog doesn't show.
    dlg = None
    for i in range(n):
        triggers.nth(i).click(force=True)
        time.sleep(0.8)
        cand = page.get_by_role("dialog").filter(has_text="Workspaces").first
        if cand.count() > 0:
            dlg = cand
            break
        try:
            page.keyboard.press("Escape")
            time.sleep(0.3)
        except Exception:
            pass
    if dlg is None:
        page.screenshot(path=str(out_dir / "img_ws_fail_no_menu.png"), full_page=True)
        raise RuntimeError(
            "could not open the Workspaces switcher — "
            "see .playwright/img_ws_fail_no_menu.png",
        )

    item = dlg.get_by_role("button").filter(
        has=page.get_by_text(workspace, exact=True),
    ).first
    try:
        item.wait_for(timeout=10_000)
    except PWTimeout:
        names = dlg.locator("button p").all_text_contents()
        page.screenshot(path=str(out_dir / "img_ws_fail_no_item.png"), full_page=True)
        raise RuntimeError(
            f"workspace {workspace!r} not offered (saw {names!r}) — check the "
            f"name. See .playwright/img_ws_fail_no_item.png",
        )
    item.click(force=True)
    # Switching workspace reloads the suite content + asset library.
    time.sleep(2.5)


# ---------------------------------------------------------------------------
# Credits
# ---------------------------------------------------------------------------
class OpenArtOutOfCreditsError(RuntimeError):
    """Raised when the active workspace can't afford the generation.

    Deliberately NOT an `OpenArtGenerationError`: that one means OpenArt ran
    the job and refused the result (e.g. a content-policy block), which is
    permanent for that prompt. This one means the job never ran, and the same
    prompt will succeed once credits exist — callers must not conflate them.
    """


# Phrases seen in the credit paywall dialog. The first is verbatim from the
# team-owner variant; the rest cover the non-owner / personal-plan wordings.
_PAYWALL_PHRASES = (
    "add more credits",
    "not enough credits",
    "insufficient credits",
    "out of credits",
    "run out of credits",
    "need more credits",
    "to continue creating",
)
# Fallback: any dialog that talks about credits AND tries to sell something.
# Guards against OpenArt rewording the copy above.
_PAYWALL_UPSELL_WORDS = ("upgrade", "add to plan", "buy", "purchase", "top up", "/month")


def _looks_like_credit_block(text: str) -> bool:
    low = text.lower()
    if any(p in low for p in _PAYWALL_PHRASES):
        return True
    return "credit" in low and any(w in low for w in _PAYWALL_UPSELL_WORDS)


def _credit_paywall_reason(page: Page) -> Optional[str]:
    """Return the paywall dialog's first line if one is open, else None.

    Only matched on credit-specific copy, so the Setting/Model popovers (also
    `[role='dialog']`) never false-positive.
    """
    dialogs = page.locator(SEL.modal)
    for i in range(dialogs.count()):
        try:
            text = dialogs.nth(i).inner_text()
        except Exception:
            continue
        if text and _looks_like_credit_block(text):
            first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
            return first or "credit paywall dialog opened"
    return None


def _dismiss_modals(page: Page, attempts: int = 3) -> None:
    """Escape out of any open dialog so the next attempt starts clean."""
    for _ in range(attempts):
        if page.locator(SEL.modal).count() == 0:
            return
        try:
            page.keyboard.press("Escape")
        except Exception:
            return
        time.sleep(0.4)


def _workspace_balance(page: Page) -> Optional[int]:
    """Credit balance of the *active* workspace, or None if unreadable.

    `POST /suite/api/user/current-workspace` is what the suite itself calls to
    populate the header credit chip, and it works for both workspace kinds
    (team plan and personal). Its `subscription_monthly_credit` is exactly the
    number the chip renders — verified 2026-07-29: 20 for "BebopBee Art Team",
    103700 for "R N". Note GET returns 405; it must be a POST.

    Never raises — a probe failure must not block a generation that might work.
    """
    try:
        resp = page.context.request.post(
            "https://openart.ai/suite/api/user/current-workspace",
            data={}, timeout=15_000,
        )
        if not resp.ok:
            return None
        value = json.loads(resp.text()).get("subscription_monthly_credit")
        return int(value) if isinstance(value, (int, float)) else None
    except Exception:
        return None


def _generate_cost(page: Page) -> Optional[int]:
    """Credit cost of the pending generation, read off the Generate button.

    The button renders its price inline: "Generate70(80)" = 70 charged, 80 list
    (discounted models show both; undiscounted show one). We take the first
    number — the amount actually charged — so we don't skip a workspace that
    can in fact afford the job. Returns None if no price is rendered yet.
    """
    try:
        text = page.locator(SEL.generate_button).first.text_content() or ""
    except Exception:
        return None
    m = re.search(r"\d[\d,]*", text)
    return int(m.group(0).replace(",", "")) if m else None


def _mark_exhausted(workspace: Optional[str]) -> None:
    if workspace:
        _credit_exhausted[workspace] = time.monotonic()


def _recently_exhausted(workspace: Optional[str]) -> bool:
    if not workspace:
        return False
    seen = _credit_exhausted.get(workspace)
    if seen is None:
        return False
    if time.monotonic() - seen > CREDIT_EXHAUSTED_TTL_S:
        del _credit_exhausted[workspace]
        return False
    return True


# ---------------------------------------------------------------------------
# Setting popover (aspect + resolution radios)
# ---------------------------------------------------------------------------
def _open_setting_popover(page: Page) -> None:
    if page.locator("[role='dialog']").count() > 0:
        return
    page.locator(SEL.setting_card).first.click()
    page.locator("[role='dialog']").first.wait_for(timeout=5_000)
    time.sleep(0.3)


def _close_popover(page: Page) -> None:
    if page.locator("[role='dialog']").count() == 0:
        return
    page.keyboard.press("Escape")
    try:
        page.locator("[role='dialog']").first.wait_for(state="detached", timeout=3_000)
    except PWTimeout:
        pass
    time.sleep(0.3)


def _select_radio(page: Page, template: str, label: str, kind: str,
                  attempts: int = 3) -> None:
    """Open the Setting popover and click the `label` radio, with retries.

    The popover open can race (or a stale/unrelated dialog can be open), which
    used to strand the bare click on its 30s default timeout. Each attempt
    re-opens a fresh popover and waits for the specific radio to be visible
    before clicking; on miss it closes the popover and retries."""
    sel = template.format(label=label)
    last: Exception | None = None
    for _ in range(attempts):
        try:
            _open_setting_popover(page)
            loc = page.locator(sel).first
            loc.wait_for(state="visible", timeout=6_000)
            loc.click()
            return
        except PWTimeout as e:
            last = e
            _close_popover(page)   # drop stale/wrong popover, then reopen
            time.sleep(0.4)
    raise RuntimeError(
        f"{kind} radio {label!r} not selectable after {attempts} attempts "
        f"(setting popover never yielded it): {last}")


def _select_aspect(page: Page, label: str = "4:3") -> None:
    _select_radio(page, SEL.aspect_radio_template, label, "aspect")


def _select_resolution(page: Page, label: str = "2K") -> None:
    _select_radio(page, SEL.resolution_radio_template, label, "resolution")


def _select_model_in_picker(page: Page, label: str) -> None:
    """Re-assert the model selection. Usually a no-op since landing on the
    slug URL already selects the right model — kept as a safety net."""
    card = page.locator(SEL.model_card).first
    card.wait_for(timeout=10_000)
    current = (card.text_content() or "").strip()
    if label in current:
        return
    card.click(force=True)
    dlg = page.get_by_role("dialog").first
    dlg.wait_for(timeout=5_000)
    dlg.locator(f"text=/^{re.escape(label)}/").first.click(force=True)
    time.sleep(1.5)


def _select_character(page: Page, character_name: str) -> None:
    """Attach a saved character as a visual reference to the current image:
    Add visual references -> "Characters & Worlds" pill -> click the named
    character card in My Library. This keeps the Create-Image form intact
    (the panel opens to the side). Screenshots each failing step to
    .playwright/witw_char_fail_<step>.png.

    Note: do NOT use the left-nav "Characters & Worlds" / "Character" /
    "Browse Library" items — those navigate to the Character *section* and
    leave the image form, hiding the generate controls.
    """
    out_dir = REPO / ".playwright"
    out_dir.mkdir(parents=True, exist_ok=True)

    def step(label: str, action) -> None:
        try:
            action()
        except Exception as e:
            try:
                page.screenshot(path=str(out_dir / f"witw_char_fail_{label}.png"), full_page=True)
            except Exception:
                pass
            raise RuntimeError(
                f"_select_character failed at step {label!r}: {e}. "
                f"See .playwright/witw_char_fail_{label}.png",
            ) from e

    def open_refs():
        page.locator("text=/Add visual references/i").first.click()
        time.sleep(1.0)
    step("1_add_visual_references", open_refs)

    # The in-form pill that opens the references panel to the character library.
    def cw_pill():
        page.locator("button:has-text('Characters & Worlds')").last.click(force=True)
        time.sleep(1.2)
    step("2_characters_and_worlds_pill", cw_pill)

    # My Library is the default sub-tab; click it if present to be safe.
    try:
        ml = page.locator("button:has-text('My Library')")
        if ml.count() > 0 and ml.first.is_visible():
            ml.first.click(force=True, timeout=2_000)
            time.sleep(0.6)
    except Exception:
        pass

    def click_char():
        name = page.locator(f"text=/{re.escape(character_name)}/").first
        name.wait_for(timeout=15_000)
        card = name.locator("xpath=ancestor::*[descendant::img][1]").first
        card.click(force=True)
        time.sleep(1.5)
    step("3_click_character", click_char)


def _set_variant_count(page: Page, target: int) -> None:
    if target < 1:
        raise ValueError(f"variant count must be ≥ 1, got {target}")
    container = page.locator(f"div:has(> {SEL.count_decrease}):has(> {SEL.count_increase})").first
    container.wait_for(timeout=5_000)
    text = container.text_content() or ""
    digits = re.findall(r"\d+", text)
    current = int(digits[0]) if digits else 1
    delta = target - current
    if delta == 0:
        return
    btn = page.locator(SEL.count_increase if delta > 0 else SEL.count_decrease).first
    for _ in range(abs(delta)):
        btn.click()
        time.sleep(0.15)


def _enter_prompt(page: Page, prompt: str) -> None:
    """Fill the TipTap/ProseMirror contenteditable using real key events so
    React's onInput fires and Generate enables."""
    box = page.locator(SEL.prompt_editor).first
    box.click()
    page.keyboard.press("ControlOrMeta+A")
    page.keyboard.press("Backspace")
    page.keyboard.insert_text(prompt)
    page.keyboard.press("Tab")
    time.sleep(0.5)


# How long to wait for the server-side upload to finish, signalled by the
# preview <img alt='Reference'> getting its src rewritten from blob:... to the
# CDN URL.
REFERENCE_UPLOAD_TIMEOUT_S = 60


def _attach_reference_image(page: Page, image_path: Path) -> None:
    """Upload `image_path` as the reference image and wait for the server
    upload to settle.

    The page has a hidden <input type='file'> that React wires to its standard
    file-picker handler. Setting the file on that input is enough to start
    both the client-side preview (blob: URL) and the server-side upload. The
    preview <img alt='Reference'> appears almost immediately with a blob: src,
    then has its src rewritten to the CDN URL once the upload completes. We
    wait for the CDN URL because submitting before the server has the file
    can produce a generation that ignores the reference.
    """
    if not image_path.exists():
        raise FileNotFoundError(f"reference image not found: {image_path}")

    file_input = page.locator(SEL.file_input).first
    file_input.wait_for(state="attached", timeout=15_000)
    file_input.set_input_files(str(image_path))

    # Wait for the preview chip to render (blob or CDN — either signals the
    # client-side attachment).
    page.locator(SEL.reference_preview).first.wait_for(timeout=15_000)

    # Then wait for the CDN URL to appear, indicating server-side ingestion.
    deadline = time.time() + REFERENCE_UPLOAD_TIMEOUT_S
    last_err: Optional[str] = None
    while time.time() < deadline:
        if page.locator(SEL.reference_cdn_preview).count() > 0:
            print(
                f"  → reference attached: {image_path.name}",
                file=sys.stderr,
            )
            return
        time.sleep(1.0)
    last_err = "reference upload never settled to a CDN URL within timeout"
    raise RuntimeError(f"{last_err} (image={image_path})")


def _diagnose(page: Page, where: str) -> None:
    out_dir = REPO / ".playwright"
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        editor_text = page.locator(SEL.prompt_editor).first.text_content() or ""
    except Exception as e:
        editor_text = f"<err: {e}>"
    try:
        btn = page.locator(SEL.generate_button).first
        btn_disabled = btn.get_attribute("disabled")
        btn_text = (btn.text_content() or "").strip()
    except Exception as e:
        btn_disabled = f"<err: {e}>"
        btn_text = ""
    try:
        page.screenshot(path=str(out_dir / f"diag_img_{where}.png"), full_page=True)
    except Exception:
        pass
    print(f"\n--- DIAG {where} ---", file=sys.stderr)
    print(f"  editor text length: {len(editor_text)}", file=sys.stderr)
    print(f"  editor text preview: {editor_text[:200]!r}", file=sys.stderr)
    print(f"  generate button disabled attr: {btn_disabled!r}", file=sys.stderr)
    print(f"  generate button text: {btn_text!r}", file=sys.stderr)
    print(f"  screenshot: {out_dir / f'diag_img_{where}.png'}", file=sys.stderr)


def _click_generate(page: Page) -> None:
    btn = page.locator(SEL.generate_button).first
    btn.wait_for(timeout=15_000)
    deadline = time.time() + 15
    while time.time() < deadline:
        if btn.is_enabled():
            break
        time.sleep(0.3)
    else:
        _diagnose(page, "generate_disabled")
        raise RuntimeError("Generate button still disabled — see diagnostic above")
    btn.click()


# How long to wait for the creation POST after clicking Generate. The paywall
# check runs concurrently, so an out-of-credits click fails in ~1s instead of
# burning this whole budget.
SUBMIT_TIMEOUT_S = 30


def _submit_and_capture(page: Page, ws_label: str) -> dict:
    """Click Generate and return the parsed creation-POST body.

    Races two outcomes instead of blindly waiting for the POST:
      - `POST /suite/api/forms/creations/...` fires  -> normal submit
      - a credit paywall dialog opens                -> OpenArtOutOfCreditsError

    Uses a response listener rather than `page.expect_response`, whose context
    manager blocks for the full timeout on exit and so can't be raced.
    """
    captured: dict[str, object] = {}

    def _on_response(response) -> None:
        if (response.request.method == "POST"
                and "/suite/api/forms/creations/" in response.url):
            captured.setdefault("response", response)

    page.on("response", _on_response)
    try:
        _click_generate(page)
        deadline = time.time() + SUBMIT_TIMEOUT_S
        while time.time() < deadline:
            if "response" in captured:
                break
            reason = _credit_paywall_reason(page)
            if reason:
                raise OpenArtOutOfCreditsError(f"{ws_label}: {reason}")
            time.sleep(0.5)
        else:
            _diagnose(page, "submit_no_post")
            raise RuntimeError(
                f"Generate clicked but no creation POST fired within "
                f"{SUBMIT_TIMEOUT_S}s — see diagnostic above",
            )
    finally:
        page.remove_listener("response", _on_response)

    resp = captured["response"]
    if not resp.ok:
        body = resp.text()[:400]
        # A server-side credit rejection is the same condition as the dialog —
        # route it to the same fallback rather than failing the row.
        if resp.status == 402 or _looks_like_credit_block(body):
            raise OpenArtOutOfCreditsError(
                f"{ws_label}: submit rejected (HTTP {resp.status}): {body}",
            )
        raise RuntimeError(f"submit POST returned HTTP {resp.status}: {body}")
    return json.loads(resp.text())


def _resolve_project_id(page: Page) -> str:
    """Return the active workspace's default project id.

    Resources are scoped per project: the list endpoint requires a
    `projectId` query param (see `_poll_resources`). We pick the workspace's
    default project ("Personal Project"), which is where the suite lands new
    creations when no project is explicitly chosen.
    """
    resp = page.context.request.get(
        "https://openart.ai/suite/api/projects?pageSize=50", timeout=15_000,
    )
    if not resp.ok:
        raise RuntimeError(f"could not list projects: HTTP {resp.status}")
    rows = json.loads(resp.text()).get("data") or []
    for r in rows:
        if isinstance(r, dict) and r.get("isDefault"):
            return r["id"]
    if rows and isinstance(rows[0], dict) and rows[0].get("id"):
        return rows[0]["id"]
    raise RuntimeError("no projects found for the active workspace")


class OpenArtGenerationError(RuntimeError):
    """Raised when OpenArt returns zero usable variants.

    The message carries the best-available failure reason (e.g. a content-policy
    block like "[google] Content Policy Violation") so callers can surface WHY
    a generation produced nothing instead of a bare "no saved paths".
    """


# Fields OpenArt carries a failure reason in. CONFIRMED (2026-07-03, captured
# from a real content-policy block): the list row has a top-level
# `failedReason` = "[google] Content Policy Violation" plus `failedCode` =
# "upstream_error", with `status` = "failed". There is NO top-level `error`
# field. The rest are kept as defensive fallbacks in case OpenArt renames.
_REASON_FIELDS = (
    "failedReason", "failedCode",
    "error", "errorMessage", "failureReason",
    "statusMessage", "statusMessages", "message", "moderationReason", "reason",
)
# Distinctive enough to avoid false positives on benign fields (URLs, mime
# types); "[google] Content Policy Violation" hits both "policy" and "violation".
_REASON_SIGNALS = ("policy", "violation", "moderat", "nsfw", "safety")


def _deep_find_reason(obj: object) -> Optional[str]:
    """Depth-first hunt for a short string that reads like a moderation reason."""
    if isinstance(obj, str):
        low = obj.lower()
        if len(obj) <= 300 and any(sig in low for sig in _REASON_SIGNALS):
            return obj.strip()
        return None
    if isinstance(obj, dict):
        for v in obj.values():
            hit = _deep_find_reason(v)
            if hit:
                return hit
    elif isinstance(obj, list):
        for v in obj:
            hit = _deep_find_reason(v)
            if hit:
                return hit
    return None


def _failure_reason(row: dict) -> str:
    """Best-effort human reason for a failed resource row.

    Checks known reason fields (top-level + nested under `metadata`), then falls
    back to any moderation-looking string anywhere in the row, then to the bare
    status. This surfaces "[google] Content Policy Violation" even before we
    know OpenArt's exact field name for it — see the instrumentation dump in
    `_poll_resources`, which logs the full failing row so we can pin the field.
    """
    containers = [row]
    meta = row.get("metadata")
    if isinstance(meta, dict):
        containers.append(meta)
    for container in containers:
        for f in _REASON_FIELDS:
            v = container.get(f)
            if isinstance(v, str) and v.strip():
                return v.strip()
    hit = _deep_find_reason(row)
    if hit:
        return hit
    return (row.get("status") or "failed")


def _poll_resources(
    page: Page,
    resource_ids: list[str],
    timeout_s: int,
) -> list[tuple[str, dict]]:
    """Poll the project-scoped resources LIST endpoint until each id settles.

    Returns one tuple per id, in the same order as `resource_ids`:
        (resource_id, {"status": "ok", "url": <full-res CDN URL>, "metadata": {...}})
        (resource_id, {"status": "failed", "error": "<reason>"})
        (resource_id, {"status": "timeout"})

    Why the LIST endpoint and not GET `/suite/api/resources/{id}`:
      OpenArt removed (or locked) the per-id resource route — it now returns
      `403 {"error":"Forbidden"}` for ids the session itself just created.
      The suite UI never calls it; it fetches
      `GET /suite/api/resources?folderIdNull=true&limit=N&projectId=<id>`,
      a newest-first list. Our just-submitted variants are the newest rows,
      so we page the list and match by `id`. Each row carries `url`,
      `status` ("completed"/…), `error`, and `metadata` — a completed row
      has a non-empty `url`.
    """
    project_id = _resolve_project_id(page)
    list_url = (
        "https://openart.ai/suite/api/resources"
        f"?folderIdNull=true&limit=50&projectId={project_id}"
    )
    wanted = set(resource_ids)
    settled: dict[str, dict] = {}
    deadline = time.time() + timeout_s
    last_progress = -1

    while len(settled) < len(wanted) and time.time() < deadline:
        progress = len(settled)
        if progress != last_progress:
            print(f"    resolved {progress}/{len(resource_ids)}", file=sys.stderr)
            last_progress = progress
        try:
            resp = page.context.request.get(list_url, timeout=15_000)
            if resp.ok:
                rows = json.loads(resp.text()).get("data") or []
                by_id = {r.get("id"): r for r in rows if isinstance(r, dict)}
                for rid in list(wanted - set(settled)):
                    row = by_id.get(rid)
                    if row is None:
                        continue  # not yet on the newest page — keep polling
                    status = (row.get("status") or "").lower()
                    url = row.get("url")
                    if url and status not in {"failed", "error"}:
                        settled[rid] = {
                            "status": "ok",
                            "url": url,
                            "metadata": row.get("metadata") or {},
                        }
                    elif status in {"failed", "error"} or row.get("error"):
                        # Instrument: the list-row `error` is usually empty on
                        # content-policy blocks, so dump the failing row once —
                        # that's how we discover which field actually carries the
                        # reason (then fold it into _REASON_FIELDS). Drop the bulky
                        # request-echo fields so the status/reason fields aren't
                        # pushed past the truncation window.
                        _dbg = {k: v for k, v in row.items()
                                if k not in ("input", "params", "visualReferences")}
                        print(
                            f"    ✗ failed row {rid}: {json.dumps(_dbg)[:4000]}",
                            file=sys.stderr,
                        )
                        settled[rid] = {
                            "status": "failed",
                            "error": _failure_reason(row),
                        }
        except Exception:
            # Transient — try again next round
            pass
        if len(settled) < len(wanted):
            time.sleep(POLL_INTERVAL_S)

    for rid in resource_ids:
        settled.setdefault(rid, {"status": "timeout"})
    return [(rid, settled[rid]) for rid in resource_ids]


def _download_via_context(page: Page, url: str, output_path: Path) -> Path:
    """Download a CDN asset using the browser's authenticated request context.

    OpenArt's CDN signs URLs against the session, so a bare HTTP GET 403s.
    The destination file is written byte-for-byte; the caller picks the
    extension. If the CDN serves WebP and the dest path is .jpg, the bytes
    will still be WebP — set the path extension to match the source URL when
    you care about format consistency.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    resp = page.context.request.get(url, timeout=120_000)
    if not resp.ok:
        raise RuntimeError(f"download failed: HTTP {resp.status} for {url}")
    output_path.write_bytes(resp.body())
    return output_path


def _url_extension(url: str) -> str:
    """Pull the file extension from a CDN URL, lowercased and dot-included.

    Falls back to '.jpg' when the URL has no recognizable image extension.
    OpenArt serves .webp, .jpg, .jpeg, .png — keep whichever the CDN chose.
    """
    m = re.search(r"\.(webp|jpe?g|png)(?:\?|$)", url, re.IGNORECASE)
    return f".{m.group(1).lower()}" if m else ".jpg"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
@contextmanager
def _browser(p: Playwright, headless: bool):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    storage_state = str(STATE_FILE) if STATE_FILE.exists() else None
    browser = p.chromium.launch(headless=headless)
    context = browser.new_context(
        storage_state=storage_state,
        accept_downloads=True,
        viewport={"width": 1440, "height": 900},
    )
    try:
        yield context
    finally:
        try:
            context.storage_state(path=str(STATE_FILE))
        except Exception:
            pass
        context.close()
        browser.close()


def generate_image(
    prompt: str,
    model: str,
    output_paths: list[Path],
    headless: bool = False,
    aspect: str = "4:3",
    resolution: str = "2K",
    keep_source_ext: bool = True,
    reference_image_path: Optional[Path] = None,
    workspace: Optional[str] = OPENART_WORKSPACE,
    character: Optional[str] = None,
    fallback_workspaces: tuple[str, ...] = OPENART_FALLBACK_WORKSPACES,
) -> list[Path]:
    """Drive openart.ai to generate `len(output_paths)` image variants.

    Args:
        prompt: full prompt text.
        model: model name (e.g. "Nano Banana Pro").
        output_paths: one destination path per variant. The variant count is
            implicit. Paths may carry any extension; if `keep_source_ext` is
            True the extension is rewritten to match the CDN source.
        headless: open a visible window when False (recommended for debug).
        aspect: aspect-ratio label in the Setting popover (default "4:3").
        resolution: resolution label in the Setting popover (default "2K").
        keep_source_ext: if True, rewrite each output path's extension to
            match the CDN's served format (.webp / .jpg / .png). If False,
            the bytes are written as-is to the path you provided.
        reference_image_path: optional local image to attach as a "same scene"
            reference. The OpenArt model uses it as a visual source — the
            output keeps the environment of the reference while applying the
            new prompt's content. Only the models that accept image refs in
            Create Image mode will use it (Nano Banana family, Seedream).
        workspace: OpenArt team to make active before generating (defaults to
            "BebopBee Art Team"). Re-asserted on every run because OpenArt can
            silently swap the active team. Pass "" / None to skip the switch.
        character: optional saved-character name to attach as a visual
            reference before the prompt (via _select_character). The character
            must live in the active `workspace`. None skips the step.
        fallback_workspaces: workspaces to retry in, in order, if the primary
            one is out of credits (defaults to `OPENART_FALLBACK_WORKSPACES`).
            Pass `()` to disable and surface the credit failure instead.
            Ignored when `character` is set — saved characters are
            workspace-scoped, so switching would silently drop the character.

    Returns saved paths in newest-first gallery order, aligned with
    `output_paths` (output_paths[0] = newest variant).

    Raises:
        OpenArtOutOfCreditsError: every candidate workspace is out of credits.
        OpenArtGenerationError: OpenArt ran the job but returned no usable
            variant (e.g. a content-policy block).
    """
    if not output_paths:
        raise ValueError("output_paths must contain at least one path")
    output_paths = [Path(p).expanduser().resolve() for p in output_paths]
    if reference_image_path is not None:
        reference_image_path = Path(reference_image_path).expanduser().resolve()
    n = len(output_paths)
    target_url = _model_url(model)

    # Saved characters live in one workspace, so a credit fallback would
    # silently generate without the character. Better to fail loudly.
    if character:
        fallback_workspaces = ()

    with sync_playwright() as p, _browser(p, headless=headless) as ctx:
        page = ctx.new_page()
        _ensure_logged_in(page, target_url, headless=headless)

        def fill_and_submit(ws: Optional[str], precheck: bool) -> dict:
            """Set up the form in workspace `ws` and submit. Raises
            OpenArtOutOfCreditsError if `ws` can't afford the generation."""
            label = ws or "active workspace"

            # Re-assert the workspace before anything else — OpenArt can
            # silently swap the active team between runs, and the trivia-images
            # art lives in a specific one.
            if ws:
                print(f"  → ensuring workspace: {ws}", file=sys.stderr)
                _select_workspace(page, ws)

            _select_model_in_picker(page, model)
            _select_aspect(page, aspect)
            _select_resolution(page, resolution)
            _close_popover(page)

            # Insert the saved character as a visual reference before the prompt.
            if character:
                _select_character(page, character)

            # Attach the reference BEFORE entering the prompt so the upload has
            # time to settle while we fill the rest of the form. The wait inside
            # _attach_reference_image still gates submission on the CDN URL.
            if reference_image_path is not None:
                _attach_reference_image(page, reference_image_path)

            _set_variant_count(page, n)
            _enter_prompt(page, prompt)

            # Cheap pre-check: the button renders the price and the API knows the
            # balance, so a doomed submit can be skipped before clicking. Only
            # ever used to jump to a fallback (`precheck` is False on the last
            # candidate) — the paywall race in _submit_and_capture is the sole
            # authority for actually failing a job, so a stale or incomplete
            # balance reading can never turn a workable generation into an error.
            balance, cost = _workspace_balance(page), _generate_cost(page)
            if balance is not None:
                detail = f", cost {cost}" if cost is not None else ""
                print(f"  → credits: {balance} in {label}{detail}", file=sys.stderr)
                if precheck and cost is not None and balance < cost:
                    raise OpenArtOutOfCreditsError(
                        f"{label}: {balance} credits left, this generation "
                        f"costs {cost}",
                    )

            print(
                f"  → submit (model={model}, aspect={aspect}, res={resolution}, "
                f"prompt={len(prompt)} chars, variants={n})",
                file=sys.stderr,
            )
            # The creation POST carries the resourceIds we need to look up the
            # full-res CDN URLs.
            return _submit_and_capture(page, label)

        candidates: list[Optional[str]] = [workspace or None]
        candidates += [w for w in fallback_workspaces if w and w != workspace]

        submit_data: dict | None = None
        for i, ws in enumerate(candidates):
            is_last = i == len(candidates) - 1
            if not is_last and _recently_exhausted(ws):
                print(
                    f"  → skipping {ws}: seen out of credits in the last "
                    f"{CREDIT_EXHAUSTED_TTL_S // 60}min",
                    file=sys.stderr,
                )
                continue
            try:
                submit_data = fill_and_submit(ws, precheck=not is_last)
                break
            except OpenArtOutOfCreditsError as e:
                _mark_exhausted(ws)
                if is_last:
                    tried = ", ".join(repr(c or "active workspace") for c in candidates)
                    raise OpenArtOutOfCreditsError(
                        f"{e} — no workspace has credits (tried {tried})",
                    ) from e
                print(
                    f"  ⚠ out of credits — {e}\n"
                    f"  ↪ retrying in workspace {candidates[i + 1]!r}",
                    file=sys.stderr,
                )
                # Drop the paywall dialog and reload so the next attempt fills a
                # clean form (the reference image re-uploads under the new
                # workspace's user id).
                _dismiss_modals(page)
                _goto_suite(page, target_url)

        assert submit_data is not None  # loop either breaks with data or raises
        resource_ids = submit_data.get("resourceIds") or []
        history_id = submit_data.get("historyId")
        if not resource_ids:
            raise RuntimeError(f"submit response missing resourceIds: {submit_data}")
        print(
            f"  → submitted: historyId={history_id} resourceIds={resource_ids}",
            file=sys.stderr,
        )
        print(f"  → polling /api/resources for {n} variant(s) (up to {GENERATION_TIMEOUT_S * n}s)…", file=sys.stderr)

        resolved = _poll_resources(page, resource_ids, GENERATION_TIMEOUT_S * max(1, n))

        saved: list[Path] = []
        failures: list[str] = []
        for (rid, info), dest in zip(resolved, output_paths):
            if info.get("status") != "ok":
                reason = info.get("error") or info.get("status") or "unknown"
                print(f"  ✗ {rid}: {info.get('status')} ({reason})", file=sys.stderr)
                failures.append(reason)
                continue
            url = info["url"]
            if keep_source_ext:
                ext = _url_extension(url)
                if dest.suffix.lower() != ext:
                    dest = dest.with_suffix(ext)
            saved.append(_download_via_context(page, url, dest))
            meta = info.get("metadata") or {}
            dims = f"{meta.get('width')}x{meta.get('height')}" if meta else "?"
            print(f"  ✓ saved {dest}  ({dims}, {meta.get('format','?')})", file=sys.stderr)
        if len(saved) < n:
            print(f"  ⚠ {n - len(saved)} variant(s) did not save successfully", file=sys.stderr)
        if not saved:
            # Nothing usable came back — carry WHY up to the caller instead of a
            # bare empty list. Every caller already treats empty as fatal; this
            # just attaches the reason (e.g. a content-policy block). Partial
            # multi-variant success still returns normally above.
            raise OpenArtGenerationError(
                "; ".join(dict.fromkeys(failures))
                or "generation failed (no variants returned)"
            )
        return saved


# ---------------------------------------------------------------------------
# Smoke-test CLI
# ---------------------------------------------------------------------------
def _probe(model: str = "Nano Banana Pro") -> int:
    target_url = _model_url(model)
    with sync_playwright() as p, _browser(p, headless=False) as ctx:
        page = ctx.new_page()
        _ensure_logged_in(page, target_url)
        print(f"\n— probe — at {target_url}; opening Playwright Inspector.")
        page.pause()
    return 0


def _main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--prompt")
    ap.add_argument("--model", default="Nano Banana Pro")
    ap.add_argument("--aspect", default="4:3")
    ap.add_argument("--resolution", default="2K")
    ap.add_argument("--variants", type=int, default=1)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--reference", type=Path,
                    help="local image to attach as a same-scene reference")
    ap.add_argument("--workspace", default=OPENART_WORKSPACE,
                    help="OpenArt team to activate before generating "
                         "(default %(default)r; pass '' to keep the active one)")
    ap.add_argument("--fallback-workspace", default=",".join(OPENART_FALLBACK_WORKSPACES),
                    help="comma-separated workspaces to retry in when the "
                         "primary is out of credits (default %(default)r; "
                         "pass '' to disable and fail instead)")
    args = ap.parse_args()

    if args.probe:
        return _probe(args.model)
    if not (args.prompt and args.out):
        ap.error("--prompt and --out are required (unless --probe)")
    if args.variants == 1:
        paths = [args.out]
    else:
        stem = args.out.stem
        suf = args.out.suffix or ".jpg"
        paths = [args.out.with_name(f"{stem}_v{i+1}{suf}") for i in range(args.variants)]
    saved = generate_image(
        prompt=args.prompt,
        model=args.model,
        output_paths=paths,
        headless=args.headless,
        aspect=args.aspect,
        resolution=args.resolution,
        reference_image_path=args.reference,
        workspace=args.workspace,
        fallback_workspaces=tuple(
            w.strip() for w in args.fallback_workspace.split(",") if w.strip()
        ),
    )
    for s in saved:
        print(f"saved: {s}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
