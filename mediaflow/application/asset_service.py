from __future__ import annotations

from pathlib import Path

from mediaflow.domain.enums import AssetKind, AssetOrigin, AssetStatus
from mediaflow.domain.models import Asset, Clip, ClipAudio, ProjectProfile, TimelineState, Transition
from mediaflow.domain.timebase import frames_to_seconds, seconds_to_frames
from mediaflow.infrastructure.file_fingerprint import fingerprint_file
from mediaflow.infrastructure.media_probe import MediaProbe
from mediaflow.infrastructure.project_repository import ProjectRepository


class AssetService:
    def __init__(self, repository: ProjectRepository, probe: MediaProbe):
        self.repository = repository
        self.probe = probe

    def import_external(self, path: str | Path) -> Asset:
        source = Path(path).resolve(strict=True)
        project = self.repository.get_project()
        main_sequence = self.repository.get_sequence(project.main_sequence_id)
        probe = self.probe.probe(source, timeline_profile=main_sequence.profile)
        asset = self.repository.add_asset(
            Asset(
                project_id=project.id,
                name=source.name,
                kind=probe.kind,
                origin=AssetOrigin.EXTERNAL,
                path=str(source),
                managed=False,
                fingerprint=fingerprint_file(source),
                metadata=probe.metadata,
            )
        )
        return asset

    def register_managed(self, path: str | Path, origin: AssetOrigin) -> Asset:
        source = Path(path).resolve(strict=True)
        try:
            source.relative_to(self.repository.project_dir)
        except ValueError as error:
            raise ValueError("Managed output must be inside the project directory") from error
        project = self.repository.get_project()
        main_sequence = self.repository.get_sequence(project.main_sequence_id)
        probe = self.probe.probe(source, timeline_profile=main_sequence.profile)
        asset = self.repository.add_asset(
            Asset(
                project_id=project.id,
                name=source.name,
                kind=probe.kind,
                origin=origin,
                path=str(source),
                managed=True,
                fingerprint=fingerprint_file(source),
                metadata=probe.metadata,
            )
        )
        return asset

    def refresh_all(self) -> list[Asset]:
        project = self.repository.get_project()
        profile = self.repository.get_sequence(project.main_sequence_id).profile
        refreshed: list[Asset] = []
        for asset in self.repository.list_assets():
            current = self.repository.refresh_asset_status(asset.id)
            source = self.repository.resolve_asset_path(current)
            if source.is_file() and (
                current.metadata.duration_frames == 0
                or (current.kind == AssetKind.VIDEO and not current.metadata.width)
            ):
                try:
                    metadata = self.probe.probe(source, timeline_profile=profile).metadata
                except Exception:
                    current = self.repository.update_asset(
                        current.model_copy(update={"status": AssetStatus.ERROR})
                    )
                else:
                    current = self.repository.update_asset(
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
        asset = self.repository.relink_asset(
            asset_id,
            replacement,
            allow_different_content=allow_different_content,
        )
        project = self.repository.get_project()
        profile = self.repository.get_sequence(project.main_sequence_id).profile
        metadata = self.probe.probe(
            self.repository.resolve_asset_path(asset),
            timeline_profile=profile,
        ).metadata
        return self.repository.update_asset(asset.model_copy(update={"metadata": metadata}))

    def relink_offline_from_directory(self, directory: str | Path) -> tuple[list[Asset], list[Asset]]:
        """Relink only exact fingerprint matches found below a user-selected directory."""
        root = Path(directory).resolve(strict=True)
        if not root.is_dir():
            raise NotADirectoryError(root)
        offline = [
            asset
            for asset in self.repository.list_assets()
            if asset.status == AssetStatus.OFFLINE and not asset.managed
        ]
        expected_sizes = {asset.fingerprint.size for asset in offline if asset.fingerprint is not None}
        candidates_by_size: dict[int, list[Path]] = {size: [] for size in expected_sizes}
        for candidate in root.rglob("*"):
            try:
                if candidate.is_file() and candidate.stat().st_size in expected_sizes:
                    candidates_by_size[candidate.stat().st_size].append(candidate.resolve())
            except OSError:
                continue

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
                        fingerprints[candidate] = fingerprint_file(candidate).edge_sha256
                    digest = fingerprints[candidate]
                except OSError:
                    continue
                if digest == asset.fingerprint.edge_sha256:
                    matches.append(candidate)
            if not matches:
                unresolved.append(asset)
                continue
            matches.sort(key=lambda item: (item.name != asset.name, str(item).lower()))
            relinked.append(self.repository.relink_asset(asset.id, matches[0]))
        return relinked, unresolved

    def suggested_profile(self, asset_id: str) -> ProjectProfile | None:
        asset = self.repository.get_asset(asset_id)
        if asset.kind != AssetKind.VIDEO:
            return None
        source = self.repository.resolve_asset_path(asset)
        return self.probe.probe(source).suggested_profile

    def adopt_main_profile_from_video(self, asset_id: str) -> Asset:
        """Adopt a video's profile when it is first placed on the main timeline.

        Asset and subtitle source times use the main sequence frame clock. Existing
        main-timeline edits are therefore reframed in the same transaction instead
        of silently changing their wall-clock timing.
        """
        asset = self.repository.get_asset(asset_id)
        if asset.kind != AssetKind.VIDEO:
            raise ValueError("Only a video can define the main sequence profile")
        source = self.repository.resolve_asset_path(asset)
        probe = self.probe.probe(source)
        if probe.suggested_profile is None:
            raise ValueError("The video does not provide a usable project profile")
        project = self.repository.get_project()
        state = self.repository.load_timeline(project.main_sequence_id)
        profile = probe.suggested_profile
        old_profile = state.sequence.profile
        if profile == old_profile:
            return asset

        updated_state = TimelineState(
            sequence=state.sequence.model_copy(update={"profile": profile}),
            tracks=state.tracks,
            clips=[self._reframe_clip(item, old_profile, profile) for item in state.clips],
            transitions=[self._reframe_transition(item, old_profile, profile) for item in state.transitions],
            markers=[
                item.model_copy(update={"frame": self._reframe(item.frame, old_profile, profile)})
                for item in state.markers
            ],
            ranges=[
                item.model_copy(
                    update={
                        "start_frame": self._reframe(item.start_frame, old_profile, profile),
                        "end_frame": max(
                            self._reframe(item.start_frame, old_profile, profile) + 1,
                            self._reframe(item.end_frame, old_profile, profile),
                        ),
                    }
                )
                for item in state.ranges
            ],
        )
        updated_assets: list[Asset] = []
        for item in self.repository.list_assets():
            item_source = self.repository.resolve_asset_path(item)
            if item_source.is_file():
                metadata = self.probe.probe(item_source, timeline_profile=profile).metadata
            else:
                metadata = item.metadata.model_copy(
                    update={
                        "duration_frames": self._reframe(item.metadata.duration_frames, old_profile, profile)
                    }
                )
            updated_assets.append(
                item.model_copy(
                    update={
                        "metadata": metadata,
                        "proxy_path": None,
                        "sdr_preview_proxy_path": None,
                    }
                )
            )

        self.repository.apply_main_profile_change(
            updated_state,
            updated_assets,
            old_profile=old_profile,
        )
        return self.repository.get_asset(asset_id)

    @classmethod
    def _reframe_clip(
        cls,
        clip: Clip,
        old_profile: ProjectProfile,
        new_profile: ProjectProfile,
    ) -> Clip:
        return clip.model_copy(
            update={
                "timeline_start": cls._reframe(clip.timeline_start, old_profile, new_profile),
                "source_in": cls._reframe(clip.source_in, old_profile, new_profile),
                "duration": max(
                    1,
                    cls._reframe(clip.timeline_end, old_profile, new_profile)
                    - cls._reframe(clip.timeline_start, old_profile, new_profile),
                ),
                "audio": ClipAudio(
                    gain_db=clip.audio.gain_db,
                    pan=clip.audio.pan,
                    fade_in_frames=cls._reframe(clip.audio.fade_in_frames, old_profile, new_profile),
                    fade_out_frames=cls._reframe(clip.audio.fade_out_frames, old_profile, new_profile),
                ),
            }
        )

    @classmethod
    def _reframe_transition(
        cls,
        transition: Transition,
        old_profile: ProjectProfile,
        new_profile: ProjectProfile,
    ) -> Transition:
        return transition.model_copy(
            update={"duration": max(1, cls._reframe(transition.duration, old_profile, new_profile))}
        )

    @staticmethod
    def _reframe(
        frames: int,
        old_profile: ProjectProfile,
        new_profile: ProjectProfile,
    ) -> int:
        return seconds_to_frames(
            frames_to_seconds(
                frames,
                old_profile.fps_numerator,
                old_profile.fps_denominator,
            ),
            new_profile.fps_numerator,
            new_profile.fps_denominator,
        )
