from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from mediaflow.application.ports import (
    SequenceBuildAudioResult,
    SequenceBuildResult,
    SequenceBuildUnitResult,
    TimelineCompilationDocuments,
)
from mediaflow.application.timeline_clock import assets_in_timeline_clock
from mediaflow.atomic_file import atomic_write_text, unique_temporary_sibling
from mediaflow.domain.enums import AssetKind, ClipMediaKind, ExportFormat, TrackKind
from mediaflow.domain.exports import ExportPreset
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.sequence_audio import select_audible_sequence_audio
from mediaflow.domain.task_commands import SequenceBuildUnit
from mediaflow.domain.timeline import TimelineState
from mediaflow.file_digest import sha256_file
from mediaflow.infrastructure.ffmpeg_runner import FfmpegRunner
from mediaflow.infrastructure.file_fingerprint import fingerprint_file
from mediaflow.infrastructure.mlt.compiler import TimelineCompiler
from mediaflow.infrastructure.mlt.export_service import MltExportService
from mediaflow.infrastructure.mlt.export_types import (
    ExportResult,
    MltExportRequest,
)
from mediaflow.infrastructure.output_reservation import output_set_transaction
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from mediaflow.infrastructure.storage_budget import (
    estimate_video_cache_bytes,
    register_project_cache_owner,
    require_project_cache_budget,
)
from mediaflow.infrastructure.web_render_service import WebRenderService
from mediaflow.infrastructure.web_render_target import WEB_RENDERER_VERSION

BUILD_PROTOCOL_VERSION = 3


@dataclass(frozen=True, slots=True)
class _PreparedUnit:
    unit: SequenceBuildUnit
    state: TimelineState
    cache_key: str
    output_path: Path
    manifest_path: Path


@dataclass(frozen=True, slots=True)
class _PreparedAssembly:
    path: Path
    concat_path: Path
    cache_key: str
    sha256: str
    status: Literal["assembled", "reused"]


class SegmentedExportService:
    """Build a video from independently cached visual units and one audio master."""

    def __init__(
        self,
        documents: TimelineCompilationDocuments,
        paths: RuntimePaths,
    ) -> None:
        self.documents = documents
        self.paths = paths
        self.compiler = TimelineCompiler(documents, paths)
        self.exporter = MltExportService(self.compiler, self.paths)
        self.web = WebRenderService(documents, self.paths)
        self.ffmpeg = FfmpegRunner(self.paths.ffmpeg)
        self.cache_root = (
            self.paths.project_cache_dir(documents.project_dir)
            / "sequence-build"
            / f"v{BUILD_PROTOCOL_VERSION}"
        )

    def build(
        self,
        state: TimelineState,
        preset: ExportPreset,
        units: list[SequenceBuildUnit],
        output_path: str | Path,
        *,
        overwrite: bool,
        progress=None,
        check_cancelled=None,
    ) -> SequenceBuildResult:
        total_frames = self._prepare_build(state, preset, units)
        check = check_cancelled or (lambda: None)
        video_preset = preset.model_copy(
            update={
                "name": f"{preset.name} / cached visual unit",
                "audio_codec": None,
                "audio_bitrate": preset.audio_bitrate,
            }
        )
        prepared = [self._prepare_visual_unit(state, video_preset, unit) for unit in units]
        unit_results = self._build_visual_units(
            prepared,
            video_preset,
            progress=progress,
            check_cancelled=check,
        )
        audio_result = self._build_audio_master(
            state,
            preset,
            progress=progress,
            check_cancelled=check,
        )
        check()
        assembly = self._prepare_assembly(
            state,
            preset,
            unit_results,
            audio_result,
            total_frames,
            check_cancelled=check,
        )
        output, final_probe = self._publish_assembly(
            state,
            preset,
            assembly,
            output_path,
            total_frames,
            overwrite=overwrite,
        )
        return self._build_result(
            output,
            final_probe,
            assembly,
            unit_results,
            audio_result,
            total_frames,
        )

    def _prepare_build(
        self,
        state: TimelineState,
        preset: ExportPreset,
        units: list[SequenceBuildUnit],
    ) -> int:
        if preset.format == ExportFormat.AUDIO:
            raise ValueError("Segmented export requires a video preset")
        self._validate_units(state, units)
        total_frames = sum(unit.end_frame - unit.start_frame for unit in units)
        project_cache = self.paths.project_cache_dir(self.documents.project_dir)
        require_project_cache_budget(
            project_cache,
            expected_new_bytes=estimate_video_cache_bytes(
                state.sequence.profile.width,
                state.sequence.profile.height,
                total_frames,
            ),
            label="MediaFlow segmented export cache",
        )
        register_project_cache_owner(
            project_cache,
            self.documents.project_dir,
        )
        return total_frames

    def _prepare_assembly(
        self,
        state: TimelineState,
        preset: ExportPreset,
        units: list[SequenceBuildUnitResult],
        audio: SequenceBuildAudioResult,
        total_frames: int,
        *,
        check_cancelled,
    ) -> _PreparedAssembly:
        assembly_key = self._hash_document(
            {
                "protocol": BUILD_PROTOCOL_VERSION,
                "preset": self._preset_identity(preset),
                "units": [
                    {
                        "id": item.unit.id,
                        "frames": (item.unit.end_frame - item.unit.start_frame),
                        "sha256": item.sha256,
                    }
                    for item in units
                ],
                "audio_sha256": audio.sha256,
            }
        )
        path = self.cache_root / "assemblies" / f"{assembly_key}.{preset.preferred_extension}"
        manifest = path.with_suffix(path.suffix + ".json")
        concat_path = path.with_suffix(".concat.txt")
        cached = self._read_cache(
            path,
            manifest,
            assembly_key,
            state,
            preset,
            total_frames,
        )
        status: Literal["assembled", "reused"] = "reused"
        if cached is None:
            status = "assembled"
            self._assemble_units(
                state,
                preset,
                units,
                audio,
                total_frames,
                path,
                concat_path,
                check_cancelled=check_cancelled,
            )
            cached = self._write_cache(
                path,
                manifest,
                assembly_key,
                total_frames,
            )
        return _PreparedAssembly(
            path=path,
            concat_path=concat_path,
            cache_key=assembly_key,
            sha256=str(cached["output_sha256"]),
            status=status,
        )

    def _assemble_units(
        self,
        state: TimelineState,
        preset: ExportPreset,
        units: list[SequenceBuildUnitResult],
        audio: SequenceBuildAudioResult,
        total_frames: int,
        path: Path,
        concat_path: Path,
        *,
        check_cancelled,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            concat_path,
            "\n".join(f"file '{self._concat_path(item.output_path)}'" for item in units) + "\n",
        )
        temporary = unique_temporary_sibling(
            path,
            label="assemble",
        )
        command = [
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_path),
        ]
        if audio.output_path is not None:
            command.extend(
                [
                    "-i",
                    str(audio.output_path),
                    "-map",
                    "0:v:0",
                    "-map",
                    "1:a:0",
                    "-c:v",
                    "copy",
                    "-c:a",
                    str(preset.audio_codec or "aac"),
                    "-b:a",
                    str(preset.audio_bitrate),
                    "-ar",
                    str(state.sequence.profile.audio_sample_rate),
                    "-ac",
                    str(state.sequence.profile.audio_channels),
                ]
            )
        else:
            command.extend(["-map", "0:v:0", "-c", "copy"])
        if preset.container in {"mp4", "mov"}:
            command.extend(["-movflags", "+faststart"])
        command.extend(["-f", preset.container, "-y", str(temporary)])
        result = self.ffmpeg.run(
            command,
            timeout=3600,
            check_cancelled=check_cancelled,
        )
        if result.returncode != 0 or not temporary.is_file() or temporary.stat().st_size == 0:
            detail = "\n".join(part for part in (result.stdout, result.stderr) if part)[-4000:]
            raise RuntimeError(f"Segment assembly failed:\n{detail}")
        probe = self.exporter.probe(temporary)
        self.exporter.validate_probe(
            state,
            preset,
            probe,
            expected_duration_frames=total_frames,
        )
        with output_set_transaction(
            (path,),
            overwrite=True,
            runtime_dir=self.paths.runtime_dir,
            failure_archive_directory_name=("Invalid Sequence Build Cache"),
        ) as transaction:
            staged = transaction.temporary_path(
                path,
                "assembly-cache",
            )
            temporary.replace(staged)
            transaction.publish()
            transaction.finalize(archive_replaced_to=(self.cache_root / "archive" / "assemblies"))

    def _publish_assembly(
        self,
        state: TimelineState,
        preset: ExportPreset,
        assembly: _PreparedAssembly,
        output_path: str | Path,
        total_frames: int,
        *,
        overwrite: bool,
    ):
        output = Path(output_path).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        if not (output.is_file() and sha256_file(output) == assembly.sha256):
            with output_set_transaction(
                (output,),
                overwrite=overwrite,
                runtime_dir=self.paths.runtime_dir,
            ) as transaction:
                staged = transaction.temporary_path(
                    output,
                    "sequence-build",
                )
                shutil.copyfile(assembly.path, staged)
                transaction.commit()
        probe = self.exporter.probe(output)
        self.exporter.validate_probe(
            state,
            preset,
            probe,
            expected_duration_frames=total_frames,
        )
        return output, probe

    @staticmethod
    def _build_result(
        output: Path,
        final_probe,
        assembly: _PreparedAssembly,
        units: list[SequenceBuildUnitResult],
        audio: SequenceBuildAudioResult,
        total_frames: int,
    ) -> SequenceBuildResult:
        actual_codecs = {item.actual_video_codec for item in units if item.actual_video_codec is not None}
        fallback_reasons = sorted(
            {item.hardware_fallback_reason for item in units if item.hardware_fallback_reason}
        )
        failure_details = [item.hardware_failure_details for item in units if item.hardware_failure_details]
        requested_codec = units[0].requested_video_codec if units else None
        actual_codec = (
            next(iter(actual_codecs))
            if len(actual_codecs) == 1
            else ("mixed" if actual_codecs else requested_codec)
        )
        return SequenceBuildResult(
            export=ExportResult(
                output_path=output,
                project_graph_path=assembly.concat_path,
                probe=final_probe,
                start_frame=0,
                end_frame=total_frames,
                requested_video_codec=requested_codec,
                actual_video_codec=actual_codec,
                hardware_fallback_reason=("; ".join(fallback_reasons) if fallback_reasons else None),
                hardware_failure_details=("\n\n".join(failure_details) if failure_details else None),
                archived_failed_outputs=tuple(
                    archived
                    for item in units
                    for archived in item.archived_failed_outputs
                    if archived.exists()
                ),
            ),
            units=tuple(units),
            audio=audio,
            assembly_status=assembly.status,
            assembly_key=assembly.cache_key,
        )

    def _build_visual_units(
        self,
        units: list[_PreparedUnit],
        preset: ExportPreset,
        *,
        progress,
        check_cancelled,
    ) -> list[SequenceBuildUnitResult]:
        results: dict[str, SequenceBuildUnitResult] = {}
        misses: list[_PreparedUnit] = []
        for item in units:
            cached = self._read_cache(
                item.output_path,
                item.manifest_path,
                item.cache_key,
                item.state,
                preset,
                item.unit.end_frame - item.unit.start_frame,
            )
            if cached is None:
                misses.append(item)
                continue
            results[item.unit.id] = SequenceBuildUnitResult(
                unit=item.unit,
                status="reused",
                cache_key=item.cache_key,
                output_path=item.output_path,
                sha256=str(cached["output_sha256"]),
                requested_video_codec=cached.get("requested_video_codec"),
                actual_video_codec=cached.get("actual_video_codec"),
                hardware_fallback_reason=cached.get("hardware_fallback_reason"),
                hardware_failure_details=cached.get("hardware_failure_details"),
                archived_failed_outputs=tuple(
                    Path(value) for value in cached.get("archived_failed_outputs", [])
                ),
            )
        requests: list[MltExportRequest] = []
        for item in misses:
            check_cancelled()
            web_ids = [
                clip.id
                for clip in item.state.clips
                if self.documents.assets.get_asset(clip.asset_id).kind == AssetKind.WEB
            ]
            self.web.ensure_clips(
                item.state,
                web_ids,
                progress=progress,
                check_cancelled=check_cancelled,
            )
            item.output_path.parent.mkdir(parents=True, exist_ok=True)
            requests.append(
                MltExportRequest(
                    state=item.state,
                    preset=preset,
                    output_path=item.output_path,
                    start_frame=item.unit.start_frame,
                    end_frame=item.unit.end_frame,
                )
            )
        if requests:
            rendered = self.exporter.export_many(
                tuple(requests),
                overwrite=True,
                archive_replaced_to=self.cache_root / "archive" / "visual",
                progress=progress,
                check_cancelled=check_cancelled,
            )
            for item, render in zip(misses, rendered, strict=True):
                cached = self._write_cache(
                    item.output_path,
                    item.manifest_path,
                    item.cache_key,
                    item.unit.end_frame - item.unit.start_frame,
                    render=render,
                )
                results[item.unit.id] = SequenceBuildUnitResult(
                    unit=item.unit,
                    status="rendered",
                    cache_key=item.cache_key,
                    output_path=item.output_path,
                    sha256=str(cached["output_sha256"]),
                    requested_video_codec=render.requested_video_codec,
                    actual_video_codec=render.actual_video_codec,
                    hardware_fallback_reason=render.hardware_fallback_reason,
                    hardware_failure_details=render.hardware_failure_details,
                    archived_failed_outputs=render.archived_failed_outputs,
                )
        ordered = [results[item.unit.id] for item in units]
        if progress is not None:
            progress(
                OperationProgress.determinate(
                    "sequence_build_units",
                    completed=len(ordered),
                    total=len(ordered),
                    unit="items",
                )
            )
        return ordered

    def _build_audio_master(
        self,
        state: TimelineState,
        final_preset: ExportPreset,
        *,
        progress,
        check_cancelled,
    ) -> SequenceBuildAudioResult:
        if final_preset.audio_codec is None or not self._has_audible_audio(state):
            return SequenceBuildAudioResult("absent", None, None, None)
        audio_state = self._audio_state(state)
        audio_preset = ExportPreset(
            name="Continuous cached audio master",
            format=ExportFormat.AUDIO,
            container="wav",
            encoder_policy=None,
            audio_codec="pcm_s24le",
            pixel_format=None,
            quality_value=0,
            preset="medium",
            gop_frames=1,
            advanced={
                "audio_sample_rate": state.sequence.profile.audio_sample_rate,
                "audio_channels": state.sequence.profile.audio_channels,
            },
        )
        key = self._audio_key(audio_state, audio_preset)
        output = self.cache_root / "audio" / f"{key}.wav"
        manifest = output.with_suffix(".wav.json")
        frames = state.duration_frames
        cached = self._read_cache(
            output,
            manifest,
            key,
            audio_state,
            audio_preset,
            frames,
        )
        if cached is not None:
            return SequenceBuildAudioResult(
                "reused",
                key,
                output,
                str(cached["output_sha256"]),
            )
        web_ids = [
            clip.id
            for clip in audio_state.clips
            if self.documents.assets.get_asset(clip.asset_id).kind == AssetKind.WEB
        ]
        self.web.ensure_clips(
            audio_state,
            web_ids,
            progress=progress,
            check_cancelled=check_cancelled,
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        self.exporter.export(
            audio_state,
            audio_preset,
            output,
            overwrite=True,
            archive_replaced_to=self.cache_root / "archive" / "audio",
            progress=progress,
            check_cancelled=check_cancelled,
        )
        cached = self._write_cache(output, manifest, key, frames)
        return SequenceBuildAudioResult(
            "rendered",
            key,
            output,
            str(cached["output_sha256"]),
        )

    def _prepare_visual_unit(
        self,
        state: TimelineState,
        preset: ExportPreset,
        unit: SequenceBuildUnit,
    ) -> _PreparedUnit:
        sliced = self._visual_state(state, preset, unit)
        key = self._visual_key(sliced, preset, unit)
        output = self.cache_root / "visual" / f"{key}.{preset.preferred_extension}"
        return _PreparedUnit(
            unit=unit,
            state=sliced,
            cache_key=key,
            output_path=output,
            manifest_path=output.with_suffix(output.suffix + ".json"),
        )

    def _visual_state(
        self,
        state: TimelineState,
        preset: ExportPreset,
        unit: SequenceBuildUnit,
    ) -> TimelineState:
        handle = max((transition.duration for transition in state.transitions), default=0)
        selection_start = max(0, unit.start_frame - handle)
        selection_end = min(state.duration_frames, unit.end_frame + handle)
        selected_clips = [
            clip
            for clip in state.clips
            if clip.timeline_start < selection_end
            and clip.timeline_end > selection_start
            and next(track for track in state.tracks if track.id == clip.track_id).kind == TrackKind.VIDEO
        ]
        if not selected_clips or max(clip.timeline_end for clip in selected_clips) < unit.end_frame:
            raise ValueError(f"Build unit {unit.id} has no complete visual coverage")
        clip_ids = {clip.id for clip in selected_clips}
        subtitle_track_id = preset.burn_subtitle_track_id
        tracks = [
            track
            for track in state.tracks
            if track.kind == TrackKind.VIDEO
            or (subtitle_track_id is not None and track.id == subtitle_track_id)
        ]
        return state.model_copy(
            update={
                "sequence": state.sequence.model_copy(update={"in_out": None}),
                "tracks": tracks,
                "clips": selected_clips,
                "compounds": [item for item in state.compounds if set(item.clip_ids).issubset(clip_ids)],
                "transitions": [
                    item
                    for item in state.transitions
                    if item.left_clip_id in clip_ids and item.right_clip_id in clip_ids
                ],
                "markers": [],
                "ranges": [],
                "web_states": {
                    clip_id: value for clip_id, value in state.web_states.items() if clip_id in clip_ids
                },
            }
        )

    def _audio_state(self, state: TimelineState) -> TimelineState:
        audio_track_ids = {track.id for track in state.tracks if track.kind == TrackKind.AUDIO}
        linked_video_track_ids = {
            track.id for track in state.tracks if track.linked_audio_track_id in audio_track_ids
        }
        tracks = [
            track
            for track in state.tracks
            if track.id in audio_track_ids or track.id in linked_video_track_ids
        ]
        clips = [
            clip
            for clip in state.clips
            if clip.media_kind in {ClipMediaKind.AUDIO_ONLY, ClipMediaKind.LINKED_AV}
            and clip.track_id in {track.id for track in tracks}
        ]
        clip_ids = {clip.id for clip in clips}
        return state.model_copy(
            update={
                "sequence": state.sequence.model_copy(update={"in_out": None}),
                "tracks": tracks,
                "clips": clips,
                "compounds": [],
                "transitions": [
                    item
                    for item in state.transitions
                    if item.left_clip_id in clip_ids and item.right_clip_id in clip_ids
                ],
                "markers": [],
                "ranges": [],
                "web_states": {
                    clip_id: value for clip_id, value in state.web_states.items() if clip_id in clip_ids
                },
            }
        )

    def _visual_key(
        self,
        state: TimelineState,
        preset: ExportPreset,
        unit: SequenceBuildUnit,
    ) -> str:
        assets = assets_in_timeline_clock(
            self.documents.projects, self.documents.sequences, self.documents.assets, state.sequence
        )
        asset_ids = sorted({clip.asset_id for clip in state.clips})
        subtitles = []
        if preset.burn_subtitle_track_id:
            segments = {
                segment.id: segment
                for document in self.documents.subtitles.list_subtitle_documents()
                for segment in self.documents.subtitles.list_subtitle_segments(document.id)
            }
            subtitles = [
                {
                    "placement": placement.model_dump(mode="json"),
                    "text": (placement.text_override or segments[placement.segment_id].text),
                }
                for placement in self.documents.subtitles.list_subtitle_placements(
                    preset.burn_subtitle_track_id
                )
                if placement.start_frame < unit.end_frame and placement.end_frame > unit.start_frame
            ]
        return self._hash_document(
            {
                "protocol": BUILD_PROTOCOL_VERSION,
                "renderer": "mediaflow-mlt-visual-unit",
                "web_renderer": WEB_RENDERER_VERSION,
                "range": unit.model_dump(mode="json"),
                "profile": state.sequence.profile.model_dump(mode="json"),
                "tracks": [
                    {
                        "id": track.id,
                        "kind": track.kind.value,
                        "position": track.position,
                        "enabled": track.enabled,
                    }
                    for track in state.tracks
                ],
                "clips": [
                    clip.model_dump(
                        mode="json",
                        exclude={"audio", "pitch_compensation"},
                    )
                    for clip in state.clips
                ],
                "transitions": [item.model_dump(mode="json") for item in state.transitions],
                "web_states": {
                    key: value.model_dump(mode="json") for key, value in sorted(state.web_states.items())
                },
                "assets": [self._asset_identity(assets[asset_id]) for asset_id in asset_ids],
                "subtitles": subtitles,
                "preset": self._preset_identity(preset),
            }
        )

    def _audio_key(self, state: TimelineState, preset: ExportPreset) -> str:
        assets = assets_in_timeline_clock(
            self.documents.projects, self.documents.sequences, self.documents.assets, state.sequence
        )
        asset_ids = sorted({clip.asset_id for clip in state.clips})
        buses = self.documents.audio.list_audio_buses(state.sequence.id)
        return self._hash_document(
            {
                "protocol": BUILD_PROTOCOL_VERSION,
                "renderer": "mediaflow-mlt-audio-master",
                "profile": state.sequence.profile.model_dump(mode="json"),
                "tracks": [
                    {
                        "id": track.id,
                        "kind": track.kind.value,
                        "enabled": track.enabled,
                        "muted": track.muted,
                        "solo": track.solo,
                        "audio_bus_id": track.audio_bus_id,
                        "linked_audio_track_id": track.linked_audio_track_id,
                        "primary_dialogue": track.primary_dialogue,
                    }
                    for track in state.tracks
                ],
                "clips": [
                    {
                        "id": clip.id,
                        "track_id": clip.track_id,
                        "asset_id": clip.asset_id,
                        "timeline_start": clip.timeline_start,
                        "source_in": clip.source_in,
                        "duration": clip.duration,
                        "media_kind": clip.media_kind.value,
                        "speed_numerator": clip.speed_numerator,
                        "speed_denominator": clip.speed_denominator,
                        "pitch_compensation": clip.pitch_compensation,
                        "audio": clip.audio.model_dump(mode="json"),
                    }
                    for clip in state.clips
                ],
                "transitions": [item.model_dump(mode="json") for item in state.transitions],
                "web_states": {
                    key: value.model_dump(mode="json") for key, value in sorted(state.web_states.items())
                },
                "assets": [self._asset_identity(assets[asset_id]) for asset_id in asset_ids],
                "buses": [bus.model_dump(mode="json") for bus in buses],
                "effects": [
                    effect.model_dump(mode="json")
                    for bus in buses
                    for effect in self.documents.audio.list_audio_effects(bus.id)
                ],
                "preset": self._preset_identity(preset),
            }
        )

    def _asset_identity(self, asset) -> dict:
        if asset.kind == AssetKind.WEB:
            return {
                "id": asset.id,
                "kind": asset.kind.value,
                "metadata": asset.metadata.model_dump(mode="json"),
                "source_hash": self.documents.web.get_web_asset_spec(asset.id).source_hash,
            }
        source = self.documents.assets.resolve_asset_path(asset)
        live = fingerprint_file(source)
        return {
            "id": asset.id,
            "kind": asset.kind.value,
            "metadata": asset.metadata.model_dump(mode="json"),
            "fingerprint": {
                "size": live.size,
                "modified_ns": live.modified_ns,
                "edge_sha256": live.edge_sha256,
            },
        }

    def _has_audible_audio(self, state: TimelineState) -> bool:
        assets = assets_in_timeline_clock(
            self.documents.projects, self.documents.sequences, self.documents.assets, state.sequence
        )
        buses = self.documents.audio.list_audio_buses(state.sequence.id)
        return bool(select_audible_sequence_audio(state, assets, buses).asset_ids)

    @staticmethod
    def _validate_units(state: TimelineState, units: list[SequenceBuildUnit]) -> None:
        if not units:
            raise ValueError("At least one build unit is required")
        if units[0].start_frame != 0:
            raise ValueError("Build units must start at frame 0")
        previous = None
        for unit in units:
            if unit.end_frame > state.duration_frames:
                raise ValueError(f"Build unit {unit.id} exceeds timeline duration {state.duration_frames}")
            if previous is not None and unit.start_frame != previous:
                raise ValueError("Build units must be ordered and contiguous")
            previous = unit.end_frame
        if previous != state.duration_frames:
            raise ValueError(f"Build units must cover the complete sequence duration {state.duration_frames}")

    def _read_cache(
        self,
        output: Path,
        manifest: Path,
        key: str,
        state: TimelineState,
        preset: ExportPreset,
        frames: int,
    ) -> dict | None:
        if not output.is_file() or not manifest.is_file():
            return None
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            if (
                payload.get("protocol") != BUILD_PROTOCOL_VERSION
                or payload.get("key") != key
                or payload.get("frames") != frames
                or payload.get("output_sha256") != sha256_file(output)
            ):
                return None
            probe = self.exporter.probe(output)
            self.exporter.validate_probe(
                state,
                preset,
                probe,
                expected_duration_frames=frames,
            )
            return payload
        except (OSError, ValueError, TypeError, json.JSONDecodeError, RuntimeError):
            return None

    def _write_cache(
        self,
        output: Path,
        manifest: Path,
        key: str,
        frames: int,
        *,
        render: ExportResult | None = None,
    ) -> dict:
        payload = {
            "protocol": BUILD_PROTOCOL_VERSION,
            "key": key,
            "frames": frames,
            "output": str(output),
            "output_sha256": sha256_file(output),
            "bytes": output.stat().st_size,
            "requested_video_codec": (render.requested_video_codec if render is not None else None),
            "actual_video_codec": (render.actual_video_codec if render is not None else None),
            "hardware_fallback_reason": (render.hardware_fallback_reason if render is not None else None),
            "hardware_failure_details": (render.hardware_failure_details if render is not None else None),
            "archived_failed_outputs": (
                [str(item) for item in render.archived_failed_outputs] if render is not None else []
            ),
        }
        atomic_write_text(
            manifest,
            json.dumps(payload, ensure_ascii=False, indent=2),
        )
        return payload

    def _preset_identity(self, preset: ExportPreset) -> dict:
        payload = preset.model_dump(mode="json")
        payload.pop("id", None)
        payload.pop("name", None)
        payload["execution"] = self.exporter.execution_identity(preset)
        return payload

    @staticmethod
    def _hash_document(value: dict) -> str:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _concat_path(path: Path) -> str:
        return str(path.resolve()).replace("\\", "/").replace("'", "'\\''")
