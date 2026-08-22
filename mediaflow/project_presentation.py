from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from mediaflow.application.presentation_models import RecentProjectSnapshot
from mediaflow.atomic_file import atomic_write_text
from mediaflow.domain.enums import AssetKind, TaskStatus, TrackKind
from mediaflow.domain.storage_names import content_addressed_child_path
from mediaflow.domain.tasks import (
    ArtifactReference,
    DiagnosticsBundleTaskOutcome,
    ExportTaskOutcome,
    SequenceBuildTaskOutcome,
    Task,
)
from mediaflow.domain.timeline import Clip, TimelineState, Track, default_clip_media_kind
from mediaflow.infrastructure.cache_manager import CacheManager
from mediaflow.infrastructure.media_thumbnail_service import MediaThumbnailService
from mediaflow.infrastructure.mlt import TimelineCompiler
from mediaflow.infrastructure.project_cover_service import ProjectCoverService
from mediaflow.infrastructure.project_migration_runner import ProjectUpgradeRequiredError
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from mediaflow.infrastructure.task_repository import TaskRepository
from mediaflow.infrastructure.timeline_filmstrip import FILMSTRIP_REQUESTS, TimelineFilmstripService


def _user_visible_task_artifacts(task: Task) -> tuple[ArtifactReference, ...]:
    if isinstance(task.outcome, ExportTaskOutcome):
        return tuple(item.output for item in task.outcome.files)
    if isinstance(task.outcome, SequenceBuildTaskOutcome):
        return (task.outcome.output.output,)
    if isinstance(task.outcome, DiagnosticsBundleTaskOutcome):
        return (task.outcome.output,)
    return tuple(task.artifacts)


class ProjectPresentationService:
    """Read-only project views and cached media prepared for application clients."""

    def __init__(self, paths: RuntimePaths) -> None:
        self.paths = paths
        self.thumbnails = MediaThumbnailService(paths)
        self.covers = ProjectCoverService(paths)

    def write_preview_snapshot(
        self,
        project_dir: str | Path,
        state: TimelineState,
        *,
        use_proxies: bool,
        prefer_sdr_preview_proxy: bool,
    ) -> Path:
        with ProjectRepository.open(project_dir, writable=False) as repository:
            document = TimelineCompiler(repository, self.paths).compile(
                state,
                use_proxies=use_proxies,
                native_preview=True,
                prefer_sdr_preview_proxy=prefer_sdr_preview_proxy,
            )
            namespace = "pv-" + hashlib.sha256(state.sequence.id.encode("utf-8")).hexdigest()[:12]
            preview_cache = self.paths.project_cache_dir(project_dir)
            destination = content_addressed_child_path(
                preview_cache / "mlt",
                document.xml,
                namespace=namespace,
                suffix=".mlt",
            )
            if not destination.is_file():
                atomic_write_text(destination, document.xml)
            CacheManager(preview_cache).prune_files(
                "mlt",
                f"{namespace}-*.mlt",
                keep=16,
                max_age_seconds=7 * 24 * 60 * 60,
            )
        return destination

    def write_asset_preview_snapshot(
        self,
        project_dir: str | Path,
        sequence_id: str,
        asset_id: str,
    ) -> Path:
        with ProjectRepository.open(project_dir, writable=False) as repository:
            sequence = repository.sequences.get_sequence(sequence_id)
            project = repository.projects.get_project()
            asset = repository.assets.get_asset(asset_id)
            if asset.kind not in {AssetKind.VIDEO, AssetKind.AUDIO, AssetKind.IMAGE}:
                raise ValueError("该素材类型不能在源监视器中播放")
            main_profile = repository.sequences.get_sequence(project.main_sequence_id).profile
            timeline_asset = asset.in_frame_clock(main_profile, sequence.profile)
            duration = timeline_asset.metadata.duration_frames or 150
            track_kind = TrackKind.AUDIO if asset.kind == AssetKind.AUDIO else TrackKind.VIDEO
            track = Track(
                id=f"source-track-{asset.id}",
                sequence_id=sequence.id,
                name="Source monitor",
                kind=track_kind,
                position=0,
            )
            clip = Clip(
                id=f"source-clip-{asset.id}",
                track_id=track.id,
                asset_id=asset.id,
                timeline_start=0,
                source_in=0,
                duration=duration,
                media_kind=default_clip_media_kind(
                    asset.kind,
                    has_audio=asset.metadata.has_audio,
                ),
            )
            state = TimelineState(sequence=sequence, tracks=[track], clips=[clip])
            document = TimelineCompiler(repository, self.paths).compile(
                state,
                use_proxies=True,
                native_preview=True,
                prefer_sdr_preview_proxy=True,
            )
            namespace = "source-" + hashlib.sha256(asset.id.encode("utf-8")).hexdigest()[:12]
            preview_cache = self.paths.project_cache_dir(project_dir)
            destination = content_addressed_child_path(
                preview_cache / "mlt",
                document.xml,
                namespace=namespace,
                suffix=".mlt",
            )
            if not destination.is_file():
                atomic_write_text(destination, document.xml)
        return destination

    def recent_projects(self, paths: list[str]) -> RecentProjectSnapshot:
        items: list[dict] = []
        totals = {
            "runningTaskCount": 0,
            "failedTaskCount": 0,
            "offlineAssetCount": 0,
            "pendingWorkflowCount": 0,
            "recentArtifactCount": 0,
        }
        for path_value in paths:
            path = Path(path_value)
            item = {
                "name": path.name,
                "path": str(path),
                "available": (path / "project.mfp").is_file(),
                "unavailableReason": "",
                "runningTaskCount": 0,
                "failedTaskCount": 0,
                "offlineAssetCount": 0,
                "pendingWorkflowCount": 0,
                "recentArtifact": "",
                "coverPath": "",
            }
            if item["available"]:
                try:
                    with ProjectRepository.open(path, writable=False) as repository:
                        tasks = TaskRepository(repository).list()
                        item["runningTaskCount"] = sum(task.status.is_active for task in tasks)
                        item["failedTaskCount"] = sum(
                            task.status == TaskStatus.FAILED for task in tasks
                        )
                        item["offlineAssetCount"] = sum(
                            not repository.assets.resolve_asset_path(asset).is_file()
                            for asset in repository.assets.list_assets()
                        )
                        item["pendingWorkflowCount"] = len(
                            repository.projects.list_workflow_runs(active_only=True)
                        )
                        cover = self.covers.cover_for(repository)
                        item["coverPath"] = str(cover) if cover else ""
                        artifacts = [
                            local
                            for task in reversed(tasks)
                            for value in reversed(_user_visible_task_artifacts(task))
                            if (local := value.local_path(path)) is not None and local.is_file()
                        ]
                        item["recentArtifact"] = str(artifacts[0]) if artifacts else ""
                except ProjectUpgradeRequiredError:
                    pass
                except (RuntimeError, sqlite3.Error):
                    item["available"] = False
                    item["unavailableReason"] = "项目文件损坏或格式不受支持"
                except OSError:
                    item["available"] = False
                    item["unavailableReason"] = "项目文件当前无法读取"
            else:
                item["unavailableReason"] = "项目文件不存在"
            items.append(item)
            for key in (
                "runningTaskCount",
                "failedTaskCount",
                "offlineAssetCount",
                "pendingWorkflowCount",
            ):
                count = item[key]
                if isinstance(count, int):
                    totals[key] += count
            totals["recentArtifactCount"] += bool(item["recentArtifact"])
        return RecentProjectSnapshot(items=items, totals=totals)

    def asset_thumbnail_paths(
        self,
        project_dir: str | Path,
        *,
        width: int = 160,
        height: int = 90,
    ) -> dict[str, str]:
        thumbnails: dict[str, str] = {}
        with ProjectRepository.open(project_dir, writable=False) as repository:
            for asset in repository.assets.list_assets():
                thumbnail = self.thumbnails.thumbnail_for(
                    repository,
                    asset,
                    width=width,
                    height=height,
                )
                if thumbnail is not None:
                    thumbnails[asset.id] = str(thumbnail)
        return thumbnails

    def timeline_filmstrip_paths(
        self,
        project_dir: str | Path,
        sequence_id: str,
        *,
        visible_start_frame: int,
        visible_end_frame: int,
        pixels_per_frame: float,
        height: int = 46,
        request_owner: str | None = None,
        request_generation: int | None = None,
    ) -> list[dict[str, object]]:
        resolved_project = Path(project_dir).resolve()
        owner = request_owner or "direct"
        generation = 0 if request_generation is None else request_generation
        request_key = (str(resolved_project), owner)
        with FILMSTRIP_REQUESTS.request(request_key, generation) as check_cancelled:
            with ProjectRepository.open(resolved_project, writable=False) as repository:
                return TimelineFilmstripService(repository, self.paths).render_visible(
                    sequence_id,
                    visible_start_frame=visible_start_frame,
                    visible_end_frame=visible_end_frame,
                    pixels_per_frame=pixels_per_frame,
                    height=height,
                    check_cancelled=check_cancelled,
                )

    @staticmethod
    def cancel_timeline_filmstrip_requests(
        project_dir: str | Path,
        *,
        request_owner: str,
        request_generation: int,
    ) -> None:
        request_key = (str(Path(project_dir).resolve()), request_owner)
        FILMSTRIP_REQUESTS.cancel(request_key, request_generation)
