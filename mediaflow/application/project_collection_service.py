from __future__ import annotations

from pathlib import Path

from mediaflow.application.edit_history import ProjectEditHistory
from mediaflow.application.ports import AssetServiceDocuments
from mediaflow.application.project_collection_storage import ProjectCollectionStorage
from mediaflow.domain.collaboration import (
    ProjectChange,
    ProjectChangeSet,
    ProjectEditAction,
    ProjectEditCommand,
)
from mediaflow.domain.enums import AssetStatus
from mediaflow.domain.project import Asset
from mediaflow.domain.project_collection import (
    ProjectCollectionCandidate,
    ProjectCollectionItem,
    ProjectCollectionPreview,
    ProjectCollectionRecord,
    ProjectCollectionResult,
)


class ProjectCollectionService:
    def __init__(
        self,
        repository: AssetServiceDocuments,
        storage: ProjectCollectionStorage,
        history: ProjectEditHistory,
    ) -> None:
        self.repository = repository
        self.storage = storage
        self.history = history

    def preview(self, asset_ids: list[str] | None = None) -> ProjectCollectionPreview:
        selected = self._selected_assets(asset_ids)
        candidates: list[ProjectCollectionCandidate] = []
        for asset in selected:
            source = self.repository.assets.resolve_asset_path(asset)
            collectable = not asset.managed and asset.status == AssetStatus.ONLINE
            reason = ""
            size = 0
            if asset.managed:
                reason = "素材已经位于项目目录内"
            elif asset.status != AssetStatus.ONLINE:
                reason = "素材离线，必须先重新定位"
            elif not source.exists():
                collectable = False
                reason = "素材路径不存在"
            else:
                try:
                    size = self.storage.content_size(source)
                except (OSError, ValueError) as error:
                    collectable = False
                    reason = str(error)
            candidates.append(
                ProjectCollectionCandidate(
                    asset_id=asset.id,
                    name=asset.name,
                    source_path=str(source),
                    managed=asset.managed,
                    status=asset.status,
                    size_bytes=size,
                    collectable=collectable,
                    reason=reason,
                )
            )
        return ProjectCollectionPreview(
            candidates=candidates,
            collectable_count=sum(item.collectable for item in candidates),
            total_bytes=sum(item.size_bytes for item in candidates if item.collectable),
        )

    def collect(self, asset_ids: list[str] | None = None) -> ProjectCollectionResult:
        preview = self.preview(asset_ids)
        blocked = [item for item in preview.candidates if not item.collectable and not item.managed]
        if blocked:
            names = "、".join(item.name for item in blocked)
            raise ValueError(f"以下素材无法归集：{names}")
        candidates = [item for item in preview.candidates if item.collectable]
        if not candidates:
            raise ValueError("项目中没有需要归集的外部素材")
        record = ProjectCollectionRecord(
            items=[
                ProjectCollectionItem(
                    asset_id=item.asset_id,
                    original_path=self.repository.assets.get_asset(item.asset_id).path,
                    original_managed=False,
                    collected_path="pending",
                    size_bytes=item.size_bytes,
                    sha256="0" * 64,
                )
                for item in candidates
            ]
        )
        copies = self.storage.collect(
            record.id,
            [(item.asset_id, Path(item.source_path)) for item in candidates],
        )
        by_id = {item.asset_id: item for item in copies}
        record = record.model_copy(
            update={
                "items": [
                    item.model_copy(
                        update={
                            "collected_path": by_id[item.asset_id]
                            .path.relative_to(self.repository.project_dir)
                            .as_posix(),
                            "size_bytes": by_id[item.asset_id].size_bytes,
                            "sha256": by_id[item.asset_id].sha256,
                        }
                    )
                    for item in record.items
                ]
            }
        )
        try:
            with self.repository.transaction():
                self.repository.assets.save_project_collection(record)
                assets = self._apply_state(record, using_collected=True)
        except BaseException:
            self.storage.archive_uncommitted(record.id)
            raise
        self._push_history(record, from_collected=False, to_collected=True, created=True)
        return ProjectCollectionResult(
            record=record,
            assets=assets,
            using_collected_files=True,
        )

    def list_records(self) -> list[ProjectCollectionRecord]:
        return self.repository.assets.list_project_collections()

    def set_state(
        self,
        collection_id: str,
        *,
        using_collected: bool,
    ) -> ProjectCollectionResult:
        record = self.repository.assets.get_project_collection(collection_id)
        before = self._uses_collected(record)
        if before == using_collected:
            return ProjectCollectionResult(
                record=record,
                assets=[self.repository.assets.get_asset(item.asset_id) for item in record.items],
                using_collected_files=before,
            )
        with self.repository.transaction():
            assets = self._apply_state(record, using_collected=using_collected)
        self._push_history(
            record,
            from_collected=before,
            to_collected=using_collected,
            created=False,
        )
        return ProjectCollectionResult(
            record=record,
            assets=assets,
            using_collected_files=using_collected,
        )

    def apply_history_action(self, action: ProjectEditAction) -> None:
        collection_id = str(action.payload.get("collection_id") or "")
        using_collected = bool(action.payload.get("using_collected"))
        record = self.repository.assets.get_project_collection(collection_id)
        self._apply_state(record, using_collected=using_collected)

    def _apply_state(
        self,
        record: ProjectCollectionRecord,
        *,
        using_collected: bool,
    ) -> list[Asset]:
        updated: list[Asset] = []
        for item in record.items:
            asset = self.repository.assets.get_asset(item.asset_id)
            if using_collected:
                path = self._project_path(item.collected_path)
                if not self.storage.verify(path, item.sha256):
                    raise RuntimeError(f"归集素材校验失败或已经丢失：{asset.name}")
                managed = True
                status = AssetStatus.ONLINE
            else:
                path = self._project_path(item.original_path) if item.original_managed else Path(
                    item.original_path
                )
                managed = item.original_managed
                status = AssetStatus.ONLINE if path.exists() else AssetStatus.OFFLINE
            updated.append(
                self.repository.assets.update_asset(
                    asset.model_copy(
                        update={
                            "path": str(path),
                            "managed": managed,
                            "status": status,
                        }
                    )
                )
            )
        return updated

    def _uses_collected(self, record: ProjectCollectionRecord) -> bool:
        values = []
        for item in record.items:
            asset = self.repository.assets.get_asset(item.asset_id)
            values.append(
                asset.managed
                and self.repository.assets.resolve_asset_path(asset)
                == self._project_path(item.collected_path)
            )
        if len(set(values)) != 1:
            raise RuntimeError("归集记录中的素材处于混合状态，无法整体切换")
        return values[0]

    def _push_history(
        self,
        record: ProjectCollectionRecord,
        *,
        from_collected: bool,
        to_collected: bool,
        created: bool,
    ) -> None:
        def action(value: bool) -> ProjectEditAction:
            return ProjectEditAction(
                kind="asset.collection-state",
                payload={"collection_id": record.id, "using_collected": value},
            )
        changes = [
            ProjectChange(
                path=f"/assets/{item.asset_id}/path",
                action="update",
                value=item.collected_path if to_collected else item.original_path,
            )
            for item in record.items
        ]
        if created:
            changes.append(
                ProjectChange(
                    path=f"/project/collections/{record.id}",
                    action="create",
                    value=record.model_dump(mode="json"),
                )
            )
        self.history.push(
            ProjectEditCommand(
                label="归集项目素材" if to_collected else "恢复素材原路径",
                undo_actions=[action(from_collected)],
                redo_actions=[action(to_collected)],
            ),
            ProjectChangeSet(changes=changes),
        )

    def _selected_assets(self, asset_ids: list[str] | None) -> list[Asset]:
        if asset_ids is None:
            return self.repository.assets.list_assets()
        selected_ids = list(dict.fromkeys(asset_ids))
        if not selected_ids:
            raise ValueError("asset_ids cannot be empty when provided")
        return [self.repository.assets.get_asset(asset_id) for asset_id in selected_ids]

    def _project_path(self, value: str) -> Path:
        path = Path(value)
        return path.resolve() if path.is_absolute() else (self.repository.project_dir / path).resolve()
