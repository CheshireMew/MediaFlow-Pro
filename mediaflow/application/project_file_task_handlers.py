from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from mediaflow.application.project_collection_service import ProjectCollectionService
from mediaflow.application.task_handler_support import ProjectTaskHandler
from mediaflow.application.task_service import TaskCompletion, TaskContext
from mediaflow.application.web_package_service import WebPackageService
from mediaflow.domain.project_archive import ProjectArchiveResult
from mediaflow.domain.task_commands import (
    ArchiveProjectCommand,
    CollectProjectAssetsCommand,
    ImportWebPackageCommand,
)
from mediaflow.domain.tasks import (
    ProjectArchiveTaskOutcome,
    ProjectCollectionTaskOutcome,
    WebPackageImportTaskOutcome,
)


class PreparedProjectArchive(Protocol):
    result: ProjectArchiveResult

    def commit(self) -> None: ...
    def discard(self) -> None: ...


class ProjectFileTaskHandlers(ProjectTaskHandler):
    """Prepare expensive project files, then join the ordinary task settlement."""

    def __init__(
        self, project_dir: Path, packages: WebPackageService, collections: ProjectCollectionService,
        prepare_archive: Callable[[str, Callable[[], None]], PreparedProjectArchive],
    ) -> None:
        super().__init__(project_dir)
        self.packages, self.collections, self.prepare_archive = packages, collections, prepare_archive

    def handle(self, context: TaskContext) -> TaskCompletion:
        command = context.task.command
        check = context.cancellation.raise_if_requested
        if isinstance(command, ImportWebPackageCommand):
            check()
            prepared_web = self.packages.prepare_import(command.source)
            context.on_discard(lambda: self.packages.discard_import(prepared_web))
            check()
            def commit_web() -> None:
                self.packages.commit_import(prepared_web)
            context.defer_project_change(commit_web)
            return self.completion(
                prepared_web.asset.path,
                outcome=WebPackageImportTaskOutcome(asset_id=prepared_web.asset.id),
            )
        if isinstance(command, CollectProjectAssetsCommand):
            prepared_collection = self.collections.prepare_collection(
                command.asset_ids, check_cancelled=check,
            )
            context.on_discard(lambda: self.collections.discard_collection(prepared_collection))
            check()
            def commit_collection() -> None:
                self.collections.commit_collection(prepared_collection)
            context.defer_project_change(commit_collection)
            return self.completion(outcome=ProjectCollectionTaskOutcome(result=prepared_collection.result))
        if isinstance(command, ArchiveProjectCommand):
            prepared_archive = self.prepare_archive(command.destination, check)
            context.on_discard(prepared_archive.discard)
            check()
            context.defer_project_change(prepared_archive.commit)
            return self.completion(
                prepared_archive.result.manifest_path,
                outcome=ProjectArchiveTaskOutcome(result=prepared_archive.result),
            )
        raise TypeError(f"Unexpected project file command: {type(command).__name__}")
