from __future__ import annotations

import base64
import json
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from mediaflow.infrastructure.web_browser import SEEK_WEB_FRAME_SCRIPT
from mediaflow.infrastructure.web_capture_models import WebFrameCaptureError
from mediaflow.infrastructure.web_capture_quality import _validate_png
from mediaflow.infrastructure.web_capture_scripts import CAPTURE_FAST_PNG as _CAPTURE_FAST_PNG

_RETRY_FRAME_QUERY = "__hf_retry_frame"
_RETRY_ATTEMPT_QUERY = "__hf_retry_attempt"


def _retry_capture_url(url: str, *, frame_index: int, attempt: int) -> str:
    if frame_index < 0 or attempt <= 1:
        raise ValueError("Editable media retry URL needs a frame and a later attempt")
    parts = urlsplit(url)
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key not in {_RETRY_FRAME_QUERY, _RETRY_ATTEMPT_QUERY}
    ]
    query.extend(
        (
            (_RETRY_FRAME_QUERY, str(frame_index)),
            (_RETRY_ATTEMPT_QUERY, str(attempt)),
        )
    )
    return urlunsplit(parts._replace(query=urlencode(query)))


def _seek_frame(page, seconds: float, frame_index: int) -> dict[str, Any]:
    try:
        result = page.evaluate(SEEK_WEB_FRAME_SCRIPT, seconds)
    except BaseException as error:
        marker = "__MEDIAFLOW_FRAME_ERROR__"
        message = str(error)
        if marker not in message:
            raise
        encoded = message.split(marker, 1)[1].split("\n", 1)[0]
        try:
            detail = json.loads(encoded)
        except json.JSONDecodeError:
            raise error from None
        if not isinstance(detail, dict):
            raise error
        detail["frame_index"] = frame_index
        raise WebFrameCaptureError(detail) from error
    if not isinstance(result, dict):
        raise RuntimeError("editable-media v6 seek did not return frame readiness details")
    return result


def _page_can_be_replaced(error: BaseException) -> bool:
    message = f"{type(error).__name__}: {error}".casefold()
    return any(
        marker in message
        for marker in (
            "targetclosed",
            "target page, context or browser has been closed",
            "page closed",
            "browser has been closed",
        )
    )


def _browser_was_closed(error: BaseException) -> bool:
    message = f"{type(error).__name__}: {error}".casefold()
    return any(
        marker in message
        for marker in (
            "browser has been closed",
            "browser disconnected",
            "browser process",
        )
    )


def capture_fast_png(page, width: int, height: int) -> bytes:
    data_url = page.evaluate(
        _CAPTURE_FAST_PNG,
        {"width": width, "height": height},
    )
    if not isinstance(data_url, str) or "," not in data_url:
        raise RuntimeError("drawElementImage returned an invalid PNG payload")
    payload = base64.b64decode(data_url.split(",", 1)[1], validate=True)
    _validate_png(payload, width, height)
    return payload


def capture_chrome_screenshot(cdp, width: int, height: int) -> bytes:
    result = cdp.send(
        "Page.captureScreenshot",
        {
            "format": "png",
            "fromSurface": True,
            "captureBeyondViewport": False,
            "optimizeForSpeed": True,
        },
    )
    encoded = result.get("data")
    if not isinstance(encoded, str):
        raise RuntimeError("Chrome returned an invalid PNG screenshot")
    payload = base64.b64decode(encoded, validate=True)
    _validate_png(payload, width, height)
    return payload
