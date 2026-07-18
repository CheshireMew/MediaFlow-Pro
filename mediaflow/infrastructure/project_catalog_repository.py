from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from mediaflow.domain.audio import AudioBus
from mediaflow.domain.enums import (
    AssetKind,
    AssetOrigin,
    AssetStatus,
    SequenceKind,
    TaskStatus,
    TrackKind,
    WorkflowStage,
    WorkflowStatus,
)
from mediaflow.domain.exports import ExportPreset
from mediaflow.domain.model_base import now_ms
from mediaflow.domain.project import Asset, MediaMetadata, Project, ProjectProfile, Sequence
from mediaflow.domain.timeline import Track
from mediaflow.domain.workflows import WorkflowRun

from .file_fingerprint import fingerprint_file, fingerprint_matches
from .project_serialization import json_value as _json
from .project_serialization import model_json as _model_json


class ProjectCatalogRepository:
    def get_project(self) -> Project:
        row = self._fetchone("SELECT * FROM project LIMIT 1")
        if row is None:
            raise RuntimeError("Project record is missing")
        return Project(
            id=row["id"],
            name=row["name"],
            root_path=str(self.project_dir),
            main_sequence_id=row["main_sequence_id"],
            workflow_auto_continue=(
                None if row["workflow_auto_continue"] < 0 else bool(row["workflow_auto_continue"])
            ),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def set_workflow_auto_continue(self, value: bool | None) -> Project:
        stored = -1 if value is None else int(value)
        with self.transaction() as connection:
            connection.execute(
                "UPDATE project SET workflow_auto_continue=?, updated_at=?",
                (stored, now_ms()),
            )
        return self.get_project()

    def save_workflow_run(self, run: WorkflowRun) -> WorkflowRun:
        project = self.get_project()
        if run.project_id != project.id:
            raise ValueError("Workflow run belongs to another project")
        self.get_sequence(run.sequence_id)
        if any(self.get_asset(asset_id).project_id != project.id for asset_id in run.asset_ids):
            raise ValueError("Workflow run contains an asset from another project")
        updated = run.model_copy(update={"updated_at": now_ms()})
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO workflow_run(
                    id, project_id, sequence_id, asset_ids_json, stage, status,
                    auto_continue, payload_json, message_code, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    sequence_id=excluded.sequence_id,
                    asset_ids_json=excluded.asset_ids_json,
                    stage=excluded.stage,
                    status=excluded.status,
                    auto_continue=excluded.auto_continue,
                    payload_json=excluded.payload_json,
                    message_code=excluded.message_code,
                    updated_at=excluded.updated_at""",
                (
                    updated.id,
                    updated.project_id,
                    updated.sequence_id,
                    _json(updated.asset_ids),
                    updated.stage.value,
                    updated.status.value,
                    int(updated.auto_continue),
                    _model_json(updated.payload),
                    updated.message_code,
                    updated.created_at,
                    updated.updated_at,
                ),
            )
            self._touch_project(connection)
        return self.get_workflow_run(updated.id)

    def get_workflow_run(self, run_id: str) -> WorkflowRun:
        row = self._fetchone("SELECT * FROM workflow_run WHERE id=?", (run_id,))
        if row is None:
            raise KeyError(run_id)
        return self._workflow_run_from_row(row)

    def list_workflow_runs(self, *, active_only: bool = False) -> list[WorkflowRun]:
        sql = "SELECT * FROM workflow_run"
        parameters: tuple = ()
        if active_only:
            sql += " WHERE status NOT IN ('completed', 'cancelled')"
        sql += " ORDER BY updated_at DESC, id"
        return [self._workflow_run_from_row(row) for row in self._fetchall(sql, parameters)]

    @staticmethod
    def _workflow_run_from_row(row: sqlite3.Row) -> WorkflowRun:
        return WorkflowRun(
            id=row["id"],
            project_id=row["project_id"],
            sequence_id=row["sequence_id"],
            asset_ids=json.loads(row["asset_ids_json"]),
            stage=WorkflowStage(row["stage"]),
            status=WorkflowStatus(row["status"]),
            auto_continue=bool(row["auto_continue"]),
            payload=json.loads(row["payload_json"]),
            message_code=row["message_code"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

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
        project = self.get_project()
        main_profile = self.get_sequence(project.main_sequence_id).profile
        position = len(self.list_sequences())
        sequence = Sequence(
            project_id=project.id,
            name=name,
            kind=SequenceKind.SHORT,
            position=position,
            profile=profile or main_profile.model_copy(update={"width": 1080, "height": 1920}),
            profile_confirmed=True,
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
            self._insert_sequence(connection, sequence)
            for bus in (master, dialogue, music, effects):
                self._insert_audio_bus(connection, bus)
            self._insert_track(
                connection,
                Track(
                    sequence_id=sequence.id,
                    name="视频 1",
                    kind=TrackKind.VIDEO,
                    position=0,
                    audio_bus_id=dialogue.id,
                ),
            )
            self._insert_track(
                connection,
                Track(
                    sequence_id=sequence.id,
                    name="音频 1",
                    kind=TrackKind.AUDIO,
                    position=1,
                    audio_bus_id=dialogue.id,
                ),
            )
            self._insert_track(
                connection,
                Track(sequence_id=sequence.id, name="字幕 1", kind=TrackKind.SUBTITLE, position=2),
            )
            self._touch_project(connection)
        return sequence

    def archive_short_sequence(self, sequence_id: str) -> Sequence:
        sequence = self.get_sequence(sequence_id)
        project = self.get_project()
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

    def add_asset(self, asset: Asset) -> Asset:
        project = self.get_project()
        if asset.project_id != project.id:
            raise ValueError("Asset belongs to a different project")
        stored_path = self._stored_path(asset.path, managed=asset.managed)
        proxy_path = self._stored_optional_path(asset.proxy_path)
        sdr_preview_proxy_path = self._stored_optional_path(asset.sdr_preview_proxy_path)
        waveform_path = self._stored_optional_path(asset.waveform_path)
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO asset(
                    id, project_id, name, kind, origin, path, managed, proxy_path,
                    sdr_preview_proxy_path, waveform_path, status, fingerprint_json,
                    metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    asset.id,
                    asset.project_id,
                    asset.name,
                    asset.kind.value,
                    asset.origin.value,
                    stored_path,
                    int(asset.managed),
                    proxy_path,
                    sdr_preview_proxy_path,
                    waveform_path,
                    asset.status.value,
                    _model_json(asset.fingerprint) if asset.fingerprint else None,
                    _model_json(asset.metadata),
                    asset.created_at,
                ),
            )
            self._touch_project(connection)
        return self.get_asset(asset.id)

    def import_external_asset(self, path: str | Path, kind: AssetKind) -> Asset:
        source = Path(path).resolve(strict=True)
        project = self.get_project()
        return self.add_asset(
            Asset(
                project_id=project.id,
                name=source.name,
                kind=kind,
                origin=AssetOrigin.EXTERNAL,
                path=str(source),
                managed=False,
                fingerprint=fingerprint_file(source),
            )
        )

    def get_asset(self, asset_id: str) -> Asset:
        row = self._fetchone("SELECT * FROM asset WHERE id=?", (asset_id,))
        if row is None:
            raise KeyError(asset_id)
        return self._asset_from_row(row)

    def list_assets(self) -> list[Asset]:
        rows = self._fetchall("SELECT * FROM asset ORDER BY created_at, name")
        return [self._asset_from_row(row) for row in rows]

    def update_asset(self, asset: Asset) -> Asset:
        current = self.get_asset(asset.id)
        if current.project_id != asset.project_id:
            raise ValueError("Asset project cannot change")
        stored_path = self._stored_path(asset.path, managed=asset.managed)
        with self.transaction() as connection:
            connection.execute(
                """UPDATE asset SET
                    name=?, kind=?, origin=?, path=?, managed=?, proxy_path=?,
                    sdr_preview_proxy_path=?, waveform_path=?, status=?,
                    fingerprint_json=?, metadata_json=?
                WHERE id=?""",
                (
                    asset.name,
                    asset.kind.value,
                    asset.origin.value,
                    stored_path,
                    int(asset.managed),
                    self._stored_optional_path(asset.proxy_path),
                    self._stored_optional_path(asset.sdr_preview_proxy_path),
                    self._stored_optional_path(asset.waveform_path),
                    asset.status.value,
                    _model_json(asset.fingerprint) if asset.fingerprint else None,
                    _model_json(asset.metadata),
                    asset.id,
                ),
            )
            self._touch_project(connection)
        return self.get_asset(asset.id)

    def refresh_asset_status(self, asset_id: str) -> Asset:
        asset = self.get_asset(asset_id)
        source = self.resolve_asset_path(asset)
        if not source.is_file():
            return self.update_asset(asset.model_copy(update={"status": AssetStatus.OFFLINE}))
        if asset.fingerprint is None:
            return self.update_asset(
                asset.model_copy(
                    update={
                        "status": AssetStatus.ONLINE,
                        "fingerprint": fingerprint_file(source),
                    }
                )
            )
        if fingerprint_matches(source, asset.fingerprint):
            if asset.status != AssetStatus.ONLINE:
                return self.update_asset(asset.model_copy(update={"status": AssetStatus.ONLINE}))
            return asset
        return self.update_asset(
            asset.model_copy(
                update={
                    "status": AssetStatus.ONLINE,
                    "fingerprint": fingerprint_file(source),
                    "proxy_path": None,
                    "sdr_preview_proxy_path": None,
                    "waveform_path": None,
                    "metadata": MediaMetadata(),
                }
            )
        )

    def relink_asset(
        self,
        asset_id: str,
        replacement: str | Path,
        *,
        allow_different_content: bool = False,
    ) -> Asset:
        asset = self.get_asset(asset_id)
        if asset.managed:
            raise ValueError("Managed project assets cannot be relinked as external files")
        candidate = Path(replacement).resolve(strict=True)
        matches = asset.fingerprint is not None and fingerprint_matches(candidate, asset.fingerprint)
        if not matches and not allow_different_content:
            raise ValueError("Replacement content does not match the missing asset")
        replacement_fingerprint = fingerprint_file(candidate)
        changed = (
            asset.fingerprint is None or replacement_fingerprint.edge_sha256 != asset.fingerprint.edge_sha256
        )
        return self.update_asset(
            asset.model_copy(
                update={
                    "name": candidate.name,
                    "path": str(candidate),
                    "status": AssetStatus.ONLINE,
                    "fingerprint": replacement_fingerprint,
                    "proxy_path": None if changed else asset.proxy_path,
                    "sdr_preview_proxy_path": (None if changed else asset.sdr_preview_proxy_path),
                    "waveform_path": None if changed else asset.waveform_path,
                    "metadata": MediaMetadata() if changed else asset.metadata,
                }
            )
        )

    def resolve_asset_path(self, asset: Asset) -> Path:
        path = Path(asset.path)
        return (self.project_dir / path).resolve() if asset.managed else path.resolve()
