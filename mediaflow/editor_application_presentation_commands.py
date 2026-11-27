from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from mediaflow.domain.enums import ColorMode
from mediaflow.domain.timeline import TimelineState

if TYPE_CHECKING:
    from mediaflow.application.media_resource_service import MediaResourceService
    from mediaflow.infrastructure.timeline_proof_frames import TimelineProofFrameService
    from mediaflow.project_presentation import ProjectPresentationService


class EditorApplicationPresentationCommands:
    media_resources: MediaResourceService
    _presentation: ProjectPresentationService
    _proof_frames: TimelineProofFrameService

    def search_media_resources(
        self,
        *,
        color_mode: str = "sdr_bt709",
        category: str | None = None,
        query: str = "",
        tags: list[str] | None = None,
        capabilities: list[str] | None = None,
    ) -> dict[str, object]:
        return self.media_resources.search(
            color_mode=ColorMode(color_mode),
            category=category,
            query=query,
            tags=tags or (),
            capabilities=capabilities or (),
        )

    def write_preview_snapshot(
        self,
        project_dir: str | Path,
        state: TimelineState,
        *,
        use_proxies: bool,
        prefer_sdr_preview_proxy: bool,
        prepare_web: bool = False,
        request_owner: str = "direct",
        request_generation: int | None = None,
    ) -> Path:
        return self._presentation.write_preview_snapshot(
            project_dir,
            state,
            use_proxies=use_proxies,
            prefer_sdr_preview_proxy=prefer_sdr_preview_proxy,
            prepare_web=prepare_web,
            request_owner=request_owner,
            request_generation=request_generation,
        )

    def cancel_preview_snapshot_requests(
        self, project_dir: str | Path, *, request_owner: str, request_generation: int,
    ) -> None:
        self._presentation.cancel_preview_snapshot_requests(
            project_dir, request_owner=request_owner, request_generation=request_generation,
        )

    def release_preview_owner(self, project_dir: str | Path, request_owner: str | None) -> None:
        self._presentation.release_preview_owner(project_dir, request_owner)

    def write_asset_preview_snapshot(
        self,
        project_dir: str | Path,
        sequence_id: str,
        asset_id: str,
        *, request_owner: str = "direct",
    ) -> Path:
        return self._presentation.write_asset_preview_snapshot(
            project_dir,
            sequence_id,
            asset_id,
            request_owner=request_owner,
        )

    def render_preview_frames(
        self,
        project_dir: str | Path,
        state: TimelineState,
        frames: list[int],
        *,
        use_proxies: bool,
        prefer_sdr_preview_proxy: bool,
    ) -> tuple[Path, list[dict[str, object]]]:
        graph = self.write_preview_snapshot(
            project_dir,
            state,
            use_proxies=use_proxies,
            prefer_sdr_preview_proxy=prefer_sdr_preview_proxy,
            prepare_web=True,
        )
        rendered = self._proof_frames.render(
            project_dir,
            graph,
            frames,
            expected_width=state.sequence.profile.width,
            expected_height=state.sequence.profile.height,
        )
        return graph, rendered
