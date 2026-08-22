from __future__ import annotations

FAST_CAPTURE_COMPATIBILITY = """
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

INJECT_FAST_CAPTURE_CANVAS = """
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
    window.__mediaflowFastCapturePhase = "verification";
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

REMOVE_FAST_CAPTURE_CANVAS = """
() => {
    const canvas = document.getElementById("__mediaflow_capture_canvas");
    const root = document.querySelector("[data-composition-id]");
    if (canvas && root && canvas.parentNode) {
        canvas.parentNode.insertBefore(root, canvas);
        canvas.remove();
    }
    delete window.__mediaflowInvalidateCapture;
    delete window.__mediaflowFastCapturePhase;
}
"""

START_FAST_CAPTURE_PRODUCTION = """
() => {
    const canvas = document.getElementById("__mediaflow_capture_canvas");
    if (!canvas) throw new Error("drawElementImage capture canvas is missing");
    window.__mediaflowFastCapturePhase = "production";
}
"""

CAPTURE_FAST_PNG = """
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
