from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote, urlparse

from mediaflow.application.ports import WebPackageValidatorPort
from mediaflow.domain.web_media import EditableMediaManifest
from mediaflow.infrastructure.chromium_runtime import find_chromium_executable


def validate_editable_media_page(page, manifest: EditableMediaManifest) -> None:
    page.wait_for_function(
        """() => window.editableMedia
            && window.editableMedia.ready instanceof Promise
            && typeof window.editableMedia.getState === 'function'
            && typeof window.editableMedia.setState === 'function'
            && typeof window.editableMedia.setTime === 'function'
            && typeof window.editableMedia.getBounds === 'function'""",
        timeout=5000,
    )
    page.evaluate("() => window.editableMedia.ready")
    page.evaluate(
        """async () => {
            await document.fonts.ready;
            await Promise.all(Array.from(document.images).map(image => image.decode()));
        }"""
    )
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
    if not isinstance(state, dict) or not isinstance(state.get("layers"), dict):
        raise ValueError("window.editableMedia.getState() must return a state object with layers")
    page.evaluate("state => window.editableMedia.setState(state)", state)
    page.evaluate("state => window.editableMedia.setState(state)", state)
    bounds = page.evaluate("() => window.editableMedia.getBounds()")
    if not isinstance(bounds, dict) or set(bounds) != set(selectors):
        raise ValueError("window.editableMedia.getBounds() must return every declared layer")


class BrowserWebPackageValidator(WebPackageValidatorPort):
    def validate(self, package_root: Path, manifest: EditableMediaManifest) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            raise RuntimeError("Playwright is required to validate editable web media") from error
        executable = find_chromium_executable()
        requested_urls: list[str] = []
        allowed = {
            (package_root / relative).resolve()
            for relative in [manifest.entry, *manifest.resources]
        }
        validation_error: Exception | None = None
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=str(executable), headless=True)
            context = browser.new_context(
                viewport={"width": manifest.canvas.width, "height": manifest.canvas.height},
                device_scale_factor=1,
            )
            context.on("request", lambda request: requested_urls.append(request.url))
            context.route("http://**/*", lambda route: route.abort())
            context.route("https://**/*", lambda route: route.abort())
            page = context.new_page()
            page.goto(
                f"{(package_root / manifest.entry).resolve().as_uri()}?capture=1",
                wait_until="load",
                timeout=15000,
            )
            try:
                validate_editable_media_page(page, manifest)
            except Exception as error:
                validation_error = error
            browser.close()

        remote = [url for url in requested_urls if url.startswith(("http://", "https://"))]
        if remote:
            raise ValueError(f"Editable media packages cannot depend on remote resources: {remote}")
        undeclared: list[str] = []
        for url in requested_urls:
            parsed = urlparse(url)
            if parsed.scheme != "file":
                continue
            path_text = unquote(parsed.path)
            if len(path_text) >= 3 and path_text[0] == "/" and path_text[2] == ":":
                path_text = path_text[1:]
            if Path(path_text).resolve() not in allowed:
                undeclared.append(url)
        if undeclared:
            raise ValueError(f"Editable media loaded undeclared local resources: {undeclared}")
        if validation_error is not None:
            raise ValueError(
                f"Editable media runtime validation failed: {validation_error}"
            ) from validation_error
