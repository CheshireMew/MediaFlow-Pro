from __future__ import annotations

from pathlib import Path

from mediaflow.application.ports import DiagnosticsTaskRuntime
from mediaflow.application.task_handler_support import ProjectTaskHandler
from mediaflow.application.task_service import TaskCompletion, TaskContext
from mediaflow.domain.task_commands import DiagnosticsBundleCommand
from mediaflow.domain.tasks import ArtifactReference, DiagnosticsBundleTaskOutcome


class DiagnosticsBundleTaskHandler(ProjectTaskHandler):
    def __init__(
        self,
        project_dir: Path,
        runtime: DiagnosticsTaskRuntime,
    ):
        super().__init__(project_dir)
        self._runtime = runtime

    def handle(self, context: TaskContext) -> TaskCompletion:
        command = self.command(context, DiagnosticsBundleCommand)
        output, sha256, included_count, skipped_count = self._runtime.create_bundle(
            command,
            check_cancelled=context.cancellation.raise_if_requested,
            report=context.report,
        )
        artifact = ArtifactReference.from_path(self.project_dir, output)
        return self.completion(
            output,
            outcome=DiagnosticsBundleTaskOutcome(
                output=artifact,
                bundle_sha256=sha256,
                included_file_count=included_count,
                skipped_item_count=skipped_count,
            ),
        )
