from __future__ import annotations

from mediaflow.automation.operation_context import OperationContext
from mediaflow.domain.downloads import DownloadRequest
from mediaflow.domain.task_commands import AnalyzeDownloadCommand, DownloadMediaCommand


def analyze(context: OperationContext) -> dict:
    return context.task_receipt(
        context.project.start_task(
            AnalyzeDownloadCommand(url=context.required("url")),
            sequence_id=context.sequence_id(),
            idempotency_key=context.task_idempotency(),
        )
    )


def start(context: OperationContext) -> dict:
    return context.task_receipt(
        context.project.start_task(
            DownloadMediaCommand(
                request=DownloadRequest.model_validate(context.required("request")),
            ),
            sequence_id=context.sequence_id(),
            idempotency_key=context.task_idempotency(),
        )
    )
