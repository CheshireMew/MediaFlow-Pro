from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, cast

from mediaflow.domain.enums import AssetKind
from mediaflow.domain.model_base import now_ms
from mediaflow.domain.voiceover import (
    VoiceoverCue,
    VoiceoverCueStatus,
    VoiceoverLatencyCalibration,
    VoiceoverLatencyMethod,
    VoiceoverTake,
)

from .project_repository_component import ProjectRepositoryComponent

if TYPE_CHECKING:
    from .asset_catalog_repository import AssetCatalogRepository
    from .project_database_session import ProjectDatabaseSession
    from .sequence_catalog_repository import SequenceCatalogRepository


class VoiceoverRepository(ProjectRepositoryComponent):
    def __init__(
        self,
        database: ProjectDatabaseSession,
        *,
        sequences: Callable[[], SequenceCatalogRepository],
        assets: Callable[[], AssetCatalogRepository],
    ) -> None:
        super().__init__(database)
        self._sequences = sequences
        self._assets = assets

    def create_cue(self, cue: VoiceoverCue) -> VoiceoverCue:
        if cue.revision != 0 or cue.takes:
            raise ValueError("新旁白提示必须从空的 revision 0 状态开始")
        self._sequences().get_sequence(cue.sequence_id)
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO voiceover_cue(
                       id, sequence_id, start_frame, end_frame, text, speaker, notes,
                       status, selected_take_id, placed_clip_id, archived, revision,
                       created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                self._cue_values(cue),
            )
            self._touch_project(connection)
        return self.get_cue(cue.id)

    def save_cue(self, cue: VoiceoverCue, *, expected_revision: int) -> VoiceoverCue:
        current = self.get_cue(cue.id)
        if current.revision != expected_revision or cue.revision != expected_revision:
            raise RuntimeError("旁白提示已被其它编辑更新，请刷新后重试")
        self._validate_selected_take(cue)
        updated = cue.model_copy(update={"revision": expected_revision + 1, "updated_at": now_ms()})
        with self.transaction() as connection:
            cursor = connection.execute(
                """UPDATE voiceover_cue SET
                       sequence_id=?, start_frame=?, end_frame=?, text=?, speaker=?, notes=?,
                       status=?, selected_take_id=?, placed_clip_id=?, archived=?, revision=?,
                       created_at=?, updated_at=?
                   WHERE id=? AND revision=?""",
                (
                    updated.sequence_id,
                    updated.start_frame,
                    updated.end_frame,
                    updated.text,
                    updated.speaker,
                    updated.notes,
                    updated.status,
                    updated.selected_take_id,
                    updated.placed_clip_id,
                    int(updated.archived),
                    updated.revision,
                    updated.created_at,
                    updated.updated_at,
                    updated.id,
                    expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("旁白提示已被其它编辑更新，请刷新后重试")
            self._touch_project(connection)
        return self.get_cue(cue.id)

    def restore_cue(self, cue: VoiceoverCue) -> VoiceoverCue:
        self.get_cue(cue.id)
        self._validate_selected_take(cue)
        with self.transaction() as connection:
            connection.execute(
                """UPDATE voiceover_cue SET
                       sequence_id=?, start_frame=?, end_frame=?, text=?, speaker=?, notes=?,
                       status=?, selected_take_id=?, placed_clip_id=?, archived=?, revision=?,
                       created_at=?, updated_at=?
                   WHERE id=?""",
                (
                    cue.sequence_id,
                    cue.start_frame,
                    cue.end_frame,
                    cue.text,
                    cue.speaker,
                    cue.notes,
                    cue.status,
                    cue.selected_take_id,
                    cue.placed_clip_id,
                    int(cue.archived),
                    cue.revision,
                    cue.created_at,
                    cue.updated_at,
                    cue.id,
                ),
            )
            self._touch_project(connection)
        return self.get_cue(cue.id)

    def get_cue(self, cue_id: str) -> VoiceoverCue:
        row = self._fetchone("SELECT * FROM voiceover_cue WHERE id=?", (cue_id,))
        if row is None:
            raise KeyError(cue_id)
        return self._cue_from_row(row)

    def list_cues(
        self,
        sequence_id: str,
        *,
        include_archived: bool = False,
    ) -> list[VoiceoverCue]:
        self._sequences().get_sequence(sequence_id)
        rows = self._fetchall(
            "SELECT * FROM voiceover_cue WHERE sequence_id=? "
            + ("" if include_archived else "AND archived=0 ")
            + "ORDER BY start_frame, created_at, id",
            (sequence_id,),
        )
        return [self._cue_from_row(row) for row in rows]

    def create_take(self, take: VoiceoverTake) -> VoiceoverTake:
        cue = self.get_cue(take.cue_id)
        if cue.archived:
            raise ValueError("已归档的旁白提示不能添加 take")
        asset = self._assets().get_asset(take.asset_id)
        if asset.kind != AssetKind.AUDIO:
            raise ValueError("旁白 take 必须引用音频素材")
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO voiceover_take(
                       id, cue_id, asset_id, name, duration_frames, notes,
                       rating, latency_compensation_samples, latency_sample_rate,
                       calibration_device_id, calibration_measured_at, archived, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                self._take_values(take),
            )
            self._touch_project(connection)
        return self.get_take(take.id)

    def save_take(self, take: VoiceoverTake) -> VoiceoverTake:
        current = self.get_take(take.id)
        if current.cue_id != take.cue_id or current.asset_id != take.asset_id:
            raise ValueError("旁白 take 不能更换所属提示或音频素材")
        with self.transaction() as connection:
            connection.execute(
                """UPDATE voiceover_take SET
                       name=?, duration_frames=?, notes=?, rating=?,
                       latency_compensation_samples=?, latency_sample_rate=?,
                       calibration_device_id=?, calibration_measured_at=?,
                       archived=?, created_at=?
                   WHERE id=?""",
                (
                    take.name,
                    take.duration_frames,
                    take.notes,
                    take.rating,
                    take.latency_compensation_samples,
                    take.latency_sample_rate,
                    take.calibration_device_id,
                    take.calibration_measured_at,
                    int(take.archived),
                    take.created_at,
                    take.id,
                ),
            )
            self._touch_project(connection)
        return self.get_take(take.id)

    def get_take(self, take_id: str) -> VoiceoverTake:
        row = self._fetchone("SELECT * FROM voiceover_take WHERE id=?", (take_id,))
        if row is None:
            raise KeyError(take_id)
        return self._take_from_row(row)

    def list_takes(self, cue_id: str, *, include_archived: bool = False) -> list[VoiceoverTake]:
        self.get_cue(cue_id)
        rows = self._fetchall(
            "SELECT * FROM voiceover_take WHERE cue_id=? "
            + ("" if include_archived else "AND archived=0 ")
            + "ORDER BY created_at, id",
            (cue_id,),
        )
        return [self._take_from_row(row) for row in rows]

    def save_latency_calibration(
        self,
        calibration: VoiceoverLatencyCalibration,
    ) -> VoiceoverLatencyCalibration:
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO voiceover_latency_calibration(
                       device_id, device_name, latency_samples, sample_rate,
                       confidence, method, measured_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(device_id) DO UPDATE SET
                       device_name=excluded.device_name,
                       latency_samples=excluded.latency_samples,
                       sample_rate=excluded.sample_rate,
                       confidence=excluded.confidence,
                       method=excluded.method,
                       measured_at=excluded.measured_at""",
                (
                    calibration.device_id,
                    calibration.device_name,
                    calibration.latency_samples,
                    calibration.sample_rate,
                    calibration.confidence,
                    calibration.method,
                    calibration.measured_at,
                ),
            )
            self._touch_project(connection)
        return self.get_latency_calibration(calibration.device_id)

    def get_latency_calibration(self, device_id: str) -> VoiceoverLatencyCalibration:
        row = self._fetchone(
            "SELECT * FROM voiceover_latency_calibration WHERE device_id=?",
            (device_id,),
        )
        if row is None:
            raise KeyError(device_id)
        return self._calibration_from_row(row)

    def list_latency_calibrations(self) -> list[VoiceoverLatencyCalibration]:
        return [
            self._calibration_from_row(row)
            for row in self._fetchall(
                "SELECT * FROM voiceover_latency_calibration ORDER BY device_name, device_id"
            )
        ]

    def _cue_from_row(self, row) -> VoiceoverCue:
        cue_id = str(row["id"])
        takes = [
            self._take_from_row(item)
            for item in self._fetchall(
                "SELECT * FROM voiceover_take WHERE cue_id=? ORDER BY created_at, id",
                (cue_id,),
            )
        ]
        return VoiceoverCue(
            id=cue_id,
            sequence_id=str(row["sequence_id"]),
            start_frame=int(row["start_frame"]),
            end_frame=int(row["end_frame"]),
            text=str(row["text"]),
            speaker=str(row["speaker"]),
            notes=str(row["notes"]),
            status=cast(VoiceoverCueStatus, str(row["status"])),
            selected_take_id=(str(row["selected_take_id"]) if row["selected_take_id"] else None),
            placed_clip_id=(str(row["placed_clip_id"]) if row["placed_clip_id"] else None),
            archived=bool(row["archived"]),
            revision=int(row["revision"]),
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
            takes=takes,
        )

    @staticmethod
    def _take_from_row(row) -> VoiceoverTake:
        return VoiceoverTake(
            id=str(row["id"]),
            cue_id=str(row["cue_id"]),
            asset_id=str(row["asset_id"]),
            name=str(row["name"]),
            duration_frames=int(row["duration_frames"]),
            notes=str(row["notes"]),
            rating=int(row["rating"]),
            latency_compensation_samples=int(row["latency_compensation_samples"]),
            latency_sample_rate=int(row["latency_sample_rate"]),
            calibration_device_id=(
                str(row["calibration_device_id"])
                if row["calibration_device_id"]
                else None
            ),
            calibration_measured_at=(
                int(row["calibration_measured_at"])
                if row["calibration_measured_at"] is not None
                else None
            ),
            archived=bool(row["archived"]),
            created_at=int(row["created_at"]),
        )

    def _validate_selected_take(self, cue: VoiceoverCue) -> None:
        self._sequences().get_sequence(cue.sequence_id)
        if cue.selected_take_id is None:
            return
        selected = self.get_take(cue.selected_take_id)
        if selected.cue_id != cue.id or selected.archived:
            raise ValueError("选中的旁白 take 不存在或已归档")

    @staticmethod
    def _calibration_from_row(row) -> VoiceoverLatencyCalibration:
        return VoiceoverLatencyCalibration(
            device_id=str(row["device_id"]),
            device_name=str(row["device_name"]),
            latency_samples=int(row["latency_samples"]),
            sample_rate=int(row["sample_rate"]),
            confidence=float(row["confidence"]),
            method=cast(VoiceoverLatencyMethod, str(row["method"])),
            measured_at=int(row["measured_at"]),
        )

    @staticmethod
    def _cue_values(cue: VoiceoverCue) -> tuple:
        return (
            cue.id,
            cue.sequence_id,
            cue.start_frame,
            cue.end_frame,
            cue.text,
            cue.speaker,
            cue.notes,
            cue.status,
            cue.selected_take_id,
            cue.placed_clip_id,
            int(cue.archived),
            cue.revision,
            cue.created_at,
            cue.updated_at,
        )

    @staticmethod
    def _take_values(take: VoiceoverTake) -> tuple:
        return (
            take.id,
            take.cue_id,
            take.asset_id,
            take.name,
            take.duration_frames,
            take.notes,
            take.rating,
            take.latency_compensation_samples,
            take.latency_sample_rate,
            take.calibration_device_id,
            take.calibration_measured_at,
            int(take.archived),
            take.created_at,
        )
