from __future__ import annotations

from pydantic import Field

from mediaflow.domain.downloads import DownloadRequest
from mediaflow.domain.enums import WorkflowStage, WorkflowStatus
from mediaflow.domain.model_base import (
    DomainModel,
    new_id,
    now_ms,
)


class WorkflowPayload(DomainModel):
    source_task_id: str = ""
    requests: list[DownloadRequest] = Field(default_factory=list)
    task_ids: list[str] = Field(default_factory=list)
    target_language: str = ""
    source_document_ids: list[str] = Field(default_factory=list)
    document_ids_before_translate: list[str] = Field(default_factory=list)
    translated_document_ids: list[str] = Field(default_factory=list)
    highlight_ids_before: list[str] = Field(default_factory=list)
    highlight_candidate_ids: list[str] = Field(default_factory=list)
    short_sequence_ids: list[str] = Field(default_factory=list)


class WorkflowPayloadPatch(DomainModel):
    source_task_id: str | None = None
    requests: list[DownloadRequest] | None = None
    task_ids: list[str] | None = None
    target_language: str | None = None
    source_document_ids: list[str] | None = None
    document_ids_before_translate: list[str] | None = None
    translated_document_ids: list[str] | None = None
    highlight_ids_before: list[str] | None = None
    highlight_candidate_ids: list[str] | None = None
    short_sequence_ids: list[str] | None = None

    def apply(self, payload: WorkflowPayload) -> WorkflowPayload:
        return payload.model_copy(update=self.model_dump(exclude_none=True))


class WorkflowRun(DomainModel):
    id: str = Field(default_factory=new_id)
    project_id: str
    sequence_id: str
    asset_ids: list[str] = Field(default_factory=list)
    stage: WorkflowStage
    status: WorkflowStatus
    auto_continue: bool = False
    payload: WorkflowPayload = Field(default_factory=WorkflowPayload)
    message_code: str = ""
    created_at: int = Field(default_factory=now_ms)
    updated_at: int = Field(default_factory=now_ms)
