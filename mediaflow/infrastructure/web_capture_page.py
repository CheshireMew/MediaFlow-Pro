from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from mediaflow.infrastructure.web_browser import SEEK_WEB_FRAME_SCRIPT
from mediaflow.infrastructure.web_capture_models import WebFrameCaptureError

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


_FAST_CAPTURE_COMPATIBILITY = """
() => {
    const root = document.querySelector("[data-composition-id]");
    if (!root) return {supported: false, reason: "missing_root"};
    const probe = document.createElement("canvas").getContext("2d");
    if (!probe || typeof probe.drawElementImage !== "function") {
        return {supported: false, reason: "api_unavailable"};
    }
    const bounds = root.getBoundingClientRect();
    if (
        Math.abs(bounds.left) > 0.5
        || Math.abs(bounds.top) > 0.5
        || Math.abs(bounds.width - window.innerWidth) > 0.5
        || Math.abs(bounds.height - window.innerHeight) > 0.5
    ) {
        return {supported: false, reason: "root_not_viewport"};
    }
    if (root.querySelector("canvas, video, iframe, object, embed")) {
        return {supported: false, reason: "dynamic_surface"};
    }
    const animations = document.getAnimations().filter(
        animation => animation.playState === "running"
    );
    if (animations.length) return {supported: false, reason: "wall_clock_animation"};
    for (const element of [root, ...root.querySelectorAll("*")]) {
        const style = getComputedStyle(element);
        if (style.display === "none" || style.visibility === "hidden") continue;
        if (
            (style.backdropFilter && style.backdropFilter !== "none")
            || (style.webkitBackdropFilter && style.webkitBackdropFilter !== "none")
            || (style.filter && style.filter !== "none")
            || (style.mixBlendMode && style.mixBlendMode !== "normal")
        ) {
            return {supported: false, reason: "unsupported_effect"};
        }
    }
    return {supported: true, reason: "eligible"};
}
"""

_INJECT_FAST_CAPTURE_CANVAS = """
({width, height}) => {
    const root = document.querySelector("[data-composition-id]");
    if (!root || document.getElementById("__mediaflow_capture_canvas")) return;
    const parent = root.parentNode;
    if (!parent) throw new Error("Editable media root has no parent");
    const canvas = document.createElement("canvas");
    canvas.id = "__mediaflow_capture_canvas";
    canvas.setAttribute("layoutsubtree", "");
    canvas.width = width;
    canvas.height = height;
    canvas.style.cssText = "display:block;position:absolute;top:0;left:0;z-index:0";
    parent.insertBefore(canvas, root);
    canvas.appendChild(root);
    const tick = document.createElement("div");
    tick.id = "__mediaflow_capture_tick";
    tick.style.cssText = [
        "position:absolute",
        "left:0",
        "top:0",
        "width:1px",
        "height:1px",
        "background:#000",
        "opacity:0.01",
        "pointer-events:none",
    ].join(";");
    canvas.appendChild(tick);
    window.__mediaflowInvalidateCapture = () => {
        tick.style.backgroundColor = tick.style.backgroundColor === "rgb(0, 0, 0)"
            ? "rgb(1, 1, 1)"
            : "rgb(0, 0, 0)";
        if (typeof canvas.requestPaint === "function") {
            try {
                canvas.requestPaint();
            } catch {
                // The paint sentinel remains a valid fallback.
            }
        }
    };
}
"""

_REMOVE_FAST_CAPTURE_CANVAS = """
() => {
    const canvas = document.getElementById("__mediaflow_capture_canvas");
    const root = document.querySelector("[data-composition-id]");
    if (canvas && root && canvas.parentNode) {
        canvas.parentNode.insertBefore(root, canvas);
        canvas.remove();
    }
    delete window.__mediaflowInvalidateCapture;
}
"""

_CAPTURE_FAST_PNG = """
({width, height}) => {
    const canvas = document.getElementById("__mediaflow_capture_canvas");
    const root = document.querySelector("[data-composition-id]");
    const context = canvas?.getContext("2d");
    if (!canvas || !root || !context || typeof context.drawElementImage !== "function") {
        throw new Error("drawElementImage capture is not initialized");
    }
    return new Promise((resolve, reject) => {
        let settled = false;
        const draw = () => {
            if (settled) return;
            settled = true;
            try {
                context.clearRect(0, 0, width, height);
                let background = "";
                for (let element = root.parentElement; element; element = element.parentElement) {
                    if (element === canvas) continue;
                    const color = getComputedStyle(element).backgroundColor;
                    if (color && color !== "transparent" && color !== "rgba(0, 0, 0, 0)") {
                        background = color;
                        break;
                    }
                }
                if (background) {
                    context.fillStyle = background;
                    context.fillRect(0, 0, width, height);
                }
                context.drawElementImage(root, 0, 0);
                setTimeout(() => {
                    try {
                        resolve(canvas.toDataURL("image/png"));
                    } catch (error) {
                        reject(error);
                    }
                }, 0);
            } catch (error) {
                reject(error);
            }
        };
        const onPaint = () => {
            canvas.removeEventListener("paint", onPaint);
            draw();
        };
        canvas.addEventListener("paint", onPaint);
        window.__mediaflowInvalidateCapture?.();
        setTimeout(() => {
            canvas.removeEventListener("paint", onPaint);
            draw();
        }, 250);
    });
}
"""
