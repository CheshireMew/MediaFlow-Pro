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
)
from mediaflow.domain.enums import (
    ColorMode,
    TaskStatus,
    TrackKind,
)
from mediaflow.domain.timebase import (
    seconds_to_frames,
)
from mediaflow.domain.timeline import compatible_track_kinds

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .project_controller import ProjectSession


class ProjectPresentationProjector:
    """Project persisted state into desktop list models and preview artifacts."""

    def __init__(self, session: ProjectSession):
        object.__setattr__(self, "_session", session)

    def __getattr__(self, name: str):
        return getattr(self._session, name)

    def __setattr__(self, name: str, value) -> None:
        if name == "_session" or not name.startswith("_"):
            object.__setattr__(self, name, value)
        else:
            setattr(self._session, name, value)

    def refresh_runtime_tool_status(self, *, preserve_cuda: bool = True) -> None:
        cuda = {
            key: self._runtime_tool_status.get(key, "")
            for key in ("cudaStatus", "cudaSummary", "gpuName", "driverVersion")
        }
        self._runtime_tool_status = {
            **self._api.runtime_tool_status(),
            **(cuda if preserve_cuda else {}),
            "busy": False,
            "progress": 0.0,
            "message": "",
            "operation": "",
        }
        self.runtimeToolsChanged.emit()

    def refresh_recent_projects(self) -> None:
        self._recent_request_id += 1
        request_id = self._recent_request_id
        paths = list(self.settings.ui.recent_project_paths)
        self._submit_background(
            "recent_projects",
            request_id,
            lambda: self._api.recent_projects(paths),
        )

    def apply_recent_projects(self, snapshot) -> None:
        self._home_summary = snapshot.totals
        items = []
        for item in snapshot.items:
            cover_path = item.get("coverPath", "")
            row = {key: value for key, value in item.items() if key != "coverPath"}
            row["coverUrl"] = QUrl.fromLocalFile(cover_path).toString() if cover_path else ""
            items.append(row)
        self._recent_project_model.set_items(items)
        self.projectStateChanged.emit()

    def discover_video_encoders(self) -> None:
        self._encoder_request_id += 1
        request_id = self._encoder_request_id
        self._submit_background(
            "video_encoders",
            request_id,
            self._api.discover_video_encoder_options,
        )

    def refresh_all(self) -> None:
        self.refresh_assets()
        self.refresh_sequences()
        self.refresh_timeline()
        self.refresh_tasks()
        self.refresh_documents()
        self.refresh_highlights()
        self.refresh_audio_buses()
        self.refresh_audio_metrics()
        self.refresh_preview_subtitles()
        self.projectStateChanged.emit()
        self.historyChanged.emit()
        self.workflowChanged.emit()

    def refresh_assets(self) -> None:
        if not self._documents:
            self._asset_model.set_items([])
            return
        assets = self._documents.list_assets()
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
                "proxyReady": bool(asset.proxy_path),
                "waveformReady": bool(asset.waveform_path),
            }
            for asset in assets
        ]
        self._asset_model.set_items(rows)
        for asset in assets:
            self.request_waveform_data(asset.id, asset.waveform_path)
        available_ids = {item["assetId"] for item in rows}
        self._selected_asset_ids = [
            asset_id for asset_id in self._selected_asset_ids if asset_id in available_ids
        ]

    def request_waveform_data(self, asset_id: str, waveform_path: str | None) -> None:
        if not waveform_path or not self._documents:
            self._waveform_cache.pop(asset_id, None)
            return
        path = Path(waveform_path)
        if not path.is_absolute():
            path = self._documents.project_dir / path
        path_value = str(path.resolve())
        cached = self._waveform_cache.get(asset_id)
        if cached and cached[0] == path_value:
            return
        request_id = (self._session_generation, asset_id, path_value)
        if request_id in self._waveform_pending:
            return
        self._waveform_pending.add(request_id)

        def load() -> tuple[int, dict]:
            modified = path.stat().st_mtime_ns
            return modified, json.loads(path.read_text(encoding="utf-8"))

        self._submit_background("waveform", request_id, load)

    def refresh_sequences(self) -> None:
        if not self._documents:
            self._sequence_model.set_items([])
            return
        self._sequence_model.set_items(
            [
                {
                    "sequenceId": sequence.id,
                    "name": sequence.name,
                    "kind": sequence.kind.value,
                    "profile": f"{sequence.profile.width}×{sequence.profile.height}",
                    "colorMode": sequence.profile.color_mode.value,
                }
                for sequence in self._documents.list_sequences()
            ]
        )

    @staticmethod
    def _audio_lane_projection(state, assets: dict) -> tuple[dict[str, int], int]:
        """Map canonical media clips onto the audio rows used by the timeline UI."""
        ordered_tracks = sorted(state.tracks, key=lambda track: (track.position, track.id))
        track_positions = {track.id: index for index, track in enumerate(ordered_tracks)}
        audio_tracks = [track for track in ordered_tracks if track.kind == TrackKind.AUDIO]
        if not audio_tracks:
            return {}, -1
        default_audio_position = track_positions[audio_tracks[0].id]
        audio_position_by_bus: dict[str, int] = {}
        for track in audio_tracks:
            if track.audio_bus_id:
                audio_position_by_bus.setdefault(track.audio_bus_id, track_positions[track.id])
        clip_positions: dict[str, int] = {}
        tracks_by_id = {track.id: track for track in ordered_tracks}
        for clip in state.clips:
            asset = assets.get(clip.asset_id)
            if asset is None or not asset.metadata.has_audio:
                continue
            source_track = tracks_by_id[clip.track_id]
            if source_track.kind == TrackKind.AUDIO:
                clip_positions[clip.id] = track_positions[source_track.id]
            elif source_track.kind == TrackKind.VIDEO:
                clip_positions[clip.id] = audio_position_by_bus.get(
                    source_track.audio_bus_id,
                    default_audio_position,
                )
        return clip_positions, default_audio_position

    def refresh_timeline(self) -> None:
        if not self._editor or not self._documents:
            self._track_model.set_items([])
            self._clip_model.set_items([])
            self._transition_model.set_items([])
            self._marker_model.set_items([])
            self._range_model.set_items([])
            return
        state = self._editor.state
        assets = {asset.id: asset for asset in self._documents.list_assets()}
        tracks_by_id = {track.id: track for track in state.tracks}
        track_positions = {track.id: index for index, track in enumerate(state.tracks)}
        audio_lane_positions, _default_audio_position = self._audio_lane_projection(state, assets)
        self._track_model.set_items(
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
                "assetKind": assets[clip.asset_id].kind.value,
                "trackKind": tracks_by_id[clip.track_id].kind.value,
                "allowedTrackKinds": [
                    kind.value for kind in compatible_track_kinds(assets[clip.asset_id].kind)
                ],
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
            }
            for clip in state.clips
        ]
        self._clip_model.set_items(clip_rows)
        available_clip_ids = {item["clipId"] for item in clip_rows}
        self._selected_clip_ids = [
            clip_id for clip_id in self._selected_clip_ids if clip_id in available_clip_ids
        ]
        clips = {clip.id: clip for clip in state.clips}
        self._transition_model.set_items(
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
                }
                for item in state.transitions
            ]
        )
        self._marker_model.set_items(
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
        self._range_model.set_items(
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
            self._task_view.values(),
            key=lambda task: (task.created_at, task.id),
        )
        pending_ids = [task.id for task in tasks if task.status == TaskStatus.PENDING]
        queue_positions = {task_id: position for position, task_id in enumerate(pending_ids, start=1)}
        self._task_model.set_items(
            [
                {
                    "taskId": task.id,
                    "displayName": task_title(task),
                    "kind": task.kind.value,
                    "status": task.status.value,
                    "statusLabel": task_status_label(task.status.value),
                    "progress": task.progress,
                    "messageCode": task.message_code,
                    "messageLabel": task_message_label(task.message_code),
                    "queuePosition": queue_positions.get(task.id, 0),
                    "error": task.error or "",
                    "artifacts": task.artifacts,
                    "executionTrace": [
                        {
                            "step": task_message_label(item.step),
                            "duration": item.duration_ms / 1000.0,
                            "status": item.status,
                            "error": item.error or "",
                        }
                        for item in task.execution_trace
                    ],
                }
                for task in reversed(tasks)
            ]
        )
        self.tasksChanged.emit()

    def refresh_documents(self) -> None:
        if not self._documents:
            self._document_model.set_items([])
            self._segment_model.set_items([])
            return
        documents = self._documents.list_subtitle_documents(self.selectedAssetId or None)
        self._document_model.set_items(
            [
                {
                    "documentId": document.id,
                    "assetId": document.asset_id,
                    "mediaAssetId": document.media_asset_id or document.asset_id,
                    "language": document.language,
                    "isSource": document.is_source,
                    "sourceDocumentId": document.source_document_id or "",
                    "segmentCount": len(self._documents.list_subtitle_segments(document.id)),
                }
                for document in documents
            ]
        )
        if self._selected_document_id and all(
            document.id != self._selected_document_id for document in documents
        ):
            self._selected_document_id = ""
        self.refresh_segments()

    def refresh_segments(self) -> None:
        if not self._documents or not self._selected_document_id:
            self._segment_model.set_items([])
            self._selected_subtitle_segment_ids = []
            return
        segments = self._documents.list_subtitle_segments(self._selected_document_id)
        project = self._documents.get_project()
        profile = self._documents.get_sequence(project.main_sequence_id).profile
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
        self._segment_model.set_items(rows)
        available = {row["segmentId"] for row in rows}
        self._selected_subtitle_segment_ids = [
            segment_id for segment_id in self._selected_subtitle_segment_ids if segment_id in available
        ]

    def refresh_highlights(self) -> None:
        if not self._documents:
            self._highlight_model.set_items([])
            return
        candidates = self._documents.list_highlights(self.selectedAssetId or None)
        self._highlight_model.set_items(
            [
                {
                    "highlightId": item.id,
                    "assetId": item.asset_id,
                    "documentId": item.document_id or "",
                    "sequenceId": item.sequence_id or "",
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
        if not self._documents or not self._active_sequence_id:
            self._audio_bus_model.set_items([])
            return
        self._audio_bus_model.set_items(
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
                for bus in self._documents.list_audio_buses(self._active_sequence_id)
            ]
        )
        bus_ids = {bus.id for bus in self._documents.list_audio_buses(self._active_sequence_id)}
        if self._selected_audio_bus_id not in bus_ids:
            self._selected_audio_bus_id = ""
        self.refresh_audio_effects()

    def refresh_audio_effects(self) -> None:
        if not self._documents or not self._selected_audio_bus_id:
            self._audio_effect_model.set_items([])
            self._selected_audio_effect_id = ""
            self.refresh_audio_effect_parameters()
            return
        effects = self._documents.list_audio_effects(self._selected_audio_bus_id)
        self._audio_effect_model.set_items(
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
        if self._selected_audio_effect_id not in {effect.id for effect in effects}:
            self._selected_audio_effect_id = ""
        self.refresh_audio_effect_parameters()

    def refresh_audio_effect_parameters(self) -> None:
        if not self._documents or not self._selected_audio_bus_id or not self._selected_audio_effect_id:
            self._audio_effect_parameter_model.set_items([])
            return
        try:
            effect = next(
                effect
                for effect in self._documents.list_audio_effects(self._selected_audio_bus_id)
                if effect.id == self._selected_audio_effect_id
            )
        except StopIteration:
            self._audio_effect_parameter_model.set_items([])
            return
        self._audio_effect_parameter_model.set_items(
            [
                {
                    **spec,
                    "value": effect.parameters[spec["key"]],
                }
                for spec in audio_parameter_specs(effect.kind)
            ]
        )

    def refresh_audio_metrics(self) -> None:
        self._audio_metrics_request_id += 1
        request_id = self._audio_metrics_request_id
        if not self._documents or not self._active_sequence_id:
            self._audio_metrics = {}
            self.audioMetricsChanged.emit()
            return
        generation = self._session_generation
        project_dir = self._documents.project_dir
        sequence_id = self._active_sequence_id
        self._submit_background(
            "audio_metrics",
            (generation, request_id, sequence_id),
            lambda: self._api.read_loudness_metrics(project_dir, sequence_id),
        )

    def schedule_preview_graph(self) -> None:
        if not self._documents or not self._editor or not self._editor.state.clips:
            if self._preview_graph_path:
                self._preview_graph_path = ""
                self.previewGraphChanged.emit()
            return
        self._preview_timer.start()

    @Slot()
    def compile_preview_graph(self) -> None:
        if not self._project or not self._editor:
            return
        state = self._editor.state
        if not state.clips:
            return
        self._preview_request_id += 1
        request_id = self._preview_request_id
        generation = self._session_generation
        project_dir = self._project.project_dir
        use_proxies = self.settings.preview.preview_quality != "source"
        prefer_sdr_preview_proxy = (
            state.sequence.profile.color_mode == ColorMode.HDR10_BT2020_PQ and not self._hdr_preview_active
        )
        self._submit_background(
            "preview",
            (generation, request_id, state.sequence.id),
            lambda: self._api.write_preview_snapshot(
                project_dir,
                state,
                use_proxies=use_proxies,
                prefer_sdr_preview_proxy=prefer_sdr_preview_proxy,
            ),
            executor=self._preview_executor,
        )

    def refresh_preview_subtitles(self) -> None:
        self._preview_subtitles = []
        self._subtitle_placement_model.set_items([])
        if not self._documents or not self._active_sequence_id:
            return
        state = self._documents.load_timeline(self._active_sequence_id)
        tracks = [
            track
            for track in state.tracks
            if track.kind == TrackKind.SUBTITLE and track.enabled
        ]
        if not tracks:
            return
        segments = {
            segment.id: segment
            for document in self._documents.list_subtitle_documents()
            for segment in self._documents.list_subtitle_segments(document.id)
        }
        assets = {asset.id: asset for asset in self._documents.list_assets()}
        audio_lane_positions, default_audio_position = self._audio_lane_projection(state, assets)
        placement_rows = []
        for track in tracks:
            for placement in self._documents.list_subtitle_placements(track.id):
                segment = segments.get(placement.segment_id)
                if segment:
                    text = placement.text_override or segment.text
                    placement_rows.append(
                        {
                            "placementId": placement.id,
                            "trackId": placement.track_id,
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
                        }
                    )
                    if track.id == tracks[0].id:
                        self._preview_subtitles.append((placement.start_frame, placement.end_frame, text))
        self._preview_subtitles.sort(key=lambda item: (item[0], item[1]))
        self._subtitle_placement_model.set_items(placement_rows)
        placement_ids = {item["placementId"] for item in placement_rows}
        if self._selected_subtitle_placement_id not in placement_ids:
            self._selected_subtitle_placement_id = ""

    def refresh_settings_models(self) -> None:
        active_id = self.settings.active_llm_provider_id
        self._llm_provider_model.set_items(
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
                for provider in self.settings.llm_providers
            ]
        )
        provider_ids = {item.id for item in self.settings.llm_providers}
        if self._selected_llm_provider_id not in provider_ids:
            self._selected_llm_provider_id = ""
        self._glossary_model.set_items(
            [
                {
                    "termId": term.id,
                    "source": term.source,
                    "target": term.target,
                    "note": term.note,
                    "category": term.category,
                }
                for term in self.settings.translation.glossary_terms
            ]
        )
        term_ids = {item.id for item in self.settings.translation.glossary_terms}
        if self._selected_glossary_term_id not in term_ids:
            self._selected_glossary_term_id = ""

    def refresh_download_entries(self) -> None:
        self._download_entry_model.set_items(
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
                    "selected": entry.index in self._download_entry_selection,
                }
                for entry in (self._download_plan.entries if self._download_plan else [])
            ]
        )
