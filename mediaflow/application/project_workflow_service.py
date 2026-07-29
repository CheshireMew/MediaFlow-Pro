from __future__ import annotations

from collections.abc import Callable

from mediaflow.application.ports import ProjectWorkflowDocuments
from mediaflow.application.task_service import TaskService
from mediaflow.application.workflow_coordinator import WorkflowCoordinator
from mediaflow.application.workflow_stage_handlers import (
    ProxyDecision,
    WorkflowStageContext,
    WorkflowTaskSpec,
    WorkflowUpdate,
    workflow_stage_handlers,
)
from mediaflow.domain.downloads import DownloadRequest
from mediaflow.domain.enums import (
    TaskStatus,
    WorkflowStage,
    WorkflowStatus,
)
from mediaflow.domain.project import Sequence
from mediaflow.domain.settings import GlobalSettings
from mediaflow.domain.task_commands import (
    TaskCommand,
    WorkflowTaskLink,
)
from mediaflow.domain.tasks import Task
from mediaflow.domain.workflows import WorkflowPayload, WorkflowPayloadPatch, WorkflowRun

StartTask = Callable[[TaskCommand, list[str] | None], Task]
CreateHighlightShort = Callable[[str], Sequence]


class ProjectWorkflowService:
    """The only use-case boundary that advances persisted project workflows."""

    def __init__(
        self,
        documents: ProjectWorkflowDocuments,
        tasks: TaskService,
        settings: GlobalSettings,
        *,
        start_task: Callable[..., Task],
        proxy_decision: Callable[..., ProxyDecision],
        create_highlight_short: CreateHighlightShort,
    ):
        self.documents = documents
        self.tasks = tasks
        self.settings = settings
        self._start_task = start_task
        self._proxy_decision = proxy_decision
        self._create_highlight_short = create_highlight_short
        self._stage_handlers = workflow_stage_handlers()
        self.coordinator = WorkflowCoordinator(
            documents,
            global_auto_continue=settings.workflow.auto_continue,
        )

    def update_settings(self, settings: GlobalSettings) -> None:
        self.settings = settings
        self.coordinator = WorkflowCoordinator(
            self.documents,
            global_auto_continue=settings.workflow.auto_continue,
        )

    def active_run(self) -> WorkflowRun | None:
        runs = self.documents.catalog.list_workflow_runs(active_only=True)
        return runs[0] if runs else None

    def set_project_mode(self, value: bool | None) -> None:
        self.documents.catalog.set_workflow_auto_continue(value)
        self.update_settings(self.settings)

    def begin_import(
        self,
        sequence_id: str,
        asset_id: str,
        *,
        source_task_id: str = "",
    ) -> WorkflowUpdate:
        if source_task_id and any(
            run.payload.source_task_id == source_task_id
            for run in self.documents.catalog.list_workflow_runs()
        ):
            return WorkflowUpdate(selected_asset_ids=[asset_id])
        run = self.coordinator.begin(
            sequence_id=sequence_id,
            stage=WorkflowStage.PREPARE_MEDIA,
            asset_ids=[asset_id],
            payload=WorkflowPayload(source_task_id=source_task_id),
        )
        return self._continue_if_configured(run)

    def begin_download(
        self,
        sequence_id: str,
        requests: list[DownloadRequest],
    ) -> WorkflowUpdate:
        if not requests:
            raise ValueError("下载工作流至少需要一个下载项目")
        run = self.coordinator.begin(
            sequence_id=sequence_id,
            stage=WorkflowStage.DOWNLOAD,
            payload=WorkflowPayload(requests=requests),
        )
        return self._stage_handlers[run.stage].start(self._stage_context(), run)

    def attach_export_task(self, run_id: str, task_id: str) -> None:
        self.coordinator.mark_running(run_id, task_ids=[task_id])

    def cancel(self, run_id: str) -> WorkflowUpdate:
        run = self.documents.catalog.get_workflow_run(run_id)
        for task_id in run.payload.task_ids:
            try:
                task = self.tasks.get(str(task_id))
            except KeyError:
                continue
            if task.status.is_active:
                self.tasks.cancel(task.id)
        self.coordinator.cancel(run_id)
        return WorkflowUpdate()

    def skip(self, run_id: str) -> WorkflowUpdate:
        run = self.documents.catalog.get_workflow_run(run_id)
        if run.status in {WorkflowStatus.COMPLETED, WorkflowStatus.CANCELLED}:
            return WorkflowUpdate()
        skippable = {
            WorkflowStage.PREPARE_MEDIA,
            WorkflowStage.TRANSCRIBE,
            WorkflowStage.TRANSLATE,
            WorkflowStage.HIGHLIGHT,
            WorkflowStage.CREATE_SHORTS,
        }
        if run.stage not in skippable:
            raise ValueError(f"当前工作流阶段不能跳过：{run.stage.value}")
        for task_id in run.payload.task_ids:
            try:
                task = self.tasks.get(str(task_id))
            except KeyError:
                continue
            if task.status.is_active:
                self.tasks.cancel(task.id)
        advanced = self.coordinator.advance(run.id)
        return self._continue_if_configured(advanced).merge(
            WorkflowUpdate(status_message=f"已跳过工作流阶段：{run.stage.value}")
        )

    def continue_run(
        self,
        run_id: str,
        *,
        target_language: str = "",
    ) -> WorkflowUpdate:
        run = self.documents.catalog.get_workflow_run(run_id)
        if run.status in {WorkflowStatus.COMPLETED, WorkflowStatus.CANCELLED}:
            return WorkflowUpdate()
        if run.status == WorkflowStatus.RUNNING:
            return WorkflowUpdate(status_message="当前工作流阶段正在运行")

        if not run.asset_ids and run.stage not in {WorkflowStage.DOWNLOAD, WorkflowStage.EXPORT}:
            self.coordinator.block(run.id, "workflow_assets_required")
            return WorkflowUpdate()
        offline = [
            asset_id
            for asset_id in run.asset_ids
            if self.documents.catalog.get_asset(asset_id).status.value == "offline"
        ]
        if offline:
            self.coordinator.block(run.id, "workflow_offline_assets")
            return WorkflowUpdate()
        return self._stage_handlers[run.stage].start(
            self._stage_context(),
            run,
            target_language=target_language,
        )

    def handle_task(self, task: Task) -> WorkflowUpdate:
        if not task.status.is_terminal:
            return WorkflowUpdate()
        link = task.command.workflow
        if link is None:
            return WorkflowUpdate()
        try:
            run = self.documents.catalog.get_workflow_run(link.run_id)
        except KeyError:
            return WorkflowUpdate()
        if run.status != WorkflowStatus.RUNNING or run.stage != link.stage:
            return WorkflowUpdate()
        task_ids = run.payload.task_ids
        if task.id not in task_ids:
            return WorkflowUpdate()
        tasks = [self.tasks.get(task_id) for task_id in task_ids]
        if any(item.status == TaskStatus.FAILED for item in tasks):
            failed = next(item for item in tasks if item.status == TaskStatus.FAILED)
            self.coordinator.block(run.id, "workflow_task_failed")
            return WorkflowUpdate(status_message=f"工作流任务失败：{failed.error or failed.kind.value}")
        if any(item.status == TaskStatus.CANCELLED for item in tasks):
            self.coordinator.block(run.id, "workflow_task_cancelled")
            return WorkflowUpdate()
        if not all(item.status == TaskStatus.COMPLETED for item in tasks):
            return WorkflowUpdate()

        return self._stage_handlers[run.stage].complete(self._stage_context(), run, tasks)

    def reconcile_interrupted(self) -> None:
        for run in self.documents.catalog.list_workflow_runs(active_only=True):
            if run.status != WorkflowStatus.RUNNING:
                continue
            task_ids = run.payload.task_ids
            if not task_ids:
                self.coordinator.block(run.id, "workflow_interrupted")
                continue
            tasks = []
            for task_id in task_ids:
                try:
                    tasks.append(self.tasks.get(task_id))
                except KeyError:
                    self.coordinator.block(run.id, "workflow_interrupted")
                    break
            else:
                if any(task.status in {TaskStatus.PENDING, TaskStatus.PAUSED} for task in tasks):
                    self.coordinator.block(run.id, "workflow_interrupted")

    def _run_workflow_tasks(
        self,
        run: WorkflowRun,
        specs: list[WorkflowTaskSpec],
        *,
        payload: WorkflowPayloadPatch | None = None,
    ) -> WorkflowUpdate:
        for task_id in run.payload.task_ids:
            try:
                existing = self.tasks.get(str(task_id))
            except KeyError:
                continue
            if existing.status.is_active:
                self.tasks.cancel(existing.id)
        stage_attempt = run.payload.stage_attempt + 1
        attempt_payload = (
            payload or WorkflowPayloadPatch()
        ).model_copy(
            update={"stage_attempt": stage_attempt}
        )
        task_ids = [
            self._start_task(
                command.model_copy(
                    update={
                        "workflow": WorkflowTaskLink(run_id=run.id, stage=run.stage),
                    }
                ),
                asset_ids,
                sequence_id=run.sequence_id,
                idempotency_key=(
                    f"workflow:{run.id}:{run.stage.value}:"
                    f"{stage_attempt}:{index}"
                ),
            ).id
            for index, (command, asset_ids) in enumerate(specs)
        ]
        self.coordinator.mark_running(
            run.id,
            task_ids=task_ids,
            payload=attempt_payload,
        )
        update = WorkflowUpdate()
        for task_id in task_ids:
            update = update.merge(self.handle_task(self.tasks.get(task_id)))
        return update

    def _stage_context(self) -> WorkflowStageContext:
        return WorkflowStageContext(
            documents=self.documents,
            coordinator=self.coordinator,
            settings=self.settings,
            start_tasks=self._run_workflow_tasks,
            continue_after=self._continue_if_configured,
            proxy_decision=self._proxy_decision,
            create_highlight_short=self._create_highlight_short,
        )

    def _continue_if_configured(self, run: WorkflowRun) -> WorkflowUpdate:
        if run.status in {WorkflowStatus.COMPLETED, WorkflowStatus.CANCELLED}:
            return WorkflowUpdate()
        if run.auto_continue or not self._stage_requires_confirmation(run.stage):
            return self.continue_run(run.id)
        return WorkflowUpdate()

    def _stage_requires_confirmation(self, stage: WorkflowStage) -> bool:
        settings = self.settings.workflow
        return {
            WorkflowStage.DOWNLOAD: settings.confirm_download,
            WorkflowStage.PREPARE_MEDIA: settings.confirm_proxy,
            WorkflowStage.TRANSCRIBE: settings.confirm_transcribe,
            WorkflowStage.TRANSLATE: settings.confirm_translate,
            WorkflowStage.HIGHLIGHT: settings.confirm_highlight,
            WorkflowStage.CREATE_SHORTS: True,
            WorkflowStage.EXPORT: settings.confirm_export,
            WorkflowStage.COMPLETE: False,
        }[stage]
