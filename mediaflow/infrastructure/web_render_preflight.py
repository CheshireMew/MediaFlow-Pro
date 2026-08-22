from __future__ import annotations

import hashlib
import json
import os
import re
from fractions import Fraction
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Literal

from mediaflow.application.web_package_files import web_package_root
from mediaflow.domain.timeline import TimelineState
from mediaflow.domain.web_manifest import WebAssetSpec
from mediaflow.domain.web_rendering import (
    WebRenderCompatibilityFinding,
    WebRenderPlan,
    WebRenderVerificationFrame,
)
from mediaflow.domain.web_state import WebClipState

from .web_capture_quality import _fast_capture_sample_indices
from .web_capture_scheduler import _configured_worker_limit, _resolve_worker_count
from .web_render_target import WebRenderTarget

WEB_RENDER_PREFLIGHT_VERSION = 1
DIRECT_H264_MIN_FRAMES = 30
DIRECT_H264_MIN_PIXEL_FRAMES = 1920 * 1080 * 300
DIRECT_H264_UHD_CANVASES = frozenset({(3840, 2160), (2160, 3840)})
DIRECT_H264_30FPS_RATES = frozenset({Fraction(30, 1), Fraction(30_000, 1001)})

_DYNAMIC_SURFACE_TAGS = {"canvas", "embed", "iframe", "object", "video"}
_COMPLEX_SVG_TAGS = {"clippath", "filter", "foreignobject", "mask"}
_VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}
_BLOCKING_STYLE_PROPERTIES = {
    "-webkit-backdrop-filter": "none",
    "backdrop-filter": "none",
    "filter": "none",
    "mix-blend-mode": "normal",
}
_CSS_RISK_PATTERNS = (
    ("css-filter", re.compile(r"(?im)^\s*(?:-webkit-)?(?:backdrop-)?filter\s*:\s*(?!none(?:\s*[;!]|\s*$))")),
    ("css-blend-mode", re.compile(r"(?im)^\s*mix-blend-mode\s*:\s*(?!normal(?:\s*[;!]|\s*$))")),
    ("css-3d-transform", re.compile(r"(?im)^\s*(?:perspective|transform-style)\s*:")),
)


def _style_declarations(value: str) -> dict[str, str]:
    declarations: dict[str, str] = {}
    for declaration in value.split(";"):
        name, separator, selected = declaration.partition(":")
        if not separator:
            continue
        declarations[name.strip().casefold()] = selected.strip().casefold().split("!important", 1)[0].strip()
    return declarations


class _CompositionMarkupInspector(HTMLParser):
    def __init__(self, path: str) -> None:
        super().__init__(convert_charrefs=True)
        self.path = path
        self.depth = 0
        self.findings: list[WebRenderCompatibilityFinding] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._handle_tag(tag, attrs, self_closing=False)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._handle_tag(tag, attrs, self_closing=True)

    def handle_endtag(self, _tag: str) -> None:
        if self.depth > 0:
            self.depth -= 1

    def _handle_tag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
        *,
        self_closing: bool,
    ) -> None:
        normalized_tag = tag.casefold()
        attributes = {name.casefold(): value for name, value in attrs}
        is_root = "data-composition-id" in attributes
        inside = self.depth > 0 or is_root
        if inside and normalized_tag in _DYNAMIC_SURFACE_TAGS:
            self.findings.append(
                WebRenderCompatibilityFinding(
                    code="dynamic-surface",
                    severity="blocking",
                    source="entry-html",
                    path=self.path,
                    line=self.getpos()[0],
                    message=(
                        f"<{normalized_tag}> is inside the composition and requires Chrome screenshot capture"
                    ),
                )
            )
        if inside and normalized_tag in _COMPLEX_SVG_TAGS:
            self.findings.append(
                WebRenderCompatibilityFinding(
                    code="complex-svg-paint",
                    severity="warning",
                    source="entry-html",
                    path=self.path,
                    line=self.getpos()[0],
                    message=(
                        f"<{normalized_tag}> needs runtime comparison against Chrome screenshot output"
                    ),
                )
            )
        if inside and attributes.get("style"):
            declarations = _style_declarations(attributes["style"] or "")
            for name, safe_value in _BLOCKING_STYLE_PROPERTIES.items():
                value = declarations.get(name)
                if value is None or value == safe_value:
                    continue
                self.findings.append(
                    WebRenderCompatibilityFinding(
                        code="inline-unsupported-effect",
                        severity="blocking",
                        source="entry-html",
                        path=self.path,
                        line=self.getpos()[0],
                        message=(
                            f"inline {name}: {value} requires Chrome screenshot capture"
                        ),
                    )
                )
        if inside and not self_closing and normalized_tag not in _VOID_TAGS:
            self.depth += 1


def _declared_resource_findings(
    package_root: Path,
    resources: list[str],
) -> list[WebRenderCompatibilityFinding]:
    findings: list[WebRenderCompatibilityFinding] = []
    for relative in resources:
        if PurePosixPath(relative).suffix.casefold() != ".css":
            continue
        content = package_root.joinpath(*PurePosixPath(relative).parts).read_text(encoding="utf-8")
        content = re.sub(r"/\*.*?\*/", "", content, flags=re.DOTALL)
        for code, pattern in _CSS_RISK_PATTERNS:
            for match in pattern.finditer(content):
                findings.append(
                    WebRenderCompatibilityFinding(
                        code=code,
                        severity="warning",
                        source="declared-resource",
                        path=relative,
                        line=content.count("\n", 0, match.start()) + 1,
                        message=(
                            "declared CSS uses a paint feature that must pass runtime screenshot comparison"
                        ),
                    )
                )
    return findings


def _compatibility_findings(entry: Path, spec: WebAssetSpec) -> list[WebRenderCompatibilityFinding]:
    package_root = web_package_root(entry, spec.manifest)
    inspector = _CompositionMarkupInspector(spec.manifest.entry)
    inspector.feed(entry.read_text(encoding="utf-8"))
    findings = inspector.findings + _declared_resource_findings(
        package_root,
        spec.manifest.resources,
    )
    return sorted(
        findings,
        key=lambda item: (
            0 if item.severity == "blocking" else 1,
            item.path.casefold(),
            item.line,
            item.code,
        ),
    )


def _is_measured_direct_h264_profile(target: WebRenderTarget) -> bool:
    return (
        (target.width, target.height) in DIRECT_H264_UHD_CANVASES
        and Fraction(target.fps_numerator, target.fps_denominator)
        in DIRECT_H264_30FPS_RATES
    )


def build_web_render_plan(
    *,
    entry: Path,
    spec: WebAssetSpec,
    clip_state: WebClipState,
    state: TimelineState,
    target: WebRenderTarget,
    capture_start_frame: int = 0,
) -> WebRenderPlan:
    findings = _compatibility_findings(entry, spec)
    screenshot_required = any(item.severity == "blocking" for item in findings)
    sizing = _resolve_worker_count(
        frame_count=target.frame_count,
        width=target.width,
        height=target.height,
        limit=_configured_worker_limit(),
    )
    frame_indices = tuple(
        capture_start_frame + relative
        for relative in _fast_capture_sample_indices(
            frame_count=target.frame_count,
            worker_count=sizing.workers,
        )
    )
    verification_frames = [
        WebRenderVerificationFrame(
            frame_index=frame_index,
            time_seconds=(
                frame_index * target.fps_denominator / target.fps_numerator
            ),
        )
        for frame_index in frame_indices
    ]
    variant = spec.manifest.variant_for(
        clip_state.variant.id if clip_state.variant is not None else None
    )
    direct_h264_rejections: list[str] = []
    if os.environ.get("MEDIAFLOW_WEB_DIRECT_H264", "1").strip().casefold() in {
        "0",
        "false",
        "off",
        "no",
    }:
        direct_h264_rejections.append("MEDIAFLOW_WEB_DIRECT_H264 disables direct H.264 encoding")
    if screenshot_required:
        direct_h264_rejections.append("static compatibility requires Chrome screenshot capture")
    if not target.animated:
        direct_h264_rejections.append("still images use the lossless PNG cache")
    if target.frame_count < DIRECT_H264_MIN_FRAMES:
        direct_h264_rejections.append(
            f"direct H.264 needs at least {DIRECT_H264_MIN_FRAMES} frames to amortize browser startup"
        )
    if target.width * target.height * target.frame_count < DIRECT_H264_MIN_PIXEL_FRAMES:
        direct_h264_rejections.append(
            "the clip is too short for direct H.264 to recover its verification startup cost"
        )
    if not _is_measured_direct_h264_profile(target):
        direct_h264_rejections.append(
            "measured direct H.264 is limited to UHD 3840x2160 or 2160x3840 "
            "at 30 or 29.97 fps"
        )
    if variant.canvas.background_mode != "opaque":
        direct_h264_rejections.append("the selected variant requires an alpha channel")
    if target.native_media_plan.video_segments:
        direct_h264_rejections.append("native-underlay video requires the alpha-preserving compositor")
    if target.width % 2 or target.height % 2:
        direct_h264_rejections.append("H.264 4:2:0 requires even canvas dimensions")
    if state.sequence.profile.color_mode.value != "sdr_bt709":
        direct_h264_rejections.append("direct H.264 currently supports SDR BT.709 sequences only")
    planned_backend: Literal["webcodecs-h264", "frame-pipe"] = (
        "webcodecs-h264" if not direct_h264_rejections else "frame-pipe"
    )
    backend_selection_reasons = (
        [
            "opaque animated SDR web clip is eligible for browser-side H.264 encoding; "
            "runtime verification and atomic frame-pipe fallback remain mandatory"
        ]
        if planned_backend == "webcodecs-h264"
        else direct_h264_rejections
    )
    payload = {
        "preflight_version": WEB_RENDER_PREFLIGHT_VERSION,
        "sequence_id": state.sequence.id,
        "clip_id": clip_state.clip_id,
        "asset_id": spec.asset_id,
        "source_hash": spec.source_hash,
        "render_key": target.key,
        "variant_id": variant.id,
        "capture_start_frame": capture_start_frame,
        "capture_mode": "screenshot" if screenshot_required else "auto",
        "planned_backend": planned_backend,
        "fallback_backend": "frame-pipe" if planned_backend == "webcodecs-h264" else None,
        "backend_selection_reasons": backend_selection_reasons,
        "findings": [item.model_dump(mode="json") for item in findings],
        "verification_frames": [item.model_dump(mode="json") for item in verification_frames],
    }
    digest = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return WebRenderPlan(
        plan_digest=digest,
        sequence_id=state.sequence.id,
        clip_id=clip_state.clip_id,
        asset_id=spec.asset_id,
        source_hash=spec.source_hash,
        render_key=target.key,
        variant_id=variant.id,
        width=target.width,
        height=target.height,
        frame_count=target.frame_count,
        fps_numerator=target.fps_numerator,
        fps_denominator=target.fps_denominator,
        cache_path=str(target.path),
        static_compatibility=("screenshot-required" if screenshot_required else "eligible"),
        capture_mode="screenshot" if screenshot_required else "auto",
        planned_backend=planned_backend,
        fallback_backend=("frame-pipe" if planned_backend == "webcodecs-h264" else None),
        backend_selection_reasons=backend_selection_reasons,
        strategy=(
            "screenshot-only"
            if screenshot_required
            else "verified-drawelement-with-atomic-screenshot-fallback"
        ),
        findings=findings,
        verification_frames=verification_frames,
    )
