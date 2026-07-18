from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from mediaflow.application.asset_service import AssetService
from mediaflow.application.edit_history import ProjectEditCommand, ProjectEditHistory
from mediaflow.application.highlight_service import HighlightService
from mediaflow.application.project_artifacts import resolve_project_artifact
from mediaflow.application.project_workflow_service import ProjectWorkflowService
from mediaflow.application.sequence_service import SequenceService
from mediaflow.application.subtitle_acquisition import SubtitleAcquisitionService
from mediaflow.application.subtitle_editing import SubtitleEditingService
from mediaflow.application.subtitle_publication import SubtitlePublicationService
from mediaflow.application.task_service import TaskService
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.application.workflow_stage_handlers import WorkflowUpdate
from mediaflow.domain.downloads import DownloadPlan
from mediaflow.domain.enums import (
    TaskStatus,
)
from mediaflow.domain.project import ProjectProfile
from mediaflow.domain.sequence_bounds import SequenceBoundaryAnalysis
from mediaflow.domain.settings import GlobalSettings, LlmProviderSettings
from mediaflow.domain.task_commands import (
    AnalyzeDownloadCommand,
    AnalyzeLoudnessCommand,
    AnalyzeSequenceBoundsCommand,
    ImportAssetCommand,
    TaskCommand,
)
from mediaflow.domain.tasks import Task
from mediaflow.domain.timeline import TimelineState
from mediaflow.infrastructure.audio_region_extractor import FfmpegAudioRegionExtractor
from mediaflow.infrastructure.cookie_store import CookieStore
from mediaflow.infrastructure.encoder_discovery import EncoderDiscoveryService
from mediaflow.infrastructure.file_fingerprint import fingerprint_file
from mediaflow.infrastructure.font_assets import subtitle_font_options
from mediaflow.infrastructure.llm_client import OpenAIJsonClient
from mediaflow.infrastructure.media_probe import MediaProbe
from mediaflow.infrastructure.mlt import SequenceBoundaryAnalysisService, TimelineCompiler
from mediaflow.infrastructure.project_cover_service import ProjectCoverService
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.proxy_service import ProxyService
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from mediaflow.infrastructure.runtime_tools import RuntimeToolService
from mediaflow.infrastructure.settings_repository import SettingsRepository
from mediaflow.infrastructure.task_handlers import ProjectTaskHandlers
from mediaflow.infrastructure.task_repository import TaskRepository
from mediaflow.infrastructure.ytdlp_service import YtDlpDownloadService


@dataclass(frozen=True, slots=True)
class RecentProjectSnapshot:
    items: list[dict]
    totals: dict[str, int]


@dataclass(frozen=True, slots=True)
class ProjectTaskResult:
    workflow: WorkflowUpdate = field(default_factory=WorkflowUpdate)
    imported_asset_id: str = ""
    imported_document_id: str = ""
    imported_purpose: str = ""
    download_plan: DownloadPlan | None = None
    sequence_bounds_status: str = ""
    sequence_id: str = ""
    audio_metrics: dict[str, float] | None = None

    def as_dict(self) -> dict:
        return {
            "workflow": {
                "selected_asset_ids": list(self.workflow.selected_asset_ids),
                "status_message": self.workflow.status_message,
            },
            "imported_asset_id": self.imported_asset_id,
            "imported_document_id": self.imported_document_id,
            "imported_purpose": self.imported_purpose,
            "download_plan": (
                self.download_plan.model_dump(mode="json") if self.download_plan is not None else None
            ),
            "sequence_bounds_status": self.sequence_bounds_status,
            "sequence_id": self.sequence_id,
            "audio_metrics": self.audio_metrics,
        }


class EditorProject:
    """One open project and all operations that depend on its document boundary."""

    def __init__(
        self,
        repository: ProjectRepository,
        *,
        settings: GlobalSettings,
        paths: RuntimePaths,
    ):
        self._repository = repository
        self._settings = settings
        self._paths = paths
        self.cookies = CookieStore(paths.runtime_dir / "cookies")
        self.history = ProjectEditHistory()
        self._timelines: dict[str, TimelineEditor] = {}
        self.documents = repository
        self.assets = AssetService(repository, MediaProbe(paths), fingerprint_file)
        self.subtitle_publication = SubtitlePublicationService(repository)
        self.subtitle_acquisition = SubtitleAcquisitionService(
            repository,
            self.subtitle_publication,
            region_audio_extractor=FfmpegAudioRegionExtractor(paths),
        )
        self.subtitle_editing = SubtitleEditingService(
            repository,
            self.subtitle_publication,
            history=self.history,
        )
        self.highlights = HighlightService(repository, OpenAIJsonClient)
        self.sequences = SequenceService(repository)
        self.tasks = TaskService(
            TaskRepository(repository.project_dir),
            recover_interrupted=not repository.read_only,
        )
        self.workflows = ProjectWorkflowService(
            repository,
            self.tasks,
            settings,
            start_task=self.start_task,
            proxy_decision=self.proxy_decision,
            create_highlight_short=self.highlights.create_short_sequence,
        )
        self.task_handlers = ProjectTaskHandlers(
            self._repository,
            self.assets,
            self._paths,
            self.cookies,
            self.subtitle_acquisition,
            self.subtitle_editing,
            self.subtitle_publication,
            settings=lambda: self._settings,
            active_llm_provider=self._active_llm_provider,
        )
        self.task_handlers.register_with(self.tasks)

    @property
    def project_dir(self) -> Path:
        return self._repository.project_dir

    @property
    def read_only(self) -> bool:
        return self._repository.read_only

    def timeline(self, sequence_id: str) -> TimelineEditor:
        editor = self._timelines.get(sequence_id)
        if editor is None:
            editor = TimelineEditor(self._repository, sequence_id, self.history)
            self._timelines[sequence_id] = editor
        return editor

    def proxy_decision(self, asset, *, dropped_frames: int = 0, manual: bool = False):
        return ProxyService.decision(asset, dropped_frames=dropped_frames, manual=manual)

    def sequence_boundary_snapshot_hash(self, sequence_id: str) -> str:
        state = self._repository.load_timeline(sequence_id)
        return SequenceBoundaryAnalysisService(
            TimelineCompiler(self._repository),
            self._paths,
        ).snapshot_hash(state)

    def start_task(
        self,
        command: TaskCommand,
        input_asset_ids: list[str] | None = None,
        *,
        sequence_id: str | None = None,
    ) -> Task:
        if self.read_only:
            raise PermissionError("项目以只读方式打开")
        project = self._repository.get_project()
        return self.tasks.start(
            project_id=project.id,
            sequence_id=sequence_id or project.main_sequence_id,
            command=command,
            input_asset_ids=input_asset_ids,
        )

    def import_asset(
        self,
        source: str | Path,
        *,
        sequence_id: str | None = None,
        purpose: str = "media",
        language: str = "auto",
        media_asset_id: str | None = None,
    ) -> Task:
        path = Path(source).expanduser().resolve(strict=True)
        if purpose not in {"media", "subtitle", "watermark"}:
            raise ValueError(f"Unknown import purpose: {purpose}")
        return self.start_task(
            ImportAssetCommand(
                source_path=str(path),
                purpose=purpose,
                language=language,
                media_asset_id=media_asset_id,
            ),
            sequence_id=sequence_id,
        )

    def consume_task_result(self, task: Task) -> ProjectTaskResult:
        project = self._repository.get_project()
        if task.project_id != project.id:
            raise ValueError("Task does not belong to this project")
        if not task.status.is_consumable:
            raise ValueError("Only terminal or paused task state can be consumed")

        workflow = self.workflows.handle_task(task)
        imported_asset_id = ""
        imported_document_id = ""
        imported_purpose = ""
        download_plan = None
        sequence_bounds_status = ""
        sequence_id = task.sequence_id or ""
        audio_metrics = None
        if task.status == TaskStatus.COMPLETED:
            if isinstance(task.command, ImportAssetCommand) and task.artifacts:
                artifact = resolve_project_artifact(self.project_dir, task.artifacts[0]).resolve()
                asset = next(
                    item
                    for item in self._repository.list_assets()
                    if self._repository.resolve_asset_path(item).resolve() == artifact
                )
                imported_asset_id = asset.id
                command = task.command
                if not isinstance(command, ImportAssetCommand):
                    raise TypeError(f"Unexpected import command: {type(command).__name__}")
                imported_purpose = command.purpose
                if imported_purpose == "subtitle":
                    documents = self._repository.list_subtitle_documents(asset.id)
                    if not documents:
                        raise RuntimeError("字幕导入任务没有生成字幕文档")
                    imported_document_id = documents[-1].id
                elif imported_purpose == "media":
                    workflow = workflow.merge(
                        self.workflows.begin_import(
                            task.sequence_id or project.main_sequence_id,
                            asset.id,
                            source_task_id=task.id,
                        )
                    )
            elif isinstance(task.command, AnalyzeDownloadCommand) and task.artifacts:
                download_plan = DownloadPlan.model_validate_json(
                    resolve_project_artifact(self.project_dir, task.artifacts[0]).read_text(encoding="utf-8")
                )
            elif isinstance(task.command, AnalyzeSequenceBoundsCommand) and task.artifacts:
                analysis = SequenceBoundaryAnalysis.model_validate_json(
                    resolve_project_artifact(self.project_dir, task.artifacts[0]).read_text(encoding="utf-8")
                )
                sequence_id = task.command.sequence_id
                current_hash = self.sequence_boundary_snapshot_hash(sequence_id)
                if analysis.snapshot_hash != current_hash:
                    sequence_bounds_status = "stale"
                else:
                    self.timeline(sequence_id).set_sequence_in_out(
                        analysis.suggested.in_frame,
                        analysis.suggested.out_frame,
                    )
                    sequence_bounds_status = (
                        "applied_without_speech" if analysis.speech_in_frame is None else "applied"
                    )
            elif isinstance(task.command, AnalyzeLoudnessCommand) and task.artifacts:
                audio_metrics = self._read_loudness_metrics(
                    resolve_project_artifact(self.project_dir, task.artifacts[0])
                )
        return ProjectTaskResult(
            workflow=workflow,
            imported_asset_id=imported_asset_id,
            imported_document_id=imported_document_id,
            imported_purpose=imported_purpose,
            download_plan=download_plan,
            sequence_bounds_status=sequence_bounds_status,
            sequence_id=sequence_id,
            audio_metrics=audio_metrics,
        )

    @staticmethod
    def _read_loudness_metrics(path: Path) -> dict[str, float]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return {
            "samplePeakDbfs": float(payload["sample_peak_dbfs"]),
            "truePeakDbtp": float(payload["true_peak_dbtp"]),
            "shortTermLufs": float(payload["short_term_lufs"]),
            "integratedLufs": float(payload["integrated_lufs"]),
        }

    def archive_short_sequence(self, sequence_id: str) -> None:
        sequence = self._repository.archive_short_sequence(sequence_id)

        def restore() -> None:
            self._repository.restore_short_sequence(sequence.id)

        def archive() -> None:
            self._repository.archive_short_sequence(sequence.id)

        self.history.push(
            ProjectEditCommand(
                label="删除短视频序列",
                undo_action=restore,
                redo_action=archive,
            )
        )

    def refresh_workflow_mode(self) -> ProjectWorkflowService:
        self.workflows.update_settings(self._settings)
        return self.workflows

    def update_settings(self, settings: GlobalSettings) -> None:
        self._settings = settings
        self.refresh_workflow_mode()

    def close(self) -> None:
        self.tasks.shutdown(wait=True)
        self._repository.close()

    def __enter__(self) -> EditorProject:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _active_llm_provider(self) -> LlmProviderSettings:
        active_id = self._settings.active_llm_provider_id
        if active_id:
            active = next(
                (
                    provider
                    for provider in self._settings.llm_providers
                    if provider.id == active_id and provider.enabled
                ),
                None,
            )
            if active is not None:
                return active
        try:
            return next(provider for provider in self._settings.llm_providers if provider.enabled)
        except StopIteration as error:
            raise RuntimeError("请先在设置中配置并启用一个 LLM 提供商") from error


class EditorApplication:
    """Single composition root shared by desktop and headless entry points."""

    def __init__(self):
        self._paths = RuntimePaths.discover()
        self._settings_repository = SettingsRepository()
        self.settings = self._settings_repository.load()
        self.cookies = CookieStore(self._paths.runtime_dir / "cookies")
        self._project_covers = ProjectCoverService(self._paths)

    @property
    def mlt_runtime_root(self) -> str:
        return str(self._paths.melt.parent) if self._paths.melt else ""

    @property
    def native_qml_root(self) -> Path | None:
        return self._paths.native_qml

    @property
    def runtime_paths(self) -> RuntimePaths:
        return self._paths

    def save_settings(self) -> None:
        self._settings_repository.save(self.settings)

    def replace_settings(self, settings: GlobalSettings) -> None:
        # Persist first so a disk error cannot leave the running application in
        # a state that was never durably accepted.
        self._settings_repository.save(settings)
        self.settings = settings

    def discover_video_encoder_options(self) -> list[dict]:
        return EncoderDiscoveryService(self._paths).video_options()

    def analyze_download_url(self, url: str) -> DownloadPlan:
        return YtDlpDownloadService.analyze_configured(
            url,
            settings=self.settings.download,
            cookies=self.cookies,
        )

    @staticmethod
    def test_llm_provider(provider: LlmProviderSettings) -> None:
        OpenAIJsonClient(provider).test_connection()

    @staticmethod
    def subtitle_font_options() -> list[dict]:
        return subtitle_font_options()

    def runtime_tool_status(self) -> dict:
        return RuntimeToolService(self.settings.asr, self._paths).status()

    def run_runtime_tool(
        self,
        operation: str,
        *,
        progress: Callable[[float, str], None],
        check_cancelled: Callable[[], None],
    ) -> object:
        tools = RuntimeToolService(self.settings.asr, self._paths)
        if operation == "inspect":
            return tools.cuda_readiness()
        if operation == "update_ytdlp":
            return tools.update_ytdlp(
                progress=progress,
                check_cancelled=check_cancelled,
            )
        if operation == "install_asr_cli":
            return str(
                tools.install_faster_whisper_cli(
                    progress=progress,
                    check_cancelled=check_cancelled,
                )
            )
        if operation == "prewarm_asr_cli":
            return str(
                tools.prewarm_cli(
                    progress=progress,
                    check_cancelled=check_cancelled,
                )
            )
        raise ValueError(f"Unknown runtime tool operation: {operation}")

    @staticmethod
    def read_loudness_metrics(project_dir: Path, sequence_id: str) -> dict[str, float]:
        path = project_dir / "generated" / "audio" / f"{sequence_id}-loudness.json"
        if not path.is_file():
            return {}
        return EditorProject._read_loudness_metrics(path)

    def write_preview_snapshot(
        self,
        project_dir: str | Path,
        state: TimelineState,
        *,
        use_proxies: bool,
        prefer_sdr_preview_proxy: bool,
    ) -> Path:
        # Preview compilation owns an independent read connection so the active
        # project can be switched or closed without sharing its repository
        # connection with a worker thread.
        with ProjectRepository.open(project_dir, writable=False) as repository:
            destination = Path(project_dir) / "cache" / "mlt" / f"{state.sequence.id}-preview.mlt"
            TimelineCompiler(repository).write(
                state,
                destination,
                use_proxies=use_proxies,
                native_preview=True,
                prefer_sdr_preview_proxy=prefer_sdr_preview_proxy,
            )
        return destination

    def create_project(
        self,
        root: str | Path,
        name: str,
        profile: ProjectProfile | None = None,
    ) -> EditorProject:
        repository = ProjectRepository.create(root, name, profile)
        return EditorProject(repository, settings=self.settings, paths=self._paths)

    def open_project(self, root: str | Path, *, writable: bool = True) -> EditorProject:
        repository = ProjectRepository.open(root, writable=writable)
        return EditorProject(repository, settings=self.settings, paths=self._paths)

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
                        tasks = TaskRepository(path).list()
                        item["runningTaskCount"] = sum(task.status.is_active for task in tasks)
                        item["failedTaskCount"] = sum(task.status == TaskStatus.FAILED for task in tasks)
                        item["offlineAssetCount"] = sum(
                            not repository.resolve_asset_path(asset).is_file()
                            for asset in repository.list_assets()
                        )
                        item["pendingWorkflowCount"] = len(repository.list_workflow_runs(active_only=True))
                        cover = self._project_covers.cover_for(repository)
                        item["coverPath"] = str(cover) if cover else ""
                        artifacts = [
                            value
                            for task in reversed(tasks)
                            for value in reversed(task.artifacts)
                            if Path(value).is_file()
                            or (not Path(value).is_absolute() and (path / value).is_file())
                        ]
                        item["recentArtifact"] = artifacts[0] if artifacts else ""
                except (RuntimeError, sqlite3.Error):
                    # A project with an older, writable-migratable schema is
                    # still available. The home screen simply omits live metrics
                    # until the user opens it through the writable boundary.
                    pass
                except OSError:
                    item["available"] = (path / "project.mfp").is_file()
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
