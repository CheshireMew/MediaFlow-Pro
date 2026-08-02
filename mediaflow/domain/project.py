from __future__ import annotations

from fractions import Fraction

from pydantic import Field, computed_field, field_validator, model_validator

from .enums import AssetKind, AssetOrigin, AssetStatus, ColorMode, SequenceKind
from .exports import ExportPreset
from .model_base import DomainModel, new_id, now_ms
from .product_identity import PRODUCT_NAME
from .timebase import reframe_frames


class ProjectProfile(DomainModel):
    width: int = 1920
    height: int = 1080
    fps_numerator: int = 30
    fps_denominator: int = 1
    color_mode: ColorMode = ColorMode.SDR_BT709
    bit_depth: int = 8
    audio_sample_rate: int = 48_000
    audio_channels: int = 2

    @model_validator(mode="after")
    def validate_profile(self) -> ProjectProfile:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("Project dimensions must be positive")
        if self.fps_numerator <= 0 or self.fps_denominator <= 0:
            raise ValueError("Project frame rate must be positive")
        if self.bit_depth not in {8, 10, 12, 16}:
            raise ValueError("Project bit depth must be 8, 10, 12, or 16")
        if self.color_mode == ColorMode.HDR10_BT2020_PQ and self.bit_depth < 10:
            raise ValueError("HDR10 projects require at least 10-bit processing")
        if self.audio_sample_rate != 48_000:
            raise ValueError(f"{PRODUCT_NAME} projects use a 48 kHz audio clock")
        if self.audio_channels not in {1, 2, 6}:
            raise ValueError("Audio channels must be mono, stereo, or 5.1")
        return self

    @computed_field
    @property
    def fps(self) -> float:
        return float(Fraction(self.fps_numerator, self.fps_denominator))


class Project(DomainModel):
    id: str = Field(default_factory=new_id)
    name: str
    main_sequence_id: str
    created_at: int = Field(default_factory=now_ms)
    updated_at: int = Field(default_factory=now_ms)
    workflow_auto_continue: bool | None = None

    @field_validator("name")
    @classmethod
    def non_empty_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Project name cannot be empty")
        return value


class AssetFingerprint(DomainModel):
    size: int
    modified_ns: int
    edge_sha256: str


class MediaMetadata(DomainModel):
    duration_frames: int = 0
    width: int | None = None
    height: int | None = None
    fps_numerator: int | None = None
    fps_denominator: int | None = None
    bitrate: int | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    pixel_format: str | None = None
    color_primaries: str | None = None
    color_transfer: str | None = None
    color_space: str | None = None
    variable_frame_rate: bool = False
    has_video: bool = False
    has_audio: bool = False

    def in_frame_clock(
        self,
        source_profile: ProjectProfile,
        destination_profile: ProjectProfile,
    ) -> MediaMetadata:
        return self.model_copy(
            update={
                "duration_frames": reframe_frames(
                    self.duration_frames,
                    source_profile,
                    destination_profile,
                )
            }
        )


class Asset(DomainModel):
    id: str = Field(default_factory=new_id)
    project_id: str
    name: str
    kind: AssetKind
    origin: AssetOrigin
    path: str
    managed: bool = False
    bin_id: str | None = None
    proxy_path: str | None = None
    sdr_preview_proxy_path: str | None = None
    waveform_path: str | None = None
    status: AssetStatus = AssetStatus.ONLINE
    fingerprint: AssetFingerprint | None = None
    metadata: MediaMetadata = Field(default_factory=MediaMetadata)
    created_at: int = Field(default_factory=now_ms)

    def in_frame_clock(
        self,
        source_profile: ProjectProfile,
        destination_profile: ProjectProfile,
    ) -> Asset:
        if (
            source_profile.fps_numerator == destination_profile.fps_numerator
            and source_profile.fps_denominator == destination_profile.fps_denominator
        ):
            return self
        return self.model_copy(
            update={
                "metadata": self.metadata.in_frame_clock(
                    source_profile,
                    destination_profile,
                )
            }
        )


class AssetBin(DomainModel):
    id: str = Field(default_factory=new_id)
    project_id: str
    name: str
    parent_id: str | None = None
    position: int = 0

    @field_validator("name")
    @classmethod
    def non_empty_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("素材文件夹名称不能为空")
        return value


class SequenceInOut(DomainModel):
    in_frame: int = Field(ge=0)
    out_frame: int = Field(ge=1)

    @model_validator(mode="after")
    def positive_range(self) -> SequenceInOut:
        if self.out_frame <= self.in_frame:
            raise ValueError("Sequence out point must be after its in point")
        return self


class Sequence(DomainModel):
    id: str = Field(default_factory=new_id)
    project_id: str
    name: str
    kind: SequenceKind
    profile: ProjectProfile = Field(default_factory=ProjectProfile)
    profile_confirmed: bool = True
    export_preset: ExportPreset | None = None
    in_out: SequenceInOut | None = None
    archived: bool = False
    position: int = 0
    timeline_revision: int = Field(default=0, ge=0)
    created_at: int = Field(default_factory=now_ms)
