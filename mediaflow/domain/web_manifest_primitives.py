from __future__ import annotations

from typing import Literal

from pydantic import Field, JsonValue, field_validator, model_validator

from .editor_fields import EditorFieldDescriptor
from .model_base import DomainModel

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
WebPlaybackMode = Literal["manual", "autoplay", "hybrid"]

CONTINUOUS_ANIMATION_FIELDS: frozenset[str] = frozenset(
    {"font_size", "x", "y", "width", "height", "rotation", "opacity", "z_index"}
)


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
            self.driver != "none" or self.camera is not None or self.key_state_review != "none"
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
                raise ValueError("Complex editable scenes need reviewed start, change, and result states")
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
