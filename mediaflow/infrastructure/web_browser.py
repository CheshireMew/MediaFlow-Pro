from __future__ import annotations

from collections.abc import Callable
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import quote, unquote, urlparse

from mediaflow.application.ports import WebPackageValidatorPort
from mediaflow.domain.web_media import (
    EditableMediaManifest,
    WebMediaSourcesManifest,
    parse_editable_media_manifest,
)
from mediaflow.infrastructure.chromium_runtime import find_chromium_executable

SEEK_WEB_FRAME_SCRIPT = """
async seconds => {
    await window.editableMedia.ready;
    await window.__hf.seek(seconds);
    const root = document.querySelector("[data-composition-id]");
    if (!root) throw new Error("Editable media composition root is missing");
    const bounds = root.getBoundingClientRect();
    void bounds.width;
    void getComputedStyle(root).opacity;
}
"""


class _QuietPackageHandler(SimpleHTTPRequestHandler):
    def log_message(self, _format: str, *args: object) -> None:
        return


class WebPackagePreviewServer:
    def __init__(self, package_root: Path):
        self.package_root = package_root.resolve(strict=True)
        handler = partial(
            _QuietPackageHandler,
            directory=str(self.package_root),
        )
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._server.daemon_threads = True
        self._thread = Thread(
            target=self._server.serve_forever,
            name="editable-media-preview",
            daemon=True,
        )
        self._closed = False
        self._thread.start()
        self.origin = f"http://127.0.0.1:{self._server.server_port}"

    def url_for(self, relative: str, *, query: str = "") -> str:
        path = quote(relative.replace("\\", "/").lstrip("/"), safe="/")
        suffix = f"?{query}" if query else ""
        return f"{self.origin}/{path}{suffix}"

    def owns_url(self, value: str) -> bool:
        return value == self.origin or value.startswith(f"{self.origin}/")

    def resolve_url(self, value: str) -> Path:
        if not self.owns_url(value):
            raise ValueError(f"URL does not belong to the editable media preview: {value}")
        relative = unquote(urlparse(value).path).lstrip("/")
        resolved = (self.package_root / relative).resolve()
        if resolved != self.package_root and self.package_root not in resolved.parents:
            raise ValueError("Editable media preview URL escapes the package")
        return resolved

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def __enter__(self) -> WebPackagePreviewServer:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def verify_non_monotonic_seek_pixels(
    page,
    duration_seconds: float,
    capture: Callable[[], bytes],
) -> None:
    def capture_at(seconds: float) -> bytes:
        page.evaluate(SEEK_WEB_FRAME_SCRIPT, seconds)
        return capture()

    probes = tuple(
        max(0.0, duration_seconds * fraction)
        for fraction in (0.0, 0.25, 0.5, 0.75, 0.95)
    )
    # Warm every comparison state before recording references. Chromium can
    # switch glyph antialiasing mode the first time a transparent surface is
    # painted at a new state even after fonts and layout report ready.
    for seconds in probes:
        capture_at(seconds)
    references = {seconds: capture_at(seconds) for seconds in probes}
    # A seekable composition must be independent of call order. The second
    # pass deliberately jumps backward and forward, then returns to frame 0
    # so lazy timeline initialization and accumulated side effects are visible.
    for seconds in (probes[2], probes[1], probes[4], probes[3], probes[0]):
        if capture_at(seconds) != references[seconds]:
            page.evaluate("() => window.__hf.seek(0)")
            raise ValueError(
                "Editable media v4 must render identical pixels after non-monotonic frame seeks"
            )


def validate_editable_media_page(
    page,
    manifest: EditableMediaManifest,
    media_sources: WebMediaSourcesManifest,
) -> None:
    page.wait_for_function(
        """() => window.editableMedia
            && window.editableMedia.ready instanceof Promise
            && typeof window.editableMedia.getManifest === 'function'
            && typeof window.editableMedia.getMediaSources === 'function'
            && typeof window.editableMedia.getState === 'function'
            && typeof window.editableMedia.setState === 'function'
            && typeof window.editableMedia.setVariant === 'function'
            && typeof window.editableMedia.setScene === 'function'
            && typeof window.editableMedia.setTime === 'function'
            && typeof window.editableMedia.getBounds === 'function'
            && window.__hf
            && typeof window.__hf.seek === 'function'
            && window.__hf.duration > 0""",
        timeout=5000,
    )
    page.evaluate("() => window.editableMedia.ready")
    page.evaluate(
        """async () => {
            await document.fonts.ready;
            await Promise.all(Array.from(document.images).map(image => image.decode()));
        }"""
    )
    runtime_manifest = page.evaluate("() => window.editableMedia.getManifest()")
    if parse_editable_media_manifest(runtime_manifest) != manifest:
        raise ValueError("window.editableMedia.getManifest() must expose the imported v4 manifest")
    runtime_media_sources = page.evaluate("() => window.editableMedia.getMediaSources()")
    if WebMediaSourcesManifest.model_validate(runtime_media_sources) != media_sources:
        raise ValueError(
            "window.editableMedia.getMediaSources() must expose the imported v4 source manifest"
        )
    expected_duration = sum(item.duration_ms for item in manifest.scenes) / 1000
    frame_protocol = page.evaluate(
        """probe => {
            const roots = Array.from(document.querySelectorAll('[data-editable-media-root]'));
            const root = roots[0] || null;
            const compositionId = root?.dataset.compositionId || '';
            const timeline = window.__timelines?.[compositionId];
            window.__hf.seek(probe);
            const seekTimeMs = window.editableMedia.getPlayback().globalTimeMs;
            window.__hf.seek(0);
            return {
                rootCount: roots.length,
                compositionRootCount: document.querySelectorAll(
                    '[data-composition-id]'
                ).length,
                rootIsFirstBodyElement: document.body.firstElementChild === root,
                root: root ? {
                    compositionId,
                    noTimeline: root.hasAttribute('data-no-timeline'),
                    duration: Number(root.dataset.duration),
                    width: Number(root.dataset.width),
                    height: Number(root.dataset.height),
                    fps: Number(root.dataset.fps),
                } : null,
                duration: Number(window.__hf.duration),
                seekTimeMs,
                timelineDuration: typeof timeline?.duration === 'function'
                    ? Number(timeline.duration())
                    : null,
                timelineHasSeek: typeof timeline?.seek === 'function',
            };
        }""",
        min(0.5, expected_duration / 2),
    )
    root = frame_protocol["root"]
    default_variant = manifest.default_variant
    root_duration = root.get("duration") if isinstance(root, dict) else None
    if (
        frame_protocol["rootCount"] != 1
        or frame_protocol["compositionRootCount"] != 1
        or frame_protocol["rootIsFirstBodyElement"] is not True
        or not isinstance(root, dict)
        or root["compositionId"] != "editable-media"
        or root["noTimeline"] is not True
        or root["width"] != default_variant.canvas.width
        or root["height"] != default_variant.canvas.height
        or root["fps"] != manifest.playback.fps
        or not isinstance(root_duration, (int, float))
        or abs(root_duration - expected_duration) > 1e-9
    ):
        raise ValueError("Editable media v4 root metadata is not synchronized")
    protocol_duration = frame_protocol.get("duration")
    seek_time_ms = frame_protocol.get("seekTimeMs")
    timeline_duration = frame_protocol.get("timelineDuration")
    if (
        not isinstance(protocol_duration, (int, float))
        or abs(protocol_duration - expected_duration) > 1e-9
        or not isinstance(seek_time_ms, (int, float))
        or abs(seek_time_ms - min(0.5, expected_duration / 2) * 1000) > 0.5
        or frame_protocol["timelineHasSeek"] is not True
        or not isinstance(timeline_duration, (int, float))
        or abs(timeline_duration - expected_duration) > 1e-9
    ):
        raise ValueError("Editable media v4 frame protocol is not deterministic")
    selectors = {layer.id: layer.selector for layer in manifest.layers}
    counts = page.evaluate(
        """selectors => Object.fromEntries(Object.entries(selectors).map(
            ([id, selector]) => [id, document.querySelectorAll(selector).length]
        ))""",
        selectors,
    )
    invalid = {layer_id: count for layer_id, count in counts.items() if count != 1}
    if invalid:
        raise ValueError(f"Editable layer selectors must match exactly one element: {invalid}")
    state = page.evaluate("() => window.editableMedia.getState()")
    if not isinstance(state, dict) or set(state) != {
        "scenes",
        "theme",
        "theme_bindings",
        "variant",
        "scene_id",
        "playback",
        "revision",
    }:
        raise ValueError("window.editableMedia.getState() must return the complete v4 state")
    scenes = state.get("scenes")
    scene_ids = {item.id for item in manifest.scenes}
    if not isinstance(scenes, dict) or set(scenes) != scene_ids:
        raise ValueError("Editable media runtime state must contain every declared scene")
    for scene_id, scene in scenes.items():
        if not isinstance(scene, dict) or set(scene) != {
            "layers",
            "animations",
            "data",
            "locks",
        }:
            raise ValueError(f"Editable media runtime scene is incomplete: {scene_id}")
        if not isinstance(scene["layers"], dict) or set(scene["layers"]) != set(selectors):
            raise ValueError(f"Editable media runtime scene layers are incomplete: {scene_id}")
    roundtrip = page.evaluate(
        """state => {
            window.editableMedia.setState(state);
            return window.editableMedia.getState();
        }""",
        state,
    )
    if roundtrip != state:
        raise ValueError("window.editableMedia.setState() must round-trip the complete v4 state")
    for variant in manifest.variants:
        selected = page.evaluate(
            "variantId => window.editableMedia.setVariant(variantId)",
            variant.id,
        )
        if selected.get("variant") != {
            "id": variant.id,
            "width": variant.canvas.width,
            "height": variant.canvas.height,
        }:
            raise ValueError(f"Editable media runtime failed to select variant: {variant.id}")
        expected_layers = {
            layer.id: manifest.layer_values_for(variant.id, layer.id)
            for layer in manifest.layers
        }
        selected_scenes = selected.get("scenes")
        if not isinstance(selected_scenes, dict) or any(
            scene_state.get("layers") != expected_layers
            for scene_state in selected_scenes.values()
            if isinstance(scene_state, dict)
        ):
            raise ValueError(
                f"Editable media runtime did not resolve partial variant layers: {variant.id}"
            )
    page.evaluate("state => window.editableMedia.setState(state)", state)
    bounds = page.evaluate("() => window.editableMedia.getBounds()")
    if not isinstance(bounds, dict) or set(bounds) != set(selectors):
        raise ValueError("window.editableMedia.getBounds() must return every declared layer")
    verify_non_monotonic_seek_pixels(
        page,
        expected_duration,
        lambda: page.screenshot(type="png", omit_background=True),
    )


class BrowserWebPackageValidator(WebPackageValidatorPort):
    def validate(self, package_root: Path, manifest: EditableMediaManifest) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            raise RuntimeError("Playwright is required to validate editable web media") from error
        executable = find_chromium_executable()
        requested_urls: list[str] = []
        media_sources = WebMediaSourcesManifest.model_validate_json(
            (package_root / manifest.media_sources).read_text(encoding="utf-8")
        )
        allowed = {
            (package_root / relative).resolve()
            for relative in [
                "editable-media.json",
                manifest.entry,
                manifest.media_sources,
                *manifest.resources,
                *(item.file.split("#", 1)[0] for item in media_sources.sources),
            ]
        }
        validation_error: Exception | None = None
        with WebPackagePreviewServer(package_root) as preview, sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=str(executable), headless=True)
            context = browser.new_context(
                viewport={
                    "width": manifest.default_variant.canvas.width,
                    "height": manifest.default_variant.canvas.height,
                },
                device_scale_factor=1,
            )
            context.on("request", lambda request: requested_urls.append(request.url))
            context.route(
                "http://**/*",
                lambda route: (
                    route.continue_()
                    if preview.owns_url(route.request.url)
                    else route.abort()
                ),
            )
            context.route("https://**/*", lambda route: route.abort())
            page = context.new_page()
            page.goto(
                preview.url_for(
                    manifest.entry,
                    query=(
                        f"capture=1&variant={manifest.default_variant_id}"
                        f"&scene={manifest.scenes[0].id}"
                    ),
                ),
                wait_until="load",
                timeout=15000,
            )
            try:
                validate_editable_media_page(page, manifest, media_sources)
            except Exception as error:
                validation_error = error
            browser.close()

        remote = [
            url
            for url in requested_urls
            if url.startswith(("http://", "https://")) and not preview.owns_url(url)
        ]
        if remote:
            raise ValueError(f"Editable media packages cannot depend on remote resources: {remote}")
        undeclared: list[str] = []
        for url in requested_urls:
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not preview.owns_url(url):
                continue
            if preview.resolve_url(url) not in allowed:
                undeclared.append(url)
        if undeclared:
            raise ValueError(f"Editable media loaded undeclared local resources: {undeclared}")
        if validation_error is not None:
            raise ValueError(
                f"Editable media runtime validation failed: {validation_error}"
            ) from validation_error
