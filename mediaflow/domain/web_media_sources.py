from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from .model_base import DomainModel
from .web_package_paths import local_media_reference, local_package_path


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
        return local_package_path(value)


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
    WebBrowserMediaBinding | WebNativeAudioBinding | WebNativeUnderlayBinding,
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
    representation: WebSourceRepresentation | WebProxyRepresentation = Field(discriminator="kind")
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
        return local_media_reference(value)

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
            raise ValueError("Editable media audio sources must use the native-audio pipeline")
        if self.binding.pipeline == "native-audio" and self.media_type != "audio":
            raise ValueError("Only editable media audio sources can use native-audio")
        if self.binding.pipeline == "native-underlay" and self.media_type != "video":
            raise ValueError("Only editable media video sources can use native-underlay")
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
                raise ValueError("Editable media proxies must preserve their source pipeline binding")
        return self


def web_media_sources_have_audio(
    media_sources: WebMediaSourcesManifest,
) -> bool:
    return any(
        isinstance(source.binding, WebNativeAudioBinding)
        or (isinstance(source.binding, WebNativeUnderlayBinding) and source.binding.audio == "include")
        for source in media_sources.sources
    )
