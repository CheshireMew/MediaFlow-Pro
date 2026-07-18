from __future__ import annotations

from pathlib import Path

from mediaflow.application.ports import AssetServiceDocuments, FingerprintFile, MediaProbePort
from mediaflow.domain.enums import AssetKind, AssetOrigin, AssetStatus
from mediaflow.domain.project import Asset, ProjectProfile, SequenceInOut
from mediaflow.domain.timebase import reframe_frames
from mediaflow.domain.timeline import Clip, ClipAudio, TimelineState, Transition


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

    def import_external(
        self,
        path: str | Path,
        *,
        expected_kind: AssetKind | None = None,
    ) -> Asset:
        source = Path(path).resolve(strict=True)
        if self.probe is None:
            raise RuntimeError("Asset import requires a media probe")
        project = self.repository.get_project()
        main_sequence = self.repository.get_sequence(project.main_sequence_id)
        probe = self.probe.probe(source, timeline_profile=main_sequence.profile)
        if expected_kind is not None and probe.kind != expected_kind:
            raise ValueError(f"素材类型必须是 {expected_kind.value}，实际识别为 {probe.kind.value}")
        asset = self.repository.import_external_asset(source, probe.kind)
        return self.repository.update_asset(asset.model_copy(update={"metadata": probe.metadata}))

    def register_output(self, path: str | Path, origin: AssetOrigin) -> Asset:
        source = Path(path).resolve(strict=True)
        if self.probe is None:
            raise RuntimeError("Asset registration requires a media probe")
        try:
            source.relative_to(self.repository.project_dir)
        except ValueError:
            managed = False
        else:
            managed = True
        project = self.repository.get_project()
        main_sequence = self.repository.get_sequence(project.main_sequence_id)
        probe = self.probe.probe(source, timeline_profile=main_sequence.profile)
        asset = self.repository.import_external_asset(source, probe.kind)
        return self.repository.update_asset(
            asset.model_copy(
                update={
                    "origin": origin,
                    "managed": managed,
                    "metadata": probe.metadata,
                }
            )
        )

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
        if self.fingerprint_file is None:
            raise RuntimeError("Batch relink requires a fingerprint provider")
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
        if profile == old_profile and state.sequence.profile_confirmed:
            return asset

        if profile == old_profile:
            state.sequence = state.sequence.model_copy(update={"profile_confirmed": True})
            self.repository.save_timeline(state)
            return asset

        in_out = state.sequence.in_out
        updated_state = TimelineState(
            sequence=state.sequence.model_copy(
                update={
                    "profile": profile,
                    "profile_confirmed": True,
                    "in_out": (
                        SequenceInOut(
                            in_frame=reframe_frames(in_out.in_frame, old_profile, profile),
                            out_frame=max(
                                reframe_frames(in_out.in_frame, old_profile, profile) + 1,
                                reframe_frames(in_out.out_frame, old_profile, profile),
                            ),
                        )
                        if in_out is not None
                        else None
                    ),
                }
            ),
            tracks=state.tracks,
            clips=[self._reframe_clip(item, old_profile, profile) for item in state.clips],
            transitions=[self._reframe_transition(item, old_profile, profile) for item in state.transitions],
            markers=[
                item.model_copy(update={"frame": reframe_frames(item.frame, old_profile, profile)})
                for item in state.markers
            ],
            ranges=[
                item.model_copy(
                    update={
                        "start_frame": reframe_frames(item.start_frame, old_profile, profile),
                        "end_frame": max(
                            reframe_frames(item.start_frame, old_profile, profile) + 1,
                            reframe_frames(item.end_frame, old_profile, profile),
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
                        "duration_frames": reframe_frames(
                            item.metadata.duration_frames,
                            old_profile,
                            profile,
                        )
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
                "timeline_start": reframe_frames(clip.timeline_start, old_profile, new_profile),
                "source_in": reframe_frames(clip.source_in, old_profile, new_profile),
                "duration": max(
                    1,
                    reframe_frames(clip.timeline_end, old_profile, new_profile)
                    - reframe_frames(clip.timeline_start, old_profile, new_profile),
                ),
                "audio": ClipAudio(
                    gain_db=clip.audio.gain_db,
                    pan=clip.audio.pan,
                    fade_in_frames=reframe_frames(
                        clip.audio.fade_in_frames,
                        old_profile,
                        new_profile,
                    ),
                    fade_out_frames=reframe_frames(
                        clip.audio.fade_out_frames,
                        old_profile,
                        new_profile,
                    ),
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
            update={
                "duration": max(
                    1,
                    reframe_frames(transition.duration, old_profile, new_profile),
                )
            }
        )
