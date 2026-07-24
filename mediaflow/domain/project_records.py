from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from .enums import ExportFormat
from .model_base import DomainModel, new_id, now_ms


class ExportQualityCheck(DomainModel):
    key: str
    label: str
    status: Literal["passed", "warning", "failed"]
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)


class ExportQualityReport(DomainModel):
    id: str = Field(default_factory=new_id)
    output_path: str
    passed: bool
    checks: list[ExportQualityCheck]
    proof_frames: list[str] = Field(default_factory=list)
    sha256: str
    analyzed_at: int = Field(default_factory=now_ms)


class ExportHistoryRecord(DomainModel):
    id: str
    task_id: str
    sequence_id: str
    output_path: str
    format: ExportFormat
    preset: dict[str, Any]
    quality: ExportQualityReport
    content_revision: int
    created_at: int = Field(default_factory=now_ms)


class ProjectVersionRecord(DomainModel):
    id: str = Field(default_factory=new_id)
    name: str
    snapshot_path: str
    sha256: str = ""
    content_revision: int
    created_at: int = Field(default_factory=now_ms)
