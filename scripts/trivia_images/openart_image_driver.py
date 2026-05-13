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

Smoke test:
    python scripts/trivia_images/openart_image_driver.py \
      --prompt "a tiny mosquito on a podium with a giant crown, Pixar style" \
      --model "Nano Banana Pro" --out scripts/trivia_images/library/_smoketest.jpg
"""
from __future__ import annotations

import argparse
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
    signed_out_marker: str = "button:has-text('Sign up to create for FREE')"
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


SEL = Selectors()


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
def _is_signed_out(page: Page) -> bool:
    return page.locator(SEL.signed_out_marker).count() > 0


def _goto_suite(page: Page, target_url: str) -> None:
    page.goto(target_url, wait_until="domcontentloaded", timeout=60_000)
    try:
        page.wait_for_selector(
            f"{SEL.signed_out_marker}, {SEL.prompt_editor}, {SEL.generate_button}",
            timeout=15_000,
        )
    except PWTimeout:
        pass


def _ensure_logged_in(page: Page, target_url: str) -> None:
    _goto_suite(page, target_url)
    if not _is_signed_out(page):
        return
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


def _select_aspect(page: Page, label: str = "4:3") -> None:
    _open_setting_popover(page)
    page.locator(SEL.aspect_radio_template.format(label=label)).first.click()


def _select_resolution(page: Page, label: str = "2K") -> None:
    _open_setting_popover(page)
    page.locator(SEL.resolution_radio_template.format(label=label)).first.click()


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


def _poll_resources(
    page: Page,
    resource_ids: list[str],
    timeout_s: int,
) -> list[tuple[str, dict]]:
    """Poll `/suite/api/resources/{id}` for each resourceId until each settles.

    Returns one tuple per id, in the same order as `resource_ids`:
        (resource_id, {"status": "ok", "url": <full-res CDN URL>, "metadata": {...}})
        (resource_id, {"status": "failed", "error": "<reason>"})
        (resource_id, {"status": "timeout"})

    The resource endpoint becomes available shortly after the POST submit
    response — initially without `url`, then populated once the generation
    completes (success or fail). We poll until every id has either:
      - a non-empty `url` (success), or
      - an error/state field indicating failure.
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
                    # 404 right after submit is normal — resource not registered yet.
                    if resp.status == 404:
                        continue
                    settled[rid] = {"status": "failed", "error": f"HTTP {resp.status}"}
                    pending.discard(rid)
                    continue
                data = json.loads(resp.text()).get("data") or {}
                url = data.get("url")
                # Some failure states surface as an `error`, `state`, or empty url
                # with `completed_at` set. Treat presence of `url` as success.
                if url:
                    settled[rid] = {
                        "status": "ok",
                        "url": url,
                        "metadata": data.get("metadata") or {},
                    }
                    pending.discard(rid)
                    continue
                # Detect terminal failure: completedAt populated but no url
                if data.get("state") in {"failed", "error"} or data.get("error"):
                    settled[rid] = {
                        "status": "failed",
                        "error": data.get("error") or data.get("state") or "unknown",
                    }
                    pending.discard(rid)
            except Exception as e:
                # Transient — try again next round
                pass
        if pending:
            time.sleep(POLL_INTERVAL_S)

    for rid in resource_ids:
        if rid not in settled:
            settled[rid] = {"status": "timeout"}
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

    Returns saved paths in newest-first gallery order, aligned with
    `output_paths` (output_paths[0] = newest variant).
    """
    if not output_paths:
        raise ValueError("output_paths must contain at least one path")
    output_paths = [Path(p).expanduser().resolve() for p in output_paths]
    if reference_image_path is not None:
        reference_image_path = Path(reference_image_path).expanduser().resolve()
    n = len(output_paths)
    target_url = _model_url(model)

    with sync_playwright() as p, _browser(p, headless=headless) as ctx:
        page = ctx.new_page()
        _ensure_logged_in(page, target_url)

        _select_model_in_picker(page, model)
        _select_aspect(page, aspect)
        _select_resolution(page, resolution)
        _close_popover(page)

        # Attach the reference BEFORE entering the prompt so the upload has
        # time to settle while we fill the rest of the form. The wait inside
        # _attach_reference_image still gates submission on the CDN URL.
        if reference_image_path is not None:
            _attach_reference_image(page, reference_image_path)

        _set_variant_count(page, n)
        _enter_prompt(page, prompt)

        print(
            f"  → submit (model={model}, aspect={aspect}, res={resolution}, "
            f"prompt={len(prompt)} chars, variants={n})",
            file=sys.stderr,
        )

        # Capture the submit POST response — it contains the resourceIds we
        # need to look up the full-res CDN URLs via /api/resources/{id}.
        # expect_response wraps the click so we don't miss it.
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
    )
    for s in saved:
        print(f"saved: {s}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
