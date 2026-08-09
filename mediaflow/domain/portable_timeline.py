from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal, cast

from pydantic import Field, field_validator, model_validator

from mediaflow.file_digest import sha256_file

from .model_base import DomainModel


class PortableTimelineProfile(DomainModel):
    width: int = Field(ge=16)
    height: int = Field(ge=16)
    frame_rate: float = Field(gt=0)
    sample_rate: int = Field(ge=8000)
    channel_layout: Literal["mono", "stereo"]
    background: str = Field(pattern="^#[0-9a-fA-F]{6}$")
    duration_seconds: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def even_dimensions(self) -> PortableTimelineProfile:
        if self.width % 2 or self.height % 2:
            raise ValueError("Portable timeline dimensions must be even")
        return self


class PortableTimelineSource(DomainModel):
    id: str = Field(min_length=1)
    kind: Literal["video", "audio", "image", "web-render"]
    file: str = Field(min_length=1)
    sha256: str = Field(pattern="^[a-f0-9]{64}$")
    duration_seconds: float | None = Field(default=None, gt=0)


class PortablePlacement(DomainModel):
    fit: Literal["contain", "cover", "stretch"] = "contain"
    x: int | None = None
    y: int | None = None
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)


class PortableFade(DomainModel):
    kind: Literal["none", "fade"]
    duration_seconds: float = Field(ge=0)


class PortableMediaClip(DomainModel):
    id: str = Field(min_length=1)
    type: Literal["media"]
    source_id: str = Field(min_length=1)
    timeline_start_seconds: float = Field(ge=0)
    source_in_seconds: float = Field(ge=0)
    duration_seconds: float = Field(gt=0)
    speed: float = Field(default=1.0, gt=0)
    placement: PortablePlacement | None = None
    opacity: float = Field(default=1.0, ge=0, le=1)
    audio_enabled: bool = False
    gain_db: float = 0.0
    fade_in: PortableFade | None = None
    fade_out: PortableFade | None = None


class PortableFreezeClip(DomainModel):
    id: str = Field(min_length=1)
    type: Literal["freeze"]
    source_id: str = Field(min_length=1)
    timeline_start_seconds: float = Field(ge=0)
    source_time_seconds: float = Field(ge=0)
    duration_seconds: float = Field(gt=0)
    placement: PortablePlacement | None = None
    opacity: float = Field(default=1.0, ge=0, le=1)
    fade_in: PortableFade | None = None
    fade_out: PortableFade | None = None


class PortableCaptionClip(DomainModel):
    id: str = Field(min_length=1)
    type: Literal["caption"]
    timeline_start_seconds: float = Field(ge=0)
    duration_seconds: float = Field(gt=0)
    text: str = Field(min_length=1)
    style_id: str = Field(min_length=1)
    language: str | None = None


PortableClip = Annotated[
    PortableMediaClip | PortableFreezeClip | PortableCaptionClip,
    Field(discriminator="type"),
]


class PortableTimelineTrack(DomainModel):
    id: str = Field(min_length=1)
    kind: Literal["video", "audio", "subtitle"]
    name: str = Field(min_length=1)
    muted: bool
    clips: list[PortableClip]


class PortableSubtitleStyle(DomainModel):
    id: str = Field(min_length=1)
    font_family: str = Field(min_length=1)
    font_size: float = Field(gt=0)
    primary_color: str = Field(pattern="^#[0-9a-fA-F]{6}$")
    outline_color: str = Field(pattern="^#[0-9a-fA-F]{6}$")
    outline_width: float = Field(ge=0)
    margin_vertical: int = Field(ge=0)
    alignment: int = Field(ge=1, le=9)
    bold: bool = False
    italic: bool = False


class PortableTimelineMarker(DomainModel):
    id: str = Field(min_length=1)
    time_seconds: float = Field(ge=0)
    label: str = Field(min_length=1)
    note: str = ""


class PortableTimelineDocument(DomainModel):
    protocol: Literal["visual-multimedia-timeline"]
    version: Literal[1]
    project_id: str = Field(min_length=1)
    profile: PortableTimelineProfile
    sources: list[PortableTimelineSource]
    tracks: list[PortableTimelineTrack] = Field(min_length=1)
    subtitle_styles: list[PortableSubtitleStyle]
    markers: list[PortableTimelineMarker]

    @field_validator("sources", "tracks", "subtitle_styles", "markers")
    @classmethod
    def unique_ids(cls, values: list[object]) -> list[object]:
        identifiers = [str(cast(Any, value).id) for value in values]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Portable timeline identifiers must be unique within each collection")
        return values

    @model_validator(mode="after")
    def coherent_references(self) -> PortableTimelineDocument:
        sources = {source.id: source for source in self.sources}
        styles = {style.id for style in self.subtitle_styles}
        clip_ids: set[str] = set()
        max_end = 0.0
        for track in self.tracks:
            previous_end = 0.0
            for clip in sorted(
                track.clips,
                key=lambda item: (item.timeline_start_seconds, item.id),
            ):
                if clip.id in clip_ids:
                    raise ValueError(f"Duplicate portable clip identifier: {clip.id}")
                clip_ids.add(clip.id)
                expected = {
                    "video": {"media", "freeze"},
                    "audio": {"media"},
                    "subtitle": {"caption"},
                }[track.kind]
                if clip.type not in expected:
                    raise ValueError(f"Portable {track.kind} track cannot contain {clip.type} clips")
                end = clip.timeline_start_seconds + clip.duration_seconds
                if clip.timeline_start_seconds < previous_end - 1e-9:
                    raise ValueError(f"Portable clips overlap on track {track.id}")
                previous_end = end
                max_end = max(max_end, end)
                if isinstance(clip, PortableCaptionClip):
                    if clip.style_id not in styles:
                        raise ValueError(f"Caption {clip.id} references missing style {clip.style_id}")
                    continue
                source = sources.get(clip.source_id)
                if source is None:
                    raise ValueError(f"Clip {clip.id} references missing source {clip.source_id}")
                if track.kind == "video" and source.kind == "audio":
                    raise ValueError(f"Video clip {clip.id} cannot use an audio source")
                if track.kind == "audio" and source.kind == "image":
                    raise ValueError(f"Audio clip {clip.id} cannot use an image source")
                for fade in (clip.fade_in, clip.fade_out):
                    if fade and fade.duration_seconds > clip.duration_seconds:
                        raise ValueError(f"Fade exceeds clip duration: {clip.id}")
        if self.profile.duration_seconds is not None and max_end > self.profile.duration_seconds + 1e-9:
            raise ValueError("Portable clips exceed the declared timeline duration")
        return self

    @property
    def duration_seconds(self) -> float:
        if self.profile.duration_seconds is not None:
            return self.profile.duration_seconds
        return max(
            (
                clip.timeline_start_seconds + clip.duration_seconds
                for track in self.tracks
                for clip in track.clips
            ),
            default=0.0,
        )


@dataclass(frozen=True, slots=True)
class LoadedPortableTimeline:
    path: Path
    root: Path
    sha256: str
    document: PortableTimelineDocument
    sources: dict[str, Path]


def _resolve_relative_file(root: Path, relative: str) -> Path:
    value = Path(relative)
    if value.is_absolute() or ".." in value.parts:
        raise ValueError(f"Portable source must stay inside the timeline directory: {relative}")
    target = (root / value).resolve(strict=True)
    try:
        target.relative_to(root)
    except ValueError as error:
        raise ValueError(f"Portable source must stay inside the timeline directory: {relative}") from error
    if not target.is_file():
        raise FileNotFoundError(target)
    return target


def load_portable_timeline(path: str | Path) -> LoadedPortableTimeline:
    source = Path(path).expanduser().resolve(strict=True)
    if not source.is_file():
        raise FileNotFoundError(source)
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"Portable timeline is not valid JSON: {source}") from error
    document = PortableTimelineDocument.model_validate(raw)
    root = source.parent.resolve()
    sources: dict[str, Path] = {}
    for item in document.sources:
        resolved = _resolve_relative_file(root, item.file)
        if sha256_file(resolved) != item.sha256:
            raise ValueError(f"Portable source hash does not match: {item.id}")
        sources[item.id] = resolved
    return LoadedPortableTimeline(
        path=source,
        root=root,
        sha256=sha256_file(source),
        document=document,
        sources=sources,
    )
