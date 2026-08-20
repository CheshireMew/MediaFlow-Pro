from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping

from mediaflow.application.timeline_clock import assets_in_timeline_clock
from mediaflow.application.timeline_integrity import validate_timeline_integrity
from mediaflow.domain.enums import TrackKind
from mediaflow.domain.exports import SubtitleStyle
from mediaflow.domain.frame_clock import (
    AssetFrameClockState,
    MainFrameClockSnapshot,
    SubtitleTrackLinkFrameClockState,
)
from mediaflow.domain.project import (
    Asset,
    ProjectProfile,
)
from mediaflow.domain.timebase import reframe_frames, reframe_interval
from mediaflow.domain.timeline import (
    Clip,
    ClipAudio,
    ClipTransform,
    ClipTransformKeyframe,
    CompoundClip,
    TimelineMarker,
    TimelineMergeConflict,
    TimelineRange,
    TimelineState,
    Track,
    Transition,
)
from mediaflow.domain.visual_effects import ClipVisualEffect

from .project_repository_component import ProjectRepositoryComponent
from .project_serialization import json_value as _json
from .project_serialization import model_json as _model_json


class TimelineRepository(ProjectRepositoryComponent):
    def load_timeline(self, sequence_id: str) -> TimelineState:
        sequence = self._relations.sequences.get_sequence(sequence_id)
        tracks = self.list_tracks(sequence_id)
        track_ids = [track.id for track in tracks]
        if not track_ids:
            return TimelineState(
                sequence=sequence,
                markers=self.list_timeline_markers(sequence_id),
                ranges=self.list_timeline_ranges(sequence_id),
                web_states=self._relations.web.list_web_clip_states(sequence_id),
            )
        placeholders = ",".join("?" for _ in track_ids)
        clip_rows = self._fetchall(
            f"SELECT * FROM clip WHERE track_id IN ({placeholders}) ORDER BY timeline_start, id",
            track_ids,
        )
        return TimelineState(
            sequence=sequence,
            tracks=tracks,
            clips=[self._clip_from_row(row) for row in clip_rows],
            compounds=[
                CompoundClip(
                    id=row["id"],
                    sequence_id=row["sequence_id"],
                    name=row["name"],
                    clip_ids=list(json.loads(row["clip_ids_json"])),
                )
                for row in self._fetchall(
                    "SELECT * FROM compound_clip WHERE sequence_id=? ORDER BY id",
                    (sequence_id,),
                )
            ],
            transitions=self.list_transitions(sequence_id),
            markers=self.list_timeline_markers(sequence_id),
            ranges=self.list_timeline_ranges(sequence_id),
            web_states=self._relations.web.list_web_clip_states(sequence_id),
        )

    def get_clip(self, sequence_id: str, clip_id: str) -> Clip:
        row = self._fetchone(
            """SELECT clip.*
                 FROM clip
                 JOIN track ON track.id=clip.track_id
                WHERE track.sequence_id=? AND clip.id=?""",
            (sequence_id, clip_id),
        )
        if row is None:
            raise KeyError(clip_id)
        return self._clip_from_row(row)

    def list_tracks(self, sequence_id: str) -> list[Track]:
        return [
            self._track_from_row(row)
            for row in self._fetchall(
                "SELECT * FROM track WHERE sequence_id=? ORDER BY position, id",
                (sequence_id,),
            )
        ]

    def list_transitions(self, sequence_id: str) -> list[Transition]:
        return [
            self._transition_from_row(row)
            for row in self._fetchall(
                """SELECT transition.*
                     FROM transition
                     JOIN track ON track.id=transition.track_id
                    WHERE track.sequence_id=?
                    ORDER BY transition.id""",
                (sequence_id,),
            )
        ]

    def list_timeline_markers(self, sequence_id: str) -> list[TimelineMarker]:
        self._relations.sequences.get_sequence(sequence_id)
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
        self._relations.sequences.get_sequence(sequence_id)
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

    def save_timeline(self, state: TimelineState) -> int:
        existing = self._relations.sequences.get_sequence(state.sequence.id)
        project = self._relations.projects.get_project()
        if state.sequence.id == project.main_sequence_id and state.sequence.profile != existing.profile:
            raise RuntimeError("Main sequence profile changes must use the frame-clock transaction")
        return self._save_timeline(state)

    def _save_timeline(
        self,
        state: TimelineState,
        *,
        validation_assets: Mapping[str, Asset] | None = None,
    ) -> int:
        existing = self._relations.sequences.get_sequence(state.sequence.id)
        if existing.project_id != state.sequence.project_id:
            raise ValueError("Sequence project cannot change")
        assets = (
            dict(validation_assets)
            if validation_assets is not None
            else assets_in_timeline_clock(
            self._relations.projects,
            self._relations.sequences,
            self._relations.assets, state.sequence)
        )
        validate_timeline_integrity(state, assets=assets)

        track_ids = {track.id for track in state.tracks}
        clip_ids = {clip.id for clip in state.clips}
        compound_ids = {compound.id for compound in state.compounds}

        with self.transaction() as connection:
            next_revision = self._relations.sequences.update_sequence_record(
                connection,
                state.sequence,
            )
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
            self._delete_missing(
                connection,
                "compound_clip",
                "id",
                {
                    row["id"]
                    for row in connection.execute(
                        "SELECT id FROM compound_clip WHERE sequence_id=?",
                        (state.sequence.id,),
                    ).fetchall()
                },
                compound_ids,
            )
            self._delete_missing(connection, "clip", "id", existing_clip_ids, clip_ids)
            self._delete_missing(connection, "track", "id", existing_track_ids, track_ids)

            connection.execute(
                "UPDATE track SET primary_dialogue=0 WHERE sequence_id=?",
                (state.sequence.id,),
            )
            for track in state.tracks:
                self._upsert_track(
                    connection,
                    track.model_copy(update={"linked_audio_track_id": None}),
                )
            for track in state.tracks:
                if track.linked_audio_track_id is not None:
                    self._upsert_track(connection, track)
            for clip in state.clips:
                self._upsert_clip(connection, clip)
            for compound in state.compounds:
                connection.execute(
                    """INSERT INTO compound_clip(id, sequence_id, name, clip_ids_json)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT(id) DO UPDATE SET
                           sequence_id=excluded.sequence_id,
                           name=excluded.name,
                           clip_ids_json=excluded.clip_ids_json""",
                    (
                        compound.id,
                        compound.sequence_id,
                        compound.name,
                        _json(compound.clip_ids),
                    ),
                )
            for web_state in state.web_states.values():
                self._relations.web.upsert_web_clip_state(connection, web_state)
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
            self._relations.sequences.store_sequence_export_preset(
                connection,
                state.sequence,
            )
            self._relations.subtitles.sync_subtitle_placements(
                connection,
                state.sequence.id,
            )
            self._touch_project(connection)
        return next_revision

    @staticmethod
    def _track_from_row(row: sqlite3.Row) -> Track:
        return Track(
            id=row["id"],
            sequence_id=row["sequence_id"],
            name=row["name"],
            kind=TrackKind(row["kind"]),
            position=row["position"],
            enabled=bool(row["enabled"]),
            locked=bool(row["locked"]),
            muted=bool(row["muted"]),
            solo=bool(row["solo"]),
            audio_bus_id=row["audio_bus_id"],
            linked_audio_track_id=row["linked_audio_track_id"],
            primary_dialogue=bool(row["primary_dialogue"]),
            subtitle_style=(
                None
                if row["subtitle_style_json"] is None
                else SubtitleStyle.model_validate_json(row["subtitle_style_json"])
            ),
        )

    @staticmethod
    def _upsert_track(connection: sqlite3.Connection, track: Track) -> None:
        connection.execute(
            """INSERT INTO track(
                id, sequence_id, name, kind, position, enabled, locked,
                muted, solo, audio_bus_id, linked_audio_track_id, primary_dialogue,
                subtitle_style_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name, kind=excluded.kind, position=excluded.position,
                enabled=excluded.enabled, locked=excluded.locked, muted=excluded.muted,
                solo=excluded.solo, audio_bus_id=excluded.audio_bus_id,
                linked_audio_track_id=excluded.linked_audio_track_id,
                primary_dialogue=excluded.primary_dialogue,
                subtitle_style_json=excluded.subtitle_style_json""",
            (
                track.id,
                track.sequence_id,
                track.name,
                track.kind.value,
                track.position,
                int(track.enabled),
                int(track.locked),
                int(track.muted),
                int(track.solo),
                track.audio_bus_id,
                track.linked_audio_track_id,
                int(track.primary_dialogue),
                None if track.subtitle_style is None else _model_json(track.subtitle_style),
            ),
        )

    @staticmethod
    def _clip_from_row(row: sqlite3.Row) -> Clip:
        return Clip(
            id=row["id"],
            track_id=row["track_id"],
            asset_id=row["asset_id"],
            timeline_start=row["timeline_start"],
            source_in=row["source_in"],
            duration=row["duration"],
            media_kind=row["media_kind"],
            speed_numerator=row["speed_numerator"],
            speed_denominator=row["speed_denominator"],
            pitch_compensation=bool(row["pitch_compensation"]),
            freeze_source_frame=row["freeze_source_frame"],
            transform=ClipTransform.model_validate_json(row["transform_json"]),
            transform_keyframes=[
                ClipTransformKeyframe.model_validate(item)
                for item in json.loads(row["transform_keyframes_json"])
            ],
            audio=ClipAudio.model_validate_json(row["audio_json"]),
            visual_effects=[
                ClipVisualEffect.model_validate(item) for item in json.loads(row["visual_effects_json"])
            ],
        )

    @staticmethod
    def _upsert_clip(connection: sqlite3.Connection, clip: Clip) -> None:
        connection.execute(
            """INSERT INTO clip(
                id, track_id, asset_id, timeline_start, source_in, duration, media_kind,
                speed_numerator, speed_denominator, pitch_compensation,
                freeze_source_frame,
                transform_json, transform_keyframes_json, audio_json,
                visual_effects_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                track_id=excluded.track_id, asset_id=excluded.asset_id,
                timeline_start=excluded.timeline_start, source_in=excluded.source_in,
                duration=excluded.duration, media_kind=excluded.media_kind,
                speed_numerator=excluded.speed_numerator,
                speed_denominator=excluded.speed_denominator,
                pitch_compensation=excluded.pitch_compensation,
                freeze_source_frame=excluded.freeze_source_frame,
                transform_json=excluded.transform_json,
                transform_keyframes_json=excluded.transform_keyframes_json,
                audio_json=excluded.audio_json,
                visual_effects_json=excluded.visual_effects_json""",
            (
                clip.id,
                clip.track_id,
                clip.asset_id,
                clip.timeline_start,
                clip.source_in,
                clip.duration,
                clip.media_kind.value,
                clip.speed_numerator,
                clip.speed_denominator,
                int(clip.pitch_compensation),
                clip.freeze_source_frame,
                _model_json(clip.transform),
                _json([item.model_dump(mode="json") for item in clip.transform_keyframes]),
                _model_json(clip.audio),
                _json([item.model_dump(mode="json") for item in clip.visual_effects]),
            ),
        )

    @staticmethod
    def _transition_from_row(row: sqlite3.Row) -> Transition:
        return Transition(
            id=row["id"],
            track_id=row["track_id"],
            left_clip_id=row["left_clip_id"],
            right_clip_id=row["right_clip_id"],
            kind=row["kind"],
            duration=row["duration"],
            parameters=json.loads(row["parameters_json"]),
        )

    @staticmethod
    def _upsert_transition(
        connection: sqlite3.Connection,
        transition: Transition,
    ) -> None:
        connection.execute(
            """INSERT INTO transition(
                id, track_id, left_clip_id, right_clip_id, kind, duration, parameters_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                track_id=excluded.track_id, left_clip_id=excluded.left_clip_id,
                right_clip_id=excluded.right_clip_id, kind=excluded.kind,
                duration=excluded.duration, parameters_json=excluded.parameters_json""",
            (
                transition.id,
                transition.track_id,
                transition.left_clip_id,
                transition.right_clip_id,
                transition.kind.value,
                transition.duration,
                _json(transition.parameters),
            ),
        )

    def _ids_for_sequence_transitions(self, sequence_id: str) -> set[str]:
        rows = self._fetchall(
            """SELECT transition.id FROM transition
            JOIN track ON track.id=transition.track_id
            WHERE track.sequence_id=?""",
            (sequence_id,),
        )
        return {row["id"] for row in rows}

    @staticmethod
    def _upsert_timeline_marker(
        connection: sqlite3.Connection,
        marker: TimelineMarker,
    ) -> None:
        connection.execute(
            """INSERT INTO timeline_marker(id, sequence_id, frame, name, color)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET frame=excluded.frame,
                   name=excluded.name, color=excluded.color""",
            (marker.id, marker.sequence_id, marker.frame, marker.name, marker.color),
        )

    @staticmethod
    def _upsert_timeline_range(
        connection: sqlite3.Connection,
        item: TimelineRange,
    ) -> None:
        connection.execute(
            """INSERT INTO timeline_range(
                   id, sequence_id, start_frame, end_frame, name, color
               ) VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET start_frame=excluded.start_frame,
                   end_frame=excluded.end_frame, name=excluded.name, color=excluded.color""",
            (
                item.id,
                item.sequence_id,
                item.start_frame,
                item.end_frame,
                item.name,
                item.color,
            ),
        )

    @staticmethod
    def _delete_missing(
        connection: sqlite3.Connection,
        table: str,
        column: str,
        existing_ids: set[str],
        retained_ids: set[str],
    ) -> None:
        for item_id in existing_ids - retained_ids:
            connection.execute(f"DELETE FROM {table} WHERE {column}=?", (item_id,))

    def save_clip_changes(self, state: TimelineState, clip_ids: set[str]) -> int:
        """Persist in-place clip edits without rewriting an unchanged timeline graph."""
        if not clip_ids:
            return state.sequence.timeline_revision
        clips = {clip.id: clip for clip in state.clips if clip.id in clip_ids}
        if set(clips) != clip_ids:
            raise ValueError("Clip delta references a missing clip")
        track_ids = {
            row["id"]
            for row in self._fetchall("SELECT id FROM track WHERE sequence_id=?", (state.sequence.id,))
        }
        if any(clip.track_id not in track_ids for clip in clips.values()):
            raise ValueError("Clip delta references a track outside the sequence")
        assets = assets_in_timeline_clock(
            self._relations.projects,
            self._relations.sequences,
            self._relations.assets, state.sequence)
        for clip in clips.values():
            asset = assets[clip.asset_id]
            clip.validate_source_range(asset.kind, asset.metadata.duration_frames)
        with self.transaction() as connection:
            next_revision = self._relations.sequences.update_sequence_record(
                connection,
                state.sequence,
            )
            for clip in clips.values():
                self._upsert_clip(connection, clip)
            self._relations.subtitles.sync_subtitle_placements(
                connection,
                state.sequence.id,
                clip_ids=clip_ids,
            )
            self._touch_project(connection)
        return next_revision

    def capture_main_frame_clock(
        self,
        sequence_id: str,
    ) -> MainFrameClockSnapshot:
        project = self._relations.projects.get_project()
        if sequence_id != project.main_sequence_id:
            raise ValueError("Only the main sequence owns the shared project frame clock")
        timeline = self.load_timeline(sequence_id)
        timeline.sequence = timeline.sequence.model_copy(update={"timeline_revision": 0})
        assets = [
            AssetFrameClockState(
                asset_id=asset.id,
                metadata=asset.metadata,
                proxy_path=asset.proxy_path,
                sdr_preview_proxy_path=asset.sdr_preview_proxy_path,
            )
            for asset in self._relations.assets.list_assets()
        ]
        segment_rows = self._fetchall("SELECT * FROM subtitle_segment ORDER BY id")
        word_rows = self._fetchall("SELECT * FROM subtitle_word ORDER BY id")
        link_rows = self._fetchall(
            """SELECT link.*
               FROM subtitle_track_document link
               JOIN track ON track.id=link.track_id
               WHERE track.sequence_id=?
               ORDER BY link.track_id, link.document_id""",
            (sequence_id,),
        )
        placement_rows = self._fetchall(
            """SELECT placement.*
               FROM subtitle_placement placement
               JOIN track ON track.id=placement.track_id
               WHERE track.sequence_id=?
               ORDER BY placement.id""",
            (sequence_id,),
        )
        return MainFrameClockSnapshot(
            timeline=timeline,
            assets=assets,
            subtitle_segments=[
                self._relations.subtitles.subtitle_segment_from_row(row)
                for row in segment_rows
            ],
            subtitle_words=[
                self._relations.subtitles.subtitle_word_from_row(row)
                for row in word_rows
            ],
            highlights=sorted(
                self._relations.highlights.list_highlights(),
                key=lambda item: item.id,
            ),
            subtitle_links=[
                SubtitleTrackLinkFrameClockState(
                    track_id=row["track_id"],
                    document_id=row["document_id"],
                    offset_frames=row["offset_frames"],
                    source_start_frame=row["source_start_frame"],
                    source_end_frame=row["source_end_frame"],
                )
                for row in link_rows
            ],
            subtitle_placements=[
                self._relations.subtitles.subtitle_placement_from_row(row)
                for row in placement_rows
            ],
        )

    def change_main_frame_clock(
        self,
        source: MainFrameClockSnapshot,
        state: TimelineState,
        assets: list[Asset],
        *,
        old_profile: ProjectProfile,
    ) -> MainFrameClockSnapshot:
        project = self._relations.projects.get_project()
        if (
            source.timeline.sequence.id != project.main_sequence_id
            or state.sequence.id != project.main_sequence_id
        ):
            raise ValueError("Only the main sequence owns the shared project frame clock")
        if source.timeline.sequence.profile != old_profile:
            raise ValueError("Frame-clock source profile does not match the captured snapshot")
        stored_asset_ids = {item.id for item in self._relations.assets.list_assets()}
        if {item.id for item in assets} != stored_asset_ids:
            raise ValueError("Frame-clock changes must include every project asset")

        with self.transaction() as connection:
            current = self.capture_main_frame_clock(project.main_sequence_id)
            self._require_snapshot_source(current, source)
            validation_assets = {asset.id: asset for asset in assets}
            current_sequence = self._relations.sequences.get_sequence(project.main_sequence_id)
            state = state.model_copy(
                update={
                    "sequence": state.sequence.model_copy(
                        update={"timeline_revision": current_sequence.timeline_revision}
                    )
                }
            )
            self._save_timeline(
                state,
                validation_assets=validation_assets,
            )
            self._write_asset_clock_states(
                connection,
                [
                    AssetFrameClockState(
                        asset_id=asset.id,
                        metadata=asset.metadata,
                        proxy_path=asset.proxy_path,
                        sdr_preview_proxy_path=asset.sdr_preview_proxy_path,
                    )
                    for asset in assets
                ],
            )
            if (
                old_profile.fps_numerator != state.sequence.profile.fps_numerator
                or old_profile.fps_denominator != state.sequence.profile.fps_denominator
            ):
                self._reframe_shared_main_clock_records(
                    connection,
                    source,
                    old_profile,
                    state.sequence.profile,
                )
            self._sync_all_subtitle_placements(
                connection,
            )
            self._touch_project(connection)
            return self.capture_main_frame_clock(project.main_sequence_id)

    def restore_main_frame_clock(
        self,
        source: MainFrameClockSnapshot,
        destination: MainFrameClockSnapshot,
    ) -> MainFrameClockSnapshot:
        project = self._relations.projects.get_project()
        if (
            source.timeline.sequence.id != project.main_sequence_id
            or destination.timeline.sequence.id != project.main_sequence_id
        ):
            raise ValueError("Frame-clock snapshot belongs to another sequence")
        with self.transaction() as connection:
            current = self.capture_main_frame_clock(project.main_sequence_id)
            if current == destination:
                return current
            self._require_snapshot_source(current, source)
            current_assets = {asset.id: asset for asset in self._relations.assets.list_assets()}
            destination_assets = {
                item.asset_id: current_assets[item.asset_id].model_copy(
                    update={
                        "metadata": item.metadata,
                        "proxy_path": item.proxy_path,
                        "sdr_preview_proxy_path": item.sdr_preview_proxy_path,
                    }
                )
                for item in destination.assets
            }
            if set(destination_assets) != set(current_assets):
                raise TimelineMergeConflict(
                    "main frame clock asset set",
                    project.main_sequence_id,
                )
            current_sequence = self._relations.sequences.get_sequence(project.main_sequence_id)
            destination_timeline = destination.timeline.model_copy(
                update={
                    "sequence": destination.timeline.sequence.model_copy(
                        update={"timeline_revision": current_sequence.timeline_revision}
                    )
                }
            )
            self._save_timeline(
                destination_timeline,
                validation_assets=destination_assets,
            )
            self._write_asset_clock_states(
                connection,
                destination.assets,
            )
            self._write_shared_main_clock_records(
                connection,
                destination,
            )
            self._sync_all_subtitle_placements(
                connection,
                excluded_sequence_ids={project.main_sequence_id},
            )
            self._touch_project(connection)
            return self.capture_main_frame_clock(project.main_sequence_id)

    @staticmethod
    def _require_snapshot_source(
        current: MainFrameClockSnapshot,
        source: MainFrameClockSnapshot,
    ) -> None:
        if current != source:
            raise TimelineMergeConflict(
                "main frame clock snapshot",
                current.timeline.sequence.id,
            )

    def _write_asset_clock_states(
        self,
        connection: sqlite3.Connection,
        assets: list[AssetFrameClockState],
    ) -> None:
        for asset in assets:
            cursor = connection.execute(
                """UPDATE asset SET proxy_path=?, sdr_preview_proxy_path=?,
                   metadata_json=? WHERE id=?""",
                (
                    self._relations.assets.store_optional_path(asset.proxy_path),
                    self._relations.assets.store_optional_path(asset.sdr_preview_proxy_path),
                    _model_json(asset.metadata),
                    asset.asset_id,
                ),
            )
            if cursor.rowcount != 1:
                raise TimelineMergeConflict("asset", asset.asset_id)

    def _sync_all_subtitle_placements(
        self,
        connection: sqlite3.Connection,
        *,
        excluded_sequence_ids: set[str] | None = None,
    ) -> None:
        excluded = excluded_sequence_ids or set()
        sequence_ids = [
            str(row["id"])
            for row in connection.execute("SELECT id FROM sequence ORDER BY position, id").fetchall()
            if str(row["id"]) not in excluded
        ]
        for sequence_id in sequence_ids:
            self._relations.subtitles.sync_subtitle_placements(
                connection,
                sequence_id,
            )

    def _reframe_shared_main_clock_records(
        self,
        connection: sqlite3.Connection,
        source: MainFrameClockSnapshot,
        old_profile: ProjectProfile,
        new_profile: ProjectProfile,
    ) -> None:
        def reframe(value: int) -> int:
            return reframe_frames(value, old_profile, new_profile)

        def reframe_range(start: int, end: int) -> tuple[int, int]:
            return reframe_interval(
                start,
                end,
                old_profile,
                new_profile,
            )

        for segment in source.subtitle_segments:
            start, end = reframe_range(segment.start_frame, segment.end_frame)
            connection.execute(
                "UPDATE subtitle_segment SET start_frame=?, end_frame=? WHERE id=?",
                (start, end, segment.id),
            )
        for word in source.subtitle_words:
            start, end = reframe_range(word.start_frame, word.end_frame)
            connection.execute(
                "UPDATE subtitle_word SET start_frame=?, end_frame=? WHERE id=?",
                (start, end, word.id),
            )
        for candidate in source.highlights:
            start, end = reframe_range(
                candidate.start_frame,
                candidate.end_frame,
            )
            connection.execute(
                "UPDATE highlight_candidate SET start_frame=?, end_frame=? WHERE id=?",
                (start, end, candidate.id),
            )
        for link in source.subtitle_links:
            source_start: int | None
            source_end: int | None
            if link.source_start_frame is not None and link.source_end_frame is not None:
                source_start, source_end = reframe_range(
                    link.source_start_frame,
                    link.source_end_frame,
                )
            else:
                source_start = (
                    reframe(link.source_start_frame) if link.source_start_frame is not None else None
                )
                source_end = reframe(link.source_end_frame) if link.source_end_frame is not None else None
            connection.execute(
                """UPDATE subtitle_track_document
                   SET offset_frames=?, source_start_frame=?, source_end_frame=?
                   WHERE track_id=? AND document_id=?""",
                (
                    reframe(link.offset_frames),
                    source_start,
                    source_end,
                    link.track_id,
                    link.document_id,
                ),
            )
        for placement in source.subtitle_placements:
            if not placement.timing_overridden:
                continue
            start, end = reframe_range(
                placement.start_frame,
                placement.end_frame,
            )
            connection.execute(
                """UPDATE subtitle_placement
                   SET start_frame=?, end_frame=?
                   WHERE id=? AND timing_overridden=1""",
                (start, end, placement.id),
            )

    def _write_shared_main_clock_records(
        self,
        connection: sqlite3.Connection,
        snapshot: MainFrameClockSnapshot,
    ) -> None:
        for segment in snapshot.subtitle_segments:
            connection.execute(
                """UPDATE subtitle_segment
                   SET source_segment_id=?, start_frame=?, end_frame=?, text=?,
                       speaker=?, confidence=? WHERE id=?""",
                (
                    segment.source_segment_id,
                    segment.start_frame,
                    segment.end_frame,
                    segment.text,
                    segment.speaker,
                    segment.confidence,
                    segment.id,
                ),
            )
        for word in snapshot.subtitle_words:
            connection.execute(
                """UPDATE subtitle_word
                   SET segment_id=?, position=?, start_frame=?, end_frame=?,
                       text=?, confidence=?, timing_source=?, excluded=?
                   WHERE id=?""",
                (
                    word.segment_id,
                    word.position,
                    word.start_frame,
                    word.end_frame,
                    word.text,
                    word.confidence,
                    word.timing_source,
                    int(word.excluded),
                    word.id,
                ),
            )
        for candidate in snapshot.highlights:
            connection.execute(
                """UPDATE highlight_candidate
                   SET project_id=?, asset_id=?, document_id=?, sequence_id=?,
                       start_frame=?, end_frame=?, title=?, reason=?, score=?,
                       selected=? WHERE id=?""",
                (
                    candidate.project_id,
                    candidate.asset_id,
                    candidate.document_id,
                    candidate.sequence_id,
                    candidate.start_frame,
                    candidate.end_frame,
                    candidate.title,
                    candidate.reason,
                    candidate.score,
                    int(candidate.selected),
                    candidate.id,
                ),
            )
        for link in snapshot.subtitle_links:
            connection.execute(
                """UPDATE subtitle_track_document
                   SET offset_frames=?, source_start_frame=?, source_end_frame=?
                   WHERE track_id=? AND document_id=?""",
                (
                    link.offset_frames,
                    link.source_start_frame,
                    link.source_end_frame,
                    link.track_id,
                    link.document_id,
                ),
            )
        sequence_id = snapshot.timeline.sequence.id
        connection.execute(
            """DELETE FROM subtitle_placement
               WHERE track_id IN (
                   SELECT id FROM track WHERE sequence_id=?
               )""",
            (sequence_id,),
        )
        for placement in snapshot.subtitle_placements:
            connection.execute(
                """INSERT INTO subtitle_placement(
                       id, track_id, segment_id, clip_id, start_frame, end_frame,
                       text_override, timing_overridden
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    placement.id,
                    placement.track_id,
                    placement.segment_id,
                    placement.clip_id,
                    placement.start_frame,
                    placement.end_frame,
                    placement.text_override,
                    int(placement.timing_overridden),
                ),
            )
