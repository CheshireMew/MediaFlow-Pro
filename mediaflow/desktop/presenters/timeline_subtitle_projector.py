from __future__ import annotations

from mediaflow.desktop.component_contexts import TimelineSubtitleProjectorContext
from mediaflow.desktop.presenters.timeline_rows import audio_lane_projection, project_subtitle_placement_row
from mediaflow.domain.enums import ClipMediaKind, TrackKind
from mediaflow.domain.subtitles import SubtitleSegment

from .base import Projector


class TimelineSubtitleProjector(Projector[TimelineSubtitleProjectorContext]):
    def refresh_preview_subtitles(self) -> None:
        self._context.state.presentation.preview_subtitles = []
        self._context.state.presentation.preview_subtitles_by_track = {}
        if not self._context.state.binding.current or not self._context.state.binding.active_sequence_id:
            self._context.models.subtitle_placements.set_items([])
            return
        state = self._context.state.binding.require_current().load_timeline(
            self._context.state.binding.active_sequence_id
        )
        tracks = [track for track in state.tracks if track.kind == TrackKind.SUBTITLE and track.enabled]
        if not tracks:
            self._context.models.subtitle_placements.set_items([])
            return
        segments = {
            segment.id: segment
            for document in self._context.state.binding.require_current().list_subtitle_documents()
            for segment in self._context.state.binding.require_current().list_subtitle_segments(document.id)
        }
        audio_lane_positions = audio_lane_projection(state)
        default_audio_position = next(
            (index for index, track in enumerate(state.tracks) if track.kind == TrackKind.AUDIO),
            -1,
        )
        placement_rows = []
        for track in tracks:
            track_subtitles: list[tuple[int, int, str]] = []
            for placement in self._context.state.binding.require_current().list_subtitle_placements(track.id):
                segment = segments.get(placement.segment_id)
                if segment:
                    text = placement.text_override or segment.text
                    placement_rows.append(
                        project_subtitle_placement_row(
                            placement,
                            segment,
                            audio_lane_positions=audio_lane_positions,
                            default_audio_position=default_audio_position,
                        )
                    )
                    if track.id == tracks[0].id:
                        self._context.state.presentation.preview_subtitles.append(
                            (placement.start_frame, placement.end_frame, text)
                        )
                    track_subtitles.append((placement.start_frame, placement.end_frame, text))
            track_subtitles.sort(key=lambda item: (item[0], item[1]))
            self._context.state.presentation.preview_subtitles_by_track[track.id] = track_subtitles
        self._context.state.presentation.preview_subtitles.sort(key=lambda item: (item[0], item[1]))
        self._context.models.subtitle_placements.set_items(placement_rows)
        placement_ids = {item["placementId"] for item in placement_rows}
        if self._context.state.selection.subtitle_placement_id not in placement_ids:
            self._context.state.selection.subtitle_placement_id = ""

    def refresh_preview_subtitle_segments(
        self,
        segments: list[SubtitleSegment],
    ) -> None:
        """Refresh placements for edited segments through one targeted project read."""

        if (
            not segments
            or not self._context.state.binding.current
            or not self._context.state.binding.active_sequence_id
            or not self._context.state.binding.timeline
        ):
            self.refresh_preview_subtitles()
            return
        track_rows = self._context.models.tracks.snapshot()
        enabled_track_ids = [
            str(track["trackId"])
            for track in track_rows
            if track["kind"] == TrackKind.SUBTITLE.value and bool(track["enabled"])
        ]
        enabled_track_id_set = set(enabled_track_ids)
        if not enabled_track_ids:
            self._context.models.subtitle_placements.set_items([])
            self._context.state.presentation.preview_subtitles = []
            self._context.state.presentation.preview_subtitles_by_track = {}
            return

        segment_by_id = {segment.id: segment for segment in segments}
        placements = self._context.state.binding.require_current().list_subtitle_placements_for_segments(
            self._context.state.binding.active_sequence_id,
            list(segment_by_id),
        )
        track_positions = {str(track["trackId"]): index for index, track in enumerate(track_rows)}
        placement_clip_ids = {placement.clip_id for placement in placements if placement.clip_id}
        audio_lane_positions = {}
        clips_model = self._context.models.clips
        for clip_id in placement_clip_ids:
            clip_row = clips_model.get(clips_model.findRow("clipId", clip_id))
            if clip_row and clip_row["mediaKind"] == ClipMediaKind.LINKED_AV.value:
                audio_lane_positions[clip_id] = int(clip_row["audioTrackPosition"])
        default_audio_position = next(
            (index for index, track in enumerate(track_rows) if track["kind"] == TrackKind.AUDIO.value),
            -1,
        )
        changed_rows = []
        for placement in placements:
            if placement.track_id not in enabled_track_id_set:
                continue
            segment = segment_by_id.get(placement.segment_id)
            if segment is None:
                self.refresh_preview_subtitles()
                return
            changed_rows.append(
                project_subtitle_placement_row(
                    placement,
                    segment,
                    audio_lane_positions=audio_lane_positions,
                    default_audio_position=default_audio_position,
                )
            )

        current_rows = self._context.models.subtitle_placements.snapshot()
        changed_segment_ids = set(segment_by_id)
        old_ids = {
            str(row["placementId"]) for row in current_rows if str(row["segmentId"]) in changed_segment_ids
        }
        changed_by_id = {str(row["placementId"]): row for row in changed_rows}
        projected = {
            str(row["placementId"]): row
            for row in current_rows
            if str(row["segmentId"]) not in changed_segment_ids
        }
        projected.update(changed_by_id)
        ordered_ids = [
            str(row["placementId"])
            for row in sorted(
                projected.values(),
                key=lambda row: (
                    track_positions.get(str(row["trackId"]), len(track_positions)),
                    int(row["startFrame"]),
                    int(row["endFrame"]),
                    str(row["placementId"]),
                ),
            )
        ]
        if not self._context.models.subtitle_placements.patch_items_by_key(
            changed_rows,
            removed_keys=old_ids - set(changed_by_id),
            ordered_keys=ordered_ids,
        ):
            self.refresh_preview_subtitles()
            return
        self._rebuild_preview_subtitle_projection(enabled_track_ids)

    def _rebuild_preview_subtitle_projection(self, enabled_track_ids: list[str]) -> None:
        rows = self._context.models.subtitle_placements.snapshot()
        by_track: dict[str, list[tuple[int, int, str]]] = {track_id: [] for track_id in enabled_track_ids}
        for row in rows:
            track_id = str(row["trackId"])
            if track_id not in by_track:
                continue
            by_track[track_id].append(
                (
                    int(row["startFrame"]),
                    int(row["endFrame"]),
                    str(row["text"]),
                )
            )
        for values in by_track.values():
            values.sort(key=lambda item: (item[0], item[1]))
        self._context.state.presentation.preview_subtitles_by_track = by_track
        self._context.state.presentation.preview_subtitles = list(by_track[enabled_track_ids[0]])
