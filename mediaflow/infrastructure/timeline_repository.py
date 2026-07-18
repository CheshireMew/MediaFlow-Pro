from __future__ import annotations

from mediaflow.domain.project import (
    Asset,
    MediaMetadata,
    ProjectProfile,
)
from mediaflow.domain.timebase import reframe_frames
from mediaflow.domain.timeline import (
    TimelineMarker,
    TimelineRange,
    TimelineState,
)

from .project_serialization import json_value as _json
from .project_serialization import model_json as _model_json


class TimelineRepository:
    def load_timeline(self, sequence_id: str) -> TimelineState:
        sequence = self.get_sequence(sequence_id)
        track_rows = self._fetchall(
            "SELECT * FROM track WHERE sequence_id=? ORDER BY position, id",
            (sequence_id,),
        )
        tracks = [self._track_from_row(row) for row in track_rows]
        track_ids = [track.id for track in tracks]
        if not track_ids:
            return TimelineState(
                sequence=sequence,
                markers=self.list_timeline_markers(sequence_id),
                ranges=self.list_timeline_ranges(sequence_id),
            )
        placeholders = ",".join("?" for _ in track_ids)
        clip_rows = self._fetchall(
            f"SELECT * FROM clip WHERE track_id IN ({placeholders}) ORDER BY timeline_start, id",
            track_ids,
        )
        transition_rows = self._fetchall(
            f"SELECT * FROM transition WHERE track_id IN ({placeholders}) ORDER BY id",
            track_ids,
        )
        return TimelineState(
            sequence=sequence,
            tracks=tracks,
            clips=[self._clip_from_row(row) for row in clip_rows],
            transitions=[self._transition_from_row(row) for row in transition_rows],
            markers=self.list_timeline_markers(sequence_id),
            ranges=self.list_timeline_ranges(sequence_id),
        )

    def list_timeline_markers(self, sequence_id: str) -> list[TimelineMarker]:
        self.get_sequence(sequence_id)
        return [
            TimelineMarker(
                id=row["id"],
                sequence_id=row["sequence_id"],
                frame=row["frame"],
                name=row["name"],
                color=row["color"],
            )
            for row in self._fetchall(
                "SELECT * FROM timeline_marker WHERE sequence_id=? ORDER BY frame, id",
                (sequence_id,),
            )
        ]

    def list_timeline_ranges(self, sequence_id: str) -> list[TimelineRange]:
        self.get_sequence(sequence_id)
        return [
            TimelineRange(
                id=row["id"],
                sequence_id=row["sequence_id"],
                start_frame=row["start_frame"],
                end_frame=row["end_frame"],
                name=row["name"],
                color=row["color"],
            )
            for row in self._fetchall(
                "SELECT * FROM timeline_range WHERE sequence_id=? ORDER BY start_frame, id",
                (sequence_id,),
            )
        ]

    def save_timeline(self, state: TimelineState) -> None:
        existing = self.get_sequence(state.sequence.id)
        if existing.project_id != state.sequence.project_id:
            raise ValueError("Sequence project cannot change")
        track_ids = {track.id for track in state.tracks}
        if any(track.sequence_id != state.sequence.id for track in state.tracks):
            raise ValueError("Timeline contains a track from another sequence")
        if any(clip.track_id not in track_ids for clip in state.clips):
            raise ValueError("Timeline clip references an unknown track")
        clip_ids = {clip.id for clip in state.clips}
        if any(
            transition.track_id not in track_ids
            or transition.left_clip_id not in clip_ids
            or transition.right_clip_id not in clip_ids
            for transition in state.transitions
        ):
            raise ValueError("Timeline transition references an unknown clip or track")
        if any(marker.sequence_id != state.sequence.id for marker in state.markers):
            raise ValueError("Timeline marker belongs to another sequence")
        if any(item.sequence_id != state.sequence.id for item in state.ranges):
            raise ValueError("Timeline range belongs to another sequence")

        frame_clock_changed = (
            existing.profile.fps_numerator != state.sequence.profile.fps_numerator
            or existing.profile.fps_denominator != state.sequence.profile.fps_denominator
        )

        def reframe(value: int) -> int:
            return reframe_frames(value, existing.profile, state.sequence.profile)

        with self.transaction() as connection:
            self._update_sequence(connection, state.sequence)
            existing_track_ids = {
                row["id"]
                for row in connection.execute(
                    "SELECT id FROM track WHERE sequence_id=?", (state.sequence.id,)
                ).fetchall()
            }
            existing_clip_ids: set[str] = set()
            if existing_track_ids:
                placeholders = ",".join("?" for _ in existing_track_ids)
                existing_clip_ids = {
                    row["id"]
                    for row in connection.execute(
                        f"SELECT id FROM clip WHERE track_id IN ({placeholders})",
                        tuple(existing_track_ids),
                    ).fetchall()
                }

            self._delete_missing(
                connection,
                "transition",
                "id",
                self._ids_for_sequence_transitions(state.sequence.id),
                {item.id for item in state.transitions},
            )
            self._delete_missing(connection, "clip", "id", existing_clip_ids, clip_ids)
            self._delete_missing(connection, "track", "id", existing_track_ids, track_ids)

            for track in state.tracks:
                self._upsert_track(connection, track)
            for clip in state.clips:
                self._upsert_clip(connection, clip)
            for transition in state.transitions:
                self._upsert_transition(connection, transition)
            self._delete_missing(
                connection,
                "timeline_marker",
                "id",
                {
                    row["id"]
                    for row in connection.execute(
                        "SELECT id FROM timeline_marker WHERE sequence_id=?",
                        (state.sequence.id,),
                    ).fetchall()
                },
                {item.id for item in state.markers},
            )
            self._delete_missing(
                connection,
                "timeline_range",
                "id",
                {
                    row["id"]
                    for row in connection.execute(
                        "SELECT id FROM timeline_range WHERE sequence_id=?",
                        (state.sequence.id,),
                    ).fetchall()
                },
                {item.id for item in state.ranges},
            )
            for marker in state.markers:
                self._upsert_timeline_marker(connection, marker)
            for item in state.ranges:
                self._upsert_timeline_range(connection, item)
            self._store_sequence_export_preset(connection, state.sequence)
            if frame_clock_changed:
                project = self.get_project()
                if state.sequence.id == project.main_sequence_id:
                    for row in connection.execute("SELECT id, metadata_json FROM asset").fetchall():
                        metadata = MediaMetadata.model_validate_json(row["metadata_json"])
                        metadata = metadata.model_copy(
                            update={"duration_frames": reframe(metadata.duration_frames)}
                        )
                        connection.execute(
                            """UPDATE asset SET proxy_path=NULL, sdr_preview_proxy_path=NULL,
                               metadata_json=? WHERE id=?""",
                            (_model_json(metadata), row["id"]),
                        )
                    for table in ("subtitle_segment", "highlight_candidate"):
                        rows = connection.execute(
                            f"SELECT id, start_frame, end_frame FROM {table}"
                        ).fetchall()
                        for row in rows:
                            start_frame = reframe(row["start_frame"])
                            connection.execute(
                                f"UPDATE {table} SET start_frame=?, end_frame=? WHERE id=?",
                                (
                                    start_frame,
                                    max(start_frame + 1, reframe(row["end_frame"])),
                                    row["id"],
                                ),
                            )
            if (
                state.sequence.profile != existing.profile
                and state.sequence.id == self.get_project().main_sequence_id
                and not frame_clock_changed
            ):
                connection.execute("UPDATE asset SET proxy_path=NULL, sdr_preview_proxy_path=NULL")
            self._sync_subtitle_placements(connection, state.sequence.id)
            self._touch_project(connection)

    def save_clip_changes(self, state: TimelineState, clip_ids: set[str]) -> None:
        """Persist in-place clip edits without rewriting an unchanged timeline graph."""
        if not clip_ids:
            return
        clips = {clip.id: clip for clip in state.clips if clip.id in clip_ids}
        if set(clips) != clip_ids:
            raise ValueError("Clip delta references a missing clip")
        track_ids = {
            row["id"]
            for row in self._fetchall("SELECT id FROM track WHERE sequence_id=?", (state.sequence.id,))
        }
        if any(clip.track_id not in track_ids for clip in clips.values()):
            raise ValueError("Clip delta references a track outside the sequence")
        with self.transaction() as connection:
            for clip in clips.values():
                self._upsert_clip(connection, clip)
            self._sync_subtitle_placements(
                connection,
                state.sequence.id,
                clip_ids=clip_ids,
            )
            self._touch_project(connection)

    def apply_main_profile_change(
        self,
        state: TimelineState,
        assets: list[Asset],
        *,
        old_profile: ProjectProfile,
    ) -> None:
        """Atomically replace the main frame clock and every value owned by it."""
        project = self.get_project()
        if state.sequence.id != project.main_sequence_id:
            raise ValueError("Only the main sequence owns the shared project frame clock")
        if {item.id for item in assets} != {item.id for item in self.list_assets()}:
            raise ValueError("Profile changes must update metadata for every project asset")
        new_profile = state.sequence.profile

        def reframe(value: int) -> int:
            return reframe_frames(value, old_profile, new_profile)

        with self.transaction() as connection:
            self._update_sequence(connection, state.sequence)
            for clip in state.clips:
                connection.execute(
                    """UPDATE clip SET timeline_start=?, source_in=?, duration=?,
                       speed_numerator=?, speed_denominator=?, pitch_compensation=?,
                       transform_json=?, audio_json=? WHERE id=?""",
                    (
                        clip.timeline_start,
                        clip.source_in,
                        clip.duration,
                        clip.speed_numerator,
                        clip.speed_denominator,
                        int(clip.pitch_compensation),
                        _model_json(clip.transform),
                        _model_json(clip.audio),
                        clip.id,
                    ),
                )
            for transition in state.transitions:
                connection.execute(
                    "UPDATE transition SET duration=?, parameters_json=? WHERE id=?",
                    (transition.duration, _json(transition.parameters), transition.id),
                )
            for marker in state.markers:
                connection.execute(
                    "UPDATE timeline_marker SET frame=? WHERE id=? AND sequence_id=?",
                    (marker.frame, marker.id, state.sequence.id),
                )
            for item in state.ranges:
                connection.execute(
                    """UPDATE timeline_range SET start_frame=?, end_frame=?
                       WHERE id=? AND sequence_id=?""",
                    (item.start_frame, item.end_frame, item.id, state.sequence.id),
                )
            for asset in assets:
                connection.execute(
                    """UPDATE asset SET proxy_path=?, sdr_preview_proxy_path=?,
                       metadata_json=? WHERE id=?""",
                    (
                        self._stored_optional_path(asset.proxy_path),
                        self._stored_optional_path(asset.sdr_preview_proxy_path),
                        _model_json(asset.metadata),
                        asset.id,
                    ),
                )

            for row in connection.execute(
                "SELECT id, start_frame, end_frame FROM subtitle_segment"
            ).fetchall():
                start_frame = reframe(row["start_frame"])
                connection.execute(
                    "UPDATE subtitle_segment SET start_frame=?, end_frame=? WHERE id=?",
                    (
                        start_frame,
                        max(start_frame + 1, reframe(row["end_frame"])),
                        row["id"],
                    ),
                )
            for row in connection.execute(
                "SELECT id, start_frame, end_frame FROM highlight_candidate"
            ).fetchall():
                start_frame = reframe(row["start_frame"])
                connection.execute(
                    "UPDATE highlight_candidate SET start_frame=?, end_frame=? WHERE id=?",
                    (
                        start_frame,
                        max(start_frame + 1, reframe(row["end_frame"])),
                        row["id"],
                    ),
                )
            self._sync_subtitle_placements(connection, state.sequence.id)
            self._touch_project(connection)
