from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .collaboration import ActorIdentity
from .model_base import DomainModel, new_id

ReviewStatus = Literal["open", "resolved", "archived"]
ReviewPriority = Literal["low", "normal", "high", "blocking"]
ReviewMarkupKind = Literal["freehand", "arrow", "rectangle", "ellipse", "text"]


def utc_now() -> datetime:
    return datetime.now(UTC)


class ReviewMessage(DomainModel):
    id: str = Field(default_factory=new_id)
    author: ActorIdentity
    body: str = Field(min_length=1, max_length=20_000)
    created_at: datetime = Field(default_factory=utc_now)
    edited_at: datetime | None = None

    @field_validator("body")
    @classmethod
    def normalized_body(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Review message body cannot be blank")
        return normalized

    @model_validator(mode="after")
    def coherent_edit_time(self) -> ReviewMessage:
        if self.edited_at is not None and self.edited_at < self.created_at:
            raise ValueError("Review message edit time cannot precede creation")
        return self


class ReviewMarkupPoint(DomainModel):
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)


class ReviewMarkupShape(DomainModel):
    id: str = Field(default_factory=new_id)
    kind: ReviewMarkupKind
    points: list[ReviewMarkupPoint] = Field(min_length=1, max_length=4096)
    color: str = Field(default="#ffcc55", pattern=r"^#[0-9a-fA-F]{6}$")
    width: float = Field(default=3.0, gt=0.0, le=64.0)
    text: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def coherent_shape(self) -> ReviewMarkupShape:
        required = {
            "freehand": 2,
            "arrow": 2,
            "rectangle": 2,
            "ellipse": 2,
            "text": 1,
        }[self.kind]
        if len(self.points) < required:
            raise ValueError(f"{self.kind} markup requires at least {required} points")
        if self.kind == "text" and not self.text.strip():
            raise ValueError("Text markup cannot be blank")
        return self


class ReviewSnapshot(DomainModel):
    id: str = Field(default_factory=new_id)
    frame: int = Field(ge=0)
    relative_path: str = Field(min_length=1)
    sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    markup: list[ReviewMarkupShape] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("relative_path")
    @classmethod
    def safe_relative_path(cls, value: str) -> str:
        normalized = value.replace("\\", "/").strip("/")
        if not normalized or any(part in {"", ".", ".."} for part in normalized.split("/")):
            raise ValueError("Review snapshot path must be a safe project-relative path")
        return normalized


class ReviewThread(DomainModel):
    id: str = Field(default_factory=new_id)
    sequence_id: str = Field(min_length=1)
    start_frame: int = Field(ge=0)
    end_frame: int | None = Field(default=None, ge=1)
    clip_id: str | None = None
    project_revision: int = Field(default=0, ge=0)
    sequence_timeline_revision: int = Field(default=0, ge=0)
    subject: str = Field(default="", max_length=240)
    priority: ReviewPriority = "normal"
    status: ReviewStatus = "open"
    archived_from: Literal["open", "resolved"] | None = None
    messages: list[ReviewMessage] = Field(min_length=1)
    snapshots: list[ReviewSnapshot] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    resolved_at: datetime | None = None
    resolved_by: ActorIdentity | None = None

    @field_validator("subject")
    @classmethod
    def normalized_subject(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def coherent_state(self) -> ReviewThread:
        if self.end_frame is not None and self.end_frame <= self.start_frame:
            raise ValueError("Review range must end after its start frame")
        if self.updated_at < self.created_at:
            raise ValueError("Review update time cannot precede creation")
        carries_resolution = self.status == "resolved" or (
            self.status == "archived" and self.archived_from == "resolved"
        )
        if carries_resolution:
            if self.resolved_at is None or self.resolved_by is None:
                raise ValueError("Resolved review threads require resolver metadata")
        elif self.resolved_at is not None or self.resolved_by is not None:
            raise ValueError("Only resolved review threads can carry resolver metadata")
        if self.status == "archived":
            if self.archived_from is None:
                raise ValueError("Archived review threads require their previous status")
        elif self.archived_from is not None:
            raise ValueError("Only archived review threads can carry archived_from")
        message_ids = [item.id for item in self.messages]
        if len(message_ids) != len(set(message_ids)):
            raise ValueError("Review message identifiers must be unique within a thread")
        snapshot_ids = [item.id for item in self.snapshots]
        if len(snapshot_ids) != len(set(snapshot_ids)):
            raise ValueError("Review snapshot identifiers must be unique within a thread")
        return self


class ReviewPackageResult(DomainModel):
    path: str = Field(min_length=1)
    sha256: str = Field(min_length=64, max_length=64)
    byte_count: int = Field(gt=0)
    thread_count: int = Field(ge=0)
    snapshot_count: int = Field(ge=0)
    imported_thread_ids: list[str] = Field(default_factory=list)
