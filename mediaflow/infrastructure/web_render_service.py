from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path, PurePosixPath
from typing import Literal

from mediaflow.application.ports import TimelineCompilationDocuments
from mediaflow.application.web_media_service import (
    web_package_root,
)
from mediaflow.atomic_file import atomic_write_text, unique_temporary_sibling
from mediaflow.domain.enums import AssetKind
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.project import Asset, AssetFingerprint
from mediaflow.domain.timeline import Clip, TimelineState
from mediaflow.domain.web_media import (
    EditableMediaManifest,
    WebAssetSpec,
    WebBrowserMediaBinding,
    WebClipExportResult,
    WebClipState,
    WebExportFormat,
    WebMediaSourcesManifest,
    WebNativeAudioBinding,
    WebNativeUnderlayBinding,
    media_source_ids_in_web_data,
    require_web_export_destination,
    resolved_web_scene_data,
    web_media_sources_have_audio,
    web_runtime_state,
)
from mediaflow.infrastructure.chromium_runtime import find_chromium_executable
from mediaflow.infrastructure.ffmpeg_runner import FfmpegInputPipe, FfmpegRunner
from mediaflow.infrastructure.file_fingerprint import (
    fingerprint_file,
    fingerprint_matches,
)
from mediaflow.infrastructure.output_reservation import (
    archive_failed_output,
    require_output_transaction_path,
    reserve_output,
    temporary_output_path,
)
from mediaflow.infrastructure.project_lock import ProcessFileLock
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from mediaflow.infrastructure.subprocess_runner import run_cancellable
from mediaflow.infrastructure.web_browser import WebPackagePreviewServer
from mediaflow.infrastructure.web_capture_engine import (
    FastCaptureFallbackRequired,
    WebCaptureMode,
    get_web_capture_engine,
)

WEB_RENDERER_VERSION = "5"
WEB_CACHE_MANIFEST_SCHEMA = "mediaflow-web-render-cache/v2"


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
                    "active_duration_ms": fraction_payload(
                        segment.active_duration_ms
                    ),
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


@dataclass(frozen=True, slots=True)
class WebRenderTarget:
    key: str
    path: Path
    animated: bool
    frame_count: int
    width: int
    height: int
    fps_numerator: int
    fps_denominator: int
    has_audio: bool
    audio_sample_rate: int
    audio_channels: int
    native_media_plan: WebNativeMediaPlan

    @property
    def manifest_path(self) -> Path:
        return self.path.with_name(f"{self.path.name}.manifest.json")


def _fraction_decimal(value: Fraction, digits: int = 9) -> str:
    rendered = f"{float(value):.{digits}f}".rstrip("0").rstrip(".")
    return rendered or "0"


def _milliseconds_as_seconds(value: Fraction) -> str:
    return _fraction_decimal(value / 1000)


def _audio_channel_layout(channels: int) -> str:
    try:
        return {1: "mono", 2: "stereo", 6: "5.1"}[channels]
    except KeyError as error:
        raise ValueError(f"Unsupported editable media audio channels: {channels}") from error


def require_committed_web_publication(
    *,
    project_dir: Path,
    package_root: Path,
    asset_id: str,
    source_hash: str,
) -> None:
    publication_root = (
        project_dir.resolve()
        / "sources"
        / "web"
    )
    package_root = package_root.resolve()
    match = re.fullmatch(r"p-([0-9a-f]{24})", package_root.name)
    if (
        package_root.parent != publication_root
        or match is None
    ):
        raise RuntimeError(
            "Editable media rendering requires an immutable managed publication"
        )
    token = match.group(1)
    receipt_path = publication_root / "receipts" / f"r-{token}.json"
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        raise RuntimeError(
            "Editable media publication receipt is unreadable"
        ) from error
    expected = {
        "schema_version": 1,
        "asset_id": asset_id,
        "source_hash": source_hash,
        "token": token,
        "directory": package_root.name,
        "status": "committed",
    }
    if receipt != expected:
        raise RuntimeError(
            "Editable media publication receipt does not match its "
            "immutable package"
        )


def build_web_render_ffmpeg_command(
    target: WebRenderTarget,
    output_path: Path,
) -> list[str]:
    if not target.animated:
        raise ValueError("FFmpeg frame-pipe rendering requires an animated target")
    fps = Fraction(target.fps_numerator, target.fps_denominator)
    duration_ms = Fraction(
        target.frame_count * target.fps_denominator * 1000,
        target.fps_numerator,
    )
    arguments = [
        "-loglevel",
        "error",
        "-f",
        "image2pipe",
        "-vcodec",
        "png",
        "-framerate",
        f"{fps.numerator}/{fps.denominator}",
        "-i",
        "-",
    ]
    if (
        not target.native_media_plan.video_segments
        and not target.native_media_plan.audio_segments
        and not target.has_audio
    ):
        return [
            *arguments,
            "-an",
            "-c:v",
            "ffv1",
            "-level",
            "3",
            "-pix_fmt",
            "bgra",
            "-y",
            str(output_path),
        ]
    next_input = 1
    video_inputs: list[tuple[WebNativeVideoSegment, int]] = []
    for video_segment in target.native_media_plan.video_segments:
        if video_segment.playback == "repeat":
            arguments.extend(["-stream_loop", "-1"])
        if video_segment.source_in_ms:
            arguments.extend(
                [
                    "-ss",
                    _milliseconds_as_seconds(
                        Fraction(video_segment.source_in_ms)
                    ),
                ]
            )
        arguments.extend(["-i", str(video_segment.path)])
        video_inputs.append((video_segment, next_input))
        next_input += 1
    audio_inputs: list[tuple[WebNativeAudioSegment, int]] = []
    for audio_segment in target.native_media_plan.audio_segments:
        if audio_segment.loop == "repeat":
            arguments.extend(["-stream_loop", "-1"])
        if audio_segment.source_in_ms:
            arguments.extend(
                [
                    "-ss",
                    _milliseconds_as_seconds(
                        Fraction(audio_segment.source_in_ms)
                    ),
                ]
            )
        arguments.extend(["-i", str(audio_segment.path)])
        audio_inputs.append((audio_segment, next_input))
        next_input += 1

    filters: list[str] = []
    video_map = "0:v:0"
    if video_inputs:
        timeline_labels: list[str] = []
        cursor_ms = Fraction(0)
        for segment_index, (video_segment, input_index) in enumerate(video_inputs):
            if video_segment.start_ms < cursor_ms:
                raise ValueError("Editable media native video segments overlap")
            if video_segment.start_ms > cursor_ms:
                gap_label = f"native_gap_{segment_index}"
                filters.append(
                    "color="
                    f"c=black@0.0:s={target.width}x{target.height}:"
                    f"r={fps.numerator}/{fps.denominator}:"
                    f"d={_milliseconds_as_seconds(video_segment.start_ms - cursor_ms)},"
                    f"format=bgra,setpts=PTS-STARTPTS[{gap_label}]"
                )
                timeline_labels.append(gap_label)
            segment_label = f"native_video_{segment_index}"
            scale = (
                f"scale={target.width}:{target.height}:"
                "force_original_aspect_ratio=increase,"
                f"crop={target.width}:{target.height}"
                if video_segment.fit == "cover"
                else (
                    f"scale={target.width}:{target.height}:"
                    "force_original_aspect_ratio=decrease,"
                    f"pad={target.width}:{target.height}:"
                    "(ow-iw)/2:(oh-ih)/2:color=black@0.0"
                )
            )
            hold = (
                f",tpad=stop_mode=clone:"
                "stop_duration="
                f"{_milliseconds_as_seconds(video_segment.active_duration_ms)}"
                if video_segment.playback == "hold"
                else ""
            )
            frozen_tail_ms = (
                video_segment.duration_ms
                - video_segment.active_duration_ms
            )
            frozen_tail = (
                ",tpad=stop_mode=clone:"
                f"stop_duration={_milliseconds_as_seconds(frozen_tail_ms)}"
                if frozen_tail_ms > 0
                else ""
            )
            filters.append(
                f"[{input_index}:v:0]"
                f"fps={fps.numerator}/{fps.denominator},{scale},format=bgra"
                f"{hold},trim=duration="
                f"{_milliseconds_as_seconds(video_segment.active_duration_ms)}"
                f"{frozen_tail},trim=duration="
                f"{_milliseconds_as_seconds(video_segment.duration_ms)},"
                f"setpts=PTS-STARTPTS[{segment_label}]"
            )
            timeline_labels.append(segment_label)
            cursor_ms = video_segment.start_ms + video_segment.duration_ms
        if cursor_ms < duration_ms:
            gap_label = "native_gap_tail"
            filters.append(
                "color="
                f"c=black@0.0:s={target.width}x{target.height}:"
                f"r={fps.numerator}/{fps.denominator}:"
                f"d={_milliseconds_as_seconds(duration_ms - cursor_ms)},"
                f"format=bgra,setpts=PTS-STARTPTS[{gap_label}]"
            )
            timeline_labels.append(gap_label)
        underlay_label = timeline_labels[0]
        if len(timeline_labels) > 1:
            underlay_label = "native_underlay"
            inputs = "".join(f"[{label}]" for label in timeline_labels)
            filters.append(
                f"{inputs}concat=n={len(timeline_labels)}:"
                f"v=1:a=0[{underlay_label}]"
            )
        filters.extend(
            [
                "[0:v:0]format=rgba[web_overlay]",
                f"[{underlay_label}][web_overlay]"
                "overlay=0:0:shortest=1:format=auto,"
                "format=bgra[web_composite]",
            ]
        )
        video_map = "[web_composite]"

    audio_map: str | None = None
    if target.has_audio:
        layout = _audio_channel_layout(target.audio_channels)
        audio_labels: list[str] = []
        for segment_index, (audio_segment, input_index) in enumerate(audio_inputs):
            if audio_segment.start_ms.denominator != 1:
                raise ValueError(
                    "Editable media native audio start must use whole milliseconds"
                )
            audio_label = f"native_audio_{segment_index}"
            filters.append(
                f"[{input_index}:a:0]"
                f"aresample={target.audio_sample_rate},"
                f"aformat=sample_fmts=fltp:channel_layouts={layout},"
                f"volume={audio_segment.gain_db:.6f}dB,apad,"
                f"atrim=duration={_milliseconds_as_seconds(audio_segment.duration_ms)},"
                "asetpts=PTS-STARTPTS,"
                f"adelay={audio_segment.start_ms.numerator}:all=1[{audio_label}]"
            )
            audio_labels.append(audio_label)
        if audio_labels:
            inputs = "".join(f"[{label}]" for label in audio_labels)
            filters.append(
                f"{inputs}amix=inputs={len(audio_labels)}:"
                "duration=longest:normalize=0,"
                f"atrim=duration={_milliseconds_as_seconds(duration_ms)},"
                f"aformat=sample_rates={target.audio_sample_rate}:"
                f"channel_layouts={layout},asetpts=PTS-STARTPTS[web_audio]"
            )
        else:
            filters.append(
                f"anullsrc=r={target.audio_sample_rate}:cl={layout},"
                f"atrim=duration={_milliseconds_as_seconds(duration_ms)},"
                "asetpts=PTS-STARTPTS[web_audio]"
            )
        audio_map = "[web_audio]"

    if filters:
        arguments.extend(["-filter_complex", ";".join(filters)])
    arguments.extend(["-map", video_map])
    if audio_map is None:
        arguments.append("-an")
    else:
        arguments.extend(["-map", audio_map])
    arguments.extend(
        [
            "-frames:v",
            str(target.frame_count),
            "-t",
            _milliseconds_as_seconds(duration_ms),
            "-c:v",
            "ffv1",
            "-level",
            "3",
            "-pix_fmt",
            "bgra",
        ]
    )
    if audio_map is not None:
        arguments.extend(["-c:a", "flac"])
    arguments.extend(["-y", str(output_path)])
    return arguments


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
    scene_bindings = [
        (
            scene,
            media_source_ids_in_web_data(
                resolved_web_scene_data(
                    clip_state,
                    manifest,
                    scene.id,
                ),
                manifest.data_fields,
            ),
        )
        for scene in manifest.scenes
    ]
    for scene, source_ids in scene_bindings:
        unknown = set(source_ids) - set(sources)
        if unknown:
            raise ValueError(
                f"Editable media scene {scene.id} references undeclared "
                f"media sources: {sorted(unknown)}"
            )
        underlays = [
            source_id
            for source_id in source_ids
            if isinstance(
                sources[source_id].binding,
                WebNativeUnderlayBinding,
            )
        ]
        if len(underlays) > 1:
            raise ValueError(
                f"Editable media scene {scene.id} selects more than one "
                "native video underlay"
            )

    def source_path(source_id: str) -> Path:
        relative = sources[source_id].file.split("#", 1)[0]
        path = package_root.joinpath(
            *PurePosixPath(relative).parts
        ).resolve(strict=True)
        try:
            path.relative_to(package_root)
        except ValueError as error:
            raise ValueError(
                f"Editable media source escaped its package: {source_id}"
            ) from error
        return path

    video_segments: list[WebNativeVideoSegment] = []
    audio_segments: list[WebNativeAudioSegment] = []
    cycle_duration_ms = Fraction(manifest.duration_ms, 1)
    cycle_start_ms = Fraction(0, 1)
    while cycle_start_ms < target_duration_ms:
        scene_start_ms = cycle_start_ms
        for scene_index, (scene, source_ids) in enumerate(scene_bindings):
            active_duration_ms = min(
                Fraction(scene.duration_ms, 1),
                target_duration_ms - scene_start_ms,
            )
            if active_duration_ms <= 0:
                break
            frozen_tail_ms = (
                max(Fraction(0), target_duration_ms - cycle_duration_ms)
                if (
                    manifest.playback.loop != "repeat"
                    and scene_index == len(scene_bindings) - 1
                )
                else Fraction(0)
            )
            video_duration_ms = active_duration_ms + frozen_tail_ms
            for source_id in source_ids:
                source = sources[source_id]
                binding = source.binding
                if isinstance(binding, WebBrowserMediaBinding):
                    continue
                path = source_path(source_id)
                if isinstance(binding, WebNativeUnderlayBinding):
                    video_segments.append(
                        WebNativeVideoSegment(
                            source_id=source_id,
                            path=path,
                            start_ms=scene_start_ms,
                            duration_ms=video_duration_ms,
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
                                loop=(
                                    "repeat"
                                    if binding.playback == "repeat"
                                    else "none"
                                ),
                                gain_db=binding.gain_db,
                            )
                        )
                    continue
                if not isinstance(binding, WebNativeAudioBinding):
                    raise TypeError(
                        f"Unknown editable media pipeline: {source_id}"
                    )
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


class WebRenderCache:
    def __init__(
        self,
        documents: TimelineCompilationDocuments,
        paths: RuntimePaths | None = None,
    ):
        self.documents = documents
        self.paths = paths or RuntimePaths.discover()

    def target(
        self,
        state: TimelineState,
        clip: Clip,
        asset: Asset | None = None,
    ) -> WebRenderTarget:
        asset = asset or self.documents.catalog.get_asset(clip.asset_id)
        if asset.kind != AssetKind.WEB:
            raise ValueError("Web render cache only accepts web clips")
        spec = self.documents.web.get_web_asset_spec(asset.id)
        clip_state = state.web_states.get(clip.id)
        if clip_state is None:
            raise ValueError(f"Web clip has no editable state: {clip.id}")
        variant = spec.manifest.variant_for(
            clip_state.variant.id if clip_state.variant is not None else None
        )
        animated = spec.manifest.duration_ms > 0 or any(
            scene.animations for scene in clip_state.scenes.values()
        )
        speed = Fraction(abs(clip.speed_numerator), clip.speed_denominator)
        consumed = max(1, -(-(clip.duration * speed.numerator) // speed.denominator))
        frame_count = max(
            1,
            clip.source_in + consumed if clip.speed_numerator > 0 else clip.source_in + 1,
        )
        package_root = web_package_root(
            self.documents.catalog.resolve_asset_path(asset),
            spec.manifest,
        )
        source_hash = spec.source_hash
        require_committed_web_publication(
            project_dir=self.documents.project_dir,
            package_root=package_root,
            asset_id=asset.id,
            source_hash=source_hash,
        )
        if clip_state.source_hash != source_hash:
            raise RuntimeError(
                "Editable media clip state does not match its immutable "
                "package publication; rebind the package"
            )
        media_sources = WebMediaSourcesManifest.model_validate_json(
            (package_root / spec.manifest.media_sources).read_text(
                encoding="utf-8"
            )
        )
        declared_has_audio = web_media_sources_have_audio(media_sources)
        if declared_has_audio != asset.metadata.has_audio:
            raise RuntimeError(
                "Editable media audio metadata no longer matches its v4 "
                "source bindings; reimport the package"
            )
        native_media_plan = build_web_native_media_plan(
            package_root=package_root,
            manifest=spec.manifest,
            media_sources=media_sources,
            clip_state=clip_state,
            target_duration_ms=Fraction(
                frame_count
                * state.sequence.profile.fps_denominator
                * 1000,
                state.sequence.profile.fps_numerator,
            ),
        )
        render_state = web_runtime_state(clip_state, spec.manifest)
        render_state.pop("revision", None)
        payload = {
            "renderer_version": WEB_RENDERER_VERSION,
            "source_hash": source_hash,
            "state": render_state,
            "sequence": state.sequence.profile.model_dump(mode="json"),
            "clip_range": {
                "source_in": clip.source_in,
                "duration": clip.duration,
                "speed_numerator": clip.speed_numerator,
                "speed_denominator": clip.speed_denominator,
            },
            "frame_count": frame_count,
            "native_media": native_media_plan.cache_payload(),
            "audio": {
                "enabled": asset.metadata.has_audio,
                "sample_rate": state.sequence.profile.audio_sample_rate,
                "channels": state.sequence.profile.audio_channels,
            },
        }
        digest = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
        suffix = ".mkv" if animated else ".png"
        return WebRenderTarget(
            key=digest,
            # The complete digest remains the logical key. A 128-bit prefix is
            # sufficient for the cache filename and keeps deep Windows project
            # paths below legacy path-length limits.
            path=(
                self.paths.project_cache_dir(
                    self.documents.project_dir
                )
                / "web"
                / f"{digest[:32]}{suffix}"
            ),
            animated=animated,
            frame_count=frame_count,
            width=variant.canvas.width,
            height=variant.canvas.height,
            fps_numerator=state.sequence.profile.fps_numerator,
            fps_denominator=state.sequence.profile.fps_denominator,
            has_audio=asset.metadata.has_audio,
            audio_sample_rate=state.sequence.profile.audio_sample_rate,
            audio_channels=state.sequence.profile.audio_channels,
            native_media_plan=native_media_plan,
        )


class WebRenderService:
    def __init__(
        self,
        documents: TimelineCompilationDocuments,
        paths: RuntimePaths | None = None,
    ) -> None:
        self.documents = documents
        self.paths = paths or RuntimePaths.discover()
        self.ffmpeg = FfmpegRunner(self.paths.ffmpeg)
        self.cache = WebRenderCache(documents, self.paths)

    def ensure_sequence(
        self,
        state: TimelineState,
        *,
        progress=None,
        check_cancelled=None,
    ) -> list[Path]:
        assets = {asset.id: asset for asset in self.documents.catalog.list_assets()}
        web_clips = [
            clip for clip in state.clips if assets.get(clip.asset_id, None) is not None
            and assets[clip.asset_id].kind == AssetKind.WEB
        ]
        results: list[Path] = []
        for index, clip in enumerate(web_clips):
            if check_cancelled is not None:
                check_cancelled()
            results.append(
                self.render_clip(
                    state,
                    clip.id,
                    progress=progress,
                    check_cancelled=check_cancelled,
                )
            )
            if progress is not None and web_clips:
                progress(
                    OperationProgress.determinate(
                        "web_render_items",
                        completed=index + 1,
                        total=len(web_clips),
                        unit="items",
                    )
                )
        return results

    def render_clip(
        self,
        state: TimelineState,
        clip_id: str,
        *,
        progress=None,
        check_cancelled=None,
    ) -> Path:
        try:
            clip = next(item for item in state.clips if item.id == clip_id)
        except StopIteration as error:
            raise KeyError(clip_id) from error
        asset = self.documents.catalog.get_asset(clip.asset_id)
        target = self.cache.target(state, clip, asset)
        if self._cache_is_ready(target):
            if progress:
                progress(
                    OperationProgress.determinate(
                        "web_render_cache_ready",
                        completed=1,
                        total=1,
                        unit="items",
                    )
                )
            return target.path
        if progress:
            progress(OperationProgress.indeterminate("web_render_preparing"))
        target.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = target.path.with_name(f"{target.path.name}.lock")
        cache_lock = self._acquire_cache_lock(
            lock_path,
            target,
            check_cancelled=check_cancelled,
        )
        if cache_lock is None:
            return target.path
        try:
            if self._cache_is_ready(target):
                return target.path
            spec = self.documents.web.get_web_asset_spec(asset.id)
            clip_state = state.web_states[clip.id]
            entry = self.documents.catalog.resolve_asset_path(asset)
            if not entry.is_file():
                raise FileNotFoundError(entry)
            self._render_browser(
                entry,
                spec,
                clip_state,
                state,
                target,
                progress=progress,
                check_cancelled=check_cancelled,
            )
            if not self._cache_is_ready(target):
                raise RuntimeError("Editable web media renderer did not produce a cache file")
            return target.path
        finally:
            cache_lock.release()

    @staticmethod
    def _cache_is_ready(target: WebRenderTarget) -> bool:
        if not target.path.is_file() or not target.manifest_path.is_file():
            return False
        try:
            payload = json.loads(target.manifest_path.read_text(encoding="utf-8"))
            fingerprint_payload = payload["fingerprint"]
            fingerprint = AssetFingerprint.model_validate(fingerprint_payload)
        except (KeyError, OSError, ValueError, TypeError, json.JSONDecodeError):
            return False
        expected = {
            "schema": WEB_CACHE_MANIFEST_SCHEMA,
            "renderer_version": WEB_RENDERER_VERSION,
            "key": target.key,
            "animated": target.animated,
            "frame_count": target.frame_count,
            "width": target.width,
            "height": target.height,
            "fps_numerator": target.fps_numerator,
            "fps_denominator": target.fps_denominator,
            "has_audio": target.has_audio,
            "audio_sample_rate": target.audio_sample_rate,
            "audio_channels": target.audio_channels,
        }
        if any(payload.get(key) != value for key, value in expected.items()):
            return False
        probe = payload.get("probe")
        if not isinstance(probe, dict):
            return False
        expected_probe = {
            "codec_name": "ffv1" if target.animated else "png",
            "width": target.width,
            "height": target.height,
            "frame_count": target.frame_count if target.animated else 1,
            "has_audio": target.has_audio,
            "audio_codec_name": "flac" if target.has_audio else None,
            "audio_sample_rate": (
                target.audio_sample_rate if target.has_audio else None
            ),
            "audio_channels": target.audio_channels if target.has_audio else None,
        }
        if any(probe.get(key) != value for key, value in expected_probe.items()):
            return False
        if target.animated and (
            probe.get("pixel_format") != "bgra"
            or probe.get("fps_numerator") != target.fps_numerator
            or probe.get("fps_denominator") != target.fps_denominator
        ):
            return False
        return fingerprint_matches(target.path, fingerprint)

    @classmethod
    def _acquire_cache_lock(
        cls,
        lock_path: Path,
        target: WebRenderTarget,
        *,
        check_cancelled=None,
    ) -> ProcessFileLock | None:
        deadline = time.monotonic() + 900
        lock = ProcessFileLock(lock_path)
        while True:
            if cls._cache_is_ready(target):
                return None
            if lock.acquire():
                return lock
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Timed out waiting for editable media cache: {target.path}"
                ) from None
            if check_cancelled is not None:
                check_cancelled()
            time.sleep(0.1)

    def export_clip(
        self,
        state: TimelineState,
        clip_id: str,
        output_path: str | Path,
        format: WebExportFormat,
        *,
        time_ms: int = 0,
        background: str = "#000000",
        overwrite: bool = False,
        progress=None,
        check_cancelled=None,
    ) -> WebClipExportResult:
        destination = require_output_transaction_path(output_path)
        try:
            clip = next(item for item in state.clips if item.id == clip_id)
        except StopIteration as error:
            raise KeyError(clip_id) from error
        target = self.cache.target(state, clip)
        require_web_export_destination(
            destination,
            format,
            overlay_suffix=target.path.suffix,
        )
        if destination.exists() and not overwrite:
            raise FileExistsError(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with reserve_output(destination, runtime_dir=self.paths.runtime_dir):
            return self._export_clip_reserved(
                state,
                clip_id,
                destination,
                format,
                clip=clip,
                target=target,
                time_ms=time_ms,
                background=background,
                overwrite=overwrite,
                progress=progress,
                check_cancelled=check_cancelled,
            )

    def _export_clip_reserved(
        self,
        state: TimelineState,
        clip_id: str,
        output_path: str | Path,
        format: WebExportFormat,
        *,
        clip: Clip,
        target: WebRenderTarget,
        time_ms: int = 0,
        background: str = "#000000",
        overwrite: bool = False,
        progress=None,
        check_cancelled=None,
    ) -> WebClipExportResult:
        destination = Path(output_path).expanduser().resolve()
        if destination.exists() and not overwrite:
            raise FileExistsError(destination)
        cache_path = self.render_clip(
            state,
            clip_id,
            progress=progress,
            check_cancelled=check_cancelled,
        )
        temporary = temporary_output_path(destination, f"web-{format}")
        try:
            self._write_export_file(
                format=format,
                cache_path=cache_path,
                target=target,
                state=state,
                clip=clip,
                destination=temporary,
                time_ms=time_ms,
                background=background,
                progress=progress,
                check_cancelled=check_cancelled,
            )
            if not temporary.is_file() or temporary.stat().st_size == 0:
                raise RuntimeError("Editable media export did not produce an output file")
            temporary.replace(destination)
        except Exception:
            archive_failed_output(temporary, destination)
            raise
        return WebClipExportResult(
            clip_id=clip_id,
            format=format,
            output_path=str(destination),
            cache_path=str(cache_path),
        )

    def _write_export_file(
        self,
        *,
        format: WebExportFormat,
        cache_path: Path,
        target: WebRenderTarget,
        state: TimelineState,
        clip: Clip,
        destination: Path,
        time_ms: int,
        background: str,
        progress=None,
        check_cancelled=None,
    ) -> None:
        if format == "overlay" or (format == "alpha_video" and target.animated):
            if progress:
                progress(OperationProgress.indeterminate("web_export_copying"))
            shutil.copyfile(cache_path, destination)
        elif format == "png":
            if cache_path.suffix.lower() == ".png" and time_ms == 0:
                if progress:
                    progress(OperationProgress.indeterminate("web_export_copying"))
                shutil.copyfile(cache_path, destination)
            else:
                self._run_ffmpeg(
                    [
                        "-ss",
                        f"{max(0, time_ms) / 1000:.6f}",
                        "-i",
                        str(cache_path),
                        "-frames:v",
                        "1",
                        "-y",
                        str(destination),
                    ],
                    duration_seconds=None,
                    progress=progress,
                    check_cancelled=check_cancelled,
                )
        elif format == "alpha_video":
            self._encode_static_alpha(
                cache_path,
                state,
                clip,
                destination,
                progress=progress,
                check_cancelled=check_cancelled,
            )
        elif format == "gif":
            fps = state.sequence.profile.fps
            duration = max(1 / fps, clip.duration / fps)
            input_args = self._looped_input(cache_path, fps, duration)
            self._run_ffmpeg(
                [
                    *input_args,
                    "-t",
                    f"{duration:.6f}",
                    "-filter_complex",
                    (
                        f"fps={fps:.6f},split[gif_a][gif_b];"
                        "[gif_a]palettegen=reserve_transparent=1[palette];"
                        "[gif_b][palette]paletteuse=alpha_threshold=128"
                    ),
                    "-loop",
                    "0",
                    "-y",
                    str(destination),
                ],
                duration_seconds=duration,
                progress=progress,
                check_cancelled=check_cancelled,
            )
        elif format == "video":
            if not re.fullmatch(r"#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?", background):
                raise ValueError("Video background must be a #RRGGBB or #RRGGBBAA color")
            profile = state.sequence.profile
            fps = profile.fps
            duration = max(1 / fps, clip.duration / fps)
            source_args = self._looped_input(cache_path, fps, duration)
            audio_output = (
                ["-map", "1:a:0", "-c:a", "aac", "-b:a", "192k"]
                if target.has_audio
                else ["-an"]
            )
            self._run_ffmpeg(
                [
                    "-f",
                    "lavfi",
                    "-i",
                    (
                        f"color=c={background}:s={profile.width}x{profile.height}:"
                        f"r={fps:.6f}:d={duration:.6f}"
                    ),
                    *source_args,
                    "-filter_complex",
                    (
                        f"[1:v]scale={profile.width}:{profile.height}:"
                        "force_original_aspect_ratio=decrease[web];"
                        "[0:v][web]overlay=(W-w)/2:(H-h)/2:"
                        "shortest=1,format=yuv420p[video]"
                    ),
                    "-map",
                    "[video]",
                    *audio_output,
                    "-t",
                    f"{duration:.6f}",
                    "-c:v",
                    "libx264",
                    "-movflags",
                    "+faststart",
                    "-y",
                    str(destination),
                ],
                duration_seconds=duration,
                progress=progress,
                check_cancelled=check_cancelled,
            )
        else:
            raise ValueError(f"Unknown editable media export format: {format}")

    def _encode_static_alpha(
        self,
        cache_path: Path,
        state: TimelineState,
        clip: Clip,
        destination: Path,
        *,
        progress=None,
        check_cancelled=None,
    ) -> None:
        fps = state.sequence.profile.fps
        duration = max(1 / fps, clip.duration / fps)
        self._run_ffmpeg(
            [
                "-loop",
                "1",
                "-framerate",
                f"{fps:.6f}",
                "-i",
                str(cache_path),
                "-t",
                f"{duration:.6f}",
                "-an",
                "-c:v",
                "ffv1",
                "-level",
                "3",
                "-pix_fmt",
                "bgra",
                "-y",
                str(destination),
            ],
            duration_seconds=duration,
            progress=progress,
            check_cancelled=check_cancelled,
        )

    @staticmethod
    def _looped_input(cache_path: Path, fps: float, duration: float) -> list[str]:
        if cache_path.suffix.lower() == ".png":
            return [
                "-loop",
                "1",
                "-framerate",
                f"{fps:.6f}",
                "-t",
                f"{duration:.6f}",
                "-i",
                str(cache_path),
            ]
        return ["-i", str(cache_path)]

    def _run_ffmpeg(
        self,
        arguments: list[str],
        *,
        duration_seconds: float | None,
        progress=None,
        check_cancelled=None,
    ) -> None:
        on_position: Callable[[float], None] | None = None
        if duration_seconds is not None and duration_seconds > 0 and progress is not None:

            def report_position(position: float) -> None:
                progress(
                    OperationProgress.determinate(
                        "web_export_encoding",
                        completed=position,
                        total=duration_seconds,
                        unit="media_seconds",
                    )
                )

            on_position = report_position
        elif progress is not None:
            progress(OperationProgress.indeterminate("web_export_encoding"))
        result = self.ffmpeg.run_progress(
            ["-loglevel", "error", *arguments],
            total_seconds=duration_seconds,
            on_position=on_position,
            check_cancelled=check_cancelled,
            timeout=1800,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "FFmpeg editable media export failed: "
                + result.stderr
            )

    def _render_browser(
        self,
        entry: Path,
        spec: WebAssetSpec,
        clip_state: WebClipState,
        state: TimelineState,
        target: WebRenderTarget,
        *,
        progress=None,
        check_cancelled=None,
    ) -> None:
        executable = find_chromium_executable()
        engine = get_web_capture_engine(executable)
        manifest = spec.manifest
        package_root = web_package_root(entry, manifest)
        variant = manifest.variant_for(
            clip_state.variant.id if clip_state.variant is not None else None
        )
        runtime_state = web_runtime_state(clip_state, manifest)
        partial = unique_temporary_sibling(
            target.path,
            label="web-render",
        )
        ffmpeg_pipe: FfmpegInputPipe | None = None
        try:
            with WebPackagePreviewServer(package_root) as preview:
                capture_url = preview.url_for(
                    manifest.entry,
                    query=(
                        f"capture=1&variant={variant.id}"
                        f"&scene={runtime_state['scene_id']}"
                    ),
                )
                capture_modes: tuple[WebCaptureMode, ...] = ("auto", "screenshot")
                if target.animated:
                    fps = Fraction(
                        state.sequence.profile.fps_numerator,
                        state.sequence.profile.fps_denominator,
                    )
                    command = build_web_render_ffmpeg_command(target, partial)
                    if progress:
                        progress(
                            OperationProgress.determinate(
                                "web_rendering",
                                completed=0,
                                total=target.frame_count,
                                unit="frames",
                            )
                        )
                    def report_frame(completed: int) -> None:
                        if progress:
                            progress(
                                OperationProgress.determinate(
                                    "web_rendering",
                                    completed=completed,
                                    total=target.frame_count,
                                    unit="frames",
                                )
                            )

                    fallback_reason: str | None = None
                    for capture_mode in capture_modes:
                        ffmpeg_pipe = self.ffmpeg.open_input_pipe(command)
                        try:
                            engine.render_frames(
                                url=capture_url,
                                allowed_origin=preview.url_for(""),
                                width=variant.canvas.width,
                                height=variant.canvas.height,
                                fps_numerator=fps.numerator,
                                fps_denominator=fps.denominator,
                                runtime_state=runtime_state,
                                determinism_key=target.key,
                                frame_count=target.frame_count,
                                on_frame=ffmpeg_pipe.write,
                                on_progress=report_frame,
                                check_cancelled=check_cancelled,
                                capture_mode=capture_mode,
                                fallback_reason=fallback_reason,
                            )
                        except FastCaptureFallbackRequired as error:
                            ffmpeg_pipe.abort()
                            ffmpeg_pipe = None
                            fallback_reason = str(error)
                            continue
                        pipe_result = ffmpeg_pipe.finish(timeout=1800)
                        ffmpeg_pipe = None
                        if pipe_result.returncode != 0:
                            raise RuntimeError(
                                "FFmpeg editable web media render failed: "
                                f"{pipe_result.stderr}"
                            )
                        break
                    else:
                        raise RuntimeError(
                            "Editable web media capture exhausted its screenshot fallback"
                        )
                else:
                    if progress:
                        progress(
                            OperationProgress.determinate(
                                "web_rendering",
                                completed=0,
                                total=1,
                                unit="frames",
                            )
                        )
                    frames: list[bytes] = []
                    fallback_reason = None
                    for capture_mode in capture_modes:
                        frames.clear()
                        try:
                            engine.render_frames(
                                url=capture_url,
                                allowed_origin=preview.url_for(""),
                                width=variant.canvas.width,
                                height=variant.canvas.height,
                                fps_numerator=state.sequence.profile.fps_numerator,
                                fps_denominator=state.sequence.profile.fps_denominator,
                                runtime_state=runtime_state,
                                determinism_key=target.key,
                                frame_count=1,
                                on_frame=frames.append,
                                check_cancelled=check_cancelled,
                                capture_mode=capture_mode,
                                fallback_reason=fallback_reason,
                            )
                        except FastCaptureFallbackRequired as error:
                            fallback_reason = str(error)
                            continue
                        break
                    else:
                        raise RuntimeError(
                            "Editable web media capture exhausted its screenshot fallback"
                        )
                    partial.write_bytes(frames[0])
                    if progress:
                        progress(
                            OperationProgress.determinate(
                                "web_rendering",
                                completed=1,
                                total=1,
                                unit="frames",
                            )
                        )
            probe = self._probe_rendered_cache(partial, target)
            partial.replace(target.path)
            self._publish_cache_manifest(target, probe)
        except BaseException:
            if ffmpeg_pipe is not None:
                ffmpeg_pipe.abort()
            raise
        finally:
            partial.unlink(missing_ok=True)

    def _probe_rendered_cache(
        self,
        path: Path,
        target: WebRenderTarget,
    ) -> dict[str, object]:
        result = run_cancellable(
            [
                str(self.paths.ffprobe),
                "-v",
                "error",
                "-show_entries",
                (
                    "stream=codec_type,codec_name,pix_fmt,width,height,"
                    "avg_frame_rate,sample_rate,channels:"
                    "format=duration"
                ),
                "-of",
                "json",
                str(path),
            ],
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            creationflags=(
                subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            ),
        )
        if result.returncode != 0:
            raise RuntimeError(
                "FFprobe rejected editable web media cache: "
                f"{result.stderr.strip()}"
            )
        try:
            probe_payload = json.loads(result.stdout)
            streams = probe_payload.get("streams") or []
            video_streams = [
                stream
                for stream in streams
                if stream.get("codec_type") == "video"
            ]
            audio_streams = [
                stream
                for stream in streams
                if stream.get("codec_type") == "audio"
            ]
            if len(video_streams) != 1:
                raise ValueError("expected exactly one video stream")
            stream = video_streams[0]
            codec_name = str(stream["codec_name"])
            pixel_format = str(stream["pix_fmt"])
            width = int(stream["width"])
            height = int(stream["height"])
            frame_rate = Fraction(str(stream.get("avg_frame_rate") or "0/1"))
            duration = Fraction(str((probe_payload.get("format") or {}).get("duration") or "0"))
            if len(audio_streams) > 1:
                raise ValueError("expected at most one audio stream")
            audio_stream = audio_streams[0] if audio_streams else None
            audio_codec_name = (
                str(audio_stream["codec_name"]) if audio_stream is not None else None
            )
            audio_sample_rate = (
                int(audio_stream["sample_rate"]) if audio_stream is not None else None
            )
            audio_channels = (
                int(audio_stream["channels"]) if audio_stream is not None else None
            )
        except (IndexError, KeyError, TypeError, ValueError, ZeroDivisionError) as error:
            raise RuntimeError(
                "FFprobe returned incomplete editable web media cache metadata"
            ) from error
        expected_codec = "ffv1" if target.animated else "png"
        expected_frame_count = target.frame_count if target.animated else 1
        expected_duration = Fraction(
            expected_frame_count * target.fps_denominator,
            target.fps_numerator,
        )
        if (
            codec_name != expected_codec
            or (width, height) != (target.width, target.height)
            or (target.animated and pixel_format != "bgra")
            or (
                target.animated
                and frame_rate
                != Fraction(target.fps_numerator, target.fps_denominator)
            )
            or (
                target.animated
                and abs(duration - expected_duration)
                > Fraction(target.fps_denominator, target.fps_numerator)
            )
            or (audio_stream is not None) != target.has_audio
            or (
                target.has_audio
                and (
                    audio_codec_name != "flac"
                    or audio_sample_rate != target.audio_sample_rate
                    or audio_channels != target.audio_channels
                )
            )
        ):
            raise RuntimeError(
                "Editable web media cache does not match its render target: "
                f"codec={codec_name}, pixel_format={pixel_format}, "
                f"size={width}x{height}, frames={expected_frame_count}, "
                f"rate={frame_rate}, duration={duration}, "
                f"audio={audio_codec_name}/{audio_sample_rate}/{audio_channels}"
            )
        return {
            "codec_name": codec_name,
            "pixel_format": pixel_format,
            "width": width,
            "height": height,
            "frame_count": expected_frame_count,
            "fps_numerator": frame_rate.numerator,
            "fps_denominator": frame_rate.denominator,
            "has_audio": audio_stream is not None,
            "audio_codec_name": audio_codec_name,
            "audio_sample_rate": audio_sample_rate,
            "audio_channels": audio_channels,
        }

    @staticmethod
    def _publish_cache_manifest(
        target: WebRenderTarget,
        probe: dict[str, object],
    ) -> None:
        fingerprint = fingerprint_file(target.path)
        payload = {
            "schema": WEB_CACHE_MANIFEST_SCHEMA,
            "renderer_version": WEB_RENDERER_VERSION,
            "key": target.key,
            "animated": target.animated,
            "frame_count": target.frame_count,
            "width": target.width,
            "height": target.height,
            "fps_numerator": target.fps_numerator,
            "fps_denominator": target.fps_denominator,
            "has_audio": target.has_audio,
            "audio_sample_rate": target.audio_sample_rate,
            "audio_channels": target.audio_channels,
            "fingerprint": fingerprint.model_dump(mode="json"),
            "probe": probe,
        }
        atomic_write_text(
            target.manifest_path,
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
