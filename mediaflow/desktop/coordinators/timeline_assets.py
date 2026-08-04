from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QUrl

from mediaflow.application.web_package_files import MANIFEST_FILE_NAME
from mediaflow.desktop.session_state import (
    ImportDropBatch,
    PlacedTimelineAsset,
    TimelinePlacement,
)
from mediaflow.domain.enums import AssetKind, ColorMode, TaskKind, TrackKind, WorkflowStage
from mediaflow.domain.sequence_audio import audio_clips_for_track
from mediaflow.domain.task_commands import (
    GenerateProxyCommand,
    GenerateWaveformCommand,
)
from mediaflow.domain.tasks import Task
from mediaflow.domain.timebase import reframe_interval

from .base import SessionCoordinator

logger = logging.getLogger(__name__)


class TimelineAssetOperations(SessionCoordinator):
    def continue_batch(self) -> None:
        while (
            self._session.asset_state.pending_batch_ids
            and not self._session.asset_state.pending_profile_asset_id
        ):
            asset_id = self._session.asset_state.pending_batch_ids.pop(0)
            placed = self.add_to_timeline(
                asset_id,
                self._session.asset_state.pending_batch_placement,
            )
            if placed is None:
                return
            if self._session.asset_state.pending_batch_placement.start_frame is not None:
                self._session.asset_state.pending_batch_placement = replace(
                    self._session.asset_state.pending_batch_placement,
                    track_id=placed.track_id,
                    start_frame=placed.end_frame,
                    force_new_track=False,
                )

    def queue_for_timeline(
        self,
        asset_ids: Iterable[str],
        placement: TimelinePlacement | None = None,
    ) -> None:
        self._session.asset_state.pending_batch_ids = list(dict.fromkeys(asset_ids))
        self._session.asset_state.pending_batch_placement = placement or TimelinePlacement()
        self.continue_batch()

    def add_to_timeline(
        self,
        asset_id: str,
        placement: TimelinePlacement,
    ) -> PlacedTimelineAsset | None:
        asset = self._session.binding.current.get_asset(asset_id)
        project = self._session.binding.current.get_project()
        if (
            asset.kind == AssetKind.VIDEO
            and self._session.binding.active_sequence_id == project.main_sequence_id
        ):
            state = self._session.binding.timeline.state
            assets = {item.id: item for item in self._session.binding.current.list_assets()}
            has_timeline_video = any(assets[item.asset_id].kind == AssetKind.VIDEO for item in state.clips)
            if not has_timeline_video:
                suggested = self._session.binding.current.suggested_profile(asset.id)
                if suggested and suggested != state.sequence.profile:
                    if state.clips and state.sequence.profile_confirmed:
                        fps = suggested.fps_numerator / suggested.fps_denominator
                        mode = "HDR10" if suggested.color_mode == ColorMode.HDR10_BT2020_PQ else "SDR"
                        self._session.asset_state.pending_profile_asset_id = asset.id
                        self._session.asset_state.pending_profile_placement = placement
                        self._session.asset_state.pending_profile_label = (
                            f"{suggested.width}×{suggested.height}  {fps:.3f} fps  {mode}"
                        ).replace(".000", "")
                        self._session.events.profileConfirmationChanged.emit()
                        return None
                    self._session.binding.current.adopt_main_profile_from_video(asset.id)
                    self._session.binding.timeline.reload()
                    asset = self._session.binding.current.get_asset(asset.id)
                    self._session.events.projectStateChanged.emit()
                elif not state.sequence.profile_confirmed:
                    self._session.binding.current.adopt_main_profile_from_video(asset.id)
                    self._session.binding.timeline.reload()
                    asset = self._session.binding.current.get_asset(asset.id)
                    self._session.events.projectStateChanged.emit()
        return self.place_on_timeline(asset, placement)

    def place_on_timeline(
        self,
        asset,
        placement: TimelinePlacement,
    ) -> PlacedTimelineAsset:
        if asset.kind == AssetKind.SUBTITLE:
            documents = self._session.binding.current.list_subtitle_documents(asset.id)
            if not documents:
                raise RuntimeError("字幕素材还没有对应的字幕文档，请重新导入 SRT")
            document = next(
                (item for item in documents if item.id == self._session.selection.document_id),
                documents[0],
            )
            segments = self._session.binding.current.list_subtitle_segments(document.id)
            if not segments:
                raise RuntimeError("字幕文档中没有可放置的字幕")
            project = self._session.binding.current.get_project()
            main_profile = self._session.binding.current.get_sequence(project.main_sequence_id).profile
            active_profile = self._session.binding.timeline.state.sequence.profile
            source_start, source_end = reframe_interval(
                min(item.start_frame for item in segments),
                max(item.end_frame for item in segments),
                main_profile,
                active_profile,
            )
            start = self._placement_start(placement, source_start)
            duration = max(1, source_end - source_start)
            subtitle_track = self._resolve_drop_track(
                TrackKind.SUBTITLE,
                placement,
                start,
                duration,
            )
            placements = self._session.binding.current.place_subtitle_document(
                document.id,
                subtitle_track.id,
                offset_frames=start - source_start,
                follow_clips=False,
            )
            self._session.selection.document_id = document.id
            self._session.selection.clip_ids = []
            self._session.selection.compound_id = ""
            self._session.projectors.timeline.refresh_timeline()
            self._session.projectors.subtitles.refresh_documents()
            self._session.projectors.timeline.refresh_preview_subtitles()
            self._session.events.projectStateChanged.emit()
            self._session.events.historyChanged.emit()
            self._session.events.selectionChanged.emit()
            self._session._set_status(f"已放入 {len(placements)} 条字幕")
            return PlacedTimelineAsset(
                track_id=subtitle_track.id,
                end_frame=start + duration,
            )
        target_kind = {
            AssetKind.VIDEO: TrackKind.VIDEO,
            AssetKind.IMAGE: TrackKind.VIDEO,
            AssetKind.WEB: TrackKind.VIDEO,
            AssetKind.AUDIO: TrackKind.AUDIO,
        }[asset.kind]
        project = self._session.binding.current.get_project()
        main_profile = self._session.binding.current.get_sequence(project.main_sequence_id).profile
        active_profile = self._session.binding.timeline.state.sequence.profile
        asset = asset.in_frame_clock(main_profile, active_profile)
        source_in = max(0, placement.source_in_frame)
        source_out = placement.source_out_frame
        if source_out is not None:
            source_out = min(asset.metadata.duration_frames, source_out)
            if source_out <= source_in:
                raise ValueError("源素材出点必须晚于入点")
        duration = (
            source_out - source_in
            if source_out is not None
            else max(1, (asset.metadata.duration_frames or 150) - source_in)
        )
        start = self._placement_start(placement, 0)
        track = self._resolve_drop_track(target_kind, placement, start, duration)
        if placement.start_frame is None:
            clips = self._session.binding.timeline.state.clips_for_track(track.id)
            start = max((clip.timeline_end for clip in clips), default=0)
        clip = self._session.binding.timeline.add_clip(
            track_id=track.id,
            asset_id=asset.id,
            timeline_start=start,
            source_in=source_in,
            duration=duration,
        )
        self._session.selection.clip_ids = [clip.id]
        self._session.selection.compound_id = ""
        self._session.projectors.timeline.refresh_timeline()
        self._session.events.projectStateChanged.emit()
        self._session.events.selectionChanged.emit()
        self.schedule_background(asset, dropped_frames=0)
        self._session.events.historyChanged.emit()
        self._session._set_status(f"已将 {asset.name} 放入时间轴")
        return PlacedTimelineAsset(track_id=track.id, end_frame=clip.timeline_end)

    def _placement_start(self, placement: TimelinePlacement, fallback: int) -> int:
        if self._timeline_is_empty():
            return 0
        if placement.start_frame is None:
            return fallback
        requested = max(0, placement.start_frame)
        if not placement.snap_enabled:
            return requested
        return self._session.binding.timeline.snap_frame(
            requested,
            self._session._timeline_snap_targets([], placement.playhead_frame),
            self._session._snap_tolerance_frames(placement.pixels_per_frame),
        )

    def _timeline_is_empty(self) -> bool:
        state = self._session.binding.timeline.state
        if state.clips:
            return False
        return not any(
            self._session.binding.current.list_subtitle_placements(track.id)
            for track in state.tracks
            if track.kind == TrackKind.SUBTITLE
        )

    def _resolve_drop_track(
        self,
        kind: TrackKind,
        placement: TimelinePlacement,
        start: int,
        duration: int,
    ):
        state = self._session.binding.timeline.state
        requested = next(
            (track for track in state.tracks if track.id == placement.track_id),
            None,
        )
        if (
            requested is not None
            and requested.kind == kind
            and not requested.locked
            and self._track_interval_available(requested.id, kind, start, duration)
        ):
            return requested
        if not placement.force_new_track and not placement.track_id:
            compatible = [track for track in state.tracks if track.kind == kind and not track.locked]
            if placement.start_frame is None:
                if compatible:
                    return compatible[0]
            else:
                available = next(
                    (
                        track
                        for track in compatible
                        if self._track_interval_available(track.id, kind, start, duration)
                    ),
                    None,
                )
                if available is not None:
                    return available
        if placement.force_new_track:
            insert_position = placement.track_position
        elif requested is not None:
            insert_position = requested.position + 1
        else:
            insert_position = None
        return self.add_timeline_track(kind, position=insert_position)

    def _track_interval_available(
        self,
        track_id: str,
        kind: TrackKind,
        start: int,
        duration: int,
    ) -> bool:
        end = start + duration
        if kind == TrackKind.SUBTITLE:
            occupied = self._session.binding.current.list_subtitle_placements(track_id)
            return all(end <= item.start_frame or start >= item.end_frame for item in occupied)
        clips = (
            audio_clips_for_track(self._session.binding.timeline.state, track_id)
            if kind == TrackKind.AUDIO
            else self._session.binding.timeline.state.clips_for_track(track_id)
        )
        return all(end <= clip.timeline_start or start >= clip.timeline_end for clip in clips)

    def add_timeline_track(self, kind: TrackKind, *, position: int | None = None):
        audio_bus_id = None
        if kind in {TrackKind.VIDEO, TrackKind.AUDIO}:
            buses = self._session.binding.current.list_audio_buses(self._session.binding.active_sequence_id)
            preferred_name = "音乐" if kind == TrackKind.AUDIO else "对白"
            audio_bus_id = next(
                (bus.id for bus in buses if bus.name == preferred_name),
                next((bus.id for bus in buses if bus.parent_bus_id is None), None),
            )
        return self._session.binding.timeline.add_track(kind, audio_bus_id=audio_bus_id, position=position)

    def start_media_import(self, source: Path) -> Task:
        if source.suffix.lower() in {".srt", ".vtt", ".ass", ".ssa"}:
            selected_media_id = next(
                (
                    asset_id
                    for asset_id in self._session.selection.asset_ids
                    if self._session.binding.current.get_asset(asset_id).kind
                    in {AssetKind.VIDEO, AssetKind.AUDIO}
                ),
                None,
            )
            return self._session.binding.current.import_asset(
                source,
                sequence_id=self._session.binding.active_sequence_id,
                purpose="subtitle",
                language=self._session.service_settings.asr.language,
                media_asset_id=selected_media_id,
            )
        return self._session.binding.current.import_asset(
            source, sequence_id=self._session.binding.active_sequence_id
        )

    def import_media_paths(
        self,
        path_values: Iterable[object],
        *,
        placement: TimelinePlacement | None = None,
    ) -> None:
        self._session._require_writable()
        sources = [self.local_path_value(value) for value in path_values]
        if not sources:
            return
        imported_asset_ids: list[str | None] = [None] * len(sources)
        tasks: list[tuple[int, Task]] = []
        for index, source in enumerate(sources):
            manifest_path = source / MANIFEST_FILE_NAME if source.is_dir() else source
            if manifest_path.is_file() and manifest_path.name == MANIFEST_FILE_NAME:
                imported_asset_ids[index] = self._session.binding.current.import_web_package(manifest_path).id
            elif source.is_file():
                tasks.append((index, self.start_media_import(source)))
            elif source.is_dir():
                raise ValueError(f"目录中缺少 {MANIFEST_FILE_NAME}：{source}")
            else:
                raise ValueError(f"素材不存在：{source}")
        if placement is not None:
            batch_id = uuid.uuid4().hex
            batch = ImportDropBatch(
                placement=placement,
                asset_ids=imported_asset_ids,
                pending_task_ids={task.id for _, task in tasks},
            )
            for index, task in tasks:
                self._session.asset_state.pending_import_tasks[task.id] = (batch_id, index)
            if batch.pending_task_ids:
                self._session.asset_state.pending_import_batches[batch_id] = batch
            else:
                self.queue_for_timeline(
                    [asset_id for asset_id in imported_asset_ids if asset_id],
                    placement,
                )
        imported_web_ids = [asset_id for asset_id in imported_asset_ids if asset_id]
        if imported_web_ids:
            self._session.selection.asset_ids = [imported_web_ids[-1]]
            self._session.projectors.assets.refresh_assets()
            self._session.events.projectStateChanged.emit()
            self._session.events.selectionChanged.emit()
        else:
            self._session.projectors.tasks.refresh_tasks()
        label = sources[0].name if len(sources) == 1 else f"{len(sources)} 个素材"
        state = "正在导入" if tasks else "已导入"
        self._session._set_status(f"{state} {label}")

    def finish_import_drop(self, task_id: str, asset_id: str) -> None:
        task_entry = self._session.asset_state.pending_import_tasks.pop(task_id, None)
        if task_entry is None:
            return
        batch_id, index = task_entry
        batch = self._session.asset_state.pending_import_batches.get(batch_id)
        if batch is None:
            return
        batch.pending_task_ids.discard(task_id)
        batch.asset_ids[index] = asset_id or None
        if batch.pending_task_ids:
            return
        self._session.asset_state.pending_import_batches.pop(batch_id, None)
        imported_ids = [item for item in batch.asset_ids if item]
        if imported_ids:
            self.queue_for_timeline(imported_ids, batch.placement)

    @staticmethod
    def local_path_value(value: object) -> Path:
        if isinstance(value, QUrl):
            candidate = value.toLocalFile() if value.isLocalFile() else value.toString()
        else:
            candidate = str(value)
        url = QUrl(candidate)
        path = url.toLocalFile() if url.isLocalFile() else candidate
        return Path(path).expanduser().resolve()

    def schedule_background(self, asset, *, dropped_frames: int) -> None:
        if not self._session.binding.current or self._session.binding.current.read_only:
            return
        prepare_media_managed = (
            dropped_frames <= 0
            and self._session.binding.current
            and any(
                run.stage == WorkflowStage.PREPARE_MEDIA and asset.id in run.asset_ids
                for run in self._session.binding.current.list_workflow_runs(active_only=True)
            )
        )
        active = {
            (task.kind, tuple(task.input_asset_ids))
            for task in self._session.binding.current.list_tasks()
            if task.status.value in {"pending", "running", "paused"}
        }
        proxy_key = (TaskKind.PROXY, (asset.id,))
        decision = self._session.binding.current.proxy_decision(asset, dropped_frames=dropped_frames)
        if (
            self._session.service_settings.preview.automatic_proxy
            and not prepare_media_managed
            and not asset.proxy_path
            and decision.required
            and proxy_key not in active
        ):
            self._session.tasks.start(
                GenerateProxyCommand(
                    asset_id=asset.id,
                    reasons=list(decision.reasons),
                ),
                [asset.id],
            )
        waveform_key = (TaskKind.WAVEFORM, (asset.id,))
        if (
            asset.kind != AssetKind.WEB
            and asset.metadata.has_audio
            and not asset.waveform_path
            and waveform_key not in active
        ):
            self._session.tasks.start(
                GenerateWaveformCommand(asset_id=asset.id),
                [asset.id],
            )
