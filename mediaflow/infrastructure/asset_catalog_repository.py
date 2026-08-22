from __future__ import annotations

import sqlite3
from pathlib import Path

from mediaflow.domain.enums import (
    AssetKind,
    AssetOrigin,
    AssetStatus,
)
from mediaflow.domain.project import (
    Asset,
    AssetBin,
    AssetFingerprint,
    MediaMetadata,
)
from mediaflow.waveform_cache import waveform_cache_is_current

from .file_fingerprint import fingerprint_file, fingerprint_matches
from .project_repository_component import ProjectRepositoryComponent
from .project_serialization import model_json as _model_json


class AssetCatalogRepository(ProjectRepositoryComponent):
    def resolve_existing_file(self, path: str | Path) -> Path:
        source = Path(path).resolve(strict=True)
        if not source.is_file():
            raise FileNotFoundError(source)
        return source

    def is_regular_file(self, path: str | Path) -> bool:
        return Path(path).is_file()

    def files_by_size(
        self,
        directory: str | Path,
        expected_sizes: set[int],
    ) -> dict[int, list[Path]]:
        root = Path(directory).resolve(strict=True)
        if not root.is_dir():
            raise NotADirectoryError(root)
        candidates: dict[int, list[Path]] = {size: [] for size in expected_sizes}
        for path in root.rglob("*"):
            try:
                if path.is_file() and path.stat().st_size in expected_sizes:
                    candidates[path.stat().st_size].append(path.resolve())
            except OSError:
                continue
        return candidates

    def add_asset(self, asset: Asset) -> Asset:
        project = self._relations.projects.get_project()
        if asset.project_id != project.id:
            raise ValueError("Asset belongs to a different project")
        stored_path = self._store_asset_path(asset.path, managed=asset.managed)
        proxy_path = self.store_optional_path(asset.proxy_path)
        sdr_preview_proxy_path = self.store_optional_path(asset.sdr_preview_proxy_path)
        waveform_path = self.store_optional_path(asset.waveform_path)
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
        project = self._relations.projects.get_project()
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
        project = self._relations.projects.get_project()
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
        project = self._relations.projects.get_project()
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
                    self.store_optional_path(asset.proxy_path),
                    self.store_optional_path(asset.sdr_preview_proxy_path),
                    self.store_optional_path(asset.waveform_path),
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
                    self.store_optional_path(proxy_path),
                    self.store_optional_path(sdr_preview_proxy_path),
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
                    self.store_optional_path(waveform_path),
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
            waveform_path = asset.waveform_path
            if waveform_path:
                candidate = Path(waveform_path)
                resolved_waveform = (
                    candidate.resolve()
                    if candidate.is_absolute()
                    else (self.project_dir / candidate).resolve()
                )
                if not waveform_cache_is_current(resolved_waveform):
                    waveform_path = None
            if asset.status != AssetStatus.ONLINE or waveform_path != asset.waveform_path:
                return self.update_asset(
                    asset.model_copy(
                        update={
                            "status": AssetStatus.ONLINE,
                            "waveform_path": waveform_path,
                        }
                    )
                )
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

    def store_optional_path(self, path: str | Path | None) -> str | None:
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
