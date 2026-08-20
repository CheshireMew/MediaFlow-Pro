from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path, PurePosixPath
from typing import Literal

from mediaflow.application.web_package_contract import resolve_media_bindings
from mediaflow.domain.web_manifest import EditableMediaManifest
from mediaflow.domain.web_media_sources import (
    WebBrowserMediaBinding,
    WebMediaSourcesManifest,
    WebNativeAudioBinding,
    WebNativeUnderlayBinding,
)
from mediaflow.domain.web_state import WebClipState


@dataclass(frozen=True, slots=True)
class WebNativeVideoSegment:
    source_id: str
    path: Path
    start_ms: Fraction
    duration_ms: Fraction
    active_duration_ms: Fraction
    source_in_ms: int
    fit: Literal["cover", "contain"]
    playback: Literal["hold", "repeat"]


@dataclass(frozen=True, slots=True)
class WebNativeAudioSegment:
    source_id: str
    path: Path
    start_ms: Fraction
    duration_ms: Fraction
    source_in_ms: int
    loop: Literal["none", "repeat"]
    gain_db: float


@dataclass(frozen=True, slots=True)
class WebNativeMediaPlan:
    video_segments: tuple[WebNativeVideoSegment, ...]
    audio_segments: tuple[WebNativeAudioSegment, ...]

    def cache_payload(self) -> dict[str, object]:
        def fraction_payload(value: Fraction) -> tuple[int, int]:
            return value.numerator, value.denominator

        return {
            "video_segments": [
                {
                    "source_id": segment.source_id,
                    "path": segment.path.as_posix(),
                    "start_ms": fraction_payload(segment.start_ms),
                    "duration_ms": fraction_payload(segment.duration_ms),
                    "active_duration_ms": fraction_payload(segment.active_duration_ms),
                    "source_in_ms": segment.source_in_ms,
                    "fit": segment.fit,
                    "playback": segment.playback,
                }
                for segment in self.video_segments
            ],
            "audio_segments": [
                {
                    "source_id": segment.source_id,
                    "path": segment.path.as_posix(),
                    "start_ms": fraction_payload(segment.start_ms),
                    "duration_ms": fraction_payload(segment.duration_ms),
                    "source_in_ms": segment.source_in_ms,
                    "loop": segment.loop,
                    "gain_db": segment.gain_db,
                }
                for segment in self.audio_segments
            ],
        }


def slice_web_native_media_plan_for_frame(
    plan: WebNativeMediaPlan,
    *,
    source_frame: int,
    fps_numerator: int,
    fps_denominator: int,
) -> WebNativeMediaPlan:
    if source_frame < 0:
        raise ValueError("Editable media filmstrip source frame cannot be negative")
    if fps_numerator <= 0 or fps_denominator <= 0:
        raise ValueError("Editable media filmstrip frame rate must be positive")
    frame_duration_ms = Fraction(fps_denominator * 1000, fps_numerator)
    frame_time_ms = source_frame * frame_duration_ms
    selected: list[WebNativeVideoSegment] = []
    for segment in plan.video_segments:
        if not segment.start_ms <= frame_time_ms < segment.start_ms + segment.duration_ms:
            continue
        offset_ms = frame_time_ms - segment.start_ms
        if segment.playback == "hold":
            offset_ms = min(
                offset_ms,
                max(Fraction(0), segment.active_duration_ms - frame_duration_ms),
            )
        selected.append(
            WebNativeVideoSegment(
                source_id=segment.source_id,
                path=segment.path,
                start_ms=Fraction(0),
                duration_ms=frame_duration_ms,
                active_duration_ms=frame_duration_ms,
                source_in_ms=segment.source_in_ms + int(offset_ms),
                fit=segment.fit,
                playback=segment.playback,
            )
        )
        break
    return WebNativeMediaPlan(video_segments=tuple(selected), audio_segments=())


def build_web_native_media_plan(
    *,
    package_root: Path,
    manifest: EditableMediaManifest,
    media_sources: WebMediaSourcesManifest,
    clip_state: WebClipState,
    target_duration_ms: Fraction,
) -> WebNativeMediaPlan:
    if target_duration_ms <= 0:
        raise ValueError("Editable media native plan needs a positive duration")
    sources = {source.id: source for source in media_sources.sources}
    package_root = package_root.resolve(strict=True)
    scene_bindings = resolve_media_bindings(manifest, media_sources, clip_state)

    def source_path(source_id: str) -> Path:
        relative = sources[source_id].file.split("#", 1)[0]
        path = package_root.joinpath(*PurePosixPath(relative).parts).resolve(strict=True)
        try:
            path.relative_to(package_root)
        except ValueError as error:
            raise ValueError(f"Editable media source escaped its package: {source_id}") from error
        return path

    video_segments: list[WebNativeVideoSegment] = []
    audio_segments: list[WebNativeAudioSegment] = []
    cycle_duration_ms = Fraction(manifest.duration_ms, 1)
    cycle_start_ms = Fraction(0, 1)
    while cycle_start_ms < target_duration_ms:
        scene_start_ms = cycle_start_ms
        for scene_index, scene_binding in enumerate(scene_bindings):
            scene = scene_binding.scene
            active_duration_ms = min(
                Fraction(scene.duration_ms, 1),
                target_duration_ms - scene_start_ms,
            )
            if active_duration_ms <= 0:
                break
            frozen_tail_ms = (
                max(Fraction(0), target_duration_ms - cycle_duration_ms)
                if manifest.playback.loop != "repeat" and scene_index == len(scene_bindings) - 1
                else Fraction(0)
            )
            for source_id in scene_binding.source_ids:
                binding = sources[source_id].binding
                if isinstance(binding, WebBrowserMediaBinding):
                    continue
                path = source_path(source_id)
                if isinstance(binding, WebNativeUnderlayBinding):
                    video_segments.append(
                        WebNativeVideoSegment(
                            source_id=source_id,
                            path=path,
                            start_ms=scene_start_ms,
                            duration_ms=active_duration_ms + frozen_tail_ms,
                            active_duration_ms=active_duration_ms,
                            source_in_ms=binding.source_in_ms,
                            fit=binding.fit,
                            playback=binding.playback,
                        )
                    )
                    if binding.audio == "include":
                        audio_segments.append(
                            WebNativeAudioSegment(
                                source_id=source_id,
                                path=path,
                                start_ms=scene_start_ms,
                                duration_ms=active_duration_ms,
                                source_in_ms=binding.source_in_ms,
                                loop="repeat" if binding.playback == "repeat" else "none",
                                gain_db=binding.gain_db,
                            )
                        )
                    continue
                if not isinstance(binding, WebNativeAudioBinding):
                    raise TypeError(f"Unknown editable media pipeline: {source_id}")
                audio_segments.append(
                    WebNativeAudioSegment(
                        source_id=source_id,
                        path=path,
                        start_ms=scene_start_ms,
                        duration_ms=active_duration_ms,
                        source_in_ms=binding.source_in_ms,
                        loop=binding.loop,
                        gain_db=binding.gain_db,
                    )
                )
            scene_start_ms += Fraction(scene.duration_ms, 1)
            if scene_start_ms >= target_duration_ms:
                break
        if manifest.playback.loop != "repeat":
            break
        cycle_start_ms += cycle_duration_ms
    return WebNativeMediaPlan(
        video_segments=tuple(video_segments),
        audio_segments=tuple(audio_segments),
    )
