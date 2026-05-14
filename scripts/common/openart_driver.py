#!/usr/bin/env python
"""Playwright driver for openart.ai video generation.

Drives the OpenArt UI to generate a single clip from a prompt with a chosen
model + duration, then downloads the result.

Auth: persistent storage state at .playwright/openart-state.json. First run is
headed and waits up to 5 minutes for the human to log in; subsequent runs reuse
the saved cookies/localStorage.

The selectors live in `SELECTORS` at the top so they can be tuned without
touching the flow logic. They use Playwright role/text locators where possible
because OpenArt's class names are hashed and unstable.

Public API:
    generate_clip(prompt, model, duration_s, output_path, headless=False) -> Path

Standalone smoke test:
    python scripts/common/openart_driver.py \\
      --prompt "test" --model "Seedance 2.0" --duration 8 \\
      --out scripts/trivia/library/_smoketest.mp4

Interactive probe (open OpenArt, login, then pause for DOM inspection):
    python scripts/common/openart_driver.py --probe
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import (
    Page,
    Playwright,
    TimeoutError as PWTimeout,
    sync_playwright,
)

REPO = Path(__file__).resolve().parents[2]
STATE_FILE = REPO / ".playwright" / "openart-state.json"

OPENART_SUITE_BASE = "https://openart.ai/suite/animate-video"

# Model display name -> URL slug on the Suite. The slug is the source of truth
# for which generator runs; OpenArt has no in-page model picker on these URLs.
MODEL_SLUGS: dict[str, str] = {
    "Seedance 2.0": "byte-plus-seedance-2",
    "HappyHorse":   "happyhorse",
}


def _model_url(model: str) -> str:
    try:
        slug = MODEL_SLUGS[model]
    except KeyError as e:
        raise ValueError(
            f"unknown model {model!r}. known: {list(MODEL_SLUGS)}",
        ) from e
    return f"{OPENART_SUITE_BASE}/{slug}"

# How long we'll wait for a generation job to finish, per model.
# Seedance 8s clips usually land in 60-180s; HappyHorse 3s in 30-90s.
# Be generous; we'd rather wait than miss the result.
GENERATION_TIMEOUT_S = 600

# Polling interval while waiting for the new clip to appear.
POLL_INTERVAL_S = 3

# How long to wait on the login page (headed) before giving up.
LOGIN_TIMEOUT_S = 300


# ---------------------------------------------------------------------------
# Selectors — adjust these as the OpenArt UI evolves.
# Prefer role/text locators over hashed class names.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Selectors:
    # On the Suite, an unauthenticated user sees "Sign up to create for FREE"
    # in place of the Generate button. We use its presence as the
    # "signed out" signal.
    signed_out_marker: str = "button:has-text('Sign up to create for FREE')"

    # The prompt input is a TipTap/ProseMirror contenteditable div, NOT a textarea.
    prompt_editor: str = "div.tiptap.ProseMirror[contenteditable='true']"

    # The "Setting" card (Output: Auto | 720p | 5s) opens a popover with
    # aspect-ratio + resolution radios and a duration slider.
    setting_card: str = "div.group:has-text('Setting'):has-text('Output')"

    # The Mode Selector radiogroup at the top of the form lets us pick
    # between "Start/End Frame" (needs 2 images) and "Text with Reference"
    # (references are optional but unlock per-segment characters).
    mode_selector: str = "[role='radiogroup'][aria-label='Mode Selector']"
    mode_text_with_reference: str = (
        "[role='radiogroup'][aria-label='Mode Selector'] [role='radio']:has-text('Text with Reference')"
    )

    # Model card on the form (icon = ModelXxx, text = "Model<name>"). Clicking
    # opens a [role='dialog'] with a list of available models.
    model_card: str = "div.group:has-text('Model'):has(svg[aria-label*='Model'])"

    # Inside the form, after Text-with-Reference is active, two pill buttons
    # gate the references type: "Upload Media" (custom file) and "Characters"
    # (saved characters). Clicking the "Characters" pill highlights it.
    references_characters_pill: str = "button:has-text('Characters')"
    # Then the "Add visual references" chip is a clickable purple area that
    # opens a side panel for browsing references.
    add_references_trigger: str = "text=/Add visual references/"

    # Side panel top-level tab. After "Add visual references" the panel
    # defaults to the Image tab; switch to Characters & Worlds first.
    side_panel_cw_tab: str = "button:has-text('Characters & Worlds')"
    # Sub-filters within Characters & Worlds.
    # "My Library" filters to user-saved references; "Characters" sub-tab is
    # followed by "World shots" in the DOM, which gives us a unique xpath.
    side_panel_my_library: str = "button:has-text('My Library')"
    side_panel_chars_subtab: str = (
        "xpath=//button[normalize-space()='Characters']"
        "[following-sibling::button[normalize-space()='World shots']]"
    )

    # NOTE: the audio switch is found dynamically in `_find_audio_switch`
    # because there can be multiple `[role='switch']` buttons on the page
    # (Audio, Auto Polish, etc.) and the structure varies by model.

    # Video-count picker (number of variants per submit). Two icon buttons
    # flank a div displaying the current count.
    count_decrease: str = "button[aria-label='Decrease video count']"
    count_increase: str = "button[aria-label='Increase video count']"

    # Inside the Setting popover (a [role='dialog']):
    aspect_radio_template: str = "[role='dialog'] [role='radio']:has-text('{label}')"
    resolution_radio_template: str = "[role='dialog'] [role='radio']:has-text('{label}')"
    # The Duration row exposes the slider thumb as [role='slider'].
    duration_slider: str = "[role='dialog'] [role='slider']"

    # Generate button has a stable data attribute. We also key on "enabled"
    # before clicking; OpenArt disables it until the form is valid.
    generate_button: str = "button[data-generate-btn='true']"


SEL = Selectors()


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
def _is_signed_out(page: Page) -> bool:
    return page.locator(SEL.signed_out_marker).count() > 0


def _goto_suite(page: Page, target_url: str) -> None:
    """Navigate to a Suite URL. The Suite keeps a long-poll connection so
    `networkidle` never fires; we settle for `domcontentloaded` and then
    wait briefly for either the signed-out marker or a textarea to render."""
    page.goto(target_url, wait_until="domcontentloaded", timeout=60_000)
    # Give React a beat; we'll check signed-in state on the next call.
    try:
        page.wait_for_selector(
            f"{SEL.signed_out_marker}, textarea, button:has-text('Generate')",
            timeout=15_000,
        )
    except PWTimeout:
        pass


def _ensure_logged_in(page: Page, target_url: str) -> None:
    """Navigate to target_url and ensure we're authenticated."""
    _goto_suite(page, target_url)
    if not _is_signed_out(page):
        return
    print(
        f"\n⚠ Not logged in. Please log in manually in the browser window. "
        f"I'll wait up to {LOGIN_TIMEOUT_S}s.\n",
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
# Generation flow — Setting popover (aspect + resolution radios, duration slider)
# ---------------------------------------------------------------------------
def _open_setting_popover(page: Page) -> None:
    """Click the Setting card to open its popover. Idempotent."""
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


def _select_aspect(page: Page, label: str = "9:16") -> None:
    _open_setting_popover(page)
    page.locator(SEL.aspect_radio_template.format(label=label)).first.click()


def _select_resolution(page: Page, label: str = "1080p") -> None:
    _open_setting_popover(page)
    page.locator(SEL.resolution_radio_template.format(label=label)).first.click()


def _set_duration(page: Page, seconds: int) -> None:
    """Drive the Radix duration slider via keyboard.

    aria-valuemin / aria-valuemax bound the slider; we focus the thumb,
    Home → vmin, then ArrowRight (target - vmin) times.
    """
    _open_setting_popover(page)
    slider = page.locator(SEL.duration_slider).first
    slider.wait_for(timeout=5_000)
    vmin = int(slider.get_attribute("aria-valuemin") or "0")
    vmax = int(slider.get_attribute("aria-valuemax") or "100")
    if seconds < vmin or seconds > vmax:
        raise ValueError(
            f"duration {seconds}s out of slider range [{vmin}, {vmax}] for this model",
        )
    slider.focus()
    page.keyboard.press("Home")
    for _ in range(seconds - vmin):
        page.keyboard.press("ArrowRight")
    actual = int(slider.get_attribute("aria-valuenow") or "0")
    if actual != seconds:
        slider.focus()
        page.keyboard.press("End")
        for _ in range(vmax - seconds):
            page.keyboard.press("ArrowLeft")


def _select_model_in_picker(page: Page, label: str) -> None:
    """Open the Model card popover and click the option matching `label`.

    Idempotent: skips clicking if the card already shows `label`.
    OpenArt's URL slug picks an initial model, but other actions
    (e.g. switching mode to Text-with-Reference) can silently swap it,
    so we always re-assert the model after mode changes.
    """
    card = page.locator(SEL.model_card).first
    try:
        card.wait_for(timeout=10_000)
    except PWTimeout:
        # Most common reasons this fires: page is showing a sign-out/verify
        # interstitial we don't detect, or OpenArt's DOM moved. Dump the page
        # so the operator can see what was actually on screen.
        _diagnose(page, "model_card_timeout")
        raise
    current = (card.text_content() or "").strip()
    if label in current:
        return
    card.click(force=True)
    dlg = page.get_by_role("dialog").first
    dlg.wait_for(timeout=5_000)
    dlg.locator(f"text=/^{re.escape(label)}/").first.click(force=True)
    # Dialog usually auto-closes after a model pick.
    time.sleep(1.5)


def _select_character(page: Page, character_name: str) -> None:
    """Pick a saved character from My Library → Characters → <name>.

    Diagnoses which step fails by saving a screenshot of the page state on
    each failure.
    """
    out_dir = REPO / ".playwright"
    out_dir.mkdir(parents=True, exist_ok=True)

    def step(label: str, action) -> None:
        try:
            action()
        except Exception as e:
            try:
                page.screenshot(path=str(out_dir / f"char_fail_{label}.png"), full_page=True)
            except Exception:
                pass
            raise RuntimeError(
                f"_select_character failed at step {label!r}: {e}. "
                f"See .playwright/char_fail_{label}.png",
            ) from e

    # 1. Activate "Characters" pill in the form (one of two pills under the
    #    prompt: "Upload Media" vs "Characters"). Click the one whose text is
    #    EXACTLY "Characters" (avoids matching "Characters & Worlds" or sidebar).
    def click_chars_pill():
        chars = page.locator("button").filter(has_text=re.compile(r"^Characters$")).first
        chars.wait_for(timeout=10_000)
        chars.click(force=True)
        time.sleep(0.6)
    step("1_chars_pill", click_chars_pill)

    # 2. Click "Add visual references" chip (opens side panel — defaults to
    #    the Image tab).
    def click_avr():
        avr = page.locator(SEL.add_references_trigger).first
        avr.wait_for(timeout=10_000)
        avr.click(force=True)
        # Wait for side panel to render — Characters & Worlds tab is the
        # most stable signal that the panel is up.
        page.locator(SEL.side_panel_cw_tab).first.wait_for(timeout=15_000)
    step("2_add_visual_references", click_avr)

    # 3a. Switch to Characters & Worlds top tab (panel opens on Image tab).
    def click_cw_tab():
        page.locator(SEL.side_panel_cw_tab).first.click(force=True)
        # Wait for sub-filters to render.
        page.locator(SEL.side_panel_my_library).first.wait_for(timeout=10_000)
        time.sleep(0.5)
    step("3a_cw_tab", click_cw_tab)

    # 3b. Source = My Library
    def click_my_library():
        page.locator(SEL.side_panel_my_library).first.click(force=True)
        time.sleep(0.8)
    step("3b_my_library", click_my_library)

    # 4. Category = Characters (sub-tab next to World shots)
    def click_chars_subtab():
        sub = page.locator(SEL.side_panel_chars_subtab).first
        sub.wait_for(timeout=10_000)
        sub.click(force=True)
        time.sleep(1.5)
    step("4_chars_subtab", click_chars_subtab)

    # 5. Click the named character.
    def click_character():
        name = page.locator(f"text=/{re.escape(character_name)}/").first
        name.wait_for(timeout=15_000)
        card = name.locator("xpath=ancestor::*[descendant::img][1]").first
        card.click(force=True)
        time.sleep(1.5)
    step("5_character", click_character)

    # 6. Best-effort confirm + close.
    for label in ("Add", "Confirm", "Done", "Apply"):
        try:
            btn = page.locator(f"button:has-text('{label}')")
            if btn.count() > 0 and btn.first.is_visible():
                btn.first.click(force=True, timeout=2_000)
                break
        except Exception:
            pass
    try:
        page.keyboard.press("Escape")
        time.sleep(0.5)
    except Exception:
        pass


def _find_audio_switch(page: Page):
    """Return the Audio toggle, or None if this model has no audio control.

    There may be multiple `[role='switch']` buttons on the page (Audio,
    Auto Polish, …) and some models (e.g. HappyHorse) don't have an Audio
    card at all. We pick the switch whose enclosing card text contains
    "Audio" but not "Polish"; returning None is a valid outcome.
    """
    # Brief wait to let the form settle. We don't *require* a switch.
    time.sleep(0.5)
    for sw in page.locator("button[role='switch']").all():
        try:
            card = sw.locator("xpath=ancestor::div[contains(@class,'group')][1]")
            if card.count() == 0:
                continue
            text = (card.first.text_content() or "").strip()
            if "Audio" in text and "Polish" not in text:
                return sw
        except Exception:
            continue
    return None


def _set_audio(page: Page, on: bool) -> None:
    """Toggle the Audio switch to the desired state. No-op when the model
    has no Audio control (e.g. HappyHorse never generates audio).
    """
    sw = _find_audio_switch(page)
    if sw is None:
        return
    target = "true" if on else "false"
    for attempt in range(3):
        actual = (sw.get_attribute("aria-checked") or "").lower()
        if actual == target:
            return
        sw.click(force=True)
        time.sleep(0.4)
    actual = (sw.get_attribute("aria-checked") or "").lower()
    if actual != target:
        raise RuntimeError(
            f"audio toggle would not stick: wanted {target!r}, got {actual!r}",
        )


def _set_variant_count(page: Page, target: int) -> None:
    """Click +/- on the video-count picker until it shows `target`.

    The picker reads its current value from the div between the two buttons.
    """
    if target < 1:
        raise ValueError(f"variant count must be ≥ 1, got {target}")
    # Read current count.
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
    """Fill the TipTap/ProseMirror contenteditable.

    `locator.fill()` doesn't trigger React's `onInput` for TipTap, so the
    Generate button stays disabled. We use real keyboard events instead.
    """
    box = page.locator(SEL.prompt_editor).first
    box.click()
    page.keyboard.press("ControlOrMeta+A")
    page.keyboard.press("Backspace")
    page.keyboard.insert_text(prompt)
    # Some React forms validate on blur — Tab away from the editor.
    page.keyboard.press("Tab")
    # Give React a beat to update form state.
    time.sleep(0.5)


def _diagnose(page: Page, where: str) -> None:
    """Dump editor text + a screenshot to .playwright/ for debugging."""
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
        page.screenshot(path=str(out_dir / f"diag_{where}.png"), full_page=True)
    except Exception:
        pass
    print(f"\n--- DIAG {where} ---", file=sys.stderr)
    print(f"  editor text length: {len(editor_text)}", file=sys.stderr)
    print(f"  editor text preview: {editor_text[:200]!r}", file=sys.stderr)
    print(f"  generate button disabled attr: {btn_disabled!r}", file=sys.stderr)
    print(f"  generate button text: {btn_text!r}", file=sys.stderr)
    print(f"  screenshot: {out_dir / f'diag_{where}.png'}", file=sys.stderr)


def _click_generate(page: Page) -> None:
    """Click Generate. Wait until OpenArt enables the button (form valid)."""
    btn = page.locator(SEL.generate_button).first
    btn.wait_for(timeout=15_000)
    deadline = time.time() + 15
    while time.time() < deadline:
        if btn.is_enabled():
            break
        time.sleep(0.3)
    else:
        _diagnose(page, "generate_disabled")
        raise RuntimeError(
            "Generate button still disabled — see diagnostic above and "
            ".playwright/diag_generate_disabled.png",
        )
    btn.click()


def _poll_resources(
    page: Page,
    resource_ids: list[str],
    timeout_s: int,
) -> list[tuple[str, dict]]:
    """Poll `/suite/api/resources/{id}` for each id until each settles.

    Returns one tuple per id in the same order as `resource_ids`:
        (resource_id, {"status": "ok", "url": <full-res CDN URL>, "metadata": {...}})
        (resource_id, {"status": "failed", "error": "<reason>"})
        (resource_id, {"status": "timeout"})

    Why this replaced the prior DOM-polling gallery approach:
      - The old `_wait_for_n_new_top` snapshotted gallery `<video>` URLs at
        baseline, then assumed any URL appearing above `baseline_top` was a
        new variant. When the gallery hadn't loaded at baseline-time
        (baseline_top=None), it treated *every* gallery URL as a result and
        could mis-attribute pre-existing videos (e.g. an earlier topic's
        cocoa-bean render) to the current submission.
      - The form-submission POST returns authoritative `resourceIds`. Polling
        the resource endpoint per id eliminates the ambiguity entirely.
    """
    import json
    pending = set(resource_ids)
    settled: dict[str, dict] = {}
    deadline = time.time() + timeout_s
    last_progress = -1

    while pending and time.time() < deadline:
        progress = len(settled)
        if progress != last_progress:
            print(f"    resolved {progress}/{len(resource_ids)}", file=sys.stderr)
            last_progress = progress
        for rid in list(pending):
            try:
                resp = page.context.request.get(
                    f"https://openart.ai/suite/api/resources/{rid}",
                    timeout=15_000,
                )
                if not resp.ok:
                    # 404 right after submit is normal — resource record
                    # not registered yet. Anything else is terminal.
                    if resp.status == 404:
                        continue
                    settled[rid] = {"status": "failed", "error": f"HTTP {resp.status}"}
                    pending.discard(rid)
                    continue
                data = json.loads(resp.text()).get("data") or {}
                url = data.get("url")
                if url:
                    settled[rid] = {
                        "status": "ok",
                        "url": url,
                        "metadata": data.get("metadata") or {},
                    }
                    pending.discard(rid)
                    continue
                if data.get("state") in {"failed", "error"} or data.get("error"):
                    settled[rid] = {
                        "status": "failed",
                        "error": data.get("error") or data.get("state") or "unknown",
                    }
                    pending.discard(rid)
            except Exception:
                # Transient — try again next round
                pass
        if pending:
            time.sleep(POLL_INTERVAL_S)

    for rid in resource_ids:
        if rid not in settled:
            settled[rid] = {"status": "timeout"}
    return [(rid, settled[rid]) for rid in resource_ids]


def _download_via_context(page: Page, url: str, output_path: Path) -> Path:
    """Download via the browser's authenticated request context.

    OpenArt's CDN signs URLs against the session, so a bare HTTP GET 403s.
    Using `page.context.request` carries cookies + auth headers.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    resp = page.context.request.get(url, timeout=120_000)
    if not resp.ok:
        raise RuntimeError(f"download failed: HTTP {resp.status} for {url}")
    output_path.write_bytes(resp.body())
    return output_path


def _strip_audio_in_place(path: Path) -> None:
    """Remux the file to drop any audio track. Pure stream copy — fast.

    OpenArt's API ignores the form's audio toggle for some models (Wan 2.7
    in particular), so videos arrive with audio even when the UI shows it
    off. Strip after download so the on-disk file matches the UI state.
    """
    import subprocess
    # Tmp file must keep the extension or ffmpeg can't pick the muxer.
    tmp = path.with_name(f".muted_{path.name}")
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(path),
        "-an",            # drop audio
        "-c:v", "copy",   # no re-encode
        "-movflags", "+faststart",
        str(tmp),
    ]
    subprocess.run(cmd, check=True)
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
@contextmanager
def _browser(p: Playwright, headless: bool):
    """Persistent context with stored auth state."""
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


def generate_clip(
    prompt: str,
    model: str,
    duration_s: int,
    output_paths: list[Path],
    headless: bool = False,
    audio_on: bool = False,
    character: str | None = None,
) -> list[Path]:
    """Drive openart.ai to generate `len(output_paths)` variants and download each.

    Args:
        prompt: full prompt text.
        model: model name (e.g. "Seedance 2.0", "HappyHorse").
        duration_s: clip duration in seconds.
        output_paths: one destination path per variant. Length determines
            the variant count submitted to OpenArt.
        headless: open a visible window when False (recommended for debug).
        audio_on: leave audio enabled when True (default off — captions and
            VO are added in post for the trivia pipeline).

    Returns the list of saved paths in newest-first gallery order, aligned
    with `output_paths` (i.e. output_paths[0] = newest variant).
    """
    if not output_paths:
        raise ValueError("output_paths must contain at least one path")
    output_paths = [Path(p).expanduser().resolve() for p in output_paths]
    n = len(output_paths)
    target_url = _model_url(model)
    with sync_playwright() as p, _browser(p, headless=headless) as ctx:
        page = ctx.new_page()
        _ensure_logged_in(page, target_url)

        # Switch input mode to "Text with Reference" — references are
        # optional, but on this UI it's the path that exposes saved
        # characters. Note: clicking this mode silently swaps the model
        # to the user's last-used reference-capable one (often Wan 2.7);
        # we re-assert the desired model immediately after.
        try:
            page.locator(SEL.mode_text_with_reference).first.click(timeout=5_000)
            time.sleep(1.0)
        except PWTimeout:
            pass

        _select_model_in_picker(page, model)

        if character:
            print(f"  → selecting character: {character}", file=sys.stderr)
            _select_character(page, character)

        # Configure inside the Setting popover, then close it.
        _select_aspect(page, "9:16")
        _select_resolution(page, "1080p")
        _set_duration(page, duration_s)
        _close_popover(page)

        _set_audio(page, audio_on)
        _set_variant_count(page, n)

        _enter_prompt(page, prompt)

        # Re-assert the audio toggle right before submit. It has been
        # observed to revert after other interactions (popover, character
        # picker), so we double-check the state at the moment of submission.
        _set_audio(page, audio_on)

        _sw = _find_audio_switch(page)
        sw_state = _sw.get_attribute("aria-checked") if _sw else "n/a"
        print(
            f"  → submit (model={model}, dur={duration_s}s, prompt={len(prompt)} chars, "
            f"variants={n}, audio={'on' if audio_on else 'off'} (toggle={sw_state})"
            f"{f', char={character}' if character else ''})",
            file=sys.stderr,
        )

        # Capture the form-submission POST response — it returns the
        # `resourceIds` for the variants this submission produced. We then
        # poll `/api/resources/{id}` per variant to get the real CDN URL.
        # This replaces the prior gallery-DOM scraping which could (and did)
        # mis-attribute older gallery items to the current submission when
        # the gallery hadn't loaded at baseline-snapshot time.
        with page.expect_response(
            lambda r: "/suite/api/forms/creations/" in r.url and r.request.method == "POST",
            timeout=30_000,
        ) as resp_info:
            _click_generate(page)
        resp = resp_info.value
        if not resp.ok:
            raise RuntimeError(f"submit POST returned HTTP {resp.status}: {resp.text()[:200]}")
        import json
        submit_data = json.loads(resp.text())
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
        for (rid, info), dest in zip(resolved, output_paths):
            if info.get("status") != "ok":
                print(f"  ✗ {rid}: {info.get('status')} ({info.get('error')})", file=sys.stderr)
                continue
            url = info["url"]
            print(f"  → {rid} URL: {url}", file=sys.stderr)
            _download_via_context(page, url, dest)
            if not audio_on:
                _strip_audio_in_place(dest)
            meta = info.get("metadata") or {}
            dims = f"{meta.get('width')}x{meta.get('height')}" if meta else "?"
            dur = meta.get("duration")
            print(
                f"  ✓ saved {dest}{' (muted)' if not audio_on else ''}  "
                f"({dims}, {dur}s)",
                file=sys.stderr,
            )
            saved.append(dest)
        if len(saved) < n:
            raise RuntimeError(f"only {len(saved)}/{n} variants saved")
        return saved


# ---------------------------------------------------------------------------
# Smoke-test CLI
# ---------------------------------------------------------------------------
def _scrape_settings(model: str = "Seedance 2.0") -> int:
    """Open each settings card and dump its popover content."""
    import json
    target_url = _model_url(model)
    out_dir = REPO / ".playwright"
    out_dir.mkdir(parents=True, exist_ok=True)

    cards_to_open = ["Setting", "Mode"]  # the ones with ArrowRight
    findings: dict = {}

    with sync_playwright() as p, _browser(p, headless=False) as ctx:
        page = ctx.new_page()
        _ensure_logged_in(page, target_url)
        time.sleep(2)

        for card_label in cards_to_open:
            print(f"\n=== opening {card_label!r} ===")
            try:
                # Click the card by matching its visible label
                card = page.locator(
                    f"div.group:has-text('{card_label}')"
                ).first
                card.click()
                time.sleep(1.5)
                # The popover content is somewhere outside the card; grab body text
                body_html = page.locator("body").inner_html()
                (out_dir / f"settings_{card_label.lower()}.html").write_text(body_html)
                # Also dump role-based items just-appeared
                roles = {}
                for r in ("dialog", "menu", "listbox", "option", "tab", "radio"):
                    items = page.get_by_role(r).all()
                    roles[r] = [(it.text_content() or "").strip()[:80] for it in items[:60]]
                findings[card_label] = roles
                print(json.dumps(roles, indent=2)[:2000])
                # Close: press Escape
                page.keyboard.press("Escape")
                time.sleep(0.8)
            except Exception as e:
                print(f"failed: {e}")
                findings[card_label] = {"error": str(e)}

        (out_dir / "settings_dump.json").write_text(json.dumps(findings, indent=2))
        print(f"\n✓ saved findings to {out_dir / 'settings_dump.json'}")
    return 0


def _scrape(model: str = "Seedance 2.0", out_path: Path | None = None) -> int:
    """Headed: log in once, then dump a structured summary of the authed DOM.

    The output is JSON written to `out_path` (default: .playwright/scrape.json)
    plus an HTML snapshot at .playwright/scrape.html. We capture textareas,
    buttons, comboboxes, role=option items, anything with a stable test-id,
    and a few targeted heuristic clusters near the prompt area.
    """
    import json

    target_url = _model_url(model)
    out_dir = REPO / ".playwright"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = Path(out_path) if out_path else out_dir / "scrape.json"
    html_path = out_dir / "scrape.html"

    with sync_playwright() as p, _browser(p, headless=False) as ctx:
        page = ctx.new_page()
        _ensure_logged_in(page, target_url)

        # Give the post-login page a couple seconds to fully render.
        time.sleep(3)

        def loc_summary(locator) -> list[dict]:
            out: list[dict] = []
            try:
                els = locator.all()
            except Exception:
                return out
            for el in els[:80]:
                try:
                    out.append({
                        "text": (el.text_content() or "").strip()[:140],
                        "aria_label": el.get_attribute("aria-label") or "",
                        "role": el.get_attribute("role") or "",
                        "type": el.get_attribute("type") or "",
                        "name": el.get_attribute("name") or "",
                        "id": el.get_attribute("id") or "",
                        "data_testid": el.get_attribute("data-testid") or "",
                        "placeholder": el.get_attribute("placeholder") or "",
                        "visible": el.is_visible(),
                    })
                except Exception:
                    pass
            return out

        summary: dict = {
            "url": page.url,
            "title": page.title(),
            "textareas": loc_summary(page.locator("textarea")),
            "inputs": loc_summary(page.locator("input")),
            "buttons": loc_summary(page.locator("button")),
            "comboboxes": loc_summary(page.get_by_role("combobox")),
            "options": loc_summary(page.get_by_role("option")),
            "test_id_elements": loc_summary(page.locator("[data-testid]")),
        }

        # Dump full HTML snapshot for any deeper digging.
        try:
            html_path.write_text(page.content())
        except Exception:
            pass

        json_path.write_text(json.dumps(summary, indent=2))
        print(f"\n✓ scrape complete")
        print(f"  json:    {json_path}")
        print(f"  html:    {html_path}")
        print(f"  buttons: {len(summary['buttons'])}")
        print(f"  inputs:  {len(summary['inputs'])}  textareas: {len(summary['textareas'])}")
        print(f"  combos:  {len(summary['comboboxes'])}  options: {len(summary['options'])}")
        print(f"  testids: {len(summary['test_id_elements'])}")
    return 0


def _probe(model: str = "Seedance 2.0") -> int:
    """Open OpenArt's per-model page, ensure login, then pause for inspection."""
    target_url = _model_url(model)
    with sync_playwright() as p, _browser(p, headless=False) as ctx:
        page = ctx.new_page()
        _ensure_logged_in(page, target_url)
        print(f"\n— probe mode — at {target_url}; opening Playwright Inspector.")
        print("  Use it to verify the selectors in `SELECTORS` at the top of this file.")
        print("  Close the Inspector window to exit.\n")
        page.pause()
    return 0


def _main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true",
                    help="open OpenArt + Playwright Inspector for selector tuning")
    ap.add_argument("--scrape", action="store_true",
                    help="open OpenArt headed; on login, dump JSON+HTML of the authed DOM")
    ap.add_argument("--scrape-settings", action="store_true",
                    help="open settings cards (Setting, Mode) and dump their popover content")
    ap.add_argument("--prompt")
    ap.add_argument("--model", default="Seedance 2.0",
                    help="e.g. 'Seedance 2.0', 'HappyHorse'")
    ap.add_argument("--duration", type=int)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--headless", action="store_true")
    args = ap.parse_args()

    if args.scrape:
        return _scrape(args.model)
    if args.scrape_settings:
        return _scrape_settings(args.model)
    if args.probe:
        return _probe(args.model)
    missing = [n for n in ("prompt", "duration", "out") if getattr(args, n) is None]
    if missing:
        ap.error(f"missing required args: {missing}")
    out = generate_clip(args.prompt, args.model, args.duration, args.out, headless=args.headless)
    print(f"saved: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
