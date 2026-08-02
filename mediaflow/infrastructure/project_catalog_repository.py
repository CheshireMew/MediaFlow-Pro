from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from mediaflow.domain.audio import AudioBus
from mediaflow.domain.enums import (
    AssetKind,
    AssetOrigin,
    AssetStatus,
    ColorMode,
    SequenceKind,
    TaskStatus,
    WorkflowStage,
    WorkflowStatus,
)
from mediaflow.domain.exports import ExportPreset
from mediaflow.domain.model_base import now_ms
from mediaflow.domain.project import (
    Asset,
    AssetBin,
    AssetFingerprint,
    MediaMetadata,
    Project,
    ProjectProfile,
    Sequence,
    SequenceInOut,
)
from mediaflow.domain.timeline import TimelineRevisionConflict
from mediaflow.domain.workflows import WorkflowRun

from .file_fingerprint import fingerprint_file, fingerprint_matches
from .project_repository_component import ProjectRepositoryComponent
from .project_serialization import json_value as _json
from .project_serialization import model_json as _model_json


class ProjectCatalogRepository(ProjectRepositoryComponent):
    def get_project(self) -> Project:
        row = self._fetchone("SELECT * FROM project LIMIT 1")
        if row is None:
            raise RuntimeError("Project record is missing")
        return Project(
            id=row["id"],
            name=row["name"],
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
                "UPDATE project SET workflow_auto_continue=?",
                (stored,),
            )
            self._touch_project(connection)
        return self.get_project()

    def save_workflow_run(self, run: WorkflowRun) -> WorkflowRun:
        project = self.get_project()
        if run.project_id != project.id:
            raise ValueError("Workflow run belongs to another project")
        self.get_sequence(run.sequence_id)
        if any(self.get_asset(asset_id).project_id != project.id for asset_id in run.asset_ids):
            raise ValueError("Workflow run contains an asset from another project")
        with self.transaction() as connection:
            latest_row = connection.execute(
                "SELECT MAX(updated_at) AS updated_at FROM workflow_run"
            ).fetchone()
            latest_updated_at = (
                int(latest_row["updated_at"])
                if latest_row is not None and latest_row["updated_at"] is not None
                else -1
            )
            updated = run.model_copy(
                update={"updated_at": max(now_ms(), latest_updated_at + 1)}
            )
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
            self._insert_sequence_record(connection, sequence)
            for bus in (master, dialogue, music, effects):
                self._owner.audio._insert_bus_record(connection, bus)
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
        stored_path = self._store_asset_path(asset.path, managed=asset.managed)
        proxy_path = self._store_optional_path(asset.proxy_path)
        sdr_preview_proxy_path = self._store_optional_path(asset.sdr_preview_proxy_path)
        waveform_path = self._store_optional_path(asset.waveform_path)
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO asset(
                    id, project_id, name, kind, origin, path, managed, bin_id, proxy_path,
                    sdr_preview_proxy_path, waveform_path, status, fingerprint_json,
                    metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    asset.id,
                    asset.project_id,
                    asset.name,
                    asset.kind.value,
                    asset.origin.value,
                    stored_path,
                    int(asset.managed),
                    asset.bin_id,
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

    def prepare_external_asset(
        self,
        path: str | Path,
        kind: AssetKind,
    ) -> Asset:
        source = Path(path).resolve(strict=True)
        fingerprint = fingerprint_file(source)
        for existing in self.list_assets():
            if (
                not existing.managed
                and existing.kind == kind
                and self.resolve_asset_path(existing).resolve() == source
                and existing.fingerprint == fingerprint
            ):
                return existing
        project = self.get_project()
        return Asset(
            project_id=project.id,
            name=source.name,
            kind=kind,
            origin=AssetOrigin.EXTERNAL,
            path=str(source),
            managed=False,
            fingerprint=fingerprint,
            metadata=MediaMetadata(
                has_video=kind in {AssetKind.VIDEO, AssetKind.IMAGE},
                has_audio=kind == AssetKind.AUDIO,
            ),
        )

    def commit_external_asset(self, asset: Asset) -> Asset:
        project = self.get_project()
        if (
            asset.project_id != project.id
            or asset.origin != AssetOrigin.EXTERNAL
            or asset.managed
            or asset.fingerprint is None
        ):
            raise ValueError("Prepared external asset is invalid")
        source = Path(asset.path).resolve(strict=True)
        if fingerprint_file(source) != asset.fingerprint:
            raise RuntimeError(
                f"素材在检查完成后发生了变化，请重新导入：{source.name}"
            )
        for existing in self.list_assets():
            if (
                not existing.managed
                and existing.kind == asset.kind
                and self.resolve_asset_path(existing).resolve() == source
                and existing.fingerprint == asset.fingerprint
            ):
                if existing.id != asset.id:
                    raise RuntimeError(
                        f"素材已被另一个操作导入，请重试：{source.name}"
                    )
                return existing
        return self.add_asset(asset)

    def import_external_asset(self, path: str | Path, kind: AssetKind) -> Asset:
        return self.commit_external_asset(
            self.prepare_external_asset(path, kind)
        )

    def get_asset(self, asset_id: str) -> Asset:
        row = self._fetchone("SELECT * FROM asset WHERE id=?", (asset_id,))
        if row is None:
            raise KeyError(asset_id)
        return self._asset_from_row(row)

    def list_assets(self) -> list[Asset]:
        rows = self._fetchall("SELECT * FROM asset ORDER BY created_at, name")
        return [self._asset_from_row(row) for row in rows]

    def list_asset_bins(self) -> list[AssetBin]:
        rows = self._fetchall(
            "SELECT * FROM asset_bin ORDER BY parent_id, position, name, id"
        )
        items = [
            AssetBin(
                id=row["id"],
                project_id=row["project_id"],
                name=row["name"],
                parent_id=row["parent_id"],
                position=row["position"],
            )
            for row in rows
        ]
        children: dict[str | None, list[AssetBin]] = {}
        for item in items:
            children.setdefault(item.parent_id, []).append(item)
        ordered: list[AssetBin] = []

        def append_children(parent_id: str | None) -> None:
            for item in children.get(parent_id, []):
                ordered.append(item)
                append_children(item.id)

        append_children(None)
        return ordered

    def create_asset_bin(self, name: str, parent_id: str | None = None) -> AssetBin:
        project = self.get_project()
        if parent_id is not None and not any(
            item.id == parent_id for item in self.list_asset_bins()
        ):
            raise KeyError(parent_id)
        siblings = [item for item in self.list_asset_bins() if item.parent_id == parent_id]
        asset_bin = AssetBin(
            project_id=project.id,
            name=name,
            parent_id=parent_id,
            position=len(siblings),
        )
        if any(item.name.casefold() == asset_bin.name.casefold() for item in siblings):
            raise ValueError("同一素材文件夹中不能使用重复名称")
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO asset_bin(id, project_id, name, parent_id, position) VALUES (?, ?, ?, ?, ?)",
                (
                    asset_bin.id,
                    asset_bin.project_id,
                    asset_bin.name,
                    asset_bin.parent_id,
                    asset_bin.position,
                ),
            )
            self._touch_project(connection)
        return asset_bin

    def move_assets_to_bin(
        self,
        asset_ids: list[str],
        bin_id: str | None,
    ) -> list[Asset]:
        selected_ids = list(dict.fromkeys(asset_ids))
        if not selected_ids:
            return []
        if bin_id is not None and not any(
            item.id == bin_id for item in self.list_asset_bins()
        ):
            raise KeyError(bin_id)
        for asset_id in selected_ids:
            self.get_asset(asset_id)
        placeholders = ",".join("?" for _ in selected_ids)
        with self.transaction() as connection:
            connection.execute(
                f"UPDATE asset SET bin_id=? WHERE id IN ({placeholders})",
                (bin_id, *selected_ids),
            )
            self._touch_project(connection)
        return [self.get_asset(asset_id) for asset_id in selected_ids]

    def update_asset(self, asset: Asset) -> Asset:
        current = self.get_asset(asset.id)
        if current.project_id != asset.project_id:
            raise ValueError("Asset project cannot change")
        stored_path = self._store_asset_path(asset.path, managed=asset.managed)
        with self.transaction() as connection:
            connection.execute(
                """UPDATE asset SET
                    name=?, kind=?, origin=?, path=?, managed=?, bin_id=?, proxy_path=?,
                    sdr_preview_proxy_path=?, waveform_path=?, status=?,
                    fingerprint_json=?, metadata_json=?
                WHERE id=?""",
                (
                    asset.name,
                    asset.kind.value,
                    asset.origin.value,
                    stored_path,
                    int(asset.managed),
                    asset.bin_id,
                    self._store_optional_path(asset.proxy_path),
                    self._store_optional_path(asset.sdr_preview_proxy_path),
                    self._store_optional_path(asset.waveform_path),
                    asset.status.value,
                    _model_json(asset.fingerprint) if asset.fingerprint else None,
                    _model_json(asset.metadata),
                    asset.id,
                ),
            )
            self._touch_project(connection)
        return self.get_asset(asset.id)

    def set_asset_proxy_paths(
        self,
        asset_id: str,
        *,
        expected_fingerprint: AssetFingerprint | None,
        proxy_path: str | Path,
        sdr_preview_proxy_path: str | Path | None,
    ) -> Asset:
        expected_fingerprint_json = (
            _model_json(expected_fingerprint) if expected_fingerprint is not None else None
        )
        with self.transaction() as connection:
            cursor = connection.execute(
                """UPDATE asset
                SET proxy_path=?, sdr_preview_proxy_path=?
                WHERE id=? AND fingerprint_json IS ?""",
                (
                    self._store_optional_path(proxy_path),
                    self._store_optional_path(sdr_preview_proxy_path),
                    asset_id,
                    expected_fingerprint_json,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("素材在代理生成期间发生了变化，已忽略旧代理")
            self._touch_project(connection)
        return self.get_asset(asset_id)

    def set_asset_waveform_path(
        self,
        asset_id: str,
        *,
        expected_fingerprint: AssetFingerprint | None,
        waveform_path: str | Path,
    ) -> Asset:
        expected_fingerprint_json = (
            _model_json(expected_fingerprint) if expected_fingerprint is not None else None
        )
        with self.transaction() as connection:
            cursor = connection.execute(
                """UPDATE asset
                SET waveform_path=?
                WHERE id=? AND fingerprint_json IS ?""",
                (
                    self._store_optional_path(waveform_path),
                    asset_id,
                    expected_fingerprint_json,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("素材在波形生成期间发生了变化，已忽略旧波形")
            self._touch_project(connection)
        return self.get_asset(asset_id)

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

    def _store_asset_path(self, path: str, *, managed: bool) -> str:
        candidate = Path(path)
        resolved = (
            (self.project_dir / candidate).resolve()
            if managed and not candidate.is_absolute()
            else candidate.resolve()
        )
        if not managed:
            return str(resolved)
        try:
            relative = resolved.relative_to(self.project_dir)
        except ValueError as error:
            raise ValueError("Managed asset must be inside the project directory") from error
        return relative.as_posix()

    def _store_optional_path(self, path: str | Path | None) -> str | None:
        if not path:
            return None
        candidate = Path(path)
        resolved = (
            (self.project_dir / candidate).resolve()
            if not candidate.is_absolute()
            else candidate.resolve()
        )
        try:
            return resolved.relative_to(self.project_dir).as_posix()
        except ValueError:
            return str(resolved)

    @staticmethod
    def _asset_from_row(row: sqlite3.Row) -> Asset:
        fingerprint = (
            AssetFingerprint.model_validate_json(row["fingerprint_json"])
            if row["fingerprint_json"]
            else None
        )
        return Asset(
            id=row["id"],
            project_id=row["project_id"],
            name=row["name"],
            kind=AssetKind(row["kind"]),
            origin=AssetOrigin(row["origin"]),
            path=row["path"],
            managed=bool(row["managed"]),
            bin_id=row["bin_id"],
            proxy_path=row["proxy_path"],
            sdr_preview_proxy_path=row["sdr_preview_proxy_path"],
            waveform_path=row["waveform_path"],
            status=AssetStatus(row["status"]),
            fingerprint=fingerprint,
            metadata=MediaMetadata.model_validate_json(row["metadata_json"]),
            created_at=row["created_at"],
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
    def _update_sequence_record(
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
    def _store_sequence_export_preset(
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
