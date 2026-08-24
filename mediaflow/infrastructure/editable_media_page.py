from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

EDITABLE_MEDIA_PAGE_TIMEOUT_MS = 15_000

_EDITABLE_MEDIA_READY = """() => window.editableMedia
    && window.editableMedia.ready instanceof Promise
    && window.__hf
    && typeof window.__hf.seek === "function"
    && window.__hf.duration > 0"""

_AWAIT_PAGE_ASSETS = """async () => {
    await document.fonts.ready;
    await Promise.all(Array.from(document.images).map(image => image.decode()));
}"""

_ROUND_TRIP_RUNTIME_STATE = """state => {
    window.editableMedia.setState(state);
    return window.editableMedia.getState();
}"""


def wait_for_editable_media_runtime(page) -> None:
    page.wait_for_function(
        _EDITABLE_MEDIA_READY,
        timeout=EDITABLE_MEDIA_PAGE_TIMEOUT_MS,
    )
    page.evaluate("() => window.editableMedia.ready")


def wait_for_editable_media_assets(page) -> None:
    page.evaluate(_AWAIT_PAGE_ASSETS)


def open_editable_media_page(
    browser: Any,
    *,
    url: str,
    width: int,
    height: int,
    owns_url: Callable[[str], bool],
    runtime_state: Mapping[str, Any] | None = None,
    state_error: Callable[[], BaseException] | None = None,
    transparent_background: bool = False,
    on_request: Callable[[Any], None] | None = None,
    on_page_error: Callable[[Any], None] | None = None,
    on_request_failed: Callable[[Any], None] | None = None,
) -> tuple[Any, Any, Any | None]:
    """Open the single supported editable-media Playwright page boundary."""

    context = browser.new_context(
        viewport={"width": width, "height": height},
        device_scale_factor=1,
    )
    try:
        if on_request is not None:
            context.on("request", on_request)
        context.route(
            "http://**/*",
            lambda route: (
                route.continue_() if owns_url(route.request.url) else route.abort()
            ),
        )
        context.route("https://**/*", lambda route: route.abort())
        page = context.new_page()
        if on_page_error is not None:
            page.on("pageerror", on_page_error)
        if on_request_failed is not None:
            page.on("requestfailed", on_request_failed)
        page.goto(
            url,
            wait_until="load",
            timeout=EDITABLE_MEDIA_PAGE_TIMEOUT_MS,
        )
        wait_for_editable_media_runtime(page)
        wait_for_editable_media_assets(page)
        if runtime_state is not None:
            roundtrip = page.evaluate(_ROUND_TRIP_RUNTIME_STATE, runtime_state)
            if roundtrip != runtime_state:
                raise (
                    state_error()
                    if state_error is not None
                    else RuntimeError(
                        "Editable media runtime rejected the persisted clip state"
                    )
                )
        cdp = None
        if transparent_background:
            cdp = context.new_cdp_session(page)
            cdp.send(
                "Emulation.setDefaultBackgroundColorOverride",
                {"color": {"r": 0, "g": 0, "b": 0, "a": 0}},
            )
        return context, page, cdp
    except BaseException:
        context.close()
        raise
