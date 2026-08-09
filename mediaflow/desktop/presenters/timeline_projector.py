from __future__ import annotations

from PySide6.QtCore import QTimer, QUrl, Slot

from mediaflow.desktop.presentation_catalogs import (
    system_name,
)
from mediaflow.domain.enums import AssetKind, ClipMediaKind, ColorMode, TrackKind
from mediaflow.domain.task_commands import RenderWebClipCommand

from .base import Projector


class TimelineProjector(Projector):
    def __init__(self, session):
        super().__init__(session)
        self._preview_timer = QTimer(session)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(180)
        self._preview_timer.timeout.connect(self.compile_preview_graph)

    def stop_preview(self) -> None:
        self._preview_timer.stop()

    def refresh_sequences(self) -> None:
        if not self._session.binding.current:
            self._session.models.sequences.set_items([])
            return
        self._session.models.sequences.set_items(
            [
                {
                    "sequenceId": sequence.id,
                    "name": sequence.name,
                    "displayName": system_name(sequence.name),
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

    def refresh_timeline(self, *, defer_clip_updates: bool = False) -> None:
        self._session.projectors.audio.invalidate_audio_metrics()
        if not self._session.binding.timeline or not self._session.binding.current:
            self._session.models.tracks.set_items([])
            self._session.models.clips.set_items([])
            self._session.models.compound_clips.set_items([])
            self._session.models.transitions.set_items([])
            self._session.models.markers.set_items([])
            self._session.models.ranges.set_items([])
            self._session.events.exportCapabilityChanged.emit()
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
                "filmstripFrames": [
                    {
                        **item,
                        "url": QUrl.fromLocalFile(str(item["path"])).toString(),
                    }
                    for item in self._session.presentation.filmstrip_frames.get(clip.id, [])
                ],
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
        if defer_clip_updates:
            self._session.models.clips.set_items_deferred(clip_rows)
        else:
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
        self._session.events.exportCapabilityChanged.emit()
        self.schedule_preview_graph()

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
        self._preview_timer.start()

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
            and not self._session.binding.current.web_render_cache_ready(state, clip.id)
        ]
        if missing_web_clips:
            active_clip_ids = {
                task.command.clip_id
                for task in self._session.task_state.items.values()
                if isinstance(task.command, RenderWebClipCommand) and task.status.is_active
            }
            for clip in missing_web_clips:
                if clip.id not in active_clip_ids:
                    self._session.tasks.create(
                        RenderWebClipCommand(sequence_id=state.sequence.id, clip_id=clip.id),
                        [clip.asset_id],
                        sequence_id=state.sequence.id,
                    )
            return
        self._session.requests.preview_id += 1
        request_id = self._session.requests.preview_id
        generation = self._session.binding.generation
        project_dir = self._session.binding.current.project_dir
        use_proxies = self._session.service_settings.preview.preview_quality != "source"
        prefer_sdr_preview_proxy = (
            state.sequence.profile.color_mode == ColorMode.HDR10_BT2020_PQ
            and not self._session.presentation.hdr_preview_active
        )
        if (
            self._session.requests.preview_future is not None
            and not self._session.requests.preview_future.running()
        ):
            self._session.requests.preview_future.cancel()
        self._session.requests.preview_future = self._session.background.submit(
            "preview",
            (generation, request_id, state.sequence.id),
            lambda: self._session._api.write_preview_snapshot(
                project_dir,
                state,
                use_proxies=use_proxies,
                prefer_sdr_preview_proxy=prefer_sdr_preview_proxy,
            ),
            executor=self._session.background.preview_executor,
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
