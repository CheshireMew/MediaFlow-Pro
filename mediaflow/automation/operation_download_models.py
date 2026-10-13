from __future__ import annotations

from pydantic import Field

from mediaflow.domain.downloads import DownloadRequest
from mediaflow.domain.task_commands import NonEmptyText

from .operation_model_common import SequenceArguments


class DownloadAnalyzeArguments(SequenceArguments):
    url: NonEmptyText
    timeout: float | None = Field(default=None, gt=0)


class DownloadStartArguments(SequenceArguments):
    request: DownloadRequest
    timeout: float | None = Field(default=None, gt=0)
