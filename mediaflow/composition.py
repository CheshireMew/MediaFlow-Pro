from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, cast

from mediaflow.application.asset_service import AssetService
from mediaflow.application.dubbing_editing import DubbingEditingService
from mediaflow.application.edit_history import (
    ProjectEditHistory,
)
from mediaflow.application.external_capabilities import (
    ReferenceComparisonCapability,
    RuntimeInspectionCapability,
    SpeechCapability,
)
from mediaflow.application.highlight_service import HighlightService
from mediaflow.application.media_resource_service import MediaResourceService
from mediaflow.application.portable_timeline_import import PortableTimelineImportService
from mediaflow.application.ports import MediaProbePort
from mediaflow.application.project_command_queue import ProjectCommandQueue
from mediaflow.application.project_task_handlers import ProjectTaskHandlers
from mediaflow.application.project_workflow_service import ProjectWorkflowService
from mediaflow.application.sequence_service import SequenceService
from mediaflow.application.subtitle_acquisition import SubtitleAcquisitionService
from mediaflow.application.subtitle_editing import SubtitleEditingService
from mediaflow.application.subtitle_publication import SubtitlePublicationService
from mediaflow.application.task_service import TaskService
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.application.transcript_editing import TranscriptEditingService
from mediaflow.application.translation_service import TranslationService
from mediaflow.application.web_media_service import WebMediaServices
from mediaflow.application.workflow_models import WorkflowUpdate
from mediaflow.domain.collaboration import (
    ActorIdentity,
    ProjectChangeEvent,
    ProjectMutationPlan,
    ProjectUndoGroup,
)
from mediaflow.domain.downloads import DownloadPlan
from mediaflow.domain.enums import TaskStatus
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.project import ProjectProfile
from mediaflow.domain.settings import LlmProviderSettings, ServiceSettings
from mediaflow.domain.tasks import ImportedAssetTaskOutcome, Task
from mediaflow.editor_application_presentation_commands import (
    EditorApplicationPresentationCommands,
)
from mediaflow.editor_project_delivery_commands import (
    EditorProjectDeliveryCommands,
)
from mediaflow.editor_project_document_commands import (
    EditorProjectDocumentCommands,
)
from mediaflow.editor_project_media_commands import EditorProjectMediaCommands
from mediaflow.editor_project_script_timeline_commands import (
    EditorProjectScriptTimelineCommands,
)
from mediaflow.editor_project_task_commands import (
    EditorProjectTaskWorkflowCommands,
)
from mediaflow.editor_project_web_commands import EditorProjectWebCommands
from mediaflow.infrastructure.asr_models import FasterWhisperModelStore
from mediaflow.infrastructure.cache_manager import CacheManager
from mediaflow.infrastructure.cookie_store import CookieStore
from mediaflow.infrastructure.editable_media_contract import editable_media_contract
from mediaflow.infrastructure.encoder_discovery import EncoderDiscoveryService
from mediaflow.infrastructure.file_fingerprint import fingerprint_file
from mediaflow.infrastructure.llm_client import OpenAIJsonClient
from mediaflow.infrastructure.media_probe import MediaProbe
from mediaflow.infrastructure.media_resource_catalog import load_media_resource_catalog
from mediaflow.infrastructure.portable_timeline_loader import load_portable_timeline
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.reference_video_comparison import ReferenceVideoComparisonService
from mediaflow.infrastructure.runtime_capabilities import RuntimeInspectionService
from mediaflow.infrastructure.runtime_context import RuntimeContext
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from mediaflow.infrastructure.runtime_tools import RuntimeToolService
from mediaflow.infrastructure.settings_repository import ServiceSettingsRepository
from mediaflow.infrastructure.speech_service import InfrastructureSpeechService
from mediaflow.infrastructure.storage_paths import default_media_root
from mediaflow.infrastructure.structured_file_reader import LocalStructuredFileReader
from mediaflow.infrastructure.subtitle_file_store import LocalSubtitleFileStore
from mediaflow.infrastructure.subtitle_publication_storage import (
    LocalSubtitlePublicationStorage,
)
from mediaflow.infrastructure.task_repository import TaskRepository
from mediaflow.infrastructure.task_runtime import InfrastructureTaskRuntimes
from mediaflow.infrastructure.timeline_proof_frames import TimelineProofFrameService
from mediaflow.infrastructure.translation_cache import TranslationCache
from mediaflow.infrastructure.web_browser import (
    BrowserWebPackageValidator,
    WebPackagePreviewServer,
)
from mediaflow.infrastructure.web_package_storage import LocalWebPackageStorage
from mediaflow.infrastructure.ytdlp_service import YtDlpDownloadService
from mediaflow.project_collaboration import (
    DEFAULT_IDEMPOTENCY_BASE,
    AutomationBatchCommand,
    ProjectCollaboration,
)
from mediaflow.project_presentation import ProjectPresentationService, RecentProjectSnapshot
from mediaflow.project_task_settlement import (
    ProjectTaskSettlement,
)


class EditorProject(
    EditorProjectDocumentCommands,
    EditorProjectMediaCommands,
    EditorProjectScriptTimelineCommands,
    EditorProjectTaskWorkflowCommands,
    EditorProjectWebCommands,
    EditorProjectDeliveryCommands,
):
    """One open project and all operations that depend on its document boundary."""

    def __init__(
        self,
        repository: ProjectRepository,
        *,
        settings: ServiceSettings,
        paths: RuntimePaths,
    ):
        self._repository = repository
        self._write_gate = ProjectCommandQueue()
        if not repository.read_only:
            self._repository._bind_mutation_gate(self._write_gate)
        self._closed = False
        self._settings = settings
        self._paths = paths
        self._cookies = CookieStore(paths.runtime_dir / "cookies")
        self._history = ProjectEditHistory()
        self._history.register_handler(
            "sequence.archive-state",
            self._apply_sequence_archive_history_action,
        )
        self._timelines: dict[str, TimelineEditor] = {}
        self._collaboration = ProjectCollaboration(
            repository,
            self._history,
            self.timeline,
            self._reload_timelines,
        )
        self._assets = AssetService(
            repository,
            cast(MediaProbePort, MediaProbe(paths)),
            fingerprint_file,
        )
        web_contract = editable_media_contract()
        self._structured_files = LocalStructuredFileReader()
        web_validator = BrowserWebPackageValidator(paths.chromium, web_contract)
        web_services = WebMediaServices(
            repository,
            self.timeline,
            web_validator,
            self._structured_files,
            LocalWebPackageStorage(),
            web_contract,
        )
        self._web_packages = web_services.packages
        self._web_clips = web_services.clips
        self._web_batches = web_services.batches
        self._web_rebind = web_services.rebind
        self._web_preview_server: WebPackagePreviewServer | None = None
        self._web_preview_root: Path | None = None
        self._subtitle_publication = SubtitlePublicationService(
            repository,
            LocalSubtitlePublicationStorage(),
        )
        self._subtitle_publication.reconcile_document_srts()
        self._subtitle_acquisition = SubtitleAcquisitionService(
            repository,
            self._subtitle_publication,
            LocalSubtitleFileStore(),
        )
        self._dubbing = DubbingEditingService(
            repository.dubbing,
            self._require_writable,
        )
        self._portable_timelines = PortableTimelineImportService(
            repository,
            self._assets,
            self._subtitle_acquisition,
            self.timeline,
            load_portable_timeline,
        )
        self._subtitle_editing = SubtitleEditingService(
            repository,
            self._subtitle_publication,
            history=self._history,
        )
        self._transcript_editing = TranscriptEditingService(
            repository,
            self._subtitle_publication,
            self._history,
            timeline_provider=self.timeline,
        )
        self._highlights = HighlightService(repository, OpenAIJsonClient)
        self._translations = TranslationService(
            repository,
            OpenAIJsonClient,
            TranslationCache(paths.project_cache_dir(repository.project_dir) / "translations"),
            self._subtitle_publication,
        )
        self._sequences = SequenceService(repository)
        self._task_followup_updates: dict[str, WorkflowUpdate] = {}
        self._task_followup_lock = threading.RLock()
        self._task_settlement = ProjectTaskSettlement(
            repository,
            self._history,
            self._write_gate,
            self.timeline,
            self._reload_timelines,
            self._require_writable,
            self._settle_task_followups,
            self.sequence_boundary_snapshot_hash,
            self.read_loudness_metrics,
        )
        self._tasks = TaskService(
            TaskRepository(repository),
            recover_expired=not repository.read_only,
            preparation_scope=self._task_settlement.preparation_scope,
            project_change_committer=self._task_settlement.commit_project_change,
            settlement_committer=self._task_settlement.commit_settlement,
        )
        self._workflows = ProjectWorkflowService(
            repository,
            self._tasks,
            settings,
            start_task=self.start_task,
            proxy_decision=self.proxy_decision,
            create_highlight_short=self._highlights.create_short_sequence,
        )
        self._task_handlers = ProjectTaskHandlers(
            self._repository,
            self._assets,
            InfrastructureTaskRuntimes.create(
                self._repository,
                self._paths,
                self._cookies,
                self._settings,
            ),
            self._subtitle_acquisition,
            self._subtitle_editing,
            self._subtitle_publication,
            self._highlights,
            self._translations,
            settings=lambda: self._settings,
            active_llm_provider=self._active_llm_provider,
            timeline_provider=self.timeline,
        )
        self._task_handlers.register_with(self._tasks)

    @property
    def project_dir(self) -> Path:
        return self._repository.project_dir

    @property
    def read_only(self) -> bool:
        return self._repository.read_only

    @property
    def known_content_revision(self) -> int:
        return self._repository.known_content_revision

    def _require_writable(self) -> None:
        if self.read_only:
            raise PermissionError("项目以只读方式打开")

    @property
    def can_undo(self) -> bool:
        return self._collaboration.can_undo

    @property
    def can_redo(self) -> bool:
        return self._collaboration.can_redo

    def list_history(self) -> list[ProjectUndoGroup]:
        return self._collaboration.list_history()

    def history_target(
        self,
        direction: Literal["undo", "redo"],
        *,
        undo_group_id: str | None = None,
    ) -> ProjectUndoGroup:
        return self._collaboration.history_target(
            direction,
            undo_group_id=undo_group_id,
        )

    def execute_history_command(
        self,
        direction: Literal["undo", "redo"],
        *,
        request_id: str,
        base_revision: int,
        actor: ActorIdentity,
        undo_group_id: str | None = None,
        on_event: Callable[[ProjectChangeEvent], None] | None = None,
    ) -> tuple[dict[str, Any], ProjectChangeEvent]:
        return self._collaboration.execute_history_command(
            direction,
            request_id=request_id,
            base_revision=base_revision,
            actor=actor,
            undo_group_id=undo_group_id,
            on_event=on_event,
        )

    def execute_automation_batch(
        self,
        commands: list[AutomationBatchCommand],
        *,
        batch_id: str,
        label: str,
        base_revision: int,
        idempotency_base_revision: int,
        on_event: Callable[[ProjectChangeEvent], None] | None = None,
    ) -> tuple[list[dict[str, Any]], ProjectChangeEvent]:
        return self._collaboration.execute_batch(
            commands,
            batch_id=batch_id,
            label=label,
            base_revision=base_revision,
            idempotency_base_revision=idempotency_base_revision,
            on_event=on_event,
        )

    def execute_automation_request(
        self,
        request_id: str | None,
        operation: str,
        arguments: dict[str, Any],
        action: Callable[[bool], dict[str, Any]],
        *,
        atomic: bool,
        base_revision: int | None = None,
        idempotency_base_revision: int | None | object = DEFAULT_IDEMPOTENCY_BASE,
        actor: ActorIdentity,
        mutation_plan: ProjectMutationPlan,
        undo_group_id: str | None = None,
        on_event: Callable[[ProjectChangeEvent], None] | None = None,
        force_event: bool = False,
        reversible: bool = False,
    ) -> tuple[dict[str, Any], ProjectChangeEvent | None]:
        return self._collaboration.execute_request(
            request_id,
            operation,
            arguments,
            action,
            atomic=atomic,
            base_revision=base_revision,
            idempotency_base_revision=idempotency_base_revision,
            actor=actor,
            mutation_plan=mutation_plan,
            undo_group_id=undo_group_id,
            on_event=on_event,
            force_event=force_event,
            reversible=reversible,
        )

    def replay_automation_request(
        self,
        request_id: str | None,
        operation: str,
        arguments: dict[str, Any],
        *,
        base_revision: int | None,
        actor: ActorIdentity,
        write_set: list[str],
        undo_group_id: str | None = None,
    ) -> tuple[dict[str, Any], ProjectChangeEvent | None] | None:
        return self._collaboration.replay_request(
            request_id,
            operation,
            arguments,
            base_revision=base_revision,
            actor=actor,
            write_set=write_set,
            undo_group_id=undo_group_id,
        )

    def automation_request_is_running(
        self,
        request_id: str | None,
        operation: str,
        arguments: dict[str, Any],
        *,
        base_revision: int | None,
        actor: ActorIdentity,
        write_set: list[str],
        undo_group_id: str | None = None,
    ) -> bool:
        return self._collaboration.request_is_running(
            request_id,
            operation,
            arguments,
            base_revision=base_revision,
            actor=actor,
            write_set=write_set,
            undo_group_id=undo_group_id,
        )

    # This class is the sole application API for an open project. Desktop and
    # automation callers do not receive repositories or concrete services.
    def timeline(self, sequence_id: str) -> TimelineEditor:
        editor = self._timelines.get(sequence_id)
        if editor is None:
            editor = TimelineEditor(self._repository, sequence_id, self._history)
            self._timelines[sequence_id] = editor
        return editor

    def _reload_timelines(self) -> None:
        for sequence_id, editor in list(self._timelines.items()):
            try:
                editor.reload()
            except Exception:
                self._timelines.pop(sequence_id, None)

    def close(self, *, timeout: float | None = None) -> None:
        if self._closed:
            return
        self.close_web_preview()
        self._tasks.shutdown(timeout=timeout)
        self._repository.close()
        self._closed = True

    @property
    def write_gate(self) -> ProjectCommandQueue:
        return self._write_gate

    def observe_implicit_project_events(
        self,
        observer: Callable[[ProjectChangeEvent], None] | None,
    ) -> None:
        self._repository.events.observe_implicit_changes(observer)

    def _settle_task_followups(self, task: Task) -> WorkflowUpdate:
        starts_import_workflow = (
            task.status == TaskStatus.COMPLETED
            and isinstance(task.outcome, ImportedAssetTaskOutcome)
            and task.outcome.purpose == "media"
        )
        if not task.status.is_terminal or self.read_only:
            return WorkflowUpdate()
        if task.command.workflow is None and not starts_import_workflow:
            return WorkflowUpdate()
        with self._task_followup_lock:
            previous = self._task_followup_updates.get(task.id)
            current = (
                self._workflows.handle_task(task) if task.command.workflow is not None else WorkflowUpdate()
            )
            if starts_import_workflow:
                outcome = task.outcome
                if not isinstance(outcome, ImportedAssetTaskOutcome):
                    raise RuntimeError("Imported task follow-up lost its persisted outcome")
                current = current.merge(
                    self._workflows.begin_import(
                        task.sequence_id or self._repository.projects.get_project().main_sequence_id,
                        outcome.asset_id,
                        source_task_id=task.id,
                    )
                )
            merged = current if previous is None else previous.merge(current)
            self._task_followup_updates[task.id] = merged
            return merged

    def reload_external_changes(self) -> None:
        self._repository.acknowledge_content_revision()
        self._history.clear()
        for editor in self._timelines.values():
            editor.reload()

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


class EditorApplication(EditorApplicationPresentationCommands):
    """Single composition root shared by desktop and headless entry points."""

    def __init__(self, runtime: RuntimeContext | None = None):
        self.runtime = runtime or RuntimeContext.discover()
        self._paths = self.runtime.paths
        CacheManager(self._paths.runtime_dir / "cache").prune_runs()
        self._settings_repository = ServiceSettingsRepository()
        self.service_settings = self._settings_repository.load()
        self._settings_repository.prepare_storage(self.service_settings)
        self.cookies = CookieStore(self._paths.runtime_dir / "cookies")
        self._encoder_discovery = EncoderDiscoveryService(self._paths)
        self._presentation = ProjectPresentationService(self._paths)
        self._proof_frames = TimelineProofFrameService(self._paths)
        self.reference_comparison: ReferenceComparisonCapability = ReferenceVideoComparisonService(
            self._paths
        )
        self.runtime_inspection: RuntimeInspectionCapability = RuntimeInspectionService(
            self.runtime,
            lambda: self.service_settings,
        )
        self.speech: SpeechCapability = InfrastructureSpeechService(
            lambda: self.service_settings,
            self._paths,
        )
        self.media_resources = MediaResourceService(
            load_media_resource_catalog,
            lambda: self.service_settings.resource_library.catalog_paths,
        )

    @property
    def mlt_runtime_root(self) -> str:
        return str(self._paths.mlt_root) if self._paths.mlt_root else ""

    @property
    def mlt_library_path(self) -> str:
        return str(self._paths.mlt_library) if self._paths.mlt_library else ""

    @property
    def mlt_repository_path(self) -> str:
        return str(self._paths.mlt_repository) if self._paths.mlt_repository else ""

    @property
    def mlt_preview_repository_path(self) -> str:
        return str(self._paths.mlt_preview_repository) if self._paths.mlt_preview_repository else ""

    @property
    def mlt_data_path(self) -> str:
        return str(self._paths.mlt_data) if self._paths.mlt_data else ""

    @property
    def native_qml_root(self) -> Path | None:
        return self._paths.native_qml

    @property
    def runtime_paths(self) -> RuntimePaths:
        return self._paths

    @property
    def default_media_directory(self) -> str:
        return default_media_root()

    @property
    def default_project_directory(self) -> str:
        return self.service_settings.default_project_directory

    def save_service_settings(self) -> None:
        self._settings_repository.save(self.service_settings)

    def replace_service_settings(self, settings: ServiceSettings) -> None:
        # Persist first so a disk error cannot leave the running application in
        # a state that was never durably accepted.
        self._settings_repository.save(settings)
        self.service_settings = self._settings_repository.normalize(settings)
        self._settings_repository.prepare_storage(self.service_settings)

    def discover_encoder_policy_options(self) -> list[dict]:
        return self._encoder_discovery.video_options()

    def analyze_download_url(
        self,
        url: str,
        *,
        check_cancelled: Callable[[], None] | None = None,
    ) -> DownloadPlan:
        return YtDlpDownloadService.analyze_configured(
            url,
            settings=self.service_settings.download,
            cookies=self.cookies,
            paths=self._paths,
            check_cancelled=check_cancelled,
        )

    @staticmethod
    def test_llm_provider(provider: LlmProviderSettings) -> None:
        OpenAIJsonClient(provider).test_connection()

    def runtime_tool_status(self) -> dict:
        return RuntimeToolService(self.service_settings, self._paths).status()

    def installed_asr_models(self) -> frozenset[str]:
        return FasterWhisperModelStore(
            self.service_settings.asr,
            self._paths,
        ).installed_models()

    def run_runtime_tool(
        self,
        operation: str,
        *,
        arguments: dict | None = None,
        progress: Callable[[OperationProgress], None],
        check_cancelled: Callable[[], None],
    ) -> object:
        tools = RuntimeToolService(self.service_settings, self._paths)
        values = arguments or {}
        if operation == "inspect":
            return tools.cuda_readiness()
        if operation == "update_ytdlp":
            return tools.update_ytdlp(
                progress=progress,
                check_cancelled=check_cancelled,
            )
        if operation == "install_components":
            return tools.install_components(
                [str(item) for item in values.get("component_ids") or ()],
                progress=progress,
                check_cancelled=check_cancelled,
            )
        if operation == "install_speaker_clustering":
            return tools.install_speaker_clustering(
                progress=progress,
                check_cancelled=check_cancelled,
            )
        if operation == "prewarm_asr_cli":
            return str(
                tools.prewarm_cli(
                    progress=progress,
                    check_cancelled=check_cancelled,
                )
            )
        raise ValueError(f"Unknown runtime tool operation: {operation}")

    def create_project(
        self,
        root: str | Path,
        name: str,
        profile: ProjectProfile | None = None,
    ) -> EditorProject:
        repository = ProjectRepository.create(
            root,
            name,
            profile,
        )
        return EditorProject(
            repository,
            settings=self.service_settings,
            paths=self._paths,
        )

    def open_project(
        self,
        root: str | Path,
        *,
        writable: bool = True,
    ) -> EditorProject:
        repository = ProjectRepository.open(
            root,
            writable=writable,
            migration_chromium=self._paths.chromium,
        )
        project = EditorProject(
            repository,
            settings=self.service_settings,
            paths=self._paths,
        )
        if writable and not project.read_only:
            project.reconcile_workflow()
        return project

    def recent_projects(self, paths: list[str]) -> RecentProjectSnapshot:
        return self._presentation.recent_projects(paths)

    def asset_thumbnail_paths(
        self,
        project_dir: str | Path,
        *,
        width: int = 160,
        height: int = 90,
    ) -> dict[str, str]:
        return self._presentation.asset_thumbnail_paths(
            project_dir,
            width=width,
            height=height,
        )

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
        return self._presentation.timeline_filmstrip_paths(
            project_dir,
            sequence_id,
            visible_start_frame=visible_start_frame,
            visible_end_frame=visible_end_frame,
            pixels_per_frame=pixels_per_frame,
            height=height,
            request_owner=request_owner,
            request_generation=request_generation,
        )

    def cancel_timeline_filmstrip_requests(
        self,
        project_dir: str | Path,
        *,
        request_owner: str,
        request_generation: int,
    ) -> None:
        self._presentation.cancel_timeline_filmstrip_requests(
            project_dir,
            request_owner=request_owner,
            request_generation=request_generation,
        )
