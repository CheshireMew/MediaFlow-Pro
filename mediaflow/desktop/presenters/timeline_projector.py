from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QTimer, QUrl, Slot

from mediaflow.application.timeline_rules import TimelineRules
from mediaflow.desktop.presentation_messages import (
    system_name,
)
from mediaflow.domain.enums import AssetKind, ClipMediaKind, ColorMode, TrackKind
from mediaflow.domain.project import Asset
from mediaflow.domain.task_commands import RenderWebClipCommand
from mediaflow.domain.timeline import Clip

from .base import Projector

PREVIEW_GRAPH_BASE_IDLE_MS = 180
PREVIEW_GRAPH_MAX_IDLE_MS = 1_200
PREVIEW_GRAPH_CLIPS_PER_ADDED_MS = 5


def preview_graph_idle_delay_ms(clip_count: int) -> int:
    """Debounce heavier native graphs until foreground timeline edits are idle."""

    return min(
        PREVIEW_GRAPH_MAX_IDLE_MS,
        PREVIEW_GRAPH_BASE_IDLE_MS
        + max(0, int(clip_count)) // PREVIEW_GRAPH_CLIPS_PER_ADDED_MS,
    )


class TimelineProjector(Projector):
    def __init__(self, session):
        super().__init__(session)
        self._clips_by_id: dict[str, Clip] = {}
        self._compounds_by_id = {}
        self._compound_by_clip_id: dict[str, str] = {}
        self._transitions_by_id = {}
        self._transition_ids_by_clip: dict[str, set[str]] = {}
        self._preview_timer = QTimer(session)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(180)
        self._preview_timer.timeout.connect(self.compile_preview_graph)

    def stop_preview(self) -> None:
        self._preview_timer.stop()

    def _clear_relation_index(self) -> None:
        self._clips_by_id = {}
        self._compounds_by_id = {}
        self._compound_by_clip_id = {}
        self._transitions_by_id = {}
        self._transition_ids_by_clip = {}

    def _index_relations(self, state) -> None:
        self._clips_by_id = {clip.id: clip for clip in state.clips}
        self._compounds_by_id = {compound.id: compound for compound in state.compounds}
        self._compound_by_clip_id = {
            clip_id: compound.id
            for compound in state.compounds
            for clip_id in compound.clip_ids
        }
        self._transitions_by_id = {item.id: item for item in state.transitions}
        transition_ids: dict[str, set[str]] = {}
        for item in state.transitions:
            transition_ids.setdefault(item.left_clip_id, set()).add(item.id)
            transition_ids.setdefault(item.right_clip_id, set()).add(item.id)
        self._transition_ids_by_clip = transition_ids

    def refresh_sequences(self) -> None:
        if not self._session.state.binding.current:
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
                for sequence in self._session.state.binding.require_current().list_sequences()
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

    def refresh_timeline(
        self,
        *,
        defer_clip_updates: bool = False,
        schedule_preview: bool = True,
    ) -> None:
        self._session.projectors.audio.invalidate_audio_metrics()
        if not self._session.state.binding.timeline or not self._session.state.binding.current:
            self._clear_relation_index()
            self._session.models.tracks.set_items([])
            self._session.models.clips.set_items([])
            self._session.models.visible_clips.set_source_items([])
            self._session.models.compound_clips.set_items([])
            self._session.models.transitions.set_items([])
            self._session.models.markers.set_items([])
            self._session.models.ranges.set_items([])
            self._session.updates.commit(export_capability=True)
            return
        state = self._session.state.binding.require_timeline().state
        assets = {asset.id: asset for asset in self._session.state.binding.require_current().list_assets()}
        tracks_by_id = {track.id: track for track in state.tracks}
        track_positions = {track.id: index for index, track in enumerate(state.tracks)}
        audio_lane_positions = self._audio_lane_projection(state)
        self._index_relations(state)
        compound_by_clip_id = self._compound_by_clip_id
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
                    for item in self._session.state.presentation.filmstrip_frames.get(clip.id, [])
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
        self._session.models.visible_clips.set_source_items(clip_rows)
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
        self._session.state.selection.clip_ids = [
            clip_id for clip_id in self._session.state.selection.clip_ids if clip_id in available_clip_ids
        ]
        available_compound_ids = {item["compoundId"] for item in compound_rows}
        if self._session.state.selection.compound_id not in available_compound_ids:
            self._session.state.selection.compound_id = ""
        self._session.models.visible_clips.set_selected_ids(
            self._session.state.selection.clip_ids
        )
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
        self._session.updates.commit(export_capability=True)
        if schedule_preview:
            self.schedule_preview_graph()

    def refresh_clip_rows(
        self,
        clip_ids: list[str],
        *,
        clips: Sequence[Clip] | None = None,
        defer_updates: bool = True,
        refresh_relations: bool = True,
        schedule_preview: bool = True,
    ) -> None:
        """Refresh edited clip rows without rebuilding every projected clip."""

        if not self._session.state.binding.timeline or not self._session.state.binding.current:
            self.refresh_timeline(
                defer_clip_updates=defer_updates,
                schedule_preview=schedule_preview,
            )
            return
        wanted = set(clip_ids)
        if not wanted:
            return
        state = self._session.state.binding.require_timeline().state
        changed_clips = (
            {clip.id: clip for clip in clips}
            if clips is not None
            else {
                clip_id: self._clips_by_id[clip_id]
                for clip_id in wanted
                if clip_id in self._clips_by_id
            }
        )
        if set(changed_clips) != wanted:
            self.refresh_timeline(
                defer_clip_updates=defer_updates,
                schedule_preview=schedule_preview,
            )
            return
        tracks = sorted(state.tracks, key=lambda item: (item.position, item.id))
        tracks_by_id = {track.id: track for track in tracks}
        track_positions = {track.id: index for index, track in enumerate(tracks)}
        current_compounds = {
            compound.id: tuple(compound.clip_ids) for compound in state.compounds
        }
        projected_compounds = {
            compound.id: tuple(compound.clip_ids)
            for compound in self._compounds_by_id.values()
        }
        if current_compounds != projected_compounds:
            self.refresh_timeline(
                defer_clip_updates=defer_updates,
                schedule_preview=schedule_preview,
            )
            return
        if len(tracks) != self._session.models.tracks.rowCount() or any(
            self._session.models.tracks.findRow("trackId", track.id) < 0
            for track in tracks
        ):
            self.refresh_timeline(
                defer_clip_updates=defer_updates,
                schedule_preview=schedule_preview,
            )
            return
        asset_rows: dict[str, Asset] | None = None
        compound_by_clip_id = self._compound_by_clip_id
        changed_rows = []
        for clip_id in clip_ids:
            clip = changed_clips[clip_id]
            row_index = self._session.models.clips.findRow("clipId", clip_id)
            before = self._session.models.clips.get(row_index)
            if not before:
                self.refresh_timeline(
                    defer_clip_updates=defer_updates,
                    schedule_preview=schedule_preview,
                )
                return
            if before["assetId"] != clip.asset_id:
                if asset_rows is None:
                    asset_rows = {
                        asset.id: asset
                        for asset in self._session.state.binding.require_current().list_assets()
                    }
                asset = asset_rows[clip.asset_id]
                asset_name = asset.name
                asset_kind = asset.kind.value
                has_audio = asset.metadata.has_audio
                waveform_ready = bool(asset.waveform_path)
            else:
                asset_name = before["assetName"]
                asset_kind = before["assetKind"]
                has_audio = before["hasAudio"]
                waveform_ready = before["waveformReady"]
            source_track = tracks_by_id[clip.track_id]
            audio_track_position = -1
            if (
                clip.media_kind == ClipMediaKind.LINKED_AV
                and source_track.linked_audio_track_id in track_positions
            ):
                audio_track_position = track_positions[source_track.linked_audio_track_id]
            changed_rows.append(
                {
                    **before,
                    "trackId": clip.track_id,
                    "trackPosition": track_positions[clip.track_id],
                    "assetId": clip.asset_id,
                    "assetName": asset_name,
                    "sourceIn": clip.source_in,
                    "startFrame": clip.timeline_start,
                    "durationFrames": clip.duration,
                    "endFrame": clip.timeline_end,
                    "speed": clip.speed_numerator / clip.speed_denominator,
                    "pitchCompensation": clip.pitch_compensation,
                    "mediaKind": clip.media_kind.value,
                    "assetKind": asset_kind,
                    "trackKind": source_track.kind.value,
                    "hasAudio": has_audio,
                    "audioTrackPosition": audio_track_position,
                    "waveformReady": waveform_ready,
                    "filmstripFrames": [
                        {
                            **item,
                            "url": QUrl.fromLocalFile(str(item["path"])).toString(),
                        }
                        for item in self._session.state.presentation.filmstrip_frames.get(
                            clip.id, []
                        )
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
            )
        if not self._session.models.clips.update_items_by_key(
            changed_rows,
            deferred=defer_updates,
        ):
            self.refresh_timeline(
                defer_clip_updates=defer_updates,
                schedule_preview=schedule_preview,
            )
            return
        if not self._session.models.visible_clips.update_source_items(changed_rows):
            self._session.models.visible_clips.set_source_items(
                self._session.models.clips.snapshot()
            )
        self._clips_by_id.update(changed_clips)
        self._session._timeline_snapping.update_clips(changed_clips.values())
        if refresh_relations:
            self._refresh_clip_relations(wanted, track_positions, tracks_by_id)
        self._session.projectors.audio.invalidate_audio_metrics()
        self._session.updates.commit(export_capability=True)
        if schedule_preview:
            self.schedule_preview_graph()

    def _refresh_clip_relations(self, changed_ids, track_positions, tracks_by_id) -> None:
        compound_ids = {
            compound_id
            for clip_id in changed_ids
            if (compound_id := self._compound_by_clip_id.get(clip_id)) is not None
        }
        compound_rows = []
        for compound_id in compound_ids:
            compound = self._compounds_by_id[compound_id]
            members = [self._clips_by_id[clip_id] for clip_id in compound.clip_ids]
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
                    "hasAudio": any(
                        bool(
                            self._session.models.clips.get(
                                self._session.models.clips.findRow("clipId", item.id)
                            )["hasAudio"]
                        )
                        for item in members
                    ),
                }
            )
        if compound_rows and not self._session.models.compound_clips.update_items_by_key(
            compound_rows
        ):
            self.refresh_timeline()
            return
        transition_ids = {
            transition_id
            for clip_id in changed_ids
            for transition_id in self._transition_ids_by_clip.get(clip_id, set())
        }
        invalid_transition_ids = {
            transition_id
            for transition_id in transition_ids
            if (
                (item := self._transitions_by_id.get(transition_id)) is None
                or not TimelineRules.transition_is_valid(item, self._clips_by_id)
            )
        }
        if invalid_transition_ids:
            self._session.models.transitions.set_items(
                [
                    row
                    for row in self._session.models.transitions.snapshot()
                    if row["transitionId"] not in invalid_transition_ids
                ]
            )
            for transition_id in invalid_transition_ids:
                item = self._transitions_by_id.pop(transition_id, None)
                if item is None:
                    continue
                for clip_id in (item.left_clip_id, item.right_clip_id):
                    self._transition_ids_by_clip.get(clip_id, set()).discard(
                        transition_id
                    )
        transition_ids -= invalid_transition_ids
        transition_rows = [
            {
                "transitionId": item.id,
                "trackId": item.track_id,
                "trackPosition": track_positions[item.track_id],
                "leftClipId": item.left_clip_id,
                "rightClipId": item.right_clip_id,
                "kind": item.kind.value,
                "durationFrames": item.duration,
                "boundaryFrame": self._clips_by_id[item.left_clip_id].timeline_end,
                "internalToCompound": bool(
                    self._compound_by_clip_id.get(item.left_clip_id)
                    and self._compound_by_clip_id.get(item.left_clip_id)
                    == self._compound_by_clip_id.get(item.right_clip_id)
                ),
            }
            for transition_id in transition_ids
            if (item := self._transitions_by_id.get(transition_id)) is not None
        ]
        if transition_rows and not self._session.models.transitions.update_items_by_key(
            transition_rows
        ):
            self.refresh_timeline()

    def schedule_preview_graph(self) -> None:
        # Invalidate an already compiled result as soon as the timeline changes,
        # rather than waiting for the debounce timer to submit its replacement.
        self._session.state.requests.preview_id += 1
        if (
            not self._session.state.binding.current
            or not self._session.state.binding.timeline
            or not self._session.state.binding.require_timeline().state.clips
        ):
            if self._session.state.presentation.preview_graph_path:
                self._session.state.presentation.preview_graph_path = ""
                self._session.updates.commit(preview_graph=True)
            return
        state = self._session.state.binding.require_timeline().state
        self._preview_timer.setInterval(preview_graph_idle_delay_ms(len(state.clips)))
        self._preview_timer.start()

    @Slot()
    def compile_preview_graph(self) -> None:
        if not self._session.state.binding.current or not self._session.state.binding.timeline:
            return
        state = self._session.state.binding.require_timeline().state
        if not state.clips:
            return
        assets = {asset.id: asset for asset in self._session.state.binding.require_current().list_assets()}
        missing_web_clips = [
            clip
            for clip in state.clips
            if assets[clip.asset_id].kind == AssetKind.WEB
            and not self._session.state.binding.require_current().web_render_cache_ready(state, clip.id)
        ]
        if missing_web_clips:
            active_clip_ids = {
                task.command.clip_id
                for task in self._session.state.tasks.items.values()
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
        request_id = self._session.state.requests.preview_id
        generation = self._session.state.binding.generation
        project_dir = self._session.state.binding.require_current().project_dir
        use_proxies = self._session.state.service_settings.preview.preview_quality != "source"
        prefer_sdr_preview_proxy = (
            state.sequence.profile.color_mode == ColorMode.HDR10_BT2020_PQ
            and not self._session.state.presentation.hdr_preview_active
        )
        if (
            self._session.state.requests.preview_future is not None
            and not self._session.state.requests.preview_future.running()
        ):
            self._session.state.requests.preview_future.cancel()
        self._session.state.requests.preview_future = self._session.background.submit(
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
        self._session.state.presentation.preview_subtitles = []
        self._session.state.presentation.preview_subtitles_by_track = {}
        if not self._session.state.binding.current or not self._session.state.binding.active_sequence_id:
            self._session.models.subtitle_placements.set_items([])
            return
        state = self._session.state.binding.require_current().load_timeline(
            self._session.state.binding.active_sequence_id
        )
        tracks = [track for track in state.tracks if track.kind == TrackKind.SUBTITLE and track.enabled]
        if not tracks:
            self._session.models.subtitle_placements.set_items([])
            return
        segments = {
            segment.id: segment
            for document in self._session.state.binding.require_current().list_subtitle_documents()
            for segment in self._session.state.binding.require_current().list_subtitle_segments(document.id)
        }
        audio_lane_positions = self._audio_lane_projection(state)
        default_audio_position = next(
            (index for index, track in enumerate(state.tracks) if track.kind == TrackKind.AUDIO),
            -1,
        )
        placement_rows = []
        for track in tracks:
            track_subtitles: list[tuple[int, int, str]] = []
            for placement in self._session.state.binding.require_current().list_subtitle_placements(track.id):
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
                        self._session.state.presentation.preview_subtitles.append(
                            (placement.start_frame, placement.end_frame, text)
                        )
                    track_subtitles.append((placement.start_frame, placement.end_frame, text))
            track_subtitles.sort(key=lambda item: (item[0], item[1]))
            self._session.state.presentation.preview_subtitles_by_track[track.id] = track_subtitles
        self._session.state.presentation.preview_subtitles.sort(key=lambda item: (item[0], item[1]))
        self._session.models.subtitle_placements.set_items(placement_rows)
        placement_ids = {item["placementId"] for item in placement_rows}
        if self._session.state.selection.subtitle_placement_id not in placement_ids:
            self._session.state.selection.subtitle_placement_id = ""
