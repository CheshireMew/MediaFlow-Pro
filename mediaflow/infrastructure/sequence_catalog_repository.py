from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from mediaflow.domain.audio import AudioBus, AudioEffect
from mediaflow.domain.enums import (
    ColorMode,
    SequenceKind,
    TaskStatus,
    WorkflowStatus,
)
from mediaflow.domain.exports import ExportPreset
from mediaflow.domain.project import (
    ProjectProfile,
    Sequence,
    SequenceInOut,
)
from mediaflow.domain.sequence_variants import SequenceVariantRecord
from mediaflow.domain.subtitles import SubtitlePlacement
from mediaflow.domain.timeline import TimelineRevisionConflict, TimelineState

from .project_repository_component import ProjectRepositoryComponent
from .project_serialization import json_value as _json_value
from .project_serialization import model_json as _model_json

if TYPE_CHECKING:
    from .audio_repository import AudioRepository
    from .project_database_session import ProjectDatabaseSession
    from .project_metadata_repository import ProjectMetadataRepository


class SequenceCatalogRepository(ProjectRepositoryComponent):
    def __init__(
        self,
        database: ProjectDatabaseSession,
        *,
        projects: Callable[[], ProjectMetadataRepository],
        audio: Callable[[], AudioRepository],
    ) -> None:
        super().__init__(database)
        self._projects = projects
        self._audio = audio

    def list_sequences(self, *, include_archived: bool = False) -> list[Sequence]:
        rows = self._fetchall(
            "SELECT * FROM sequence "
            + ("" if include_archived else "WHERE archived=0 ")
            + "ORDER BY position, created_at"
        )
        presets = {
            row["sequence_id"]: row["preset_json"]
            for row in self._fetchall("SELECT sequence_id, preset_json FROM sequence_export_setting")
        }
        return [self._sequence_from_row(row, presets.get(row["id"])) for row in rows]

    def get_sequence(self, sequence_id: str) -> Sequence:
        row = self._fetchone("SELECT * FROM sequence WHERE id=?", (sequence_id,))
        if row is None:
            raise KeyError(sequence_id)
        preset = self._fetchone(
            "SELECT preset_json FROM sequence_export_setting WHERE sequence_id=?",
            (sequence_id,),
        )
        return self._sequence_from_row(row, preset["preset_json"] if preset else None)

    def save_sequence_export_preset(self, sequence_id: str, preset: ExportPreset) -> Sequence:
        self.get_sequence(sequence_id)
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO sequence_export_setting(sequence_id, preset_json)
                   VALUES (?, ?)
                   ON CONFLICT(sequence_id) DO UPDATE SET preset_json=excluded.preset_json""",
                (sequence_id, _model_json(preset)),
            )
            self._touch_project(connection)
        return self.get_sequence(sequence_id)

    def create_short_sequence(
        self,
        name: str,
        profile: ProjectProfile | None = None,
    ) -> Sequence:
        return self.commit_short_sequence(
            self.prepare_short_sequence(name, profile)
        )

    def prepare_short_sequence(
        self,
        name: str,
        profile: ProjectProfile | None = None,
    ) -> Sequence:
        project = self._projects().get_project()
        main_profile = self.get_sequence(project.main_sequence_id).profile
        position = len(self.list_sequences())
        return Sequence(
            project_id=project.id,
            name=name,
            kind=SequenceKind.SHORT,
            position=position,
            profile=profile or main_profile.model_copy(update={"width": 1080, "height": 1920}),
            profile_confirmed=True,
        )

    def commit_short_sequence(self, sequence: Sequence) -> Sequence:
        if sequence.kind != SequenceKind.SHORT:
            raise ValueError(
                "Only a short sequence can use short-sequence registration"
            )
        master = AudioBus(sequence_id=sequence.id, name="主总线", position=0)
        dialogue = AudioBus(
            sequence_id=sequence.id,
            name="对白",
            parent_bus_id=master.id,
            position=1,
        )
        music = AudioBus(
            sequence_id=sequence.id,
            name="音乐",
            parent_bus_id=master.id,
            position=2,
        )
        effects = AudioBus(
            sequence_id=sequence.id,
            name="效果",
            parent_bus_id=master.id,
            position=3,
        )
        with self.transaction() as connection:
            self._insert_sequence_record(connection, sequence)
            for bus in (master, dialogue, music, effects):
                self._audio().insert_bus_record(connection, bus)
            self._touch_project(connection)
        return sequence

    def archive_short_sequence(self, sequence_id: str) -> Sequence:
        sequence = self.get_sequence(sequence_id)
        project = self._projects().get_project()
        if sequence.id == project.main_sequence_id or sequence.kind != SequenceKind.SHORT:
            raise ValueError("主序列不能删除")
        if sequence.archived:
            return sequence
        active_workflow = self._fetchone(
            """SELECT id FROM workflow_run
               WHERE sequence_id=? AND status IN (?, ?, ?) LIMIT 1""",
            (
                sequence_id,
                WorkflowStatus.RUNNING.value,
                WorkflowStatus.AWAITING_CONFIRMATION.value,
                WorkflowStatus.BLOCKED.value,
            ),
        )
        if active_workflow is not None:
            raise ValueError("该短视频序列仍有未完成工作流，不能删除")
        active_task = self._fetchone(
            """SELECT id FROM task
               WHERE sequence_id=? AND status IN (?, ?, ?) LIMIT 1""",
            (
                sequence_id,
                TaskStatus.PENDING.value,
                TaskStatus.RUNNING.value,
                TaskStatus.PAUSED.value,
            ),
        )
        if active_task is not None:
            raise ValueError("该短视频序列仍有活动任务，不能删除")
        with self.transaction() as connection:
            connection.execute("UPDATE sequence SET archived=1 WHERE id=?", (sequence_id,))
            self._touch_project(connection)
        return self.get_sequence(sequence_id)

    def restore_short_sequence(self, sequence_id: str) -> Sequence:
        sequence = self.get_sequence(sequence_id)
        if sequence.kind != SequenceKind.SHORT:
            raise ValueError("主序列不需要恢复")
        with self.transaction() as connection:
            connection.execute("UPDATE sequence SET archived=0 WHERE id=?", (sequence_id,))
            self._touch_project(connection)
        return self.get_sequence(sequence_id)

    def save_sequence_variant(self, record: SequenceVariantRecord) -> SequenceVariantRecord:
        sequence = self.get_sequence(record.sequence_id)
        source = self.get_sequence(record.source_sequence_id)
        if sequence.kind != SequenceKind.SHORT:
            raise ValueError("Only short sequences can be registered as delivery variants")
        if sequence.id == source.id:
            raise ValueError("A sequence variant cannot derive from itself")
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO sequence_variant(
                       sequence_id, source_sequence_id, preset_id, reframe_mode,
                       source_timeline_revision, baseline_timeline_json,
                       baseline_audio_buses_json, baseline_audio_effects_json,
                       baseline_subtitle_placements_json, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(sequence_id) DO UPDATE SET
                       source_sequence_id=excluded.source_sequence_id,
                       preset_id=excluded.preset_id,
                       reframe_mode=excluded.reframe_mode,
                       source_timeline_revision=excluded.source_timeline_revision,
                       baseline_timeline_json=excluded.baseline_timeline_json,
                       baseline_audio_buses_json=excluded.baseline_audio_buses_json,
                       baseline_audio_effects_json=excluded.baseline_audio_effects_json,
                       baseline_subtitle_placements_json=excluded.baseline_subtitle_placements_json,
                       created_at=excluded.created_at,
                       updated_at=excluded.updated_at""",
                (
                    record.sequence_id,
                    record.source_sequence_id,
                    record.preset_id,
                    record.reframe_mode,
                    record.source_timeline_revision,
                    _json_value(
                        record.baseline_timeline.model_dump(
                            mode="json", exclude_computed_fields=True
                        )
                    )
                    if record.baseline_timeline is not None
                    else None,
                    _json_value(
                        [
                            item.model_dump(mode="json", exclude_computed_fields=True)
                            for item in record.baseline_audio_buses
                        ]
                    ),
                    _json_value(
                        [
                            item.model_dump(mode="json", exclude_computed_fields=True)
                            for item in record.baseline_audio_effects
                        ]
                    ),
                    _json_value(
                        [
                            item.model_dump(mode="json", exclude_computed_fields=True)
                            for item in record.baseline_subtitle_placements
                        ]
                    ),
                    record.created_at,
                    record.updated_at,
                ),
            )
            self._touch_project(connection)
        return self.get_sequence_variant(record.sequence_id)

    def get_sequence_variant(self, sequence_id: str) -> SequenceVariantRecord:
        row = self._fetchone(
            "SELECT * FROM sequence_variant WHERE sequence_id=?",
            (sequence_id,),
        )
        if row is None:
            raise KeyError(sequence_id)
        return self._variant_from_row(row)

    def list_sequence_variants(
        self,
        source_sequence_id: str | None = None,
        *,
        include_archived: bool = False,
    ) -> list[SequenceVariantRecord]:
        conditions: list[str] = []
        values: list[object] = []
        if source_sequence_id is not None:
            self.get_sequence(source_sequence_id)
            conditions.append("variant.source_sequence_id=?")
            values.append(source_sequence_id)
        if not include_archived:
            conditions.append("sequence.archived=0")
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        rows = self._fetchall(
            """SELECT variant.* FROM sequence_variant AS variant
               JOIN sequence ON sequence.id=variant.sequence_id"""
            + where
            + " ORDER BY variant.created_at, variant.sequence_id",
            tuple(values),
        )
        return [self._variant_from_row(row) for row in rows]

    @staticmethod
    def _variant_from_row(row: sqlite3.Row) -> SequenceVariantRecord:
        return SequenceVariantRecord(
            sequence_id=row["sequence_id"],
            source_sequence_id=row["source_sequence_id"],
            preset_id=row["preset_id"],
            reframe_mode=row["reframe_mode"],
            source_timeline_revision=row["source_timeline_revision"],
            baseline_timeline=(
                TimelineState.model_validate_json(row["baseline_timeline_json"])
                if row["baseline_timeline_json"]
                else None
            ),
            baseline_audio_buses=TypeAdapter(list[AudioBus]).validate_python(
                json.loads(row["baseline_audio_buses_json"])
            ),
            baseline_audio_effects=TypeAdapter(list[AudioEffect]).validate_python(
                json.loads(row["baseline_audio_effects_json"])
            ),
            baseline_subtitle_placements=TypeAdapter(
                list[SubtitlePlacement]
            ).validate_python(json.loads(row["baseline_subtitle_placements_json"])),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _insert_sequence_record(
        connection: sqlite3.Connection,
        sequence: Sequence,
    ) -> None:
        profile = sequence.profile
        connection.execute(
            """INSERT INTO sequence(
                id, project_id, name, kind, position, width, height,
                fps_numerator, fps_denominator, color_mode, bit_depth,
                audio_sample_rate, audio_channels, profile_confirmed,
                in_frame, out_frame, archived, timeline_revision, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                sequence.id,
                sequence.project_id,
                sequence.name,
                sequence.kind.value,
                sequence.position,
                profile.width,
                profile.height,
                profile.fps_numerator,
                profile.fps_denominator,
                profile.color_mode.value,
                profile.bit_depth,
                profile.audio_sample_rate,
                profile.audio_channels,
                int(sequence.profile_confirmed),
                sequence.in_out.in_frame if sequence.in_out else None,
                sequence.in_out.out_frame if sequence.in_out else None,
                int(sequence.archived),
                sequence.timeline_revision,
                sequence.created_at,
            ),
        )

    @staticmethod
    def update_sequence_record(
        connection: sqlite3.Connection,
        sequence: Sequence,
    ) -> int:
        profile = sequence.profile
        cursor = connection.execute(
            """UPDATE sequence SET
                name=?, kind=?, position=?, width=?, height=?, fps_numerator=?,
                fps_denominator=?, color_mode=?, bit_depth=?, audio_sample_rate=?,
                audio_channels=?, profile_confirmed=?, in_frame=?, out_frame=?,
                archived=?, timeline_revision=timeline_revision+1
               WHERE id=? AND timeline_revision=?""",
            (
                sequence.name,
                sequence.kind.value,
                sequence.position,
                profile.width,
                profile.height,
                profile.fps_numerator,
                profile.fps_denominator,
                profile.color_mode.value,
                profile.bit_depth,
                profile.audio_sample_rate,
                profile.audio_channels,
                int(sequence.profile_confirmed),
                sequence.in_out.in_frame if sequence.in_out else None,
                sequence.in_out.out_frame if sequence.in_out else None,
                int(sequence.archived),
                sequence.id,
                sequence.timeline_revision,
            ),
        )
        if cursor.rowcount != 1:
            row = connection.execute(
                "SELECT timeline_revision FROM sequence WHERE id=?",
                (sequence.id,),
            ).fetchone()
            if row is None:
                raise KeyError(sequence.id)
            raise TimelineRevisionConflict(
                sequence.id,
                expected=sequence.timeline_revision,
                actual=int(row["timeline_revision"]),
            )
        return sequence.timeline_revision + 1

    @staticmethod
    def _sequence_from_row(
        row: sqlite3.Row,
        preset_json: str | None = None,
    ) -> Sequence:
        return Sequence(
            id=row["id"],
            project_id=row["project_id"],
            name=row["name"],
            kind=SequenceKind(row["kind"]),
            position=row["position"],
            export_preset=(
                ExportPreset.model_validate_json(preset_json) if preset_json else None
            ),
            in_out=(
                SequenceInOut(in_frame=row["in_frame"], out_frame=row["out_frame"])
                if row["in_frame"] is not None and row["out_frame"] is not None
                else None
            ),
            archived=bool(row["archived"]),
            timeline_revision=int(row["timeline_revision"]),
            profile_confirmed=bool(row["profile_confirmed"]),
            profile=ProjectProfile(
                width=row["width"],
                height=row["height"],
                fps_numerator=row["fps_numerator"],
                fps_denominator=row["fps_denominator"],
                color_mode=ColorMode(row["color_mode"]),
                bit_depth=row["bit_depth"],
                audio_sample_rate=row["audio_sample_rate"],
                audio_channels=row["audio_channels"],
            ),
            created_at=row["created_at"],
        )

    @staticmethod
    def store_sequence_export_preset(
        connection: sqlite3.Connection,
        sequence: Sequence,
    ) -> None:
        if sequence.export_preset is None:
            connection.execute(
                "DELETE FROM sequence_export_setting WHERE sequence_id=?",
                (sequence.id,),
            )
            return
        connection.execute(
            """INSERT INTO sequence_export_setting(sequence_id, preset_json)
               VALUES (?, ?)
               ON CONFLICT(sequence_id) DO UPDATE SET preset_json=excluded.preset_json""",
            (sequence.id, _model_json(sequence.export_preset)),
        )
