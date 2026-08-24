from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING

from mediaflow.application.timeline_clock import assets_in_timeline_clock
from mediaflow.application.timeline_integrity import validate_timeline_integrity
from mediaflow.domain.enums import TrackKind
from mediaflow.domain.exports import SubtitleStyle
from mediaflow.domain.project import Asset
from mediaflow.domain.timeline import (
    Clip,
    ClipAudio,
    ClipTransform,
    ClipTransformKeyframe,
    CompoundClip,
    TimelineMarker,
    TimelineRange,
    TimelineState,
    Track,
    Transition,
)
from mediaflow.domain.visual_effects import ClipVisualEffect

from .project_repository_component import ProjectRepositoryComponent
from .project_serialization import json_value as _json
from .project_serialization import model_json as _model_json

if TYPE_CHECKING:
    from .asset_catalog_repository import AssetCatalogRepository
    from .project_database_session import ProjectDatabaseSession
    from .project_metadata_repository import ProjectMetadataRepository
    from .sequence_catalog_repository import SequenceCatalogRepository
    from .subtitle_repository import SubtitleRepository
    from .web_media_repository import WebMediaRepository


class TimelineRepository(ProjectRepositoryComponent):
    def __init__(
        self,
        database: ProjectDatabaseSession,
        *,
        projects: Callable[[], ProjectMetadataRepository],
        sequences: Callable[[], SequenceCatalogRepository],
        assets: Callable[[], AssetCatalogRepository],
        subtitles: Callable[[], SubtitleRepository],
        web: Callable[[], WebMediaRepository],
    ) -> None:
        super().__init__(database)
        self._projects = projects
        self._sequences = sequences
        self._assets = assets
        self._subtitles = subtitles
        self._web = web

    def load_timeline(self, sequence_id: str) -> TimelineState:
        sequence = self._sequences().get_sequence(sequence_id)
        tracks = self.list_tracks(sequence_id)
        track_ids = [track.id for track in tracks]
        if not track_ids:
            return TimelineState(
                sequence=sequence,
                markers=self.list_timeline_markers(sequence_id),
                ranges=self.list_timeline_ranges(sequence_id),
                web_states=self._web().list_web_clip_states(sequence_id),
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
            web_states=self._web().list_web_clip_states(sequence_id),
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
        self._sequences().get_sequence(sequence_id)
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
        self._sequences().get_sequence(sequence_id)
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
        existing = self._sequences().get_sequence(state.sequence.id)
        project = self._projects().get_project()
        if state.sequence.id == project.main_sequence_id and state.sequence.profile != existing.profile:
            raise RuntimeError("Main sequence profile changes must use the frame-clock transaction")
        return self._save_timeline(state)

    def _save_timeline(
        self,
        state: TimelineState,
        *,
        validation_assets: Mapping[str, Asset] | None = None,
    ) -> int:
        existing = self._sequences().get_sequence(state.sequence.id)
        if existing.project_id != state.sequence.project_id:
            raise ValueError("Sequence project cannot change")
        assets = (
            dict(validation_assets)
            if validation_assets is not None
            else assets_in_timeline_clock(
            self._projects(),
            self._sequences(),
            self._assets(), state.sequence)
        )
        validate_timeline_integrity(state, assets=assets)

        track_ids = {track.id for track in state.tracks}
        clip_ids = {clip.id for clip in state.clips}
        compound_ids = {compound.id for compound in state.compounds}

        with self.transaction() as connection:
            next_revision = self._sequences().update_sequence_record(
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
                self._web().upsert_web_clip_state(connection, web_state)
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
            self._sequences().store_sequence_export_preset(
                connection,
                state.sequence,
            )
            self._subtitles().sync_subtitle_placements(
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
            self._projects(),
            self._sequences(),
            self._assets(), state.sequence)
        for clip in clips.values():
            asset = assets[clip.asset_id]
            clip.validate_source_range(asset.kind, asset.metadata.duration_frames)
        with self.transaction() as connection:
            next_revision = self._sequences().update_sequence_record(
                connection,
                state.sequence,
            )
            for clip in clips.values():
                self._upsert_clip(connection, clip)
            self._subtitles().sync_subtitle_placements(
                connection,
                state.sequence.id,
                clip_ids=clip_ids,
            )
            self._touch_project(connection)
        return next_revision

    def save_clip_set_changes(
        self,
        state: TimelineState,
        *,
        changed_clip_ids: set[str],
        removed_clip_ids: set[str],
        changed_web_state_ids: set[str],
    ) -> int:
        """Persist a small clip membership delta without rewriting the timeline graph."""

        if changed_clip_ids & removed_clip_ids:
            raise ValueError("Changed and removed clip deltas must be disjoint")
        clips = {clip.id: clip for clip in state.clips}
        if not changed_clip_ids <= set(clips):
            raise ValueError("Clip set delta references a missing changed clip")
        if removed_clip_ids & set(clips):
            raise ValueError("Clip set delta retains a removed clip")
        if not changed_web_state_ids <= (set(clips) | removed_clip_ids):
            raise ValueError("Web clip state delta references an unknown clip")

        existing = self._sequences().get_sequence(state.sequence.id)
        project = self._projects().get_project()
        if existing.project_id != state.sequence.project_id:
            raise ValueError("Sequence project cannot change")
        if state.sequence.id == project.main_sequence_id and state.sequence.profile != existing.profile:
            raise RuntimeError("Main sequence profile changes must use the frame-clock transaction")
        assets = assets_in_timeline_clock(
            self._projects(),
            self._sequences(),
            self._assets(),
            state.sequence,
        )
        validate_timeline_integrity(state, assets=assets)

        track_ids = {
            row["id"]
            for row in self._fetchall(
                "SELECT id FROM track WHERE sequence_id=?",
                (state.sequence.id,),
            )
        }
        if any(clips[clip_id].track_id not in track_ids for clip_id in changed_clip_ids):
            raise ValueError("Clip set delta references a track outside the sequence")

        affected_clip_ids = changed_clip_ids | removed_clip_ids | changed_web_state_ids
        with self.transaction() as connection:
            next_revision = self._sequences().update_sequence_record(
                connection,
                state.sequence,
            )
            for clip_id in sorted(removed_clip_ids):
                connection.execute("DELETE FROM clip WHERE id=?", (clip_id,))
            for clip_id in sorted(changed_clip_ids):
                self._upsert_clip(connection, clips[clip_id])
            for clip_id in sorted(changed_web_state_ids):
                web_state = state.web_states.get(clip_id)
                if web_state is None:
                    connection.execute(
                        "DELETE FROM web_clip_state WHERE clip_id=?",
                        (clip_id,),
                    )
                else:
                    self._web().upsert_web_clip_state(connection, web_state)
            self._sequences().store_sequence_export_preset(
                connection,
                state.sequence,
            )
            if affected_clip_ids:
                self._subtitles().sync_subtitle_placements(
                    connection,
                    state.sequence.id,
                    clip_ids=affected_clip_ids,
                )
            self._touch_project(connection)
        return next_revision

    def save_frame_clock_timeline(
        self,
        state: TimelineState,
        *,
        validation_assets: Mapping[str, Asset],
    ) -> int:
        """Persist a timeline selected by the frame-clock transaction owner."""

        return self._save_timeline(state, validation_assets=validation_assets)
