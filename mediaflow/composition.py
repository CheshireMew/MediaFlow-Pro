from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

from mediaflow.application.asset_service import AssetService
from mediaflow.application.edit_history import (
    ProjectEditAction,
    ProjectEditCommand,
    ProjectEditHistory,
)
from mediaflow.application.events import TaskEvent
from mediaflow.application.highlight_service import HighlightService
from mediaflow.application.ports import MediaProbePort
from mediaflow.application.project_command_queue import ProjectCommandQueue
from mediaflow.application.project_task_handlers import ProjectTaskHandlers
from mediaflow.application.project_workflow_service import ProjectWorkflowService
from mediaflow.application.sequence_service import SequenceService
from mediaflow.application.subtitle_acquisition import SubtitleAcquisitionService
from mediaflow.application.subtitle_editing import SubtitleEditingService
from mediaflow.application.subtitle_publication import SubtitlePublicationService
from mediaflow.application.task_service import TaskService, TaskSettlementPersistence
from mediaflow.application.timeline_clock import asset_in_timeline_clock
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.application.transcript_editing import TranscriptEditingService
from mediaflow.application.translation_service import TranslationService
from mediaflow.application.web_media_service import WebMediaServices
from mediaflow.application.web_package_files import web_package_root
from mediaflow.application.workflow_stage_handlers import WorkflowUpdate
from mediaflow.atomic_file import atomic_write_text
from mediaflow.domain.collaboration import (
    ActiveUndoGroupState,
    ActorIdentity,
    ProjectChange,
    ProjectChangeEvent,
    ProjectRevisionConflict,
    ProjectUndoGroup,
)
from mediaflow.domain.downloads import DownloadPlan
from mediaflow.domain.enums import (
    AssetKind,
    AssetOrigin,
    TaskStatus,
    TrackKind,
)
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.project import ProjectProfile
from mediaflow.domain.settings import LlmProviderSettings, ServiceSettings
from mediaflow.domain.storage_names import content_addressed_child_path
from mediaflow.domain.task_commands import (
    ImportAssetCommand,
    TaskCommand,
)
from mediaflow.domain.tasks import (
    ArtifactReference,
    DownloadAnalysisTaskOutcome,
    ExportTaskOutcome,
    ImportedAssetTaskOutcome,
    LoudnessTaskOutcome,
    SequenceBoundaryTaskOutcome,
    SequenceBuildTaskOutcome,
    Task,
)
from mediaflow.domain.timeline import Clip, TimelineState, Track, default_clip_media_kind
from mediaflow.infrastructure.asr_models import FasterWhisperModelStore
from mediaflow.infrastructure.cache_manager import CacheManager
from mediaflow.infrastructure.cookie_store import CookieStore
from mediaflow.infrastructure.encoder_discovery import EncoderDiscoveryService
from mediaflow.infrastructure.fcpxml_export import FcpxmlExportService
from mediaflow.infrastructure.file_fingerprint import fingerprint_file
from mediaflow.infrastructure.llm_client import OpenAIJsonClient
from mediaflow.infrastructure.media_probe import MediaProbe
from mediaflow.infrastructure.media_thumbnail_service import MediaThumbnailService
from mediaflow.infrastructure.mlt import (
    LoudnessAnalysisService,
    SequenceBoundaryAnalysisService,
    TimelineCompiler,
)
from mediaflow.infrastructure.project_cover_service import ProjectCoverService
from mediaflow.infrastructure.project_migration_runner import ProjectUpgradeRequiredError
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.proxy_service import ProxyService
from mediaflow.infrastructure.runtime_context import RuntimeContext
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from mediaflow.infrastructure.runtime_tools import RuntimeToolService
from mediaflow.infrastructure.settings_repository import ServiceSettingsRepository
from mediaflow.infrastructure.storage_paths import default_media_root
from mediaflow.infrastructure.task_repository import TaskRepository
from mediaflow.infrastructure.task_runtime import InfrastructureTaskRuntimes
from mediaflow.infrastructure.translation_cache import TranslationCache
from mediaflow.infrastructure.web_browser import (
    BrowserWebPackageValidator,
    WebPackagePreviewServer,
)
from mediaflow.infrastructure.web_render_service import WebRenderCache, WebRenderService
from mediaflow.infrastructure.ytdlp_service import YtDlpDownloadService

_UNSET_AUTOMATION_BASE = object()


def _user_visible_task_artifacts(
    task: Task,
) -> tuple[ArtifactReference, ...]:
    """Return deliverables, excluding internal graphs and QA evidence."""

    if isinstance(task.outcome, ExportTaskOutcome):
        return tuple(item.output for item in task.outcome.files)
    if isinstance(task.outcome, SequenceBuildTaskOutcome):
        return (task.outcome.output.output,)
    return tuple(task.artifacts)


def _task_project_write_set(task: Task) -> list[str]:
    command = task.command
    command_type = str(getattr(command, "command_type", task.kind.value))
    paths: list[str]
    if command_type in {"import_asset", "download_media", "generate_proxy"}:
        paths = ["/assets"]
    elif command_type in {
        "transcribe_sequence",
        "translate_document",
        "translate_segments",
    }:
        paths = ["/subtitles"]
    elif command_type == "analyze_highlights":
        paths = ["/highlights"]
    elif command_type in {"export_sequence", "build_sequence", "export_highlights"}:
        paths = ["/exports/history"]
    elif command_type in {"render_web_clip", "export_web_clip"}:
        paths = ["/web/cache"]
    else:
        paths = [f"/tasks/{task.id}/output"]
    if command.workflow is not None:
        paths.append("/workflow")
    return paths


def _automation_request_input_hash(
    *,
    arguments: dict[str, Any],
    base_revision: int | None,
    actor: ActorIdentity,
    write_set: list[str],
    undo_group_id: str | None,
) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "arguments": arguments,
                "base_revision": base_revision,
                "actor": actor.model_dump(mode="json"),
                "write_set": write_set,
                "undo_group_id": undo_group_id,
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _project_write_paths_overlap(left: str, right: str) -> bool:
    normalized_left = left.rstrip("/")
    normalized_right = right.rstrip("/")
    return (
        normalized_left == normalized_right
        or normalized_left.startswith(normalized_right + "/")
        or normalized_right.startswith(normalized_left + "/")
    )


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

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> ProjectTaskResult:
        workflow = values.get("workflow") or {}
        download_plan = values.get("download_plan")
        audio_metrics = values.get("audio_metrics")
        return cls(
            workflow=WorkflowUpdate(
                selected_asset_ids=[str(value) for value in workflow.get("selected_asset_ids") or []],
                status_message=str(workflow.get("status_message") or ""),
            ),
            imported_asset_id=str(values.get("imported_asset_id") or ""),
            imported_document_id=str(values.get("imported_document_id") or ""),
            imported_purpose=str(values.get("imported_purpose") or ""),
            download_plan=(DownloadPlan.model_validate(download_plan) if download_plan is not None else None),
            sequence_bounds_status=str(values.get("sequence_bounds_status") or ""),
            sequence_id=str(values.get("sequence_id") or ""),
            audio_metrics=(
                {str(key): float(value) for key, value in audio_metrics.items()}
                if isinstance(audio_metrics, dict)
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class AutomationBatchCommand:
    request_id: str
    operation: str
    arguments: dict[str, Any]
    actor: ActorIdentity
    write_set: list[str]
    action: Callable[[], dict[str, Any]]


class EditorProject:
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
        self._assets = AssetService(
            repository,
            cast(MediaProbePort, MediaProbe(paths)),
            fingerprint_file,
        )
        web_validator = BrowserWebPackageValidator(paths.chromium)
        web_services = WebMediaServices(repository, self.timeline, web_validator)
        self._web_packages = web_services.packages
        self._web_clips = web_services.clips
        self._web_batches = web_services.batches
        self._web_rebind = web_services.rebind
        self._web_preview_server: WebPackagePreviewServer | None = None
        self._web_preview_root: Path | None = None
        self._subtitle_publication = SubtitlePublicationService(repository)
        self._subtitle_publication.reconcile_document_srts()
        self._subtitle_acquisition = SubtitleAcquisitionService(
            repository,
            self._subtitle_publication,
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
        self._tasks = TaskService(
            TaskRepository(repository),
            recover_expired=not repository.read_only,
            preparation_scope=self._task_preparation_scope,
            project_change_committer=self._commit_task_project_change,
            settlement_committer=self._commit_task_settlement,
        )
        self._workflows = ProjectWorkflowService(
            repository,
            self._tasks,
            settings,
            start_task=self.start_task,
            proxy_decision=self.proxy_decision,
            create_highlight_short=self._highlights.create_short_sequence,
        )
        self._task_followup_updates: dict[str, WorkflowUpdate] = {}
        self._task_followup_lock = threading.RLock()
        self._task_handlers = ProjectTaskHandlers(
            self._repository,
            self._assets,
            InfrastructureTaskRuntimes.create(
                self._repository,
                self._paths,
                self._cookies,
            ),
            self._subtitle_acquisition,
            self._subtitle_editing,
            self._subtitle_publication,
            self._highlights,
            self._translations,
            settings=lambda: self._settings,
            active_llm_provider=self._active_llm_provider,
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
        return self._repository.history.has_applied()

    @property
    def can_redo(self) -> bool:
        return self._repository.history.has_undone()

    def list_history(self) -> list[ProjectUndoGroup]:
        return self._repository.history.list_groups()

    def history_target(
        self,
        direction: Literal["undo", "redo"],
        *,
        undo_group_id: str | None = None,
    ) -> ProjectUndoGroup:
        group = (
            self._repository.history.get(undo_group_id)
            if undo_group_id
            else (
                self._repository.history.latest_applied()
                if direction == "undo"
                else self._repository.history.latest_undone()
            )
        )
        expected_state = "applied" if direction == "undo" else "undone"
        if group is None or group.state != expected_state:
            raise RuntimeError(f"Nothing to {direction}")
        return group

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
        input_hash = _automation_request_input_hash(
            arguments={
                "direction": direction,
                "undo_group_id": undo_group_id,
            },
            base_revision=base_revision,
            actor=actor,
            write_set=[],
            undo_group_id=undo_group_id,
        )
        operation = f"history.{direction}"
        with self._repository.transaction():
            cached = self._repository.automation_result(
                request_id,
                operation,
                input_hash,
            )
            if cached is not None:
                event = self._repository.events.for_request(request_id)
                if event is None:
                    raise RuntimeError("Persisted history request has no project event")
                return cached, event
            before_revision = self._repository.content_revision()
            group = self.history_target(
                direction,
                undo_group_id=undo_group_id,
            )
            self._raise_if_write_set_conflicts(
                start_revision=base_revision,
                current_revision=before_revision,
                write_set=group.write_set,
                reason="one or more fields changed after the requested base revision",
            )
            self._ensure_history_command_handlers(group.command)
            self._raise_if_history_conflicts(group, current_revision=before_revision)
            actions = (
                group.command.undo_actions
                if direction == "undo"
                else group.command.redo_actions
            )
            with self._repository.coalesced_revision():
                self._history.apply_actions(actions)
            after_revision = self._repository.content_revision()
            if after_revision != before_revision + 1:
                raise RuntimeError(
                    f"history.{direction} must advance exactly one revision"
                )
            state: ActiveUndoGroupState = (
                "undone" if direction == "undo" else "applied"
            )
            transitioned = self._repository.history.transition(
                group.id,
                expected=cast(ActiveUndoGroupState, group.state),
                state=state,
                state_revision=after_revision,
            )
            result = {
                "direction": direction,
                "undo_group": transitioned.model_dump(
                    mode="json",
                    exclude_computed_fields=True,
                ),
                "can_undo": self._repository.history.latest_applied() is not None,
                "can_redo": self._repository.history.latest_undone() is not None,
            }
            stored = self._repository.save_automation_result(
                request_id,
                operation,
                input_hash,
                result,
            )
            event = self._repository.events.append(
                base_revision=before_revision,
                project_revision=after_revision,
                operation=operation,
                actor=actor,
                request_id=request_id,
                undo_group_id=group.id,
                write_set=group.write_set,
                changes=[
                    ProjectChange(path=path, action="invoke")
                    for path in group.write_set
                ],
                operation_result=stored,
                inverse_command=group.command,
            )
            if on_event is not None:
                self._repository.enlist_transaction_publication(
                    on_commit=lambda: on_event(event),
                    on_rollback=lambda _error: None,
                )
            return stored, event

    def _ensure_history_command_handlers(self, command: ProjectEditCommand) -> None:
        for action in (*command.undo_actions, *command.redo_actions):
            prefix = "timeline.restore:"
            if action.kind.startswith(prefix):
                sequence_id = action.kind.removeprefix(prefix).strip()
                if not sequence_id:
                    raise RuntimeError("Persisted timeline action has no sequence id")
                self.timeline(sequence_id)

    def _raise_if_history_conflicts(
        self,
        group: ProjectUndoGroup,
        *,
        current_revision: int,
    ) -> None:
        self._raise_if_write_set_conflicts(
            start_revision=group.state_revision,
            current_revision=current_revision,
            write_set=group.write_set,
            reason="one or more fields changed after the undo target",
        )

    def _raise_if_write_set_conflicts(
        self,
        *,
        start_revision: int,
        current_revision: int,
        write_set: list[str],
        reason: str,
    ) -> None:
        if start_revision > current_revision:
            raise ProjectRevisionConflict(
                expected_revision=start_revision,
                current_revision=current_revision,
                write_set=write_set,
                conflicting_events=[],
                reason="the requested base revision is newer than the project",
            )
        events = self._repository.events.list_after_revision(start_revision)
        covered_revision = start_revision
        conflicts: list[ProjectChangeEvent] = []
        for event in events:
            if event.base_revision != covered_revision:
                raise ProjectRevisionConflict(
                    expected_revision=start_revision,
                    current_revision=current_revision,
                    write_set=write_set,
                    conflicting_events=[],
                    reason="the durable event journal does not cover the requested revision",
                )
            if any(
                _project_write_paths_overlap(left, right)
                for left in write_set
                for right in event.write_set
            ):
                conflicts.append(event)
            covered_revision = event.project_revision
        if covered_revision != current_revision:
            raise ProjectRevisionConflict(
                expected_revision=start_revision,
                current_revision=current_revision,
                write_set=write_set,
                conflicting_events=[],
                reason="the durable event journal does not reach the current revision",
            )
        if conflicts:
            raise ProjectRevisionConflict(
                expected_revision=start_revision,
                current_revision=current_revision,
                write_set=write_set,
                conflicting_events=conflicts,
                reason=reason,
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
        if not commands:
            raise ValueError("Automation batch must contain at least one command")
        checkpoint = self._history.checkpoint()
        try:
            with self._repository.transaction():
                before_revision = self._repository.content_revision()
                if base_revision != before_revision:
                    raise RuntimeError(
                        "Project revision conflict: "
                        f"expected {base_revision}, current {before_revision}"
                    )
                results: list[dict[str, Any]] = []
                with self._repository.coalesced_revision():
                    for command in commands:
                        input_hash = _automation_request_input_hash(
                            arguments=command.arguments,
                            base_revision=idempotency_base_revision,
                            actor=command.actor,
                            write_set=command.write_set,
                            undo_group_id=batch_id,
                        )
                        cached = self._repository.automation_result(
                            command.request_id,
                            command.operation,
                            input_hash,
                        )
                        if cached is not None:
                            raise RuntimeError(
                                "Atomic collaboration batch contains an already completed request"
                            )
                        result = command.action()
                        results.append(
                            self._repository.save_automation_result(
                                command.request_id,
                                command.operation,
                                input_hash,
                                result,
                            )
                        )
                after_revision = self._repository.content_revision()
                if after_revision != before_revision + 1:
                    raise RuntimeError(
                        "Atomic collaboration batch must advance exactly one revision"
                    )
                durable_command = self._history.combined_since(
                    checkpoint,
                    label=label,
                )
                if durable_command is None:
                    raise RuntimeError(
                        "Atomic collaboration batch did not produce an inverse command"
                    )
                combined_write_set = sorted(
                    {path for command in commands for path in command.write_set}
                )
                self._history.squash_since(checkpoint, label=label)
                self._repository.history.record_group(
                    group_id=batch_id,
                    source_revision=after_revision,
                    label=durable_command.label,
                    actor=commands[0].actor,
                    write_set=combined_write_set,
                    command=durable_command,
                )
                event_result = {
                    "batch_id": batch_id,
                    "results": [
                        {
                            "request_id": command.request_id,
                            "result": result,
                        }
                        for command, result in zip(commands, results, strict=True)
                    ],
                }
                event = self._repository.events.append(
                    base_revision=before_revision,
                    project_revision=after_revision,
                    operation="operation.execute_batch",
                    actor=commands[0].actor,
                    request_id=batch_id,
                    undo_group_id=batch_id,
                    write_set=combined_write_set,
                    changes=[
                        ProjectChange(path=path, action="invoke")
                        for path in combined_write_set
                    ],
                    operation_result=event_result,
                    inverse_command=durable_command,
                )
                if on_event is not None:
                    self._repository.enlist_transaction_publication(
                        on_commit=lambda: on_event(event),
                        on_rollback=lambda _error: None,
                    )
                return results, event
        except BaseException:
            self._history.restore(checkpoint)
            for sequence_id, editor in list(self._timelines.items()):
                try:
                    editor.reload()
                except Exception:
                    self._timelines.pop(sequence_id, None)
            raise

    def execute_automation_request(
        self,
        request_id: str | None,
        operation: str,
        arguments: dict[str, Any],
        action: Callable[[bool], dict[str, Any]],
        *,
        atomic: bool,
        base_revision: int | None = None,
        idempotency_base_revision: int | None | object = _UNSET_AUTOMATION_BASE,
        actor: ActorIdentity,
        write_set: list[str] | None = None,
        undo_group_id: str | None = None,
        on_event: Callable[[ProjectChangeEvent], None] | None = None,
        force_event: bool = False,
        reversible: bool = False,
    ) -> tuple[dict[str, Any], ProjectChangeEvent | None]:
        identity = actor
        command_write_set = list(write_set or ())
        if not request_id:
            return action(False), None
        input_hash = _automation_request_input_hash(
            arguments=arguments,
            base_revision=(
                base_revision
                if idempotency_base_revision is _UNSET_AUTOMATION_BASE
                else cast(int | None, idempotency_base_revision)
            ),
            actor=identity,
            write_set=command_write_set,
            undo_group_id=undo_group_id,
        )
        if not atomic:
            cached, retrying = self._repository.begin_automation_request(
                request_id,
                operation,
                input_hash,
            )
            if cached is not None:
                return cached, None
            return self._repository.save_automation_result(
                request_id,
                operation,
                input_hash,
                action(retrying),
            ), None
        history_checkpoint = self._history.checkpoint()
        try:
            with self._repository.transaction():
                cached = self._repository.automation_result(
                    request_id,
                    operation,
                    input_hash,
                )
                if cached is not None:
                    return cached, self._repository.events.for_request(request_id)
                before_revision = self._repository.content_revision()
                if base_revision is None:
                    raise ValueError("base_revision is required for project writes")
                if base_revision != before_revision:
                    raise RuntimeError(
                        "Project revision conflict: "
                        f"expected {base_revision}, current {before_revision}"
                    )
                result = action(False)
                command = (
                    self._history.combined_since(history_checkpoint)
                    if reversible
                    else None
                )
                stored = self._repository.save_automation_result(
                    request_id,
                    operation,
                    input_hash,
                    result,
                )
                after_revision = self._repository.content_revision()
                if reversible and after_revision != before_revision and command is None:
                    raise RuntimeError(
                        f"Reversible operation {operation!r} did not produce an inverse command"
                    )
                event = None
                if force_event or after_revision != before_revision:
                    group_id = undo_group_id or request_id
                    if command is None:
                        self._repository.history.discard_redo()
                    else:
                        self._repository.history.record_group(
                            group_id=group_id,
                            source_revision=after_revision,
                            label=command.label,
                            actor=identity,
                            write_set=command_write_set,
                            command=command,
                        )
                    event = self._repository.events.append(
                        base_revision=before_revision,
                        project_revision=after_revision,
                        operation=operation,
                        actor=identity,
                        request_id=request_id,
                        undo_group_id=group_id,
                        write_set=command_write_set,
                        changes=[
                            ProjectChange(
                                path=path,
                                action="invoke",
                                value=result,
                            )
                            for path in command_write_set
                        ],
                        operation_result=result,
                        inverse_command=command,
                        replace_implicit=operation == "project.upgrade",
                    )
                    if on_event is not None:
                        self._repository.enlist_transaction_publication(
                            on_commit=lambda: on_event(event),
                            on_rollback=lambda _error: None,
                        )
                return stored, event
        except BaseException:
            self._history.restore(history_checkpoint)
            for sequence_id, editor in list(self._timelines.items()):
                try:
                    editor.reload()
                except Exception:
                    self._timelines.pop(sequence_id, None)
            raise

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
        """Return an exact durable retry before applying revision conflict rules."""

        if not request_id:
            return None
        input_hash = _automation_request_input_hash(
            arguments=arguments,
            base_revision=base_revision,
            actor=actor,
            write_set=write_set,
            undo_group_id=undo_group_id,
        )
        result = self._repository.automation_result(
            request_id,
            operation,
            input_hash,
        )
        if result is None:
            return None
        return result, self._repository.events.for_request(request_id)

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
        if not request_id:
            return False
        input_hash = _automation_request_input_hash(
            arguments=arguments,
            base_revision=base_revision,
            actor=actor,
            write_set=write_set,
            undo_group_id=undo_group_id,
        )
        return self._repository._automation_request_is_running(
            request_id,
            operation,
            input_hash,
        )

    # This class is the sole application API for an open project. Desktop and
    # automation callers do not receive repositories or concrete services.
    def get_project(self):
        return self._repository.catalog.get_project()

    def content_revision(self) -> int:
        return self._repository.content_revision()

    @property
    def owns_project_writer(self) -> bool:
        return self._repository.owns_project_lock and not self._repository.read_only

    def list_project_events(self, *, after_cursor: int = 0) -> list[ProjectChangeEvent]:
        return self._repository.events.list_events(after_cursor=after_cursor)

    def project_event_cursor(self) -> int:
        return self._repository.events.latest_cursor()

    def project_event_for_undo_group(
        self,
        undo_group_id: str,
    ) -> ProjectChangeEvent | None:
        return self._repository.events.for_undo_group(undo_group_id)

    def list_project_events_after_revision(self, revision: int) -> list[ProjectChangeEvent]:
        return self._repository.events.list_after_revision(revision)

    def has_pending_project_upgrade(self) -> bool:
        return self._repository.events.has_pending_upgrade()

    def get_sequence(self, sequence_id: str):
        return self._repository.catalog.get_sequence(sequence_id)

    def list_sequences(self, *, include_archived: bool = False):
        return self._repository.catalog.list_sequences(include_archived=include_archived)

    def create_short_sequence(self, name: str, profile: ProjectProfile | None = None):
        return self._repository.catalog.create_short_sequence(name, profile)

    def get_asset(self, asset_id: str):
        return self._repository.catalog.get_asset(asset_id)

    def list_assets(self):
        return self._repository.catalog.list_assets()

    def list_asset_bins(self):
        return self._repository.catalog.list_asset_bins()

    def create_asset_bin(self, name: str, parent_id: str | None = None):
        self._require_writable()
        return self._repository.catalog.create_asset_bin(name, parent_id)

    def move_assets_to_bin(self, asset_ids: list[str], bin_id: str | None):
        self._require_writable()
        return self._repository.catalog.move_assets_to_bin(asset_ids, bin_id)

    def resolve_asset_path(self, asset):
        return self._repository.catalog.resolve_asset_path(asset)

    def load_timeline(self, sequence_id: str) -> TimelineState:
        return self._repository.timeline.load_timeline(sequence_id)

    def get_subtitle_document(self, document_id: str):
        return self._repository.subtitles.get_subtitle_document(document_id)

    def list_subtitle_documents(
        self,
        asset_id: str | None = None,
        *,
        sequence_id: str | None = None,
    ):
        return self._repository.subtitles.list_subtitle_documents(
            asset_id,
            sequence_id=sequence_id,
        )

    def list_subtitle_segments(self, document_id: str):
        return self._repository.subtitles.list_subtitle_segments(document_id)

    def list_subtitle_words(self, document_id: str, *, include_excluded: bool = True):
        return self._repository.subtitles.list_subtitle_words(
            document_id,
            include_excluded=include_excluded,
        )

    def subtitle_segment_summary(self, document_id: str) -> tuple[int, int, int]:
        return self._repository.subtitles.subtitle_segment_summary(document_id)

    def place_subtitle_document(self, *args: Any, **kwargs: Any):
        return self._repository.subtitles.place_subtitle_document(*args, **kwargs)

    def list_subtitle_placements(self, track_id: str):
        return self._repository.subtitles.list_subtitle_placements(track_id)

    def get_subtitle_placement(self, placement_id: str):
        return self._repository.subtitles.get_subtitle_placement(placement_id)

    def update_subtitle_placement_text(self, placement_id: str, text_override: str | None):
        return self._repository.subtitles.update_subtitle_placement_text(
            placement_id,
            text_override,
        )

    def apply_subtitle_placement_to_document(self, placement_id: str, text: str):
        return self._repository.subtitles.apply_subtitle_placement_to_document(
            placement_id,
            text,
        )

    def get_web_asset_spec(self, asset_id: str):
        return self._web_packages.inspect_asset(asset_id)

    def list_web_assets(self):
        return self._repository.web.list_web_asset_specs()

    def web_editor_entry_url(self, asset_id: str) -> str:
        asset = self._repository.catalog.get_asset(asset_id)
        spec = self._web_packages.inspect_asset(asset_id)
        package_root = web_package_root(
            self._repository.catalog.resolve_asset_path(asset),
            spec.manifest,
        )
        if self._web_preview_server is None or self._web_preview_root != package_root:
            self.close_web_preview()
            self._web_preview_server = WebPackagePreviewServer(package_root)
            self._web_preview_root = package_root
        return self._web_preview_server.url_for(
            spec.manifest.entry,
            query=(
                f"capture=1&variant={spec.manifest.default_variant_id}&scene={spec.manifest.scenes[0].id}"
            ),
        )

    def close_web_preview(self) -> None:
        if self._web_preview_server is not None:
            self._web_preview_server.close()
        self._web_preview_server = None
        self._web_preview_root = None

    def web_render_cache_ready(self, state: TimelineState, clip_id: str) -> bool:
        try:
            clip = next(item for item in state.clips if item.id == clip_id)
        except StopIteration as error:
            raise KeyError(clip_id) from error
        asset = self._repository.catalog.get_asset(clip.asset_id)
        return WebRenderCache(
            self._repository,
            self._paths,
        ).target(state, clip, asset).path.is_file()

    def list_audio_buses(self, sequence_id: str):
        return self._repository.audio.list_audio_buses(sequence_id)

    def save_audio_bus(self, bus):
        return self._repository.audio.save_audio_bus(bus)

    def list_audio_effects(self, bus_id: str):
        return self._repository.audio.list_audio_effects(bus_id)

    def save_audio_effect(self, effect):
        return self._repository.audio.save_audio_effect(effect)

    def save_audio_effect_chain(self, bus_id: str, effects: list):
        return self._repository.audio.save_audio_effect_chain(bus_id, effects)

    def remove_audio_effect(self, effect_id: str) -> None:
        self._repository.audio.remove_audio_effect(effect_id)

    def list_export_history(self, sequence_id: str | None = None):
        return self._repository.records.list_export_history(sequence_id)

    def save_sequence_export_preset(self, sequence_id: str, preset):
        return self._repository.catalog.save_sequence_export_preset(sequence_id, preset)

    def list_highlights(self, asset_id: str | None = None):
        return self._repository.highlights.list_highlights(asset_id)

    def list_workflow_runs(self, *, active_only: bool = False):
        return self._repository.catalog.list_workflow_runs(active_only=active_only)

    def import_external_asset(self, source: str | Path, *, expected_kind=None):
        return self._assets.import_external(source, expected_kind=expected_kind)

    def capture_asset_frame(self, asset_id: str, frame: int, sequence_id: str):
        self._require_writable()
        asset = self._repository.catalog.get_asset(asset_id)
        sequence = self._repository.catalog.get_sequence(sequence_id)
        asset = asset_in_timeline_clock(
            self._repository.catalog,
            asset,
            sequence,
        )
        path = MediaThumbnailService(self._paths).capture_frame(
            self._repository,
            asset,
            frame=max(0, int(frame)),
            profile=sequence.profile,
        )
        return self._assets.register_output(path, AssetOrigin.GENERATED)

    def relink_asset(
        self,
        asset_id: str,
        replacement: str | Path,
        *,
        allow_different_content: bool = False,
    ):
        return self._assets.relink(
            asset_id,
            replacement,
            allow_different_content=allow_different_content,
        )

    def relink_offline_assets(self, directory: str | Path):
        return self._assets.relink_offline_from_directory(directory)

    def suggested_profile(self, asset_id: str) -> ProjectProfile | None:
        return self._assets.suggested_profile(asset_id)

    def adopt_main_profile_from_video(self, asset_id: str):
        return self._assets.adopt_main_profile_from_video(asset_id)

    def update_subtitle_segment(self, *args: Any, **kwargs: Any):
        return self._subtitle_editing.update_segment(*args, **kwargs)

    def add_subtitle_segment(self, *args: Any, **kwargs: Any):
        return self._subtitle_editing.add_segment(*args, **kwargs)

    def delete_subtitle_segments(self, document_id: str, segment_ids: list[str]) -> int:
        return self._subtitle_editing.delete_segments(document_id, segment_ids)

    def merge_subtitle_segments(self, document_id: str, segment_ids: list[str]):
        return self._subtitle_editing.merge_segments(document_id, segment_ids)

    def split_subtitle_segment(self, *args: Any, **kwargs: Any):
        return self._subtitle_editing.split_segment(*args, **kwargs)

    def smart_split_subtitle_document(self, document_id: str, *, text_limit: int = 24) -> int:
        return self._subtitle_editing.smart_split_document(document_id, text_limit=text_limit)

    def fix_subtitle_overlaps(self, document_id: str) -> int:
        return self._subtitle_editing.fix_overlaps(document_id)

    def selected_subtitle_segments_srt(self, document_id: str, segment_ids: list[str]) -> str:
        return self._subtitle_editing.selected_segments_srt(document_id, segment_ids)

    def replace_selected_subtitle_texts(self, *args: Any, **kwargs: Any):
        return self._subtitle_editing.replace_selected_texts(*args, **kwargs)

    def replace_all_subtitle_text(self, *args: Any, **kwargs: Any):
        return self._subtitle_editing.replace_all(*args, **kwargs)

    def replace_subtitle_match(self, *args: Any, **kwargs: Any):
        return self._subtitle_editing.replace_match(*args, **kwargs)

    def find_subtitle_matches(self, *args: Any, **kwargs: Any):
        return self._subtitle_editing.find_matches(*args, **kwargs)

    def update_subtitle_placement_range(self, *args: Any, **kwargs: Any):
        return self._subtitle_editing.update_placement_range(*args, **kwargs)

    def reset_subtitle_placement_range(self, placement_id: str):
        return self._subtitle_editing.reset_placement_range(placement_id)

    def write_subtitle_srt(
        self,
        document_id: str,
        destination: str | Path | None = None,
    ) -> Path:
        return self._subtitle_publication.write_document_srt(document_id, destination)

    def inspect_transcript(self, *args: Any, **kwargs: Any):
        return self._transcript_editing.inspect_transcript(*args, **kwargs)

    def preview_transcript_edit(self, *args: Any, **kwargs: Any):
        return self._transcript_editing.preview_plan(*args, **kwargs)

    def apply_transcript_edit(self, *args: Any, **kwargs: Any):
        return self._transcript_editing.apply_plan(*args, **kwargs)

    def add_manual_highlight(self, *args: Any, **kwargs: Any):
        return self._highlights.add_manual_candidate(*args, **kwargs)

    def update_highlight(self, *args: Any, **kwargs: Any):
        return self._highlights.update_candidate(*args, **kwargs)

    def set_highlight_selected(self, candidate_id: str, selected: bool):
        return self._highlights.set_selected(candidate_id, selected)

    def delete_highlight(self, candidate_id: str) -> None:
        self._highlights.delete_candidate(candidate_id)

    def create_highlight_short(self, candidate_id: str, *, name: str | None = None):
        return self._highlights.create_short_sequence(candidate_id, name=name)

    def selected_highlights(self, asset_id: str | None = None):
        return self._highlights.selected_candidates(asset_id)

    def create_short_from_range(self, *args: Any, **kwargs: Any):
        return self._sequences.create_short_from_range(*args, **kwargs)

    def create_short_from_bounds(self, *args: Any, **kwargs: Any):
        return self._sequences.create_short_from_bounds(*args, **kwargs)

    def subscribe_task_events(
        self,
        callback: Callable[[TaskEvent], None],
        *,
        include_snapshot: bool = True,
    ) -> int:
        return self._tasks.events.subscribe(callback, include_snapshot=include_snapshot)

    def unsubscribe_task_events(self, token: int) -> None:
        self._tasks.events.unsubscribe(token)

    def list_tasks(self) -> list[Task]:
        return self._tasks.list()

    def task_snapshot(self) -> tuple[list[Task], int]:
        return self._tasks.snapshot()

    def task_events_after(self, cursor: int, *, limit: int = 500) -> list[TaskEvent]:
        return self._tasks.events_after(cursor, limit=limit)

    def get_task(self, task_id: str) -> Task:
        return self._tasks.get(task_id)

    def wait_for_task(self, task_id: str, timeout: float | None = None) -> Task:
        return self._tasks.wait(task_id, timeout)

    def resume_task(
        self,
        task_id: str,
        *,
        allow_existing: bool = False,
    ) -> Task:
        self._require_writable()
        return self._tasks.resume(
            task_id,
            allow_existing=allow_existing,
        )

    def retry_task(self, task_id: str) -> Task:
        self._require_writable()
        return self._tasks.retry(task_id)

    def pause_task(self, task_id: str) -> None:
        self._require_writable()
        self._tasks.pause(task_id)

    def cancel_task(self, task_id: str) -> None:
        self._require_writable()
        self._tasks.cancel(task_id)

    def pause_all_tasks(self) -> int:
        self._require_writable()
        return self._tasks.pause_all()

    def cancel_all_tasks(self) -> int:
        self._require_writable()
        return self._tasks.cancel_all()

    def delete_task(self, task_id: str) -> None:
        self._require_writable()
        self._tasks.delete(task_id)

    def clear_task_history(self) -> int:
        self._require_writable()
        return self._tasks.clear_history()

    def active_workflow(self):
        return self._workflows.active_run()

    def set_workflow_mode(self, value: bool | None) -> None:
        self._workflows.set_project_mode(value)

    def begin_download_workflow(self, *args: Any, **kwargs: Any):
        return self._workflows.begin_download(*args, **kwargs)

    def attach_export_task(self, run_id: str, task_id: str) -> None:
        self._workflows.attach_export_task(run_id, task_id)

    def cancel_workflow(self, run_id: str) -> WorkflowUpdate:
        return self._workflows.cancel(run_id)

    def skip_workflow(self, run_id: str) -> WorkflowUpdate:
        return self._workflows.skip(run_id)

    def continue_workflow(self, *args: Any, **kwargs: Any) -> WorkflowUpdate:
        return self._workflows.continue_run(*args, **kwargs)

    def reconcile_workflow(self) -> None:
        self._require_writable()
        self._workflows.reconcile_interrupted()

    def import_web_package(self, source: str | Path):
        return self._web_packages.import_package(source)

    def populate_sample_project(self) -> None:
        from mediaflow.application.sample_project_service import SampleProjectService

        self._require_writable()
        SampleProjectService(
            self._repository,
            self.timeline,
            self.project_dir,
        ).populate()

    def inspect_web_asset(self, asset_id: str):
        return self._web_packages.inspect_asset(asset_id)

    def get_web_clip(self, clip_id: str):
        return self._web_clips.get_clip(clip_id)

    def describe_web_clip_editing(self, *args: Any, **kwargs: Any):
        return self._web_clips.describe_clip_editing(*args, **kwargs)

    def update_web_clip(self, *args: Any, **kwargs: Any):
        return self._web_clips.update_clip(*args, **kwargs)

    def diff_web_clip_update(self, *args: Any, **kwargs: Any):
        return self._web_clips.diff_clip_update(*args, **kwargs)

    def select_web_variant(self, *args: Any, **kwargs: Any):
        return self._web_clips.select_variant(*args, **kwargs)

    def commit_web_runtime_state(self, *args: Any, **kwargs: Any):
        return self._web_clips.commit_runtime_state(*args, **kwargs)

    def set_web_keyframe(self, *args: Any, **kwargs: Any):
        return self._web_clips.set_keyframe(*args, **kwargs)

    def remove_web_keyframe(self, *args: Any, **kwargs: Any):
        return self._web_clips.remove_keyframe(*args, **kwargs)

    def move_web_keyframe(self, *args: Any, **kwargs: Any):
        return self._web_clips.move_keyframe(*args, **kwargs)

    def update_web_parameter(self, *args: Any, **kwargs: Any):
        return self._web_clips.update_parameter(*args, **kwargs)

    def set_web_parameter_keyframe(self, *args: Any, **kwargs: Any):
        return self._web_clips.set_parameter_keyframe(*args, **kwargs)

    def remove_web_parameter_keyframe(self, *args: Any, **kwargs: Any):
        return self._web_clips.remove_parameter_keyframe(*args, **kwargs)

    def move_web_parameter_keyframe(self, *args: Any, **kwargs: Any):
        return self._web_clips.move_parameter_keyframe(*args, **kwargs)

    def set_web_parameter_lock(self, *args: Any, **kwargs: Any):
        return self._web_clips.set_parameter_lock(*args, **kwargs)

    def update_web_theme(self, *args: Any, **kwargs: Any):
        return self._web_clips.update_theme(*args, **kwargs)

    def update_web_data(self, *args: Any, **kwargs: Any):
        return self._web_clips.update_data(*args, **kwargs)

    def update_web_data_from_file(self, *args: Any, **kwargs: Any):
        return self._web_clips.update_data_from_file(*args, **kwargs)

    def set_web_field_locks(self, *args: Any, **kwargs: Any):
        return self._web_clips.set_field_locks(*args, **kwargs)

    def web_runtime_state(self, *args: Any, **kwargs: Any):
        return self._web_clips.runtime_state(*args, **kwargs)

    def create_web_variants(self, *args: Any, **kwargs: Any):
        return self._web_batches.create_variants(*args, **kwargs)

    def read_web_variant_records(self, source: str | Path):
        return self._web_batches.read_variant_records(source)

    def plan_web_asset_rebind(self, *args: Any, **kwargs: Any):
        return self._web_rebind.plan_rebind_asset(*args, **kwargs)

    def commit_web_asset_rebind(self, *args: Any, **kwargs: Any):
        return self._web_rebind.commit_rebind_asset(*args, **kwargs)

    def prepare_web_sequence(self, state: TimelineState) -> None:
        WebRenderService(self._repository, self._paths).ensure_sequence(state)

    def export_web_clip(self, *args: Any, **kwargs: Any):
        return WebRenderService(self._repository, self._paths).export_clip(*args, **kwargs)

    def timeline(self, sequence_id: str) -> TimelineEditor:
        editor = self._timelines.get(sequence_id)
        if editor is None:
            editor = TimelineEditor(self._repository, sequence_id, self._history)
            self._timelines[sequence_id] = editor
        return editor

    def create_version(self, name: str):
        return self._repository.records.create_project_version(name)

    def list_versions(self):
        return self._repository.records.list_project_versions()

    def restore_version(self, version_id: str):
        with self._repository.transaction():
            record = self._repository.records.restore_project_version(version_id)
            self._subtitle_publication.reconcile_document_srts()
        sequence_ids = {
            sequence.id for sequence in self._repository.catalog.list_sequences(include_archived=True)
        }
        for sequence_id, editor in list(self._timelines.items()):
            if sequence_id in sequence_ids:
                try:
                    editor.reload()
                except Exception:
                    self._timelines.pop(sequence_id, None)
            else:
                self._timelines.pop(sequence_id)
        self._history.clear()
        return record

    def export_fcpxml(
        self,
        sequence_id: str,
        destination: str | Path,
        *,
        overwrite: bool = False,
    ) -> Path:
        state = self._repository.timeline.load_timeline(sequence_id)
        exporter = FcpxmlExportService(self._repository, self._paths)
        output = exporter.preflight(
            state,
            destination,
            overwrite=overwrite,
        )
        WebRenderService(
            self._repository,
            self._paths,
        ).ensure_sequence(state)
        return exporter.export(
            state,
            output,
            overwrite=overwrite,
        )

    def proxy_decision(self, asset, *, dropped_frames: int = 0, manual: bool = False):
        return ProxyService.decision(asset, dropped_frames=dropped_frames, manual=manual)

    def sequence_boundary_snapshot_hash(self, sequence_id: str) -> str:
        state = self._repository.timeline.load_timeline(sequence_id)
        return SequenceBoundaryAnalysisService(
            TimelineCompiler(self._repository, self._paths),
            self._paths,
        ).snapshot_hash(state)

    def loudness_snapshot_hash(self, sequence_id: str) -> str:
        state = self._repository.timeline.load_timeline(sequence_id)
        return LoudnessAnalysisService(
            TimelineCompiler(self._repository, self._paths),
            self._paths,
        ).snapshot_hash(state)

    def read_loudness_metrics(self, sequence_id: str) -> dict[str, float]:
        snapshot_hash = self.loudness_snapshot_hash(sequence_id)
        path = LoudnessAnalysisService.result_path(
            self.project_dir,
            sequence_id,
            snapshot_hash,
        )
        metrics = LoudnessAnalysisService.read_metrics(
            path,
            expected_sequence_id=sequence_id,
            expected_snapshot_hash=snapshot_hash,
        )
        return metrics.desktop_payload() if metrics is not None else {}

    def start_task(
        self,
        command: TaskCommand,
        input_asset_ids: list[str] | None = None,
        *,
        sequence_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> Task:
        self._require_writable()
        project = self._repository.catalog.get_project()
        return self._tasks.start(
            project_id=project.id,
            sequence_id=sequence_id or project.main_sequence_id,
            command=command,
            input_asset_ids=input_asset_ids,
            idempotency_key=idempotency_key,
        )

    def import_asset(
        self,
        source: str | Path,
        *,
        sequence_id: str | None = None,
        purpose: str = "media",
        language: str = "auto",
        media_asset_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> Task:
        path = Path(source).expanduser().resolve(strict=True)
        if purpose not in {"media", "subtitle", "watermark"}:
            raise ValueError(f"Unknown import purpose: {purpose}")
        import_purpose = cast(Literal["media", "subtitle", "watermark"], purpose)
        return self.start_task(
            ImportAssetCommand(
                source_path=str(path),
                purpose=import_purpose,
                language=language,
                media_asset_id=media_asset_id,
            ),
            sequence_id=sequence_id,
            idempotency_key=idempotency_key,
        )

    def committed_task_result(self, task_id: str) -> ProjectTaskResult | None:
        stored = self._repository._committed_task_result(task_id)
        return ProjectTaskResult.from_dict(stored) if stored is not None else None

    def _commit_task_settlement(
        self,
        task: Task,
        persist: TaskSettlementPersistence,
        project_changes: tuple[Callable[[], None], ...],
    ) -> Task:
        """Atomically publish one settled task state and its project command."""

        with (
            self._write_gate,
            self._task_project_change_scope(task),
            self._repository.transaction(),
            self._repository.coalesced_revision(),
        ):
            completed = persist()
            for change in project_changes:
                change()
            if completed.status.is_terminal:
                self._consume_task_result(completed)
        return completed

    def _commit_task_project_change(
        self,
        task: Task,
        change: Callable[[], None],
    ) -> None:
        with (
            self._write_gate,
            self._repository._task_project_command(),
            self._task_project_change_scope(task),
            self._repository.transaction(),
            self._repository.coalesced_revision(),
        ):
            change()

    def _consume_task_result(self, task: Task) -> ProjectTaskResult:
        project = self._repository.catalog.get_project()
        if task.project_id != project.id:
            raise ValueError("Task does not belong to this project")
        if not task.status.is_consumable:
            raise ValueError("Only terminal task state can be consumed")
        self._require_writable()
        workflow = self._settle_task_followups(task)
        history_checkpoint = self._history.checkpoint()
        try:
            stored, _applied = self._repository.consume_task_result_once(
                task.id,
                task.project_id,
                task.revision,
                lambda: self._apply_task_result(
                    task,
                    workflow,
                ).as_dict(),
            )
        except Exception:
            self._history.restore(history_checkpoint)
            for editor in self._timelines.values():
                editor.reload()
            raise
        return ProjectTaskResult.from_dict(stored)

    def _apply_task_result(
        self,
        task: Task,
        workflow: WorkflowUpdate,
    ) -> ProjectTaskResult:
        imported_asset_id = ""
        imported_document_id = ""
        imported_purpose = ""
        download_plan = None
        sequence_bounds_status = ""
        sequence_id = task.sequence_id or ""
        audio_metrics = None
        if task.status == TaskStatus.COMPLETED:
            if isinstance(task.outcome, ImportedAssetTaskOutcome):
                imported_asset_id = task.outcome.asset_id
                imported_document_id = task.outcome.document_id or ""
                imported_purpose = task.outcome.purpose
            elif isinstance(task.outcome, DownloadAnalysisTaskOutcome):
                download_plan = task.outcome.plan
            elif isinstance(task.outcome, SequenceBoundaryTaskOutcome):
                analysis = task.outcome.analysis
                sequence_id = analysis.sequence_id
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
            elif isinstance(task.outcome, LoudnessTaskOutcome):
                current_hash = self.loudness_snapshot_hash(sequence_id)
                audio_metrics = {}
                if task.artifacts:
                    metrics = LoudnessAnalysisService.read_metrics(
                        task.artifacts[-1].resolve(self.project_dir),
                        expected_sequence_id=sequence_id,
                        expected_snapshot_hash=current_hash,
                    )
                    if metrics is not None:
                        audio_metrics = metrics.desktop_payload()
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

    def archive_short_sequence(self, sequence_id: str) -> None:
        sequence = self._repository.catalog.archive_short_sequence(sequence_id)
        self._history.push(
            ProjectEditCommand(
                label="删除短视频序列",
                undo_actions=[
                    ProjectEditAction(
                        kind="sequence.archive-state",
                        payload={"sequence_id": sequence.id, "archived": False},
                    )
                ],
                redo_actions=[
                    ProjectEditAction(
                        kind="sequence.archive-state",
                        payload={"sequence_id": sequence.id, "archived": True},
                    )
                ],
            )
        )

    def _apply_sequence_archive_history_action(
        self,
        action: ProjectEditAction,
    ) -> None:
        sequence_id = str(action.payload.get("sequence_id") or "")
        if bool(action.payload.get("archived")):
            self._repository.catalog.archive_short_sequence(sequence_id)
        else:
            self._repository.catalog.restore_short_sequence(sequence_id)

    def refresh_workflow_mode(self) -> ProjectWorkflowService:
        self._workflows.update_settings(self._settings)
        return self._workflows

    def update_settings(self, settings: ServiceSettings) -> None:
        self._settings = settings
        self.refresh_workflow_mode()

    def close(self, *, timeout: float | None = None) -> None:
        if self._closed:
            return
        self.close_web_preview()
        self._tasks.shutdown(timeout=timeout)
        self._repository.close()
        self._closed = True

    def _task_project_change_scope(self, task: Task):
        command = task.command
        command_type = str(getattr(command, "command_type", task.kind.value))
        write_set = _task_project_write_set(task)
        return self._repository.events.change_scope(
            operation=f"task.{command_type}",
            actor=ActorIdentity(
                kind="system",
                id="editor-service-task-runner",
                name="MediaFlow Pro task runner",
            ),
            request_id=f"task-{task.id}",
            undo_group_id=f"task-{task.id}",
            write_set=write_set,
        )

    def _task_preparation_scope(self, task: Task):
        return self._repository._task_preparation_scope(task.id)

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
                        task.sequence_id or self._repository.catalog.get_project().main_sequence_id,
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


class EditorApplication:
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
        self._media_thumbnails = MediaThumbnailService(self._paths)
        self._project_covers = ProjectCoverService(self._paths)

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
        return (
            str(self._paths.mlt_preview_repository)
            if self._paths.mlt_preview_repository
            else ""
        )

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
        if operation == "prewarm_asr_cli":
            return str(
                tools.prewarm_cli(
                    progress=progress,
                    check_cancelled=check_cancelled,
                )
            )
        raise ValueError(f"Unknown runtime tool operation: {operation}")

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
            document = TimelineCompiler(repository, self._paths).compile(
                state,
                use_proxies=use_proxies,
                native_preview=True,
                prefer_sdr_preview_proxy=prefer_sdr_preview_proxy,
            )
            sequence_namespace = "pv-" + hashlib.sha256(state.sequence.id.encode("utf-8")).hexdigest()[:12]
            preview_cache = self._paths.project_cache_dir(project_dir)
            destination = content_addressed_child_path(
                preview_cache / "mlt",
                document.xml,
                namespace=sequence_namespace,
                suffix=".mlt",
            )
            if not destination.is_file():
                atomic_write_text(destination, document.xml)
            CacheManager(preview_cache).prune_files(
                "mlt",
                f"{sequence_namespace}-*.mlt",
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
            sequence = repository.catalog.get_sequence(sequence_id)
            project = repository.catalog.get_project()
            asset = repository.catalog.get_asset(asset_id)
            if asset.kind not in {AssetKind.VIDEO, AssetKind.AUDIO, AssetKind.IMAGE}:
                raise ValueError("该素材类型不能在源监视器中播放")
            main_profile = repository.catalog.get_sequence(project.main_sequence_id).profile
            timeline_asset = asset.in_frame_clock(main_profile, sequence.profile)
            duration = timeline_asset.metadata.duration_frames or 150
            track_kind = (
                TrackKind.AUDIO if asset.kind == AssetKind.AUDIO else TrackKind.VIDEO
            )
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
            document = TimelineCompiler(repository, self._paths).compile(
                state,
                use_proxies=True,
                native_preview=True,
                prefer_sdr_preview_proxy=True,
            )
            namespace = "source-" + hashlib.sha256(asset.id.encode("utf-8")).hexdigest()[:12]
            preview_cache = self._paths.project_cache_dir(project_dir)
            destination = content_addressed_child_path(
                preview_cache / "mlt",
                document.xml,
                namespace=namespace,
                suffix=".mlt",
            )
            if not destination.is_file():
                atomic_write_text(destination, document.xml)
        return destination

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
                        item["failedTaskCount"] = sum(task.status == TaskStatus.FAILED for task in tasks)
                        item["offlineAssetCount"] = sum(
                            not repository.catalog.resolve_asset_path(asset).is_file()
                            for asset in repository.catalog.list_assets()
                        )
                        item["pendingWorkflowCount"] = len(
                            repository.catalog.list_workflow_runs(active_only=True)
                        )
                        cover = self._project_covers.cover_for(repository)
                        item["coverPath"] = str(cover) if cover else ""
                        artifacts = [
                            value.resolve(path)
                            for task in reversed(tasks)
                            for value in reversed(
                                _user_visible_task_artifacts(task)
                            )
                            if value.resolve(path).is_file()
                        ]
                        item["recentArtifact"] = str(artifacts[0]) if artifacts else ""
                except ProjectUpgradeRequiredError:
                    # A project with an older, writable-migratable schema is
                    # still available. The home screen simply omits live metrics
                    # until the user opens it through the writable boundary.
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
            for asset in repository.catalog.list_assets():
                thumbnail = self._media_thumbnails.thumbnail_for(
                    repository,
                    asset,
                    width=width,
                    height=height,
                )
                if thumbnail is not None:
                    thumbnails[asset.id] = str(thumbnail)
        return thumbnails
