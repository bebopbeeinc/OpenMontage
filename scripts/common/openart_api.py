#!/usr/bin/env python
"""Typed helpers over the OpenArt MCP tools.

`openart_mcp` owns auth and transport; this module owns the OpenArt domain:
model names, workspaces, credit prechecks, reference uploads, submit/poll, and
downloading the finished bytes. The two drivers (`openart_driver` for video,
`trivia_images/openart_image_driver` for images) are thin wrappers over this.

Everything here is synchronous and thread-safe, because the trivia-images
server calls it from worker threads.
"""
from __future__ import annotations

import mimetypes
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

import requests

import openart_mcp as mcp
from openart_mcp import (  # noqa: F401 - re-exported for callers
    OpenArtAuthError,
    OpenArtMCPError,
    call_tool,
)

# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------


class OpenArtOutOfCreditsError(RuntimeError):
    """Every candidate workspace lacks the credits for this job.

    Retryable once credits exist — the prompt itself is fine.
    """


class OpenArtGenerationError(RuntimeError):
    """OpenArt ran the job but produced no usable output.

    `code` carries OpenArt's own failure code when it gave one. The common
    case is a content-policy refusal, which is permanent — do not retry it.
    """

    def __init__(self, message: str, code: Optional[str] = None) -> None:
        super().__init__(message)
        self.code = code


class OpenArtRateLimitError(OpenArtGenerationError):
    """Upstream provider quota (HTTP 429). Transient — safe to retry."""


class OpenArtModelUnavailableError(RuntimeError):
    """The requested model has no route on the MCP surface."""


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------

# Display name (what the callers and style YAMLs say) -> MCP model id.
# Keep the display names stable: they appear in sheets, style playbooks and
# CLI flags across the trivia pipelines.
MODEL_IDS: dict[str, str] = {
    "Nano Banana 2":       "nano-banana-2",
    "Nano Banana 2 Lite":  "nano-banana-2-lite",
    "Nano Banana Pro":     "nano-banana-pro",
    "GPT Image 2":         "gpt-image-2",
    "Seedream 4.5":        "byte-plus-seedream-4-5",
    "Seedream 5 Pro":      "byte-plus-seedream-5-pro",
    "Seedance 2.0":        "byte-plus-seedance-2",
    "Seedance 2.0 Fast":   "byte-plus-seedance-2-fast",
    "Seedance 2.5":        "byte-plus-seedance-2-5",
    "Kling 3.0 Omni":      "kling-3-omni",
    "Kling 3 Omni":        "kling-3-omni",
    "Veo 3.1":             "veo3-1",
    "Wan 2.7":             "wan2-7",
}

# Models the Playwright drivers could reach through the web UI that the MCP
# surface does not expose. Named explicitly so the failure says *why* rather
# than "unknown model" — and so nothing silently substitutes a different
# generator, which the project's governance rules forbid.
MODELS_WITHOUT_MCP_ROUTE: dict[str, str] = {
    "Nano Banana": (
        "\"Nano Banana\" (the original) is not offered by the OpenArt MCP "
        "server. Use \"Nano Banana 2\" or \"Nano Banana Pro\"."
    ),
    "HappyHorse": (
        "HappyHorse is not offered by the OpenArt MCP server (it was only ever "
        "reachable through the web UI). Pick a model from openart_model_list — "
        "Seedance 2.0 Fast is the closest short-clip option — or generate this "
        "segment by hand in the OpenArt UI."
    ),
}


def model_id(display_name: str) -> str:
    """Map a display name to its MCP model id."""
    if display_name in MODEL_IDS:
        return MODEL_IDS[display_name]
    if display_name in MODELS_WITHOUT_MCP_ROUTE:
        raise OpenArtModelUnavailableError(MODELS_WITHOUT_MCP_ROUTE[display_name])
    # Allow raw MCP ids to pass through, so a caller can name a model this map
    # hasn't caught up with yet.
    if re.fullmatch(r"[a-z0-9][a-z0-9-]*", display_name):
        return display_name
    raise OpenArtModelUnavailableError(
        f"unknown model {display_name!r}. Known: {sorted(MODEL_IDS)}",
    )


# --------------------------------------------------------------------------
# Account / workspace
# --------------------------------------------------------------------------

def account() -> dict:
    """Email, plan, and the credit balance of the *current* workspace."""
    return call_tool("openart_account_get")


def workspaces() -> list[dict]:
    return call_tool("openart_workspace_list").get("workspaces", [])


# The workspace `ensure_workspace` last established. NOT a cache of "which
# workspace is active" — another session can change that at any time, which is
# exactly why the drivers re-assert it per run. It exists only so callers that
# need the id for a local cache key don't re-query what we just fetched.
_LAST_WORKSPACE: Optional[dict] = None


def current_workspace() -> Optional[dict]:
    for ws in workspaces():
        if ws.get("isCurrent"):
            return ws
    return None


def last_known_workspace() -> Optional[dict]:
    """The workspace we most recently selected, else a live lookup."""
    return _LAST_WORKSPACE or current_workspace()


def ensure_workspace(name: Optional[str]) -> Optional[dict]:
    """Make `name` the active workspace, switching only when it isn't already.

    OpenArt persists the active workspace per account, and other sessions can
    change it underneath us — the Playwright drivers re-asserted it on every
    run for exactly this reason, and so do we.
    """
    global _LAST_WORKSPACE
    if not name:
        _LAST_WORKSPACE = current_workspace()
        return _LAST_WORKSPACE
    found = None
    for ws in workspaces():
        if ws.get("name") == name:
            found = ws
            if ws.get("isCurrent"):
                _LAST_WORKSPACE = ws
                return ws
            break
    if found is None:
        raise OpenArtMCPError(
            f"workspace {name!r} not found. Available: "
            f"{[w.get('name') for w in workspaces()]}",
        )
    call_tool("openart_workspace_select", {"id": found["id"]})
    _LAST_WORKSPACE = found
    return found


def credits() -> Optional[int]:
    value = account().get("credits")
    return int(value) if isinstance(value, (int, float)) else None


def estimate_credits(model: str, mode: str, params: Optional[dict] = None) -> Optional[int]:
    """Cost of one job at these exact settings, or None if unpriceable."""
    args: dict[str, Any] = {"model": model, "mode": mode}
    if params:
        args["params"] = params
    try:
        cost = call_tool("openart_model_cost", args)
    except OpenArtMCPError:
        return None
    items = cost.get("items") or []
    if not items:
        return None
    total = items[0].get("totalCredits")
    return int(total) if isinstance(total, (int, float)) else None


def check_affordable(model: str, mode: str, params: Optional[dict] = None) -> None:
    """Raise OpenArtOutOfCreditsError if the active workspace can't pay.

    Replaces the old driver's paywall-phrase scraping: the balance and the
    price are both first-class API values now.
    """
    have = credits()
    want = estimate_credits(model, mode, params)
    if have is None or want is None:
        return  # can't price it — let the generate call be the judge
    if have < want:
        ws = current_workspace() or {}
        raise OpenArtOutOfCreditsError(
            f"workspace {ws.get('name', '?')!r} has {have} credits, "
            f"{model}/{mode} costs {want}",
        )


# --------------------------------------------------------------------------
# Model forms
# --------------------------------------------------------------------------

_FORM_CACHE: dict[tuple[str, str], dict] = {}
_FORM_LOCK = threading.Lock()


def form_schema(model: str, mode: str) -> dict:
    """The JSON schema of a model+mode's form fields, cached per process."""
    key = (model, mode)
    with _FORM_LOCK:
        if key not in _FORM_CACHE:
            res = call_tool("openart_model_form_get", {"model": model, "mode": mode})
            _FORM_CACHE[key] = res.get("jsonSchema") or {}
        return _FORM_CACHE[key]


def filter_params(model: str, mode: str, params: dict) -> dict:
    """Drop params this model+mode doesn't accept.

    Form fields differ per model — Seedance takes `duration` and `generateAudio`,
    Nano Banana doesn't — and the API rejects unknown keys outright
    (`additionalProperties: false`). Letting the schema decide means the drivers
    can offer one superset of options without a per-model branch.
    """
    props = form_schema(model, mode).get("properties")
    if not isinstance(props, dict) or not props:
        return dict(params)
    return {k: v for k, v in params.items() if k in props and v is not None}


# --------------------------------------------------------------------------
# Reference uploads
# --------------------------------------------------------------------------

UPLOAD_TIMEOUT_S = 120


def upload_reference(
    path: Path,
    media_type: str = "image",
    purpose: str = "create-image",
    label: Optional[str] = None,
) -> dict:
    """Upload a local file and return a visualReference ready for generation.

    Three steps, per the MCP upload contract: sign, PUT the bytes ourselves
    (this is a headless client, so there's no widget to do it), then confirm
    acceptance and take the verified reference back.
    """
    path = Path(path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"reference file does not exist: {path}")
    size = path.stat().st_size
    content_type = mimetypes.guess_type(path.name)[0] or f"{media_type}/octet-stream"
    label = label or path.name

    signed = call_tool("openart_upload_sign", {
        "mediaType": media_type,
        "size": size,
        "contentType": content_type,
        "filename": path.name,
        "label": label,
        "purpose": purpose,
    })
    sign_url = signed.get("signURL") or signed.get("signUrl")
    access_url = signed.get("accessURL") or signed.get("accessUrl")
    if not sign_url or not access_url:
        raise OpenArtMCPError(f"upload_sign returned no URLs: {signed}")

    put = requests.put(
        sign_url,
        data=path.read_bytes(),
        headers={"Content-Type": content_type},
        timeout=UPLOAD_TIMEOUT_S,
    )
    if not put.ok:
        raise OpenArtMCPError(f"reference upload PUT failed: {put.status_code} {put.text[:200]}")

    meta_args = {"mediaUrl": access_url, "mediaType": media_type, "label": label}
    if signed.get("uploadId"):
        meta_args["uploadId"] = signed["uploadId"]
    meta = call_tool("openart_upload_metadata_get", meta_args)
    ref = meta.get("visualReference") or signed.get("visualReference")
    if not ref:
        raise OpenArtMCPError(f"upload was not accepted by OpenArt: {meta}")
    return ref


def reference_for_url(
    url: str,
    label: Optional[str] = None,
    media_type: str = "image",
) -> dict:
    """Resolve an existing OpenArt media URL into a usable visualReference.

    For media already in OpenArt (a saved character's reference image, say),
    there is nothing to upload — but the generate form needs a full reference
    object, not a bare URL. `openart_upload_metadata_get` inspects existing
    media and hands back exactly that, with the right id attached.
    """
    meta = call_tool("openart_upload_metadata_get", {
        "mediaUrl": url,
        "mediaType": media_type,
        "label": label or url.split("?", 1)[0].rsplit("/", 1)[-1],
    })
    ref = meta.get("visualReference")
    if not ref:
        raise OpenArtMCPError(f"OpenArt did not resolve {url} to a reference: {meta}")
    return ref


# --------------------------------------------------------------------------
# Generate / poll / download
# --------------------------------------------------------------------------

WAIT_WINDOW_S = 90          # per openart_creation_wait call (the tool's max)
DEFAULT_TOTAL_TIMEOUT_S = 600


def submit(media: str, model: str, mode: str, params: dict,
           project_id: Optional[str] = None) -> str:
    """Submit a generation and return its historyId."""
    tool = "openart_generate_image" if media == "image" else "openart_generate_video"
    args: dict[str, Any] = {"model": model, "mode": mode, "params": params}
    if project_id:
        args["projectId"] = project_id
    try:
        res = call_tool(tool, args)
    except OpenArtMCPError as e:
        raise _classify(str(e)) from e
    history_id = res.get("historyId")
    if not history_id:
        raise OpenArtGenerationError(f"{tool} returned no historyId: {res}")
    return history_id


def wait(history_id: str, total_timeout_s: int = DEFAULT_TOTAL_TIMEOUT_S) -> dict:
    """Block until the generation reaches a terminal state, then return it.

    `openart_creation_wait` caps each call at 90s and answers STILL_RUNNING
    when the job outlasts the window — that is not a failure, so we keep
    calling with the same historyId until the overall budget runs out.
    """
    deadline = time.time() + total_timeout_s
    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            raise OpenArtGenerationError(
                f"generation {history_id} did not finish within {total_timeout_s}s",
                code="timeout",
            )
        window = max(1, min(WAIT_WINDOW_S, int(remaining)))
        rec = call_tool(
            "openart_creation_wait",
            {"historyId": history_id, "timeoutSeconds": window},
            # The server holds this open for `window` seconds by design; the
            # transport has to outlast it or we hang up on a healthy wait.
            read_timeout=window + mcp.LONG_POLL_MARGIN_S,
        )
        status = str(rec.get("status", "")).upper()
        if status == "COMPLETED":
            return rec
        if status in ("FAILED", "CANCELLED"):
            raise _classify(_failure_message(rec, status), rec)
        time.sleep(max(1, int(rec.get("pollAfterSeconds") or 3)))


_RATE_LIMIT_SIGNALS = ("rate_limit", "rate limit", "429", "quota", "too many requests")
_CREDIT_SIGNALS = ("out of credit", "insufficient credit", "not enough credit",
                   "credit balance", "upgrade your plan")


def _classify(message: str, record: Optional[dict] = None) -> RuntimeError:
    """Turn a failure message into the most specific error class we can.

    The distinction matters to callers: rate limits are retryable, credits are
    retryable once topped up, and content-policy blocks are permanent.
    """
    code = None
    if record:
        code = record.get("failedCode") or record.get("errorCode") or record.get("code")
    haystack = f"{code or ''} {message}".lower()
    if any(s in haystack for s in _RATE_LIMIT_SIGNALS):
        return OpenArtRateLimitError(message, code=str(code) if code else "rate_limit")
    if any(s in haystack for s in _CREDIT_SIGNALS):
        return OpenArtOutOfCreditsError(message)
    return OpenArtGenerationError(message, code=str(code) if code else None)


def _failure_message(rec: dict, status: str) -> str:
    for field in ("error", "errorMessage", "failedReason", "message", "reason"):
        value = rec.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    if status == "CANCELLED":
        return "OpenArt cancelled the generation"
    return "OpenArt reported FAILED with no reason"


def result_urls(record: dict) -> list[str]:
    """Every downloadable asset URL in a completed record, newest-first.

    The wait payload has grown fields over time, so we sweep the known list
    shapes rather than pinning one.
    """
    urls: list[str] = []

    def take(value: Any) -> None:
        if isinstance(value, str) and value.startswith("http") and value not in urls:
            urls.append(value)

    for key in ("resources", "items", "assets", "results", "outputs"):
        for entry in _as_list(record.get(key)):
            if isinstance(entry, dict):
                take(entry.get("url") or entry.get("resourceUrl") or entry.get("mediaUrl"))
            else:
                take(entry)
    if not urls:
        take(record.get("url"))
    return urls


def _as_list(value: Any) -> Iterable:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return value.values()
    return ()


DOWNLOAD_TIMEOUT_S = 300


def download(url: str, output_path: Path, keep_source_ext: bool = False) -> Path:
    """Stream one finished asset to disk.

    OpenArt's CDN serves these unsigned, so a plain GET is enough — the old
    driver only needed the browser context because it resolved assets through
    the session-scoped gallery API.
    """
    output_path = Path(output_path).expanduser().resolve()
    if keep_source_ext:
        ext = _extension_for(url)
        if ext and output_path.suffix.lower() != ext:
            output_path = output_path.with_suffix(ext)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with requests.get(url, stream=True, timeout=DOWNLOAD_TIMEOUT_S) as r:
        r.raise_for_status()
        with open(output_path, "wb") as fh:
            for chunk in r.iter_content(chunk_size=1 << 16):
                fh.write(chunk)
    return output_path


def _extension_for(url: str) -> Optional[str]:
    stem = url.split("?", 1)[0].rsplit("/", 1)[-1]
    if "." in stem:
        ext = "." + stem.rsplit(".", 1)[-1].lower()
        if 2 <= len(ext) <= 6:
            return ext
    return None


def generate(
    media: str,
    model_display: str,
    mode: str,
    params: "dict | Callable[[], dict]",
    output_paths: list[Path],
    workspace: Optional[str] = None,
    fallback_workspaces: tuple[str, ...] = (),
    keep_source_ext: bool = False,
    total_timeout_s: int = DEFAULT_TOTAL_TIMEOUT_S,
    project_id: Optional[str] = None,
) -> list[Path]:
    """Submit one job, wait for it, and download each variant.

    Tries `workspace` first, then each of `fallback_workspaces` in order, but
    only for credit exhaustion — a content-policy block would fail the same way
    everywhere, so it propagates immediately.

    `params` may be a callable, which is re-invoked after each workspace
    switch. Reference uploads are workspace-scoped, so anything that uploads
    must be built that way or a fallback would submit references belonging to
    the workspace it just left.

    Returns the saved paths aligned with `output_paths` (newest variant first).
    """
    mid = model_id(model_display)
    candidates = [workspace, *fallback_workspaces] if workspace else [None]

    last_credit_error: Optional[OpenArtOutOfCreditsError] = None
    for candidate in candidates:
        if candidate:
            print(f"  → workspace: {candidate}", file=sys.stderr)
            ensure_workspace(candidate)
        try:
            built = filter_params(mid, mode, params() if callable(params) else params)
            check_affordable(mid, mode, built)
            print(f"  → submit {mid}/{mode}", file=sys.stderr)
            history_id = submit(media, mid, mode, built, project_id=project_id)
            print(f"  → submitted ({history_id}); waiting", file=sys.stderr)
            record = wait(history_id, total_timeout_s=total_timeout_s)
        except OpenArtOutOfCreditsError as e:
            last_credit_error = e
            print(f"  ! out of credits in {candidate or 'active workspace'}", file=sys.stderr)
            continue

        urls = result_urls(record)
        if not urls:
            raise OpenArtGenerationError(
                f"generation {history_id} completed with no output — OpenArt "
                f"usually means a content-policy block here; check the prompt",
            )
        saved: list[Path] = []
        for dest, url in zip(output_paths, urls):
            saved.append(download(url, dest, keep_source_ext=keep_source_ext))
        if len(urls) < len(output_paths):
            print(f"  ! got {len(urls)} variant(s), asked for {len(output_paths)}",
                  file=sys.stderr)
        return saved

    raise last_credit_error or OpenArtOutOfCreditsError("no workspace could pay for this job")
