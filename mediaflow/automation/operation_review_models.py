from __future__ import annotations

from typing import Literal

from pydantic import Field

from mediaflow.domain.model_base import DomainModel
from mediaflow.domain.review import (
    ReviewMarkupShape,
    ReviewPackageResult,
    ReviewPriority,
    ReviewSnapshot,
    ReviewThread,
)

from .operation_model_common import SequenceArguments


class ReviewListArguments(SequenceArguments):
    status: Literal["open", "resolved", "archived", "all"] = "open"


class ReviewThreadCreateArguments(SequenceArguments):
    start_frame: int = Field(ge=0)
    end_frame: int | None = Field(default=None, ge=1)
    clip_id: str | None = None
    subject: str = Field(default="", max_length=240)
    priority: ReviewPriority = "normal"
    body: str = Field(min_length=1, max_length=20_000)


class ReviewThreadArguments(SequenceArguments):
    thread_id: str = Field(min_length=1)


class ReviewThreadReplyArguments(ReviewThreadArguments):
    body: str = Field(min_length=1, max_length=20_000)


class ReviewMessageEditArguments(ReviewThreadReplyArguments):
    message_id: str = Field(min_length=1)


class ReviewThreadUpdateArguments(ReviewThreadArguments):
    start_frame: int = Field(ge=0)
    end_frame: int | None = Field(default=None, ge=1)
    clip_id: str | None = None
    subject: str = Field(default="", max_length=240)
    priority: ReviewPriority = "normal"


class ReviewThreadResult(DomainModel):
    thread: ReviewThread


class ReviewThreadListResult(DomainModel):
    threads: list[ReviewThread]


class ReviewSummaryResult(DomainModel):
    open: int = Field(ge=0)
    resolved: int = Field(ge=0)
    archived: int = Field(ge=0)
    blocking_open: int = Field(ge=0)


class ReviewSnapshotCaptureArguments(ReviewThreadArguments):
    frame: int = Field(ge=0)
    use_proxies: bool = True


class ReviewSnapshotMarkupArguments(ReviewThreadArguments):
    snapshot_id: str = Field(min_length=1)
    markup: list[ReviewMarkupShape] = Field(max_length=4096)


class ReviewSnapshotResult(DomainModel):
    snapshot: ReviewSnapshot
    thread: ReviewThread


class ReviewPackageExportArguments(SequenceArguments):
    destination: str = Field(min_length=1)
    thread_ids: list[str] | None = None
    overwrite: bool = False


class ReviewPackageImportArguments(SequenceArguments):
    source: str = Field(min_length=1)


ReviewPackageOperationResult = ReviewPackageResult
