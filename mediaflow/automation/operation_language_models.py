from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from mediaflow.domain.dubbing import DubbingSession, DubbingSettings
from mediaflow.domain.model_base import DomainModel
from mediaflow.domain.project_records import ProjectVersionRecord
from mediaflow.domain.settings import AsrSettings
from mediaflow.domain.subtitles import SubtitleDocument, SubtitleSegment, SubtitleWord
from mediaflow.domain.transcript_edits import (
    TranscriptEditPlan,
    TranscriptEditRequest,
    TranscriptEditResult,
    TranscriptSnapshot,
)

from .operation_model_common import SequenceArguments


class TranscriptSequenceTranscribeArguments(SequenceArguments):
    asr: AsrSettings | None = None
    start_frame: int | None = Field(default=None, ge=0)
    end_frame: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def valid_range(self) -> TranscriptSequenceTranscribeArguments:
        if self.start_frame is not None and self.end_frame is not None and self.end_frame <= self.start_frame:
            raise ValueError("end_frame must be after start_frame")
        return self


class SubtitleSegmentUpdateArguments(DomainModel):
    document_id: str = Field(min_length=1)
    segment_id: str = Field(min_length=1)
    start_frame: int = Field(ge=0)
    end_frame: int = Field(gt=0)
    text: str

    @model_validator(mode="after")
    def positive_range(self) -> SubtitleSegmentUpdateArguments:
        if self.end_frame <= self.start_frame:
            raise ValueError("end_frame must be after start_frame")
        return self


class TranscriptGetArguments(SequenceArguments):
    document_id: str | None = None


class ScriptSegmentUpdateArguments(DomainModel):
    document_id: str = Field(min_length=1)
    segment_id: str = Field(min_length=1)
    text: str | None = None
    speaker: str | None = None

    @model_validator(mode="after")
    def has_change(self) -> ScriptSegmentUpdateArguments:
        if not ({"text", "speaker"} & self.model_fields_set):
            raise ValueError("Script segment update must change text or speaker")
        if "text" in self.model_fields_set:
            if self.text is None or not self.text.strip():
                raise ValueError("Script segment text cannot be empty")
            object.__setattr__(self, "text", " ".join(self.text.split()))
        return self


class ScriptSegmentSplitArguments(DomainModel):
    document_id: str = Field(min_length=1)
    segment_id: str = Field(min_length=1)
    split_frame: int | None = Field(default=None, ge=0)
    split_index: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def one_split_point(self) -> ScriptSegmentSplitArguments:
        if (self.split_frame is None) == (self.split_index is None):
            raise ValueError("Specify exactly one of split_frame or split_index")
        return self


class ScriptSegmentMergeArguments(DomainModel):
    document_id: str = Field(min_length=1)
    segment_ids: list[str] = Field(min_length=2)

    @model_validator(mode="after")
    def unique_segments(self) -> ScriptSegmentMergeArguments:
        object.__setattr__(
            self,
            "segment_ids",
            list(dict.fromkeys(value.strip() for value in self.segment_ids if value.strip())),
        )
        if len(self.segment_ids) < 2:
            raise ValueError("At least two distinct script segments are required")
        return self


class ScriptSegmentMoveArguments(SequenceArguments):
    document_id: str = Field(min_length=1)
    segment_id: str = Field(min_length=1)
    position: int = Field(ge=0)
    expected_content_revision: int = Field(ge=0)


class ScriptGapCloseArguments(SequenceArguments):
    document_id: str = Field(min_length=1)
    segment_id: str = Field(min_length=1)
    expected_content_revision: int = Field(ge=0)


class TranscriptEditPreviewArguments(DomainModel):
    edit: TranscriptEditRequest


class TranscriptEditApplyArguments(DomainModel):
    plan: TranscriptEditPlan
    accept_warnings: bool | None = None


class DubbingPrepareArguments(SequenceArguments):
    source_document_id: str = Field(min_length=1)
    target_language: str = Field(default="zh_CN", min_length=1)
    target_document_id: str | None = None
    settings: DubbingSettings = Field(default_factory=DubbingSettings)


class DubbingSessionArguments(DomainModel):
    session_id: str = Field(min_length=1)


class DubbingListArguments(SequenceArguments):
    pass


class DubbingSynthesizeArguments(SequenceArguments):
    session_id: str = Field(min_length=1)
    utterance_ids: list[str] = Field(default_factory=list)
    regenerate: bool = False


class DubbingCommitArguments(SequenceArguments):
    session_id: str = Field(min_length=1)
    track_name: str = Field(default="中文配音", min_length=1)
    mute_source_dialogue: bool = True


class DubbingSpeakerUpdateArguments(DomainModel):
    session_id: str = Field(min_length=1)
    speaker_id: str = Field(min_length=1)
    expected_revision: int = Field(ge=0)
    display_name: str = Field(min_length=1)
    review_status: Literal["automatic", "accepted", "needs_review"]
    primary_reference_id: str = Field(min_length=1)


class DubbingReferenceUpdateArguments(DomainModel):
    session_id: str = Field(min_length=1)
    speaker_id: str = Field(min_length=1)
    reference_id: str = Field(min_length=1)
    expected_revision: int = Field(ge=0)
    text: str = Field(min_length=1)
    language: str = Field(min_length=1)


class DubbingUtteranceUpdateArguments(DomainModel):
    session_id: str = Field(min_length=1)
    utterance_id: str = Field(min_length=1)
    expected_revision: int = Field(ge=0)
    target_text: str = Field(min_length=1)
    speaker_id: str = Field(min_length=1)
    review_status: Literal["automatic", "accepted", "needs_review"]


class SubtitleDocumentWithSegments(SubtitleDocument):
    segments: list[SubtitleSegment]


class SubtitleListResult(DomainModel):
    documents: list[SubtitleDocumentWithSegments]


class SubtitleSegmentResult(DomainModel):
    segment: SubtitleSegment


class DubbingSessionResult(DomainModel):
    session: DubbingSession


class DubbingSessionListResult(DomainModel):
    sessions: list[DubbingSession]


class TranscriptResult(DomainModel):
    transcript: TranscriptSnapshot


class ScriptParagraph(DomainModel):
    position: int = Field(ge=0)
    segment: SubtitleSegment
    words: list[SubtitleWord]
    timeline_start_frame: int = Field(ge=0)
    timeline_end_frame: int = Field(gt=0)
    gap_before_frames: int = Field(ge=0)
    overlap_with_previous_frames: int = Field(ge=0)
    timing_precision: Literal[
        "recognized_words",
        "mixed_words",
        "estimated_words",
        "segment_only",
    ]


class ScriptInspectResult(DomainModel):
    content_revision: int = Field(ge=0)
    sequence_id: str
    timeline_duration_frames: int = Field(ge=0)
    document: SubtitleDocument
    paragraphs: list[ScriptParagraph]
    recognized_word_count: int = Field(ge=0)
    estimated_word_count: int = Field(ge=0)
    deletion_workflow: Literal["transcript.edit.preview -> transcript.edit.apply"] = (
        "transcript.edit.preview -> transcript.edit.apply"
    )


class ScriptSegmentSplitResult(DomainModel):
    segments: list[SubtitleSegment] = Field(min_length=2, max_length=2)


class ScriptTimelineEditResult(DomainModel):
    segment: SubtitleSegment
    recovery_version: ProjectVersionRecord
    content_revision: int = Field(ge=0)
    before_duration_frames: int = Field(ge=0)
    after_duration_frames: int = Field(ge=0)
    changed_timeline_frames: int = Field(ge=1)


class TranscriptEditPlanResult(DomainModel):
    plan: TranscriptEditPlan


class TranscriptEditResultDocument(DomainModel):
    edit: TranscriptEditResult
