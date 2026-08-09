from __future__ import annotations

import hashlib
import json
import mimetypes
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal, cast

from pydantic import Field, JsonValue, field_validator, model_validator

from .editable_media_contract import validate_editable_media_document
from .editor_fields import EditorFieldDescriptor, EditorFieldValue
from .model_base import DomainModel, now_ms

WebLayerKind = Literal["text", "image", "shape", "group", "component"]
WebEditableField = Literal[
    "content",
    "color",
    "font_family",
    "font_size",
    "image",
    "x",
    "y",
    "width",
    "height",
    "rotation",
    "opacity",
    "z_index",
    "visible",
    "enter_ms",
    "exit_ms",
    "delay_ms",
    "duration_ms",
]
WebDataKind = Literal[
    "string",
    "number",
    "boolean",
    "date",
    "media-source",
    "list",
    "table",
    "json",
]
WebThemeKind = Literal["color", "font", "number", "string"]
WebParameterScope = Literal["global", "scene"]
WebInterpolation = Literal["continuous", "discrete"]
WebEasingKind = Literal["linear", "ease_in", "ease_out", "ease_in_out", "step", "cubic_bezier"]
WebExportFormat = Literal["png", "gif", "alpha_video", "video", "overlay"]
WebPlaybackMode = Literal["manual", "autoplay", "hybrid"]

WEB_EXPORT_FORMATS: tuple[WebExportFormat, ...] = (
    "png",
    "gif",
    "alpha_video",
    "video",
    "overlay",
)
_WEB_EXPORT_SUFFIXES: dict[WebExportFormat, tuple[str, ...]] = {
    "png": (".png",),
    "gif": (".gif",),
    "alpha_video": (".mkv",),
    "video": (".mp4", ".mov", ".mkv"),
    "overlay": (".png", ".mkv"),
}
_WEB_EXPORT_DEFAULT_SUFFIXES: dict[WebExportFormat, str] = {
    "png": ".png",
    "gif": ".gif",
    "alpha_video": ".mkv",
    "video": ".mp4",
    "overlay": ".png",
}
_MEDIA_MIME_TYPES = {
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".m4a": "audio/mp4",
    ".mkv": "video/x-matroska",
    ".mov": "video/quicktime",
    ".mp3": "audio/mpeg",
    ".mp4": "video/mp4",
    ".png": "image/png",
    ".wav": "audio/wav",
    ".webm": "video/webm",
    ".webp": "image/webp",
}


def media_mime_type(file_name: str) -> str | None:
    """Resolve supported media types without consulting machine MIME registries."""

    clean_name = file_name.split("#", 1)[0]
    suffix = Path(clean_name).suffix.casefold()
    return _MEDIA_MIME_TYPES.get(suffix) or mimetypes.guess_type(
        clean_name,
        strict=False,
    )[0]


def web_export_suffixes(
    format_name: str,
    *,
    overlay_suffix: str | None = None,
) -> tuple[str, ...]:
    if format_name not in _WEB_EXPORT_SUFFIXES:
        raise ValueError(f"未知的网页导出格式：{format_name}")
    export_format = cast(WebExportFormat, format_name)
    if export_format != "overlay" or overlay_suffix is None:
        return _WEB_EXPORT_SUFFIXES[export_format]
    normalized = overlay_suffix.strip().lower()
    if normalized not in _WEB_EXPORT_SUFFIXES["overlay"]:
        raise ValueError(f"网页叠加层不支持输出扩展名：{overlay_suffix}")
    return (normalized,)


def default_web_export_suffix(
    format_name: str,
    *,
    overlay_suffix: str | None = None,
) -> str:
    suffixes = web_export_suffixes(
        format_name,
        overlay_suffix=overlay_suffix,
    )
    if format_name == "overlay" and overlay_suffix is not None:
        return suffixes[0]
    return _WEB_EXPORT_DEFAULT_SUFFIXES[cast(WebExportFormat, format_name)]


def require_web_export_destination(
    output_path: str | Path,
    format_name: str,
    *,
    overlay_suffix: str | None = None,
) -> Path:
    destination = Path(output_path).expanduser().resolve()
    suffixes = web_export_suffixes(
        format_name,
        overlay_suffix=overlay_suffix,
    )
    if destination.suffix.lower() not in suffixes:
        readable = "、".join(suffixes)
        raise ValueError(
            f"网页导出格式“{format_name}”需要使用以下扩展名：{readable}"
        )
    return destination


CONTINUOUS_ANIMATION_FIELDS: frozenset[str] = frozenset(
    {"font_size", "x", "y", "width", "height", "rotation", "opacity", "z_index"}
)


def _local_package_path(value: str) -> str:
    normalized = value.strip()
    if "\\" in normalized:
        raise ValueError("Editable media paths must use /")
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts:
        raise ValueError("Editable media paths must stay inside the package")
    if ":" in path.parts[0] or "://" in normalized:
        raise ValueError("Editable media paths cannot use a URL or drive protocol")
    return path.as_posix()


def _local_media_reference(value: str) -> str:
    reference = value.strip().replace("\\", "/")
    file_part, separator, fragment = reference.partition("#")
    normalized = _local_package_path(file_part)
    if separator and not fragment:
        raise ValueError("Editable media references cannot end with an empty fragment")
    return f"{normalized}#{fragment}" if separator else normalized


class WebCanvas(DomainModel):
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    background_mode: Literal["transparent", "opaque"] = "transparent"
    background_color: str = "#000000"


class WebLayerBounds(DomainModel):
    x: float = 0.0
    y: float = 0.0
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    rotation: float = 0.0


class WebVariantLayer(DomainModel):
    x: float | None = None
    y: float | None = None
    width: float | None = Field(default=None, gt=0)
    height: float | None = Field(default=None, gt=0)
    rotation: float | None = None
    font_size: float | None = Field(default=None, gt=0)
    opacity: float | None = Field(default=None, ge=0, le=1)
    z_index: float | None = None
    visible: bool | None = None


class WebFieldConstraint(DomainModel):
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = Field(default=None, gt=0)
    choices: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def ordered_range(self) -> WebFieldConstraint:
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("Editable field minimum cannot exceed maximum")
        return self


class WebParameterBinding(DomainModel):
    scope: WebParameterScope
    css_variable: str | None = None

    @field_validator("css_variable")
    @classmethod
    def valid_parameter_css_variable(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith("--"):
            raise ValueError("Parameter CSS variables must start with --")
        return value


class WebParameter(DomainModel):
    descriptor: EditorFieldDescriptor
    binding: WebParameterBinding



class WebFrameReadiness(DomainModel):
    default_timeout_ms: int = Field(ge=100, le=30_000)
    maximum_timeout_ms: int = Field(ge=100, le=30_000)
    retry_limit: int = Field(ge=0, le=3)

    @model_validator(mode="after")
    def coherent_timeouts(self) -> WebFrameReadiness:
        if self.default_timeout_ms > self.maximum_timeout_ms:
            raise ValueError("Frame readiness default timeout cannot exceed maximum")
        return self


class WebComponentMetadata(DomainModel):
    id: str
    name: str
    category: str = "general"
    tags: list[str] = Field(default_factory=list)
    description: str = ""
    preview_background: str = "#16181d"
    preview_accent: str = "#5b8cff"
    aspect_ratios: list[str] = Field(default_factory=lambda: ["16:9"])

    @field_validator("id", "name", "category")
    @classmethod
    def non_empty_component_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Editable media component metadata cannot be empty")
        return value


class WebThemeVariable(DomainModel):
    id: str
    name: str
    kind: WebThemeKind
    css_variable: str
    default: str | float
    constraints: WebFieldConstraint | None = None

    @field_validator("id", "name", "css_variable")
    @classmethod
    def non_empty_theme_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Editable media theme fields cannot be empty")
        return value

    @field_validator("css_variable")
    @classmethod
    def valid_css_variable(cls, value: str) -> str:
        if not value.startswith("--"):
            raise ValueError("Theme CSS variables must start with --")
        return value

    @model_validator(mode="after")
    def value_matches_kind(self) -> WebThemeVariable:
        if self.kind == "number" and not isinstance(self.default, (int, float)):
            raise ValueError("Number theme defaults must be numeric")
        if self.kind != "number" and not isinstance(self.default, str):
            raise ValueError("Text theme defaults must be strings")
        return self


class WebPlaybackControls(DomainModel):
    keyboard: bool
    wheel: bool
    touch: bool
    overview: bool


class WebPlayback(DomainModel):
    mode: WebPlaybackMode
    fps: float = Field(gt=0, le=240)
    loop: Literal["none", "repeat"]
    controls: WebPlaybackControls


class WebAccessibility(DomainModel):
    title_data_field: str
    canvas_selector: str


class WebSceneStep(DomainModel):
    id: str
    at_ms: int = Field(ge=0)
    label: str
    state_kind: Literal["start", "change", "result", "hold"]
    review: bool
    description: str

    @field_validator("id", "label", "description")
    @classmethod
    def non_empty_step_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Editable media scene step fields cannot be empty")
        return value


class WebCameraDepthLayer(DomainModel):
    layer_id: str
    depth: float = Field(ge=-2, le=2)


class WebCameraKeyframe(DomainModel):
    step_id: str
    x: float
    y: float
    zoom: float = Field(ge=0.1, le=4)
    focus_depth: float = Field(ge=-2, le=2)
    aperture: float = Field(ge=0, le=40)
    easing: Literal["linear", "step", "ease_in", "ease_out", "ease_in_out"]


class WebSceneCamera(DomainModel):
    root_layer_id: str
    depth_layers: list[WebCameraDepthLayer]
    readability_layer_ids: list[str]
    keyframes: list[WebCameraKeyframe] = Field(min_length=1)


class WebSceneMotion(DomainModel):
    complexity: Literal["static", "simple", "complex"]
    driver: Literal["none", "object", "camera", "mixed"]
    semantic_purpose: str
    key_state_review: Literal["none", "required"]
    camera: WebSceneCamera | None

    @field_validator("semantic_purpose")
    @classmethod
    def non_empty_semantic_purpose(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Editable scene motion must explain its semantic purpose")
        return value

    @model_validator(mode="after")
    def coherent_driver(self) -> WebSceneMotion:
        camera_driven = self.driver in {"camera", "mixed"}
        if camera_driven != (self.camera is not None):
            raise ValueError("Editable scene motion driver and camera must agree")
        if self.complexity == "static" and (
            self.driver != "none"
            or self.camera is not None
            or self.key_state_review != "none"
        ):
            raise ValueError("Static editable scenes cannot declare active motion")
        if self.complexity != "static" and self.driver == "none":
            raise ValueError("Non-static editable scenes need a motion driver")
        return self


class WebAssetSlotBinding(DomainModel):
    data_field: str


class WebScene(DomainModel):
    id: str
    name: str
    page_role: str
    content_shape: str
    layout_id: str
    duration_ms: int = Field(gt=0)
    primary_blocks: int = Field(ge=0)
    steps: list[WebSceneStep]
    motion: WebSceneMotion
    parameters: dict[str, str | float | int | bool]
    data: dict[str, JsonValue]
    asset_slots: dict[str, WebAssetSlotBinding]

    @field_validator("id", "name", "page_role", "content_shape", "layout_id")
    @classmethod
    def non_empty_scene_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Editable media scene fields cannot be empty")
        return value

    @model_validator(mode="after")
    def valid_steps(self) -> WebScene:
        ids = [item.id for item in self.steps]
        times = [item.at_ms for item in self.steps]
        if not ids or len(set(ids)) != len(ids):
            raise ValueError("Editable media scene step identifiers must be unique")
        if times != sorted(times) or len(set(times)) != len(times):
            raise ValueError("Editable media scene steps must have unique ascending times")
        if times[0] != 0 or times[-1] >= self.duration_ms:
            raise ValueError("Editable media scene steps must start at zero and stay in the scene")
        if self.steps[0].state_kind != "start":
            raise ValueError("Editable media scene steps must begin with a start state")
        if self.motion.complexity == "complex":
            reviewed = {item.state_kind for item in self.steps if item.review}
            if self.motion.key_state_review != "required" or not {
                "start",
                "change",
                "result",
            }.issubset(reviewed):
                raise ValueError(
                    "Complex editable scenes need reviewed start, change, and result states"
                )
        if self.motion.camera is not None:
            step_ids = {item.id for item in self.steps}
            camera_step_ids = [item.step_id for item in self.motion.camera.keyframes]
            if len(set(camera_step_ids)) != len(camera_step_ids):
                raise ValueError("Editable camera keyframe step identifiers must be unique")
            if not set(camera_step_ids).issubset(step_ids):
                raise ValueError("Editable camera keyframes must reference scene steps")
        return self


class WebLayoutCapacity(DomainModel):
    maximum_primary_blocks: int = Field(ge=0)


class WebAssetSlot(DomainModel):
    id: str
    required: bool
    ratio: str = Field(pattern=r"^\d+(?:\.\d+)?:\d+(?:\.\d+)?$")
    fit: Literal["cover", "contain"]
    preserve_full_frame: bool


class WebLayoutContract(DomainModel):
    id: str
    name: str
    page_roles: list[str]
    content_shapes: list[str]
    required_data_fields: list[str]
    required_layer_ids: list[str]
    title_layer_ids: list[str]
    content_layer_ids: list[str]
    capacity: WebLayoutCapacity
    asset_slots: list[WebAssetSlot]


class WebVariant(DomainModel):
    id: str
    name: str
    canvas: WebCanvas
    layers: dict[str, WebVariantLayer]

    @field_validator("id", "name")
    @classmethod
    def non_empty_variant_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Editable media variant fields cannot be empty")
        return value


class WebQualityRoundtrip(DomainModel):
    data_field: str
    layer_id: str


class WebQualityGap(DomainModel):
    above: str
    below: str
    min_px: float = Field(ge=0)


class WebQualityNavigationSafeArea(DomainModel):
    bottom: float = Field(ge=0)
    layer_ids: list[str]


class WebQualityTitleToContent(DomainModel):
    title_layer_id: str
    content_layer_ids: list[str]
    minimum_px: float = Field(ge=0)


class WebQualityBottomWhitespace(DomainModel):
    content_layer_ids: list[str]
    maximum_ratio: float = Field(ge=0, le=1)


class WebQualitySafeArea(DomainModel):
    top: float = Field(ge=0)
    right: float = Field(ge=0)
    bottom: float = Field(ge=0)
    left: float = Field(ge=0)


class WebQualityThumbnail(DomainModel):
    width: float = Field(gt=0)
    minimum_text_px: float = Field(ge=0)
    text_layer_ids: list[str]


class WebQualityBandOccupancy(DomainModel):
    bands: int = Field(ge=1)
    minimum_fill: float = Field(ge=0, le=1)
    maximum_underfilled_bands: int = Field(ge=0)


class WebQualityRules(DomainModel):
    canvas_selector: str | None = None
    roundtrip: WebQualityRoundtrip | None = None
    required_layer_ids: list[str] = Field(default_factory=list)
    required_title_layer_ids: list[str] = Field(default_factory=list)
    bounds_tolerance_px: float | None = Field(default=None, ge=0)
    allow_overflow_layer_ids: list[str] = Field(default_factory=list)
    safe_area: WebQualitySafeArea | None = None
    safe_area_layer_ids: list[str] = Field(default_factory=list)
    minimum_font_px: dict[str, float] = Field(default_factory=dict)
    minimum_gaps: list[WebQualityGap] = Field(default_factory=list)
    content_bounds_layer_ids: list[str] = Field(default_factory=list)
    minimum_content_span: float | None = Field(default=None, ge=0, le=1)
    band_occupancy: WebQualityBandOccupancy | None = None
    thumbnail: WebQualityThumbnail | None = None
    navigation_safe_area: WebQualityNavigationSafeArea | None = None
    title_to_content: WebQualityTitleToContent | None = None
    bottom_whitespace: WebQualityBottomWhitespace | None = None


class WebQuality(WebQualityRules):
    canvas_selector: str
    bounds_tolerance_px: float = Field(ge=0)
    variant_overrides: dict[str, WebQualityRules] = Field(default_factory=dict)
    scene_overrides: dict[str, WebQualityRules] = Field(default_factory=dict)


class WebDelivery(DomainModel):
    preview: Literal["local-server"]
    remote_dependencies: Literal["forbid"]


class WebSourceRepresentation(DomainModel):
    kind: Literal["source"]
    source_id: None
    build: None
    verification: None


class WebProxyBuild(DomainModel):
    tool: str = Field(min_length=1)
    command: list[str] = Field(min_length=1)
    created_at: str = Field(min_length=1)


class WebProxyVerification(DomainModel):
    duration_tolerance_seconds: float = Field(ge=0)
    frame_rate_tolerance: float = Field(ge=0)
    aspect_ratio_tolerance: float = Field(ge=0)
    require_rotation_match: bool
    require_audio_stream_count_match: bool


class WebProxyRepresentation(DomainModel):
    kind: Literal["proxy"]
    source_id: str = Field(min_length=1)
    build: WebProxyBuild
    verification: WebProxyVerification


class WebMediaAcquisition(DomainModel):
    method: Literal[
        "user-provided",
        "project-owned",
        "external-download",
        "generated",
        "generated-in-project",
    ]
    source_url: str
    captured_at: str | None


class WebMediaRights(DomainModel):
    status: Literal["confirmed", "pending", "not-required"]
    license: str
    attribution: str
    terms_url: str

    @model_validator(mode="after")
    def confirmed_rights_have_a_basis(self) -> WebMediaRights:
        if self.status == "confirmed" and not self.license:
            raise ValueError("Confirmed editable media rights need a license basis")
        return self


class WebMediaIntegrity(DomainModel):
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    bytes: int = Field(gt=0)
    mime_type: str = Field(min_length=1)


class WebMediaGeneration(DomainModel):
    provider: str = Field(min_length=1)
    model: str
    prompt: str
    seed: str | float | int | None
    created_at: str = Field(min_length=1)


class WebMediaSpeech(DomainModel):
    provider_voice_id: str
    voice_name: str
    language: str
    text_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    exact_identity: bool

    @model_validator(mode="after")
    def exact_identity_is_named(self) -> WebMediaSpeech:
        if self.exact_identity and (not self.provider_voice_id or not self.voice_name):
            raise ValueError("Exact speech identity needs both provider voice id and voice name")
        return self


class WebMediaCapture(DomainModel):
    file: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("file")
    @classmethod
    def local_capture_file(cls, value: str) -> str:
        return _local_package_path(value)


class WebMediaProvenanceRun(DomainModel):
    recorded_at: str = Field(min_length=1)
    provider: str
    job_id: str
    capture: WebMediaCapture | None


class WebMediaSubject(DomainModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


class WebMediaCropRect(DomainModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def stays_inside_source(self) -> WebMediaCropRect:
        if self.x + self.width > 1.000001 or self.y + self.height > 1.000001:
            raise ValueError("Editable media crop must stay inside its source")
        return self


class WebMediaCrop(DomainModel):
    object_position: str | None = None
    rect: WebMediaCropRect | None = None

    @model_validator(mode="after")
    def has_a_crop_method(self) -> WebMediaCrop:
        if self.object_position is None and self.rect is None:
            raise ValueError("Editable media crop needs object_position or rect")
        return self


class WebBrowserMediaBinding(DomainModel):
    pipeline: Literal["browser"]


class WebNativeAudioBinding(DomainModel):
    pipeline: Literal["native-audio"]
    loop: Literal["none", "repeat"]
    source_in_ms: int = Field(ge=0)
    gain_db: float


class WebNativeUnderlayBinding(DomainModel):
    pipeline: Literal["native-underlay"]
    fit: Literal["cover", "contain"]
    playback: Literal["hold", "repeat"]
    source_in_ms: int = Field(ge=0)
    audio: Literal["include", "exclude"]
    gain_db: float


WebMediaBinding = Annotated[
    WebBrowserMediaBinding
    | WebNativeAudioBinding
    | WebNativeUnderlayBinding,
    Field(discriminator="pipeline"),
]


class WebMediaSource(DomainModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    media_type: Literal[
        "photo",
        "screenshot",
        "video",
        "video-frame",
        "audio",
        "subtitle",
        "icon",
        "document",
        "generated",
    ]
    file: str
    binding: WebMediaBinding
    representation: WebSourceRepresentation | WebProxyRepresentation = Field(
        discriminator="kind"
    )
    acquisition: WebMediaAcquisition
    rights: WebMediaRights
    usage: str = Field(min_length=1)
    integrity: WebMediaIntegrity | None
    generation: WebMediaGeneration | None
    speech: WebMediaSpeech | None
    provenance_runs: list[WebMediaProvenanceRun] = Field(min_length=1)
    subject: WebMediaSubject | None
    crops: dict[str, WebMediaCrop]
    notes: str

    @field_validator("file")
    @classmethod
    def local_file(cls, value: str) -> str:
        return _local_media_reference(value)

    @model_validator(mode="after")
    def valid_source_record(self) -> WebMediaSource:
        generated_in_project = self.acquisition.method == "generated-in-project"
        if self.integrity is None and not generated_in_project:
            raise ValueError("Independent editable media files need integrity metadata")
        if (
            self.acquisition.method in {"generated", "generated-in-project"}
            and self.representation.kind != "proxy"
            and self.generation is None
        ):
            raise ValueError("Generated editable media needs generation metadata")
        if self.media_type == "audio" and self.binding.pipeline != "native-audio":
            raise ValueError(
                "Editable media audio sources must use the native-audio pipeline"
            )
        if (
            self.binding.pipeline == "native-audio"
            and self.media_type != "audio"
        ):
            raise ValueError(
                "Only editable media audio sources can use native-audio"
            )
        if (
            self.binding.pipeline == "native-underlay"
            and self.media_type != "video"
        ):
            raise ValueError(
                "Only editable media video sources can use native-underlay"
            )
        return self


class WebMediaSourcesManifest(DomainModel):
    protocol: Literal["visual-multimedia-media-sources"]
    version: Literal[4]
    sources: list[WebMediaSource]

    @model_validator(mode="after")
    def valid_sources(self) -> WebMediaSourcesManifest:
        ids = [item.id for item in self.sources]
        files = [item.file for item in self.sources]
        if len(set(ids)) != len(ids):
            raise ValueError("Editable media source identifiers must be unique")
        if len(set(files)) != len(files):
            raise ValueError("Editable media source files must be unique")
        sources = {item.id: item for item in self.sources}
        for item in self.sources:
            if item.representation.kind != "proxy":
                continue
            source = sources.get(item.representation.source_id)
            if source is None:
                raise ValueError(
                    f"Editable media proxy source does not exist: {item.representation.source_id}"
                )
            if source.representation.kind != "source":
                raise ValueError("Editable media proxies must point directly to an original source")
            if source.media_type != item.media_type or item.media_type not in {"video", "audio"}:
                raise ValueError("Editable media proxies must preserve a video or audio media type")
            if item.acquisition.method != "generated-in-project":
                raise ValueError("Editable media proxies must be generated inside the project")
            if item.rights != source.rights:
                raise ValueError("Editable media proxies must inherit original source rights")
            if item.binding != source.binding:
                raise ValueError(
                    "Editable media proxies must preserve their source pipeline binding"
                )
        return self


class WebDataColumn(DomainModel):
    id: str
    name: str
    kind: Literal["string", "number", "boolean", "date", "media-source"] = "string"


class WebDataField(DomainModel):
    id: str
    name: str
    kind: WebDataKind
    default: JsonValue = None
    columns: list[WebDataColumn] = Field(default_factory=list)

    @field_validator("id", "name")
    @classmethod
    def non_empty_data_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Editable media data fields cannot be empty")
        return value

    @model_validator(mode="after")
    def table_columns_only(self) -> WebDataField:
        if self.columns and self.kind != "table":
            raise ValueError("Only table data fields can declare columns")
        if len({column.id for column in self.columns}) != len(self.columns):
            raise ValueError("Editable media table columns must be unique")
        if self.default is None:
            return self
        matches = {
            "string": isinstance(self.default, str),
            "date": isinstance(self.default, str),
            "media-source": isinstance(self.default, str),
            "number": isinstance(self.default, (int, float))
            and not isinstance(self.default, bool),
            "boolean": isinstance(self.default, bool),
            "list": isinstance(self.default, list),
            "table": isinstance(self.default, list)
            and all(isinstance(row, dict) for row in self.default),
            "json": True,
        }[self.kind]
        if not matches:
            raise ValueError(f"Data field {self.id} default does not match kind {self.kind}")
        if self.kind == "table" and self.columns:
            if not isinstance(self.default, list):
                raise ValueError(f"Data field {self.id} table default must be a list")
            columns = {column.id: column.kind for column in self.columns}
            for index, row in enumerate(self.default):
                if not isinstance(row, dict):
                    raise ValueError(f"Data field {self.id} default row {index} must be an object")
                if set(row) != set(columns):
                    raise ValueError(
                        f"Data field {self.id} default row {index} columns do not match"
                    )
                for column_id, column_kind in columns.items():
                    value = row[column_id]
                    valid = {
                        "string": isinstance(value, str),
                        "date": isinstance(value, str),
                        "media-source": isinstance(value, str),
                        "number": isinstance(value, (int, float))
                        and not isinstance(value, bool),
                        "boolean": isinstance(value, bool),
                    }[column_kind]
                    if not valid:
                        raise ValueError(
                            f"Data field {self.id} default row {index} column "
                            f"{column_id} does not match kind {column_kind}"
                        )
        return self


class WebLayerManifest(DomainModel):
    id: str
    name: str
    kind: WebLayerKind
    selector: str
    parent_id: str | None = None
    default_bounds: WebLayerBounds
    editable: tuple[WebEditableField, ...] = ()
    constraints: dict[WebEditableField, WebFieldConstraint] = Field(default_factory=dict)

    @field_validator("id", "name", "selector")
    @classmethod
    def non_empty_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Editable layer identifiers, names, and selectors cannot be empty")
        return value

    @model_validator(mode="after")
    def constraints_are_editable(self) -> WebLayerManifest:
        unknown = set(self.constraints) - set(self.editable)
        if unknown:
            raise ValueError(f"Constraints reference non-editable fields: {sorted(unknown)}")
        if len(set(self.editable)) != len(self.editable):
            raise ValueError("Editable layer fields must be unique")
        return self


class WebProductionMetadata(DomainModel):
    source_id: str | None = Field(default=None, min_length=1)
    source_version: str | None = Field(default=None, min_length=1)
    content_unit_id: str | None = Field(default=None, min_length=1)
    media_project_id: str | None = Field(default=None, min_length=1)
    media_script_version: str | None = Field(default=None, min_length=1)
    profile_id: str | None = Field(default=None, min_length=1)


class EditableMediaManifest(DomainModel):
    protocol: Literal["editable-media"]
    version: Literal[6]
    entry: str
    media_sources: str
    playback: WebPlayback
    frame_readiness: WebFrameReadiness
    accessibility: WebAccessibility
    layers: list[WebLayerManifest]
    component: WebComponentMetadata
    theme_variables: list[WebThemeVariable]
    parameters: list[WebParameter]
    scenes: list[WebScene]
    layout_contracts: list[WebLayoutContract]
    variants: list[WebVariant]
    default_variant_id: str
    data_fields: list[WebDataField]
    quality: WebQuality
    delivery: WebDelivery
    resources: list[str]
    production: WebProductionMetadata | None = None

    @field_validator("entry", "media_sources")
    @classmethod
    def local_manifest_path(cls, value: str) -> str:
        return _local_package_path(value)

    @field_validator("resources")
    @classmethod
    def local_resources(cls, values: list[str]) -> list[str]:
        normalized = [_local_package_path(value) for value in values]
        if len(set(normalized)) != len(normalized):
            raise ValueError("Editable media resources must be unique")
        return normalized

    @model_validator(mode="after")
    def valid_contract(self) -> EditableMediaManifest:
        ids = [layer.id for layer in self.layers]
        if not ids:
            raise ValueError("Editable media must declare at least one editable layer")
        if len(set(ids)) != len(ids):
            raise ValueError("Editable layer identifiers must be unique")
        known = set(ids)
        for layer in self.layers:
            if layer.parent_id == layer.id:
                raise ValueError(f"Editable layer cannot be its own parent: {layer.id}")
            if layer.parent_id is not None and layer.parent_id not in known:
                raise ValueError(f"Editable layer parent does not exist: {layer.parent_id}")
        for layer in self.layers:
            visited = {layer.id}
            parent_id = layer.parent_id
            while parent_id is not None:
                if parent_id in visited:
                    raise ValueError("Editable layer groups cannot contain a cycle")
                visited.add(parent_id)
                parent_id = next(item.parent_id for item in self.layers if item.id == parent_id)

        data_fields = {item.id: item for item in self.data_fields}
        data_ids = set(data_fields)
        if len(data_ids) != len(self.data_fields):
            raise ValueError("Editable media data field identifiers must be unique")
        if self.accessibility.title_data_field not in data_ids:
            raise ValueError("Editable media accessibility title data field does not exist")

        contract_ids = [item.id for item in self.layout_contracts]
        if not contract_ids or len(set(contract_ids)) != len(contract_ids):
            raise ValueError("Editable media layout contract identifiers must be unique")
        contracts = {item.id: item for item in self.layout_contracts}
        for contract in self.layout_contracts:
            asset_slot_ids = [item.id for item in contract.asset_slots]
            if len(set(asset_slot_ids)) != len(asset_slot_ids):
                raise ValueError(f"Layout contract {contract.id} asset slots must be unique")
            unknown_layers = (
                set(contract.required_layer_ids)
                | set(contract.title_layer_ids)
                | set(contract.content_layer_ids)
            ) - known
            if unknown_layers:
                raise ValueError(
                    f"Layout contract {contract.id} references unknown layers: "
                    f"{sorted(unknown_layers)}"
                )
            unknown_data = set(contract.required_data_fields) - data_ids
            if unknown_data:
                raise ValueError(
                    f"Layout contract {contract.id} references unknown data fields: "
                    f"{sorted(unknown_data)}"
                )

        scene_ids = [item.id for item in self.scenes]
        if not scene_ids or len(set(scene_ids)) != len(scene_ids):
            raise ValueError("Editable media scene identifiers must be unique")
        for scene in self.scenes:
            scene_contract = contracts.get(scene.layout_id)
            if scene_contract is None:
                raise ValueError(f"Scene {scene.id} layout contract does not exist")
            if scene.page_role not in scene_contract.page_roles:
                raise ValueError(f"Scene {scene.id} page role violates its layout contract")
            if scene.content_shape not in scene_contract.content_shapes:
                raise ValueError(f"Scene {scene.id} content shape violates its layout contract")
            if scene.primary_blocks > scene_contract.capacity.maximum_primary_blocks:
                raise ValueError(f"Scene {scene.id} exceeds its layout capacity")
            unknown_data = set(scene.data) - data_ids
            if unknown_data:
                raise ValueError(
                    f"Scene {scene.id} references unknown data fields: {sorted(unknown_data)}"
                )
            missing_data = {
                field_id
                for field_id in scene_contract.required_data_fields
                if field_id not in scene.data and data_fields[field_id].default is None
            }
            if missing_data:
                raise ValueError(
                    f"Scene {scene.id} is missing required data fields: {sorted(missing_data)}"
                )
            asset_slots = {item.id: item for item in scene_contract.asset_slots}
            unknown_asset_slots = set(scene.asset_slots) - set(asset_slots)
            if unknown_asset_slots:
                raise ValueError(
                    f"Scene {scene.id} references unknown asset slots: "
                    f"{sorted(unknown_asset_slots)}"
                )
            missing_asset_slots = {
                item.id
                for item in scene_contract.asset_slots
                if item.required and item.id not in scene.asset_slots
            }
            if missing_asset_slots:
                raise ValueError(
                    f"Scene {scene.id} is missing required asset slots: "
                    f"{sorted(missing_asset_slots)}"
                )
            for slot_id, binding in scene.asset_slots.items():
                field = data_fields.get(binding.data_field)
                if field is None:
                    raise ValueError(
                        f"Scene {scene.id} asset slot {slot_id} references an unknown data field"
                    )
                if field.kind != "media-source":
                    raise ValueError(
                        f"Scene {scene.id} asset slot {slot_id} must bind a media-source field"
                    )
                value = scene.data.get(field.id, field.default)
                if asset_slots[slot_id].required and (
                    not isinstance(value, str) or not value
                ):
                    raise ValueError(
                        f"Scene {scene.id} asset slot {slot_id} has no media source"
                    )

        variant_ids = [item.id for item in self.variants]
        if not variant_ids or len(set(variant_ids)) != len(variant_ids):
            raise ValueError("Editable media variant identifiers must be unique")
        if self.default_variant_id not in set(variant_ids):
            raise ValueError("Editable media default variant does not exist")
        for variant in self.variants:
            unknown_layers = set(variant.layers) - known
            if unknown_layers:
                raise ValueError(
                    f"Variant {variant.id} references unknown layers: {sorted(unknown_layers)}"
                )

        if len({item.id for item in self.theme_variables}) != len(self.theme_variables):
            raise ValueError("Editable media theme variable identifiers must be unique")
        if len({item.css_variable for item in self.theme_variables}) != len(self.theme_variables):
            raise ValueError("Editable media theme CSS variables must be unique")
        parameter_ids = [item.descriptor.id for item in self.parameters]
        if len(set(parameter_ids)) != len(parameter_ids):
            raise ValueError("Editable media parameter identifiers must be unique")
        parameter_css_variables = [
            item.binding.css_variable
            for item in self.parameters
            if item.binding.css_variable is not None
        ]
        if len(set(parameter_css_variables)) != len(parameter_css_variables):
            raise ValueError("Editable media parameter CSS variables must be unique")
        theme_css_variables = {item.css_variable for item in self.theme_variables}
        overlap = theme_css_variables.intersection(parameter_css_variables)
        if overlap:
            raise ValueError(
                f"Editable media theme and parameter CSS variables overlap: {sorted(overlap)}"
            )
        parameters = {item.descriptor.id: item for item in self.parameters}
        for scene in self.scenes:
            unknown_parameters = set(scene.parameters) - set(parameters)
            if unknown_parameters:
                raise ValueError(
                    f"Scene {scene.id} references unknown parameters: "
                    f"{sorted(unknown_parameters)}"
                )
            for parameter_id, value in scene.parameters.items():
                definition = parameters[parameter_id]
                if definition.binding.scope != "scene":
                    raise ValueError(
                        f"Scene {scene.id} cannot override global parameter {parameter_id}"
                    )
                definition.descriptor.validate_value(value)

        unknown_variant_overrides = set(self.quality.variant_overrides) - set(variant_ids)
        if unknown_variant_overrides:
            raise ValueError(
                "Editable media quality variant overrides reference unknown variants: "
                f"{sorted(unknown_variant_overrides)}"
            )
        unknown_scene_overrides = set(self.quality.scene_overrides) - set(scene_ids)
        if unknown_scene_overrides:
            raise ValueError(
                "Editable media quality scene overrides reference unknown scenes: "
                f"{sorted(unknown_scene_overrides)}"
            )
        quality_rules: list[tuple[str, WebQualityRules]] = [
            ("quality", self.quality),
            *[
                (f"quality.variant_overrides.{variant_id}", rules)
                for variant_id, rules in self.quality.variant_overrides.items()
            ],
            *[
                (f"quality.scene_overrides.{scene_id}", rules)
                for scene_id, rules in self.quality.scene_overrides.items()
            ],
        ]
        for label, rules in quality_rules:
            quality_layers = (
                set(rules.required_layer_ids)
                | set(rules.required_title_layer_ids)
                | set(rules.allow_overflow_layer_ids)
                | set(rules.safe_area_layer_ids)
                | set(rules.minimum_font_px)
                | set(rules.content_bounds_layer_ids)
                | (
                    set(rules.thumbnail.text_layer_ids)
                    if rules.thumbnail is not None
                    else set()
                )
                | (
                    set(rules.navigation_safe_area.layer_ids)
                    if rules.navigation_safe_area is not None
                    else set()
                )
                | (
                    {rules.title_to_content.title_layer_id}
                    | set(rules.title_to_content.content_layer_ids)
                    if rules.title_to_content is not None
                    else set()
                )
                | (
                    set(rules.bottom_whitespace.content_layer_ids)
                    if rules.bottom_whitespace is not None
                    else set()
                )
                | (
                    {rules.roundtrip.layer_id}
                    if rules.roundtrip is not None
                    else set()
                )
                | {
                    layer_id
                    for gap in rules.minimum_gaps
                    for layer_id in (gap.above, gap.below)
                }
            )
            unknown_quality_layers = quality_layers - known
            if unknown_quality_layers:
                raise ValueError(
                    f"{label} references unknown layers: {sorted(unknown_quality_layers)}"
                )
            if (
                rules.roundtrip is not None
                and rules.roundtrip.data_field not in data_ids
            ):
                raise ValueError(f"{label} roundtrip data field does not exist")
            if (
                rules.canvas_selector is not None
                and rules.canvas_selector != self.accessibility.canvas_selector
            ):
                raise ValueError(
                    "Editable media canvas selectors must use one canonical value"
                )
        if self.accessibility.canvas_selector != self.quality.canvas_selector:
            raise ValueError("Editable media canvas selectors must use one canonical value")
        return self

    @property
    def duration_ms(self) -> int:
        return sum(item.duration_ms for item in self.scenes)

    @property
    def default_variant(self) -> WebVariant:
        return self.variant_for(self.default_variant_id)

    def variant_for(self, variant_id: str | None) -> WebVariant:
        resolved = variant_id or self.default_variant_id
        try:
            return next(item for item in self.variants if item.id == resolved)
        except StopIteration as error:
            raise ValueError(f"Editable media variant does not exist: {resolved}") from error

    def layer_values_for(
        self,
        variant_id: str | None,
        layer_id: str,
    ) -> dict[str, JsonValue]:
        try:
            layer = next(item for item in self.layers if item.id == layer_id)
        except StopIteration as error:
            raise ValueError(f"Editable media layer does not exist: {layer_id}") from error
        values = cast(
            dict[str, JsonValue],
            layer.default_bounds.model_dump(mode="json"),
        )
        variant_layer = self.variant_for(variant_id).layers.get(layer_id)
        if variant_layer is not None:
            values.update(
                cast(
                    dict[str, JsonValue],
                    variant_layer.model_dump(mode="json", exclude_none=True),
                )
            )
        return values

    def parameter_for(self, parameter_id: str) -> WebParameter:
        try:
            return next(item for item in self.parameters if item.descriptor.id == parameter_id)
        except StopIteration as error:
            raise ValueError(
                f"Editable media parameter does not exist: {parameter_id}"
            ) from error


def parse_editable_media_manifest(document: object) -> EditableMediaManifest:
    validate_editable_media_document(document)
    return EditableMediaManifest.model_validate(document)


def parse_editable_media_manifest_json(value: str) -> EditableMediaManifest:
    return parse_editable_media_manifest(json.loads(value))


def editable_media_manifest_document(
    manifest: EditableMediaManifest,
) -> dict[str, JsonValue]:
    document = manifest.model_dump(mode="json", exclude_none=True)
    scenes = document.get("scenes")
    if not isinstance(scenes, list):
        raise RuntimeError("Editable media manifest scenes were not serialized")
    for scene in scenes:
        if not isinstance(scene, dict):
            raise RuntimeError(
                "Editable media manifest scene was not serialized"
            )
        motion = scene.get("motion")
        if not isinstance(motion, dict):
            raise RuntimeError(
                "Editable media manifest motion was not serialized"
            )
        motion.setdefault("camera", None)
    parameters = document.get("parameters")
    if not isinstance(parameters, list):
        raise RuntimeError("Editable media manifest parameters were not serialized")
    for parameter in parameters:
        if not isinstance(parameter, dict):
            raise RuntimeError("Editable media parameter was not serialized")
        descriptor = parameter.get("descriptor")
        binding = parameter.get("binding")
        if not isinstance(descriptor, dict) or not isinstance(binding, dict):
            raise RuntimeError("Editable media parameter binding was not serialized")
        descriptor.setdefault("unit", None)
        descriptor.setdefault("options_source", None)
        binding.setdefault("css_variable", None)
    validate_editable_media_document(document)
    return cast(dict[str, JsonValue], document)


class WebAssetSpec(DomainModel):
    asset_id: str
    manifest: EditableMediaManifest
    source_hash: str


def web_asset_spec_document(spec: WebAssetSpec) -> dict[str, JsonValue]:
    return {
        "asset_id": spec.asset_id,
        "manifest": editable_media_manifest_document(spec.manifest),
        "source_hash": spec.source_hash,
    }


class WebEasing(DomainModel):
    kind: WebEasingKind = "linear"
    x1: float = Field(default=0.25, ge=0, le=1)
    y1: float = 0.1
    x2: float = Field(default=0.25, ge=0, le=1)
    y2: float = 1.0


class WebKeyframe(DomainModel):
    time_ms: int = Field(ge=0)
    value: JsonValue
    easing: WebEasing = Field(default_factory=WebEasing)


class WebAnimationTrack(DomainModel):
    field: WebEditableField
    interpolation: WebInterpolation = "continuous"
    keyframes: list[WebKeyframe]

    @model_validator(mode="after")
    def valid_keyframes(self) -> WebAnimationTrack:
        if not self.keyframes:
            raise ValueError("Editable media animation tracks need at least one keyframe")
        times = [item.time_ms for item in self.keyframes]
        if times != sorted(times) or len(set(times)) != len(times):
            raise ValueError("Editable media keyframes must have unique ascending times")
        if self.interpolation == "continuous":
            if self.field not in CONTINUOUS_ANIMATION_FIELDS:
                raise ValueError(f"Field {self.field} only supports discrete keyframes")
            if any(
                not isinstance(item.value, (int, float)) or isinstance(item.value, bool)
                for item in self.keyframes
            ):
                raise ValueError("Continuous keyframe values must be numeric")
        return self


class WebParameterAnimationTrack(DomainModel):
    parameter_id: str
    interpolation: WebInterpolation = "continuous"
    keyframes: list[WebKeyframe]

    @model_validator(mode="after")
    def valid_keyframes(self) -> WebParameterAnimationTrack:
        if not self.keyframes:
            raise ValueError("Editable parameter tracks need at least one keyframe")
        times = [item.time_ms for item in self.keyframes]
        if times != sorted(times) or len(set(times)) != len(times):
            raise ValueError("Editable parameter keyframes need unique ascending times")
        if self.interpolation == "continuous" and any(
            not isinstance(item.value, (int, float)) or isinstance(item.value, bool)
            for item in self.keyframes
        ):
            raise ValueError("Continuous parameter keyframes must be numeric")
        return self


def web_media_sources_have_audio(
    media_sources: WebMediaSourcesManifest,
) -> bool:
    return any(
        isinstance(source.binding, WebNativeAudioBinding)
        or (
            isinstance(source.binding, WebNativeUnderlayBinding)
            and source.binding.audio == "include"
        )
        for source in media_sources.sources
    )


class WebLayerOverride(DomainModel):
    content: str | None = None
    color: str | None = None
    font_family: str | None = None
    font_size: float | None = Field(default=None, gt=0)
    image: str | None = None
    x: float | None = None
    y: float | None = None
    width: float | None = Field(default=None, gt=0)
    height: float | None = Field(default=None, gt=0)
    rotation: float | None = None
    opacity: float | None = Field(default=None, ge=0, le=1)
    z_index: int | None = None
    visible: bool | None = None
    enter_ms: int | None = Field(default=None, ge=0)
    exit_ms: int | None = Field(default=None, ge=0)
    delay_ms: int | None = Field(default=None, ge=0)
    duration_ms: int | None = Field(default=None, ge=0)

    @field_validator("image")
    @classmethod
    def local_image(cls, value: str | None) -> str | None:
        return _local_package_path(value) if value is not None else None

    @model_validator(mode="after")
    def ordered_visibility_time(self) -> WebLayerOverride:
        if self.enter_ms is not None and self.exit_ms is not None and self.exit_ms < self.enter_ms:
            raise ValueError("Layer exit time cannot precede its enter time")
        return self

    def changed_fields(self) -> set[str]:
        return set(self.model_dump(exclude_none=True))


class WebDataSnapshot(DomainModel):
    source_kind: Literal["inline", "file", "api"] = "inline"
    source_label: str = ""
    captured_at: int = Field(default_factory=now_ms)
    values: dict[str, JsonValue] = Field(default_factory=dict)
    content_hash: str = ""

    @model_validator(mode="after")
    def fill_content_hash(self) -> WebDataSnapshot:
        payload = json.dumps(
            self.values,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        expected = hashlib.sha256(payload).hexdigest()
        if self.content_hash and self.content_hash != expected:
            raise ValueError("Editable media data snapshot hash does not match its values")
        object.__setattr__(self, "content_hash", expected)
        return self


class WebSceneState(DomainModel):
    layers: dict[str, WebLayerOverride] = Field(default_factory=dict)
    animations: dict[str, dict[WebEditableField, WebAnimationTrack]] = Field(default_factory=dict)
    parameters: dict[str, str | float | int | bool] = Field(default_factory=dict)
    parameter_animations: dict[str, WebParameterAnimationTrack] = Field(
        default_factory=dict
    )
    parameter_locks: tuple[str, ...] = ()
    data_snapshot: WebDataSnapshot = Field(default_factory=WebDataSnapshot)
    locks: dict[str, tuple[WebEditableField, ...]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def coherent_state(self) -> WebSceneState:
        for layer_id, tracks in self.animations.items():
            for field, track in tracks.items():
                if field != track.field:
                    raise ValueError(f"Animation track key does not match its field: {layer_id}/{field}")
        for layer_id, fields in self.locks.items():
            if len(set(fields)) != len(fields):
                raise ValueError(f"Locked fields must be unique: {layer_id}")
        for parameter_id, parameter_track in self.parameter_animations.items():
            if parameter_id != parameter_track.parameter_id:
                raise ValueError(
                    f"Parameter track key does not match its id: {parameter_id}"
                )
        if len(set(self.parameter_locks)) != len(self.parameter_locks):
            raise ValueError("Scene parameter locks must be unique")
        return self


class WebRuntimeVariant(DomainModel):
    id: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class WebRuntimePlayback(DomainModel):
    mode: WebPlaybackMode


class WebClipState(DomainModel):
    clip_id: str
    scenes: dict[str, WebSceneState] = Field(default_factory=dict)
    theme: dict[str, str | float] = Field(default_factory=dict)
    parameters: dict[str, str | float | int | bool] = Field(default_factory=dict)
    parameter_locks: tuple[str, ...] = ()
    variant: WebRuntimeVariant | None = None
    scene_id: str | None = None
    playback: WebRuntimePlayback | None = None
    source_hash: str = ""
    batch_name: str = ""
    revision: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def coherent_state(self) -> WebClipState:
        if any(not scene_id.strip() for scene_id in self.scenes):
            raise ValueError("Editable media scene state identifiers cannot be empty")
        if len(set(self.parameter_locks)) != len(self.parameter_locks):
            raise ValueError("Global parameter locks must be unique")
        return self


class WebStateDiff(DomainModel):
    clip_id: str
    before_revision: int
    changes: dict[str, dict[Literal["before", "after"], JsonValue]] = Field(
        default_factory=dict
    )
    locked_paths: list[str] = Field(default_factory=list)


class WebEditDocument(DomainModel):
    clip_id: str
    scene_id: str
    variant_id: str
    revision: int
    scene_duration_ms: int
    fields: list[EditorFieldValue]


class WebRebindConflict(DomainModel):
    path: str
    kind: Literal[
        "removed-layer",
        "removed-field",
        "removed-scene",
        "removed-parameter",
        "incompatible-value",
        "out-of-range-keyframe",
        "removed-data-field",
        "removed-theme-variable",
        "removed-variant",
        "removed-media-source",
    ]
    message: str
    current_value: JsonValue = None
    allowed_resolutions: tuple[Literal["drop", "default"], ...]


class WebRebindPlan(DomainModel):
    asset_id: str
    old_source_hash: str
    new_source_hash: str
    plan_digest: str
    retained_layers: list[str] = Field(default_factory=list)
    added_layers: list[str] = Field(default_factory=list)
    removed_layers: list[str] = Field(default_factory=list)
    affected_clips: list[str] = Field(default_factory=list)
    conflicts: list[WebRebindConflict] = Field(default_factory=list)


class WebRebindCommitReport(DomainModel):
    asset_id: str
    old_source_hash: str
    new_source_hash: str
    plan_digest: str
    migrated_clips: list[str] = Field(default_factory=list)
    resolved_paths: dict[str, Literal["drop", "default"]] = Field(default_factory=dict)
    archive_path: str = ""


class WebVariantResult(DomainModel):
    sequence_id: str
    clip_id: str
    name: str
    revision: int


class WebClipExportResult(DomainModel):
    clip_id: str
    format: WebExportFormat
    output_path: str
    cache_path: str


def resolved_web_scene_data(
    state: WebClipState,
    manifest: EditableMediaManifest,
    scene_id: str,
) -> dict[str, JsonValue]:
    try:
        scene = next(item for item in manifest.scenes if item.id == scene_id)
    except StopIteration as error:
        raise ValueError(
            f"Editable media scene does not exist: {scene_id}"
        ) from error
    current = state.scenes.get(scene.id, WebSceneState())
    data = {item.id: item.default for item in manifest.data_fields}
    data.update(scene.data)
    data.update(current.data_snapshot.values)
    return data


def media_source_ids_in_web_data(
    data: Mapping[str, JsonValue],
    fields: list[WebDataField],
) -> tuple[str, ...]:
    source_ids: list[str] = []
    for field in fields:
        value = data.get(field.id)
        if field.kind == "media-source":
            if isinstance(value, str) and value:
                source_ids.append(value)
            continue
        if field.kind != "table" or not isinstance(value, list):
            continue
        source_columns = {
            column.id
            for column in field.columns
            if column.kind == "media-source"
        }
        for row in value:
            if not isinstance(row, dict):
                continue
            for column_id in source_columns:
                source_id = row.get(column_id)
                if isinstance(source_id, str) and source_id:
                    source_ids.append(source_id)
    return tuple(dict.fromkeys(source_ids))


def web_runtime_state(
    state: WebClipState,
    manifest: EditableMediaManifest,
) -> dict[str, JsonValue]:
    variant = manifest.variant_for(state.variant.id if state.variant is not None else None)
    known_scene_ids = {item.id for item in manifest.scenes}
    scene_id = state.scene_id if state.scene_id in known_scene_ids else manifest.scenes[0].id
    resolved_scenes: dict[str, JsonValue] = {}
    for scene in manifest.scenes:
        current = state.scenes.get(scene.id, WebSceneState())
        resolved_layers: dict[str, JsonValue] = {}
        for layer in manifest.layers:
            values = manifest.layer_values_for(variant.id, layer.id)
            values.update(
                current.layers.get(layer.id, WebLayerOverride()).model_dump(exclude_none=True)
            )
            resolved_layers[layer.id] = values
        data = resolved_web_scene_data(state, manifest, scene.id)
        scene_parameters = {
            item.descriptor.id: item.descriptor.default
            for item in manifest.parameters
            if item.binding.scope == "scene"
        }
        scene_parameters.update(scene.parameters)
        scene_parameters.update(current.parameters)
        resolved_scenes[scene.id] = cast(
            JsonValue,
            {
                "layers": resolved_layers,
                "animations": {
                    layer_id: {
                        field: track.model_dump(mode="json")
                        for field, track in tracks.items()
                    }
                    for layer_id, tracks in current.animations.items()
                },
                "parameters": cast(JsonValue, scene_parameters),
                "parameter_animations": {
                    parameter_id: track.model_dump(mode="json")
                    for parameter_id, track in current.parameter_animations.items()
                },
                "parameter_locks": list(current.parameter_locks),
                "data": data,
                "locks": {
                    layer_id: list(fields) for layer_id, fields in current.locks.items()
                },
            },
        )
    theme = {item.id: item.default for item in manifest.theme_variables}
    theme.update(state.theme)
    parameters = {
        item.descriptor.id: item.descriptor.default
        for item in manifest.parameters
        if item.binding.scope == "global"
    }
    parameters.update(state.parameters)
    return {
        "scenes": cast(JsonValue, resolved_scenes),
        "theme": cast(JsonValue, theme),
        "theme_bindings": cast(JsonValue, {
            item.id: item.css_variable for item in manifest.theme_variables
        }),
        "parameters": cast(JsonValue, parameters),
        "parameter_bindings": cast(JsonValue, {
            item.descriptor.id: item.binding.css_variable
            for item in manifest.parameters
            if item.binding.css_variable is not None
        }),
        "parameter_locks": list(state.parameter_locks),
        "variant": cast(JsonValue, {
            "id": variant.id,
            "width": variant.canvas.width,
            "height": variant.canvas.height,
        }),
        "scene_id": scene_id,
        "playback": cast(JsonValue, {
            "mode": state.playback.mode if state.playback is not None else manifest.playback.mode,
        }),
        "revision": state.revision,
    }
