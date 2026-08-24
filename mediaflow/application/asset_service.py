from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from mediaflow.application.ports import AssetServiceDocuments, FingerprintFile, MediaProbePort
from mediaflow.application.timeline_clock import reframe_timeline_clock
from mediaflow.application.timeline_validator import TimelineValidator
from mediaflow.atomic_file import atomic_write_bytes
from mediaflow.domain.enums import AssetKind, AssetOrigin, AssetStatus
from mediaflow.domain.lut import validate_cube_lut
from mediaflow.domain.project import Asset, MediaMetadata, ProjectProfile


@dataclass(frozen=True, slots=True)
class PreparedAssetRegistration:
    candidate: Asset
    asset: Asset


class AssetService:
    def __init__(
        self,
        repository: AssetServiceDocuments,
        probe: MediaProbePort | None,
        fingerprint_file: FingerprintFile | None = None,
    ):
        self.repository = repository
        self.probe = probe
        self.fingerprint_file = fingerprint_file

    @property
    def _media_probe(self) -> MediaProbePort:
        if self.probe is None:
            raise RuntimeError("This operation requires a media probe")
        return self.probe

    def import_external(
        self,
        path: str | Path,
        *,
        expected_kind: AssetKind | None = None,
        check_cancelled: Callable[[], None] | None = None,
    ) -> Asset:
        prepared = self.prepare_external(
            path,
            expected_kind=expected_kind,
        )
        if check_cancelled is not None:
            check_cancelled()
        with self.repository.transaction():
            return self.commit_prepared(prepared)

    def import_lut(self, path: str | Path) -> Asset:
        source = self.repository.assets.resolve_existing_file(path)
        if source.suffix.casefold() != ".cube":
            raise ValueError("LUT 资源必须是 .cube 文件")
        content = source.read_bytes()
        try:
            decoded = content.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise ValueError("LUT 文件必须使用 UTF-8 文本编码") from error
        validate_cube_lut(decoded)
        digest = hashlib.sha256(content).hexdigest()
        destination = (
            self.repository.project_dir / "resources" / "luts" / f"{digest}.cube"
        ).resolve()
        if destination.is_file():
            if hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
                raise RuntimeError("项目中的同名 LUT 内容与目录哈希不一致")
        else:
            atomic_write_bytes(destination, content)
        for asset in self.repository.assets.list_assets():
            if (
                asset.kind == AssetKind.LUT
                and self.repository.assets.resolve_asset_path(asset).resolve() == destination
            ):
                return asset
        if self.fingerprint_file is None:
            raise RuntimeError("LUT 导入需要文件指纹服务")
        project = self.repository.projects.get_project()
        return self.repository.assets.add_asset(
            Asset(
                project_id=project.id,
                name=source.name,
                kind=AssetKind.LUT,
                origin=AssetOrigin.EXTERNAL,
                path=str(destination),
                managed=True,
                fingerprint=self.fingerprint_file(destination),
                metadata=MediaMetadata(),
            )
        )

    def prepare_external(
        self,
        path: str | Path,
        *,
        expected_kind: AssetKind | None = None,
    ) -> PreparedAssetRegistration:
        source = self.repository.assets.resolve_existing_file(path)
        project = self.repository.projects.get_project()
        main_sequence = self.repository.sequences.get_sequence(project.main_sequence_id)
        probe = self._media_probe.probe(source, timeline_profile=main_sequence.profile)
        if expected_kind is not None and probe.kind != expected_kind:
            raise ValueError(f"素材类型必须是 {expected_kind.value}，实际识别为 {probe.kind.value}")
        candidate = self.repository.assets.prepare_external_asset(
            source,
            probe.kind,
        )
        asset = candidate.model_copy(update={"metadata": probe.metadata})
        return PreparedAssetRegistration(candidate=candidate, asset=asset)

    def prepare_output(
        self,
        path: str | Path,
        origin: AssetOrigin,
    ) -> PreparedAssetRegistration:
        source = self.repository.assets.resolve_existing_file(path)
        try:
            source.relative_to(self.repository.project_dir)
        except ValueError:
            managed = False
        else:
            managed = True
        project = self.repository.projects.get_project()
        main_sequence = self.repository.sequences.get_sequence(project.main_sequence_id)
        probe = self._media_probe.probe(
            source,
            timeline_profile=main_sequence.profile,
        )
        candidate = self.repository.assets.prepare_external_asset(
            source,
            probe.kind,
        )
        asset = candidate.model_copy(
            update={
                "origin": origin,
                "managed": managed,
                "metadata": probe.metadata,
            }
        )
        return PreparedAssetRegistration(candidate=candidate, asset=asset)

    def commit_prepared(self, prepared: PreparedAssetRegistration) -> Asset:
        asset = self.repository.assets.commit_external_asset(
            prepared.candidate
        )
        if asset.id != prepared.asset.id:
            raise RuntimeError(
                f"素材在准备后被另一个操作登记，请重试：{prepared.asset.name}"
            )
        return self.repository.assets.update_asset(
            asset.model_copy(
                update={
                    "origin": prepared.asset.origin,
                    "managed": prepared.asset.managed,
                    "metadata": prepared.asset.metadata,
                }
            )
        )

    def register_output(self, path: str | Path, origin: AssetOrigin) -> Asset:
        return self.commit_prepared(self.prepare_output(path, origin))

    def refresh_all(self) -> list[Asset]:
        project = self.repository.projects.get_project()
        profile = self.repository.sequences.get_sequence(project.main_sequence_id).profile
        refreshed: list[Asset] = []
        for asset in self.repository.assets.list_assets():
            current = self.repository.assets.refresh_asset_status(asset.id)
            source = self.repository.assets.resolve_asset_path(current)
            if current.kind != AssetKind.LUT and self.repository.assets.is_regular_file(source) and (
                current.metadata.duration_frames == 0
                or (current.kind == AssetKind.VIDEO and not current.metadata.width)
            ):
                try:
                    metadata = self._media_probe.probe(source, timeline_profile=profile).metadata
                except Exception:
                    current = self.repository.assets.update_asset(
                        current.model_copy(update={"status": AssetStatus.ERROR})
                    )
                else:
                    current = self.repository.assets.update_asset(
                        current.model_copy(update={"metadata": metadata, "status": AssetStatus.ONLINE})
                    )
            refreshed.append(current)
        return refreshed

    def relink(
        self,
        asset_id: str,
        replacement: str | Path,
        *,
        allow_different_content: bool = False,
    ) -> Asset:
        asset = self.repository.assets.relink_asset(
            asset_id,
            replacement,
            allow_different_content=allow_different_content,
        )
        project = self.repository.projects.get_project()
        profile = self.repository.sequences.get_sequence(project.main_sequence_id).profile
        metadata = self._media_probe.probe(
            self.repository.assets.resolve_asset_path(asset),
            timeline_profile=profile,
        ).metadata
        return self.repository.assets.update_asset(asset.model_copy(update={"metadata": metadata}))

    def relink_offline_from_directory(self, directory: str | Path) -> tuple[list[Asset], list[Asset]]:
        """Relink only exact fingerprint matches found below a user-selected directory."""
        if self.fingerprint_file is None:
            raise RuntimeError("Batch relink requires a fingerprint provider")
        offline = [
            asset
            for asset in self.repository.assets.list_assets()
            if asset.status == AssetStatus.OFFLINE and not asset.managed
        ]
        expected_sizes = {asset.fingerprint.size for asset in offline if asset.fingerprint is not None}
        candidates_by_size = self.repository.assets.files_by_size(
            directory,
            expected_sizes,
        )

        fingerprints: dict[Path, str] = {}
        relinked: list[Asset] = []
        unresolved: list[Asset] = []
        for asset in offline:
            if asset.fingerprint is None:
                unresolved.append(asset)
                continue
            matches: list[Path] = []
            for candidate in candidates_by_size.get(asset.fingerprint.size, []):
                try:
                    if candidate not in fingerprints:
                        fingerprints[candidate] = self.fingerprint_file(candidate).edge_sha256
                    digest = fingerprints[candidate]
                except OSError:
                    continue
                if digest == asset.fingerprint.edge_sha256:
                    matches.append(candidate)
            if not matches:
                unresolved.append(asset)
                continue
            matches.sort(key=lambda item: (item.name != asset.name, str(item).lower()))
            relinked.append(self.repository.assets.relink_asset(asset.id, matches[0]))
        return relinked, unresolved

    def suggested_profile(self, asset_id: str) -> ProjectProfile | None:
        asset = self.repository.assets.get_asset(asset_id)
        if asset.kind != AssetKind.VIDEO:
            return None
        source = self.repository.assets.resolve_asset_path(asset)
        return self._media_probe.probe(source).suggested_profile

    def adopt_main_profile_from_video(self, asset_id: str) -> Asset:
        """Adopt a video's profile when it is first placed on the main timeline.

        Asset and subtitle source times use the main sequence frame clock. Existing
        main-timeline edits are therefore reframed in the same transaction instead
        of silently changing their wall-clock timing.
        """
        asset = self.repository.assets.get_asset(asset_id)
        if asset.kind != AssetKind.VIDEO:
            raise ValueError("Only a video can define the main sequence profile")
        source = self.repository.assets.resolve_asset_path(asset)
        probe = self._media_probe.probe(source)
        if probe.suggested_profile is None:
            raise ValueError("The video does not provide a usable project profile")
        project = self.repository.projects.get_project()
        source_snapshot = self.repository.frame_clock.capture_main_frame_clock(
            project.main_sequence_id
        )
        state = source_snapshot.timeline
        profile = probe.suggested_profile
        old_profile = state.sequence.profile
        if profile == old_profile and state.sequence.profile_confirmed:
            return asset

        if profile == old_profile:
            state.sequence = state.sequence.model_copy(update={"profile_confirmed": True})
            self.repository.timeline.save_timeline(state)
            return asset

        stored_assets = self.repository.assets.list_assets()
        refreshed_metadata = {}
        for item in stored_assets:
            item_source = self.repository.assets.resolve_asset_path(item)
            if (
                item.kind in {AssetKind.VIDEO, AssetKind.AUDIO}
                and item.status == AssetStatus.ONLINE
                and self.repository.assets.is_regular_file(item_source)
            ):
                refreshed_metadata[item.id] = self._media_probe.probe(
                    item_source,
                    timeline_profile=profile,
                ).metadata
        change = reframe_timeline_clock(
            state,
            stored_assets,
            profile,
            asset_source_profile=old_profile,
            metadata_overrides=refreshed_metadata,
            invalidate_proxies=True,
        )
        TimelineValidator(self.repository).validate(
            change.state,
            baseline=state,
            allow_locked_changes=True,
            assets={item.id: item for item in change.assets},
        )
        self.repository.frame_clock.change_main_frame_clock(
            source_snapshot,
            change.state,
            list(change.assets),
            old_profile=old_profile,
        )
        return self.repository.assets.get_asset(asset_id)
