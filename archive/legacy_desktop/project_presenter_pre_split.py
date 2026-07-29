from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QUrl, Slot

from mediaflow.desktop.presentation_catalogs import (
    audio_effect_label,
    audio_parameter_specs,
    system_name,
    task_message_label,
    task_status_label,
    task_title,
    transcription_configuration_label,
)
from mediaflow.domain.enums import (
    AssetKind,
    ClipMediaKind,
    ColorMode,
    TaskStatus,
    TrackKind,
)
from mediaflow.domain.task_commands import RenderWebClipCommand, TranscribeSequenceCommand
from mediaflow.domain.timebase import (
    seconds_to_frames,
)
from mediaflow.infrastructure.web_render_service import WebRenderCache

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .project_controller import ProjectSession


class ProjectPresentationProjector:
    """Project persisted state into desktop list models and preview artifacts."""

    def __init__(self, session: ProjectSession):
        self._session = session

    def refresh_runtime_tool_status(self, *, preserve_cuda: bool = True) -> None:
        cuda = {
            key: self._session.runtime_state.status.get(key, "")
            for key in ("cudaStatus", "cudaSummary", "gpuName", "driverVersion")
        }
        self._session.runtime_state.status = {
            **self._session._api.runtime_tool_status(),
            **(cuda if preserve_cuda else {}),
            "busy": False,
            "progressMode": "indeterminate",
            "progressValue": 0.0,
            "message": "",
            "operation": "",
        }
        self._session.events.runtimeToolsChanged.emit()

    def refresh_recent_projects(self) -> None:
        self._session.requests.recent_id += 1
        request_id = self._session.requests.recent_id
        paths = list(self._session.settings.ui.recent_project_paths)
        self._session._submit_background(
            "recent_projects",
            request_id,
            lambda: self._session._api.recent_projects(paths),
        )

    def apply_recent_projects(self, snapshot) -> None:
        self._session.presentation.home_summary = snapshot.totals
        items = []
        for item in snapshot.items:
            cover_path = item.get("coverPath", "")
            row = {key: value for key, value in item.items() if key != "coverPath"}
            row["coverUrl"] = QUrl.fromLocalFile(cover_path).toString() if cover_path else ""
            items.append(row)
        self._session.models.recent_projects.set_items(items)
        self._session.events.projectStateChanged.emit()

    def discover_video_encoders(self) -> None:
        self._session.requests.encoder_id += 1
        request_id = self._session.requests.encoder_id
        self._session._submit_background(
            "video_encoders",
            request_id,
            self._session._api.discover_video_encoder_options,
        )

    def refresh_all(self) -> None:
        self.refresh_assets()
        self.refresh_sequences()
        self.refresh_tasks()
        self.refresh_active_sequence(refresh_sequences=False)
        self._session.events.workflowChanged.emit()

    def refresh_active_sequence(self, *, refresh_sequences: bool = False) -> None:
        if refresh_sequences:
            self.refresh_sequences()
        self.refresh_timeline()
        self.refresh_documents()
        self.refresh_highlights()
        self.refresh_audio_buses()
        self.refresh_audio_metrics()
        self.refresh_preview_subtitles()
        self._session.events.projectStateChanged.emit()
        self._session.events.historyChanged.emit()

    def refresh_assets(self) -> None:
        if not self._session.binding.current:
            self._session.models.assets.set_items([])
            return
        assets = self._session.binding.current.list_assets()
        available_ids = {asset.id for asset in assets}
        self._session.asset_state.thumbnail_paths = {
            asset_id: path
            for asset_id, path in self._session.asset_state.thumbnail_paths.items()
            if asset_id in available_ids
        }
        transcript_terms: dict[str, list[str]] = {}
        for document in self._session.binding.current.list_subtitle_documents():
            text = " ".join(
                segment.text for segment in self._session.binding.current.list_subtitle_segments(document.id)
            )
            for asset_id in {document.asset_id, document.media_asset_id} - {None}:
                transcript_terms.setdefault(asset_id, []).append(text)
        rows = [
            {
                "assetId": asset.id,
                "name": asset.name,
                "kind": asset.kind.value,
                "path": asset.path,
                "status": asset.status.value,
                "managed": asset.managed,
                "durationFrames": asset.metadata.duration_frames,
                "width": asset.metadata.width or 0,
                "height": asset.metadata.height or 0,
                "previewUrl": (
                    QUrl.fromLocalFile(self._session.asset_state.thumbnail_paths[asset.id]).toString()
                    if asset.id in self._session.asset_state.thumbnail_paths
                    else ""
                ),
                "proxyReady": bool(asset.proxy_path),
                "waveformReady": bool(asset.waveform_path),
                "searchText": self._asset_search_text(
                    asset,
                    transcript_terms.get(asset.id, []),
                ),
            }
            for asset in assets
        ]
        self._session.models.assets.set_items(rows)
        for asset in assets:
            self.request_waveform_data(asset.id, asset.waveform_path)
        self._session.selection.asset_ids = [
            asset_id for asset_id in self._session.selection.asset_ids if asset_id in available_ids
        ]
        self.request_asset_thumbnails(assets)

    @staticmethod
    def _asset_search_text(asset, transcript_terms: list[str]) -> str:
        terms = [
            asset.name,
            asset.kind.value,
            asset.metadata.video_codec or "",
            asset.metadata.audio_codec or "",
            f"{asset.metadata.width or 0}x{asset.metadata.height or 0}",
        ]
        if asset.metadata.width and asset.metadata.height:
            terms.append(
                "横屏 landscape" if asset.metadata.width >= asset.metadata.height else "竖屏 portrait"
            )
        terms.extend(transcript_terms)
        return " ".join(term for term in terms if term).casefold()

    def request_asset_thumbnails(self, assets) -> None:
        if not self._session.binding.current or not any(
            asset.status.value == "online" and asset.kind.value in {"video", "image"} for asset in assets
        ):
            return
        if self._session.asset_state.thumbnail_pending_request is not None:
            self._session.asset_state.thumbnail_refresh_requested = True
            return
        self._session.asset_state.thumbnail_request_id += 1
        request_id = (
            self._session.binding.generation,
            self._session.asset_state.thumbnail_request_id,
            str(self._session.binding.current.project_dir),
        )
        project_dir = self._session.binding.current.project_dir
        self._session.asset_state.thumbnail_pending_request = request_id
        self._session._submit_background(
            "asset_thumbnails",
            request_id,
            lambda: self._session._api.asset_thumbnail_paths(project_dir),
        )

    def apply_asset_thumbnails(self, paths: dict[str, str]) -> None:
        self._session.asset_state.thumbnail_paths = dict(paths)
        if not self._session.binding.current:
            return
        assets = self._session.binding.current.list_assets()
        rows = []
        for asset in assets:
            current = self._session.models.assets.get(
                self._session.models.assets.findRow("assetId", asset.id)
            )
            if not current:
                continue
            current["previewUrl"] = (
                QUrl.fromLocalFile(self._session.asset_state.thumbnail_paths[asset.id]).toString()
                if asset.id in self._session.asset_state.thumbnail_paths
                else ""
            )
            rows.append(current)
        self._session.models.assets.set_items(rows)

    def request_waveform_data(self, asset_id: str, waveform_path: str | None) -> None:
        if not waveform_path or not self._session.binding.current:
            self._session.asset_state.waveform_cache.pop(asset_id, None)
            return
        path = Path(waveform_path)
        if not path.is_absolute():
            path = self._session.binding.current.project_dir / path
        path_value = str(path.resolve())
        cached = self._session.asset_state.waveform_cache.get(asset_id)
        if cached and cached[0] == path_value:
            return
        request_id = (self._session.binding.generation, asset_id, path_value)
        if request_id in self._session.asset_state.waveform_pending:
            return
        self._session.asset_state.waveform_pending.add(request_id)

        def load() -> tuple[int, dict]:
            modified = path.stat().st_mtime_ns
            return modified, json.loads(path.read_text(encoding="utf-8"))

        self._session._submit_background("waveform", request_id, load)

    def refresh_sequences(self) -> None:
        if not self._session.binding.current:
            self._session.models.sequences.set_items([])
            return
        self._session.models.sequences.set_items(
            [
                {
                    "sequenceId": sequence.id,
                    "name": sequence.name,
                    "kind": sequence.kind.value,
                    "profile": f"{sequence.profile.width}×{sequence.profile.height}",
                    "colorMode": sequence.profile.color_mode.value,
                }
                for sequence in self._session.binding.current.list_sequences()
            ]
        )

    @staticmethod
    def _audio_lane_projection(state) -> dict[str, int]:
        """Map still-linked video clips onto their paired audio tracks."""
        ordered_tracks = sorted(state.tracks, key=lambda track: (track.position, track.id))
        track_positions = {track.id: index for index, track in enumerate(ordered_tracks)}
        clip_positions: dict[str, int] = {}
        tracks_by_id = {track.id: track for track in ordered_tracks}
        for clip in state.clips:
            if clip.media_kind != ClipMediaKind.LINKED_AV:
                continue
            source_track = tracks_by_id[clip.track_id]
            if source_track.linked_audio_track_id in track_positions:
                clip_positions[clip.id] = track_positions[source_track.linked_audio_track_id]
        return clip_positions

    def refresh_timeline(self) -> None:
        if not self._session.binding.timeline or not self._session.binding.current:
            self._session.models.tracks.set_items([])
            self._session.models.clips.set_items([])
            self._session.models.compound_clips.set_items([])
            self._session.models.transitions.set_items([])
            self._session.models.markers.set_items([])
            self._session.models.ranges.set_items([])
            return
        state = self._session.binding.timeline.state
        assets = {asset.id: asset for asset in self._session.binding.current.list_assets()}
        tracks_by_id = {track.id: track for track in state.tracks}
        track_positions = {track.id: index for index, track in enumerate(state.tracks)}
        audio_lane_positions = self._audio_lane_projection(state)
        compound_by_clip_id = {
            clip_id: compound.id for compound in state.compounds for clip_id in compound.clip_ids
        }
        self._session.models.tracks.set_items(
            [
                {
                    "trackId": track.id,
                    "name": track.name,
                    "displayName": system_name(track.name),
                    "kind": track.kind.value,
                    "position": track.position,
                    "enabled": track.enabled,
                    "locked": track.locked,
                    "muted": track.muted,
                    "solo": track.solo,
                    "audioBusId": track.audio_bus_id or "",
                    "linkedAudioTrackId": track.linked_audio_track_id or "",
                    "primaryDialogue": track.primary_dialogue,
                }
                for track in state.tracks
            ]
        )
        clip_rows = [
            {
                "clipId": clip.id,
                "trackId": clip.track_id,
                "trackPosition": track_positions[clip.track_id],
                "assetId": clip.asset_id,
                "assetName": assets[clip.asset_id].name,
                "sourceIn": clip.source_in,
                "startFrame": clip.timeline_start,
                "durationFrames": clip.duration,
                "endFrame": clip.timeline_end,
                "speed": clip.speed_numerator / clip.speed_denominator,
                "pitchCompensation": clip.pitch_compensation,
                "mediaKind": clip.media_kind.value,
                "assetKind": assets[clip.asset_id].kind.value,
                "trackKind": tracks_by_id[clip.track_id].kind.value,
                "hasAudio": assets[clip.asset_id].metadata.has_audio,
                "audioTrackPosition": audio_lane_positions.get(clip.id, -1),
                "waveformReady": bool(assets[clip.asset_id].waveform_path),
                "x": clip.transform.x,
                "y": clip.transform.y,
                "scaleX": clip.transform.scale_x,
                "scaleY": clip.transform.scale_y,
                "rotation": clip.transform.rotation,
                "cropLeft": clip.transform.crop_left,
                "cropTop": clip.transform.crop_top,
                "cropRight": clip.transform.crop_right,
                "cropBottom": clip.transform.crop_bottom,
                "opacity": clip.transform.opacity,
                "gainDb": clip.audio.gain_db,
                "pan": clip.audio.pan,
                "fadeInFrames": clip.audio.fade_in_frames,
                "fadeOutFrames": clip.audio.fade_out_frames,
                "compoundId": compound_by_clip_id.get(clip.id, ""),
                "canDetachAudio": clip.media_kind == ClipMediaKind.LINKED_AV,
                "transformKeyframeCount": len(clip.transform_keyframes),
                "transformKeyframeSource": (
                    clip.transform_keyframes[0].source if clip.transform_keyframes else ""
                ),
            }
            for clip in state.clips
        ]
        self._session.models.clips.set_items(clip_rows)
        clips_by_id = {clip.id: clip for clip in state.clips}
        compound_rows = []
        for compound in state.compounds:
            members = [clips_by_id[clip_id] for clip_id in compound.clip_ids]
            first = members[0]
            compound_rows.append(
                {
                    "compoundId": compound.id,
                    "name": compound.name,
                    "primaryClipId": first.id,
                    "memberClipIds": list(compound.clip_ids),
                    "memberCount": len(members),
                    "trackId": first.track_id,
                    "trackPosition": track_positions[first.track_id],
                    "trackKind": tracks_by_id[first.track_id].kind.value,
                    "startFrame": first.timeline_start,
                    "endFrame": members[-1].timeline_end,
                    "durationFrames": members[-1].timeline_end - first.timeline_start,
                    "hasAudio": any(assets[clip.asset_id].metadata.has_audio for clip in members),
                }
            )
        self._session.models.compound_clips.set_items(compound_rows)
        available_clip_ids = {item["clipId"] for item in clip_rows}
        self._session.selection.clip_ids = [
            clip_id for clip_id in self._session.selection.clip_ids if clip_id in available_clip_ids
        ]
        available_compound_ids = {item["compoundId"] for item in compound_rows}
        if self._session.selection.compound_id not in available_compound_ids:
            self._session.selection.compound_id = ""
        clips = clips_by_id
        self._session.models.transitions.set_items(
            [
                {
                    "transitionId": item.id,
                    "trackId": item.track_id,
                    "trackPosition": track_positions[item.track_id],
                    "leftClipId": item.left_clip_id,
                    "rightClipId": item.right_clip_id,
                    "kind": item.kind.value,
                    "durationFrames": item.duration,
                    "boundaryFrame": clips[item.left_clip_id].timeline_end,
                    "internalToCompound": bool(
                        compound_by_clip_id.get(item.left_clip_id)
                        and compound_by_clip_id.get(item.left_clip_id)
                        == compound_by_clip_id.get(item.right_clip_id)
                    ),
                }
                for item in state.transitions
            ]
        )
        self._session.models.markers.set_items(
            [
                {
                    "markerId": item.id,
                    "frame": item.frame,
                    "name": item.name,
                    "markerColor": item.color,
                }
                for item in state.markers
            ]
        )
        self._session.models.ranges.set_items(
            [
                {
                    "rangeId": item.id,
                    "startFrame": item.start_frame,
                    "endFrame": item.end_frame,
                    "name": item.name,
                    "rangeColor": item.color,
                }
                for item in state.ranges
            ]
        )
        self.schedule_preview_graph()

    def refresh_tasks(self) -> None:
        tasks = sorted(
            self._session.task_state.items.values(),
            key=lambda task: (task.created_at, task.id),
        )
        pending_ids = [task.id for task in tasks if task.status == TaskStatus.PENDING]
        queue_positions = {task_id: position for position, task_id in enumerate(pending_ids, start=1)}
        self._session.models.tasks.set_items(
            [
                {
                    "taskId": task.id,
                    "displayName": task_title(task),
                    "configurationLabel": (
                        transcription_configuration_label(task.command)
                        if isinstance(task.command, TranscribeSequenceCommand)
                        else ""
                    ),
                    "commandType": task.command.command_type,
                    "kind": task.kind.value,
                    "status": task.status.value,
                    "statusLabel": task_status_label(task.status.value),
                    "progressMode": task.progress.mode,
                    "progressValue": task.progress.percent or 0.0,
                    "progressCompleted": task.progress.completed or 0.0,
                    "progressTotal": task.progress.total or 0.0,
                    "progressUnit": task.progress.unit or "",
                    "hasOverallProgress": task.progress.overall_percent is not None,
                    "overallProgressValue": task.progress.overall_percent or 0.0,
                    "overallProgressCompleted": task.progress.overall_completed or 0.0,
                    "overallProgressTotal": task.progress.overall_total or 0.0,
                    "overallProgressUnit": task.progress.overall_unit or "",
                    "progressItemIndex": task.progress.item_index or 0,
                    "progressItemTotal": task.progress.item_total or 0,
                    "progressItemLabel": task.progress.item_label or "",
                    "messageCode": task.progress.message_code,
                    "messageLabel": task_message_label(task.progress.message_code),
                    "queuePosition": queue_positions.get(task.id, 0),
                    "inputAssetIds": list(task.input_asset_ids),
                    "contextId": self._task_context_id(task.command),
                    "error": task.error or "",
                    "artifacts": [
                        str(item.resolve(self._session.binding.current.project_dir))
                        for item in task.artifacts
                    ],
                    "executionTrace": [
                        {
                            "step": task_message_label(item.step),
                            "duration": item.duration_ms / 1000.0,
                            "status": item.status,
                            "error": item.error or "",
                        }
                        for item in task.execution_trace
                    ],
                    "createdAt": task.created_at,
                }
                for task in reversed(tasks)
            ]
        )
        self._session.events.tasksChanged.emit()

    def refresh_documents(self) -> None:
        if not self._session.binding.current:
            self._session.models.documents.set_items([])
            self._session.models.segments.set_items([])
            return
        documents = self._session.binding.current.list_subtitle_documents()
        self._session.models.documents.set_items(
            [
                {
                    "documentId": document.id,
                    "assetId": document.asset_id,
                    "mediaAssetId": document.media_asset_id or document.asset_id,
                    "sequenceId": document.sequence_id or "",
                    "language": document.language,
                    "isSource": document.is_source,
                    "sourceDocumentId": document.source_document_id or "",
                    "segmentCount": self._session.binding.current.subtitle_segment_summary(document.id)[0],
                }
                for document in documents
            ]
        )
        if self._session.selection.document_id and all(
            document.id != self._session.selection.document_id for document in documents
        ):
            self._session.selection.document_id = ""
        self.refresh_segments()

    def refresh_segments(self) -> None:
        if not self._session.binding.current or not self._session.selection.document_id:
            self._session.models.segments.set_items([])
            self._session.selection.subtitle_segment_ids = []
            return
        segments = self._session.binding.current.list_subtitle_segments(self._session.selection.document_id)
        project = self._session.binding.current.get_project()
        profile = self._session.binding.current.get_sequence(project.main_sequence_id).profile
        tolerance = max(
            1,
            seconds_to_frames(0.05, profile.fps_numerator, profile.fps_denominator),
        )
        overlap_ids: set[str] = set()
        for previous, current in zip(segments, segments[1:], strict=False):
            if current.start_frame < previous.end_frame - tolerance:
                overlap_ids.update((previous.id, current.id))
        rows = [
            {
                "segmentId": segment.id,
                "startFrame": segment.start_frame,
                "endFrame": segment.end_frame,
                "text": segment.text,
                "speaker": segment.speaker or "",
                "confidence": segment.confidence if segment.confidence is not None else -1,
                "hasOverlap": segment.id in overlap_ids,
            }
            for segment in segments
        ]
        self._session.models.segments.set_items(rows)
        available = {row["segmentId"] for row in rows}
        self._session.selection.subtitle_segment_ids = [
            segment_id
            for segment_id in self._session.selection.subtitle_segment_ids
            if segment_id in available
        ]

    def refresh_highlights(self) -> None:
        if not self._session.binding.current:
            self._session.models.highlights.set_items([])
            return
        selected_asset_id = (
            self._session.selection.asset_ids[0] if self._session.selection.asset_ids else None
        )
        candidates = self._session.binding.current.list_highlights(selected_asset_id)
        documents = {
            document.id: document for document in self._session.binding.current.list_subtitle_documents()
        }
        self._session.models.highlights.set_items(
            [
                {
                    "highlightId": item.id,
                    "assetId": item.asset_id,
                    "documentId": item.document_id or "",
                    "sequenceId": item.sequence_id or "",
                    "sourceSequenceId": (
                        documents[item.document_id].sequence_id or "" if item.document_id in documents else ""
                    ),
                    "startFrame": item.start_frame,
                    "endFrame": item.end_frame,
                    "title": item.title,
                    "reason": item.reason,
                    "score": item.score,
                    "selected": item.selected,
                }
                for item in candidates
            ]
        )

    def refresh_audio_buses(self) -> None:
        if not self._session.binding.current or not self._session.binding.active_sequence_id:
            self._session.models.audio_buses.set_items([])
            return
        self._session.models.audio_buses.set_items(
            [
                {
                    "busId": bus.id,
                    "name": bus.name,
                    "displayName": system_name(bus.name),
                    "parentBusId": bus.parent_bus_id or "",
                    "gainDb": bus.gain_db,
                    "muted": bus.muted,
                    "solo": bus.solo,
                    "channelLayout": bus.channel_layout,
                }
                for bus in self._session.binding.current.list_audio_buses(
                    self._session.binding.active_sequence_id
                )
            ]
        )
        bus_ids = {
            bus.id
            for bus in self._session.binding.current.list_audio_buses(
                self._session.binding.active_sequence_id
            )
        }
        if self._session.selection.audio_bus_id not in bus_ids:
            self._session.selection.audio_bus_id = ""
        self.refresh_audio_effects()

    def refresh_audio_effects(self) -> None:
        if not self._session.binding.current or not self._session.selection.audio_bus_id:
            self._session.models.audio_effects.set_items([])
            self._session.selection.audio_effect_id = ""
            self.refresh_audio_effect_parameters()
            return
        effects = self._session.binding.current.list_audio_effects(self._session.selection.audio_bus_id)
        self._session.models.audio_effects.set_items(
            [
                {
                    "effectId": effect.id,
                    "busId": effect.bus_id,
                    "kind": effect.kind.value,
                    "displayName": audio_effect_label(effect.kind),
                    "position": effect.position,
                    "enabled": effect.enabled,
                    "parameters": effect.parameters,
                }
                for effect in effects
            ]
        )
        if self._session.selection.audio_effect_id not in {effect.id for effect in effects}:
            self._session.selection.audio_effect_id = ""
        self.refresh_audio_effect_parameters()

    def refresh_audio_effect_parameters(self) -> None:
        if (
            not self._session.binding.current
            or not self._session.selection.audio_bus_id
            or not self._session.selection.audio_effect_id
        ):
            self._session.models.audio_effect_parameters.set_items([])
            return
        try:
            effect = next(
                effect
                for effect in self._session.binding.current.list_audio_effects(
                    self._session.selection.audio_bus_id
                )
                if effect.id == self._session.selection.audio_effect_id
            )
        except StopIteration:
            self._session.models.audio_effect_parameters.set_items([])
            return
        self._session.models.audio_effect_parameters.set_items(
            [
                {
                    **spec,
                    "value": effect.parameters[spec["key"]],
                }
                for spec in audio_parameter_specs(effect.kind)
            ]
        )

    def refresh_audio_metrics(self) -> None:
        self._session.requests.audio_metrics_id += 1
        request_id = self._session.requests.audio_metrics_id
        if not self._session.binding.current or not self._session.binding.active_sequence_id:
            self._session.presentation.audio_metrics = {}
            self._session.events.audioMetricsChanged.emit()
            return
        generation = self._session.binding.generation
        project_dir = self._session.binding.current.project_dir
        sequence_id = self._session.binding.active_sequence_id
        self._session._submit_background(
            "audio_metrics",
            (generation, request_id, sequence_id),
            lambda: self._session._api.read_loudness_metrics(project_dir, sequence_id),
        )

    def schedule_preview_graph(self) -> None:
        if (
            not self._session.binding.current
            or not self._session.binding.timeline
            or not self._session.binding.timeline.state.clips
        ):
            if self._session.presentation.preview_graph_path:
                self._session.presentation.preview_graph_path = ""
                self._session.events.previewGraphChanged.emit()
            return
        self._session._preview_timer.start()

    @Slot()
    def compile_preview_graph(self) -> None:
        if not self._session.binding.current or not self._session.binding.timeline:
            return
        state = self._session.binding.timeline.state
        if not state.clips:
            return
        assets = {asset.id: asset for asset in self._session.binding.current.list_assets()}
        missing_web_clips = [
            clip
            for clip in state.clips
            if assets[clip.asset_id].kind == AssetKind.WEB
            and not WebRenderCache(self._session.binding.current)
            .target(state, clip, assets[clip.asset_id])
            .path.is_file()
        ]
        if missing_web_clips:
            active_clip_ids = {
                task.command.clip_id
                for task in self._session.task_state.items.values()
                if isinstance(task.command, RenderWebClipCommand) and task.status.is_active
            }
            for clip in missing_web_clips:
                if clip.id not in active_clip_ids:
                    self._session._create_task(
                        RenderWebClipCommand(sequence_id=state.sequence.id, clip_id=clip.id),
                        [clip.asset_id],
                        sequence_id=state.sequence.id,
                    )
            return
        self._session.requests.preview_id += 1
        request_id = self._session.requests.preview_id
        generation = self._session.binding.generation
        project_dir = self._session.binding.current.project_dir
        use_proxies = self._session.settings.preview.preview_quality != "source"
        prefer_sdr_preview_proxy = (
            state.sequence.profile.color_mode == ColorMode.HDR10_BT2020_PQ
            and not self._session.presentation.hdr_preview_active
        )
        if (
            self._session.requests.preview_future is not None
            and not self._session.requests.preview_future.running()
        ):
            self._session.requests.preview_future.cancel()
        self._session.requests.preview_future = self._session._submit_background(
            "preview",
            (generation, request_id, state.sequence.id),
            lambda: self._session._api.write_preview_snapshot(
                project_dir,
                state,
                use_proxies=use_proxies,
                prefer_sdr_preview_proxy=prefer_sdr_preview_proxy,
            ),
            executor=self._session._preview_executor,
        )

    def refresh_preview_subtitles(self) -> None:
        self._session.presentation.preview_subtitles = []
        self._session.presentation.preview_subtitles_by_track = {}
        if not self._session.binding.current or not self._session.binding.active_sequence_id:
            self._session.models.subtitle_placements.set_items([])
            return
        state = self._session.binding.current.load_timeline(self._session.binding.active_sequence_id)
        tracks = [track for track in state.tracks if track.kind == TrackKind.SUBTITLE and track.enabled]
        if not tracks:
            self._session.models.subtitle_placements.set_items([])
            return
        segments = {
            segment.id: segment
            for document in self._session.binding.current.list_subtitle_documents()
            for segment in self._session.binding.current.list_subtitle_segments(document.id)
        }
        audio_lane_positions = self._audio_lane_projection(state)
        default_audio_position = next(
            (index for index, track in enumerate(state.tracks) if track.kind == TrackKind.AUDIO),
            -1,
        )
        placement_rows = []
        for track in tracks:
            track_subtitles: list[tuple[int, int, str]] = []
            for placement in self._session.binding.current.list_subtitle_placements(track.id):
                segment = segments.get(placement.segment_id)
                if segment:
                    text = placement.text_override or segment.text
                    placement_rows.append(
                        {
                            "placementId": placement.id,
                            "trackId": placement.track_id,
                            "documentId": segment.document_id,
                            "segmentId": placement.segment_id,
                            "clipId": placement.clip_id or "",
                            "audioTrackPosition": audio_lane_positions.get(
                                placement.clip_id or "",
                                default_audio_position,
                            ),
                            "startFrame": placement.start_frame,
                            "endFrame": placement.end_frame,
                            "text": text,
                            "sourceText": segment.text,
                            "hasOverride": placement.text_override is not None,
                            "timingOverridden": placement.timing_overridden,
                        }
                    )
                    if track.id == tracks[0].id:
                        self._session.presentation.preview_subtitles.append(
                            (placement.start_frame, placement.end_frame, text)
                        )
                    track_subtitles.append((placement.start_frame, placement.end_frame, text))
            track_subtitles.sort(key=lambda item: (item[0], item[1]))
            self._session.presentation.preview_subtitles_by_track[track.id] = track_subtitles
        self._session.presentation.preview_subtitles.sort(key=lambda item: (item[0], item[1]))
        self._session.models.subtitle_placements.set_items(placement_rows)
        placement_ids = {item["placementId"] for item in placement_rows}
        if self._session.selection.subtitle_placement_id not in placement_ids:
            self._session.selection.subtitle_placement_id = ""

    @staticmethod
    def _task_context_id(command: object) -> str:
        for attribute in ("document_id", "sequence_id", "asset_id"):
            value = getattr(command, attribute, None)
            if value:
                return str(value)
        return ""

    def refresh_settings_models(self) -> None:
        active_id = self._session.settings.active_llm_provider_id
        self._session.models.llm_providers.set_items(
            [
                {
                    "providerId": provider.id,
                    "name": provider.name,
                    "baseUrl": provider.base_url,
                    "apiKey": provider.api_key,
                    "model": provider.model,
                    "enabled": provider.enabled,
                    "active": provider.id == active_id,
                }
                for provider in self._session.settings.llm_providers
            ]
        )
        provider_ids = {item.id for item in self._session.settings.llm_providers}
        if self._session.selection.llm_provider_id not in provider_ids:
            self._session.selection.llm_provider_id = ""
        self._session.models.glossary.set_items(
            [
                {
                    "termId": term.id,
                    "source": term.source,
                    "target": term.target,
                    "note": term.note,
                    "category": term.category,
                }
                for term in self._session.settings.translation.glossary_terms
            ]
        )
        term_ids = {item.id for item in self._session.settings.translation.glossary_terms}
        if self._session.selection.glossary_term_id not in term_ids:
            self._session.selection.glossary_term_id = ""

    def refresh_download_entries(self) -> None:
        self._session.models.download_entries.set_items(
            [
                {
                    "entryIndex": entry.index,
                    "mediaId": entry.media_id,
                    "title": entry.title,
                    "pageUrl": entry.page_url,
                    "duration": entry.duration,
                    "uploader": entry.uploader,
                    "available": entry.available,
                    "unavailableReason": entry.unavailable_reason,
                    "selected": entry.index in self._session.download_state.selected_entries,
                }
                for entry in (
                    self._session.download_state.plan.entries if self._session.download_state.plan else []
                )
            ]
        )
