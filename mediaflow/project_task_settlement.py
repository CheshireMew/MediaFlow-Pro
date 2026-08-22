from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from mediaflow.application.edit_history import ProjectEditHistory
from mediaflow.application.project_command_queue import ProjectCommandQueue
from mediaflow.application.task_service import TaskSettlementPersistence
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.application.workflow_models import WorkflowUpdate
from mediaflow.domain.collaboration import ActorIdentity
from mediaflow.domain.downloads import DownloadPlan
from mediaflow.domain.enums import TaskStatus
from mediaflow.domain.tasks import (
    DownloadAnalysisTaskOutcome,
    ImportedAssetTaskOutcome,
    LoudnessTaskOutcome,
    SequenceBoundaryTaskOutcome,
    Task,
)
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.project_collaboration import require_planned_changes


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

    def as_dict(self) -> dict[str, Any]:
        return {
            "workflow": {
                "selected_asset_ids": list(self.workflow.selected_asset_ids),
                "status_source": self.workflow.status_source,
                "status_arguments": list(self.workflow.status_arguments),
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
                status_source=str(workflow.get("status_source") or ""),
                status_arguments=tuple(str(value) for value in workflow.get("status_arguments") or []),
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


class ProjectTaskSettlement:
    """Commit terminal tasks and their project effects as one transaction."""

    def __init__(
        self,
        repository: ProjectRepository,
        history: ProjectEditHistory,
        write_gate: ProjectCommandQueue,
        timeline_provider: Callable[[str], TimelineEditor],
        reload_timelines: Callable[[], None],
        require_writable: Callable[[], None],
        settle_followups: Callable[[Task], WorkflowUpdate],
        sequence_boundary_snapshot_hash: Callable[[str], str],
        read_loudness_metrics: Callable[[str], dict[str, float]],
    ) -> None:
        self._repository = repository
        self._history = history
        self._write_gate = write_gate
        self._timeline_provider = timeline_provider
        self._reload_timelines = reload_timelines
        self._require_writable = require_writable
        self._settle_followups = settle_followups
        self._sequence_boundary_snapshot_hash = sequence_boundary_snapshot_hash
        self._read_loudness_metrics = read_loudness_metrics

    def committed_result(self, task_id: str) -> ProjectTaskResult | None:
        stored = self._repository.operations.committed_task_result(task_id)
        return ProjectTaskResult.from_dict(stored) if stored is not None else None

    def commit_settlement(
        self,
        task: Task,
        persist: TaskSettlementPersistence,
        project_changes: tuple[Callable[[], None], ...],
    ) -> Task:
        checkpoint = self._history.checkpoint()
        actor = task_actor()
        operation = task_operation(task)
        group_id = f"task-{task.id}"
        try:
            with (
                self._write_gate,
                self.change_scope(task),
                self._repository.transaction(),
            ):
                before_revision = self._repository.content_revision()
                with self._repository.coalesced_revision():
                    completed = persist()
                    for change in project_changes:
                        change()
                    if completed.status.is_terminal:
                        self._consume_result(completed)
                after_revision = self._repository.content_revision()
                command = self._history.combined_since(
                    checkpoint,
                    label=f"后台任务：{operation}",
                )
                if command is not None:
                    if after_revision != before_revision + 1:
                        raise RuntimeError("A task project edit must advance exactly one revision")
                    change_set = self._history.change_set_since(checkpoint)
                    require_planned_changes(
                        operation,
                        task_project_write_set(task),
                        change_set,
                    )
                    if not change_set.changes:
                        raise RuntimeError("A reversible task project edit produced no observable changes")
                    self._history.squash_since(
                        checkpoint,
                        label=command.label,
                    )
                    self._repository.history.record_group(
                        group_id=group_id,
                        source_revision=after_revision,
                        label=command.label,
                        actor=actor,
                        write_set=change_set.write_set,
                        command=command,
                    )
                    event = self._repository.events.append(
                        base_revision=before_revision,
                        project_revision=after_revision,
                        operation=operation,
                        actor=actor,
                        request_id=f"{group_id}:{after_revision}",
                        undo_group_id=group_id,
                        write_set=change_set.write_set,
                        changes=change_set.changes,
                        operation_result={
                            "task_id": completed.id,
                            "status": completed.status.value,
                        },
                        inverse_command=command,
                    )
                    self._repository.events.publish_after_commit(event)
                return completed
        except BaseException:
            self._history.restore(checkpoint)
            self._reload_timelines()
            raise

    def commit_project_change(
        self,
        task: Task,
        change: Callable[[], None],
    ) -> None:
        with (
            self._write_gate,
            self._repository._task_project_command(),
            self.change_scope(task),
            self._repository.transaction(),
            self._repository.coalesced_revision(),
        ):
            change()

    def preparation_scope(self, task: Task):
        return self._repository._task_preparation_scope(task.id)

    def change_scope(self, task: Task):
        write_set = task_project_write_set(task)
        return self._repository.events.change_scope(
            operation=task_operation(task),
            actor=task_actor(),
            request_id=f"task-{task.id}",
            undo_group_id=f"task-{task.id}",
            write_set=write_set,
        )

    def _consume_result(self, task: Task) -> ProjectTaskResult:
        project = self._repository.projects.get_project()
        if task.project_id != project.id:
            raise ValueError("Task does not belong to this project")
        if not task.status.is_consumable:
            raise ValueError("Only terminal task state can be consumed")
        self._require_writable()
        workflow = self._settle_followups(task)
        checkpoint = self._history.checkpoint()
        try:
            stored, _applied = self._repository.operations.consume_task_result_once(
                task.id,
                task.project_id,
                task.revision,
                lambda: self._apply_result(task, workflow).as_dict(),
            )
        except Exception:
            self._history.restore(checkpoint)
            self._reload_timelines()
            raise
        return ProjectTaskResult.from_dict(stored)

    def _apply_result(
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
                current_hash = self._sequence_boundary_snapshot_hash(sequence_id)
                if analysis.snapshot_hash != current_hash:
                    sequence_bounds_status = "stale"
                else:
                    self._timeline_provider(sequence_id).set_sequence_in_out(
                        analysis.suggested.in_frame,
                        analysis.suggested.out_frame,
                    )
                    sequence_bounds_status = (
                        "applied_without_speech" if analysis.speech_in_frame is None else "applied"
                    )
            elif isinstance(task.outcome, LoudnessTaskOutcome):
                audio_metrics = self._read_loudness_metrics(sequence_id)
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


def task_project_write_set(task: Task) -> list[str]:
    command = task.command
    command_type = str(getattr(command, "command_type", task.kind.value))
    paths = [f"/tasks/{task.id}"]
    if command_type == "import_asset":
        paths.append("/assets")
        if str(getattr(command, "purpose", "media")) == "subtitle":
            paths.append("/subtitles")
    elif command_type == "download_media":
        paths.extend(("/assets", "/subtitles"))
    elif command_type in {"generate_proxy", "generate_waveform"}:
        paths.append("/assets")
    elif command_type in {
        "transcribe_sequence",
        "translate_document",
        "translate_segments",
    }:
        paths.append("/subtitles")
        if command_type == "transcribe_sequence":
            sequence_id = task.sequence_id or str(getattr(command, "sequence_id", ""))
            if not sequence_id:
                raise RuntimeError(f"Task {task.id} has no sequence mutation boundary")
            paths.extend(("/assets", f"/sequences/{sequence_id}"))
    elif command_type == "analyze_highlights":
        paths.append("/highlights")
    elif command_type in {
        "export_sequence",
        "build_sequence",
    }:
        paths.append("/records/exports")
    elif command_type == "export_highlights":
        paths.append("/sequences")
    elif command_type in {
        "analyze_sequence_bounds",
        "analyze_scenes",
        "track_subject",
    }:
        sequence_id = task.sequence_id or str(getattr(command, "sequence_id", ""))
        if not sequence_id:
            raise RuntimeError(f"Task {task.id} has no sequence mutation boundary")
        paths.append(f"/sequences/{sequence_id}")
    if command.workflow is not None:
        paths.append(f"/tasks/workflows/{command.workflow.run_id}")
    return sorted(set(paths))


def task_operation(task: Task) -> str:
    command_type = str(getattr(task.command, "command_type", task.kind.value))
    return f"task.{command_type}"


def task_actor() -> ActorIdentity:
    return ActorIdentity(
        kind="system",
        id="editor-service-task-runner",
        name="MediaFlow Pro task runner",
    )
