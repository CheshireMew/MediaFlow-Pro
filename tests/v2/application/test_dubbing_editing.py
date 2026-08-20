from __future__ import annotations

from mediaflow.application.dubbing_editing import DubbingEditingService
from mediaflow.domain.dubbing import (
    DubbingReference,
    DubbingSession,
    DubbingSpeaker,
    DubbingUtterance,
)

_HASH = "0" * 64


def _reference(speaker_id: str, reference_id: str, *, primary: bool) -> DubbingReference:
    return DubbingReference(
        id=reference_id,
        speaker_id=speaker_id,
        path=f"generated/dubbing/references/{reference_id}.wav",
        sha256=_HASH,
        start_frame=0,
        end_frame=90,
        text=f"reference {reference_id}",
        language="en",
        duration_seconds=3.0,
        primary=primary,
    )


def _utterance(
    utterance_id: str,
    speaker_id: str,
    start_frame: int,
) -> DubbingUtterance:
    return DubbingUtterance(
        id=utterance_id,
        speaker_id=speaker_id,
        source_segment_ids=[f"source-{utterance_id}"],
        target_segment_ids=[f"target-{utterance_id}"],
        start_frame=start_frame,
        end_frame=start_frame + 90,
        source_text=f"source {utterance_id}",
        target_text=f"译文 {utterance_id}",
        status="generated",
        output_path=f"generated/dubbing/utterances/{utterance_id}.wav",
        output_sha256=_HASH,
        natural_duration_seconds=2.0,
        fitted_duration_seconds=3.0,
        speed_factor=1.0,
        reference_sha256=_HASH,
    )


def _session() -> DubbingSession:
    return DubbingSession(
        id="session-1",
        project_id="project-1",
        sequence_id="sequence-1",
        source_document_id="source-document",
        target_document_id="target-document",
        source_language="en",
        target_language="zh_CN",
        dialogue_track_id="dialogue-track",
        source_timeline_revision=1,
        status="synthesized",
        speakers=[
            DubbingSpeaker(
                id="speaker-a",
                label="SPEAKER_00",
                display_name="说话人 1",
                references=[
                    _reference("speaker-a", "reference-a1", primary=True),
                    _reference("speaker-a", "reference-a2", primary=False),
                ],
            ),
            DubbingSpeaker(
                id="speaker-b",
                label="SPEAKER_01",
                display_name="说话人 2",
                references=[
                    _reference("speaker-b", "reference-b1", primary=True),
                ],
            ),
        ],
        utterances=[
            _utterance("utterance-a", "speaker-a", 0),
            _utterance("utterance-b", "speaker-b", 90),
        ],
        master_path="generated/dubbing/master.wav",
        master_sha256=_HASH,
        master_duration_seconds=6.0,
        master_asset_id="master-asset",
        committed_track_id="committed-track",
        committed_clip_id="committed-clip",
    )


class _Documents:
    def __init__(self, session: DubbingSession) -> None:
        self.session = session

    def get_session(self, session_id: str) -> DubbingSession:
        assert session_id == self.session.id
        return self.session

    def list_sessions(self, *, sequence_id: str | None = None) -> list[DubbingSession]:
        assert sequence_id in {None, self.session.sequence_id}
        return [self.session]

    def save_session(
        self,
        session: DubbingSession,
        *,
        expected_revision: int,
    ) -> DubbingSession:
        assert expected_revision == self.session.revision
        validated = DubbingSession.model_validate(session.model_dump())
        self.session = validated.model_copy(
            update={"revision": expected_revision + 1}
        )
        return self.session


def test_switching_primary_reference_invalidates_only_that_speakers_audio() -> None:
    documents = _Documents(_session())
    service = DubbingEditingService(documents, lambda: None)

    updated = service.update_speaker(
        "session-1",
        "speaker-a",
        expected_revision=0,
        display_name="Alice",
        review_status="accepted",
        primary_reference_id="reference-a2",
    )

    speaker_a = next(item for item in updated.speakers if item.id == "speaker-a")
    utterance_a, utterance_b = updated.utterances
    assert speaker_a.primary_reference is not None
    assert speaker_a.primary_reference.id == "reference-a2"
    assert utterance_a.status == "pending"
    assert utterance_a.output_path is None
    assert utterance_b.status == "generated"
    assert utterance_b.output_path is not None
    assert updated.status == "review"
    assert updated.master_path is None
    assert updated.master_asset_id is None
    assert updated.committed_track_id == "committed-track"
    assert updated.committed_clip_id == "committed-clip"


def test_review_only_edit_preserves_audio_but_text_edit_invalidates_one_line() -> None:
    documents = _Documents(_session())
    service = DubbingEditingService(documents, lambda: None)

    reviewed = service.update_utterance(
        "session-1",
        "utterance-a",
        expected_revision=0,
        target_text="译文 utterance-a",
        speaker_id="speaker-a",
        review_status="accepted",
    )
    assert reviewed.utterances[0].status == "generated"
    assert reviewed.master_path == "generated/dubbing/master.wav"

    changed = service.update_utterance(
        "session-1",
        "utterance-a",
        expected_revision=1,
        target_text="新的中文译文",
        speaker_id="speaker-a",
        review_status="accepted",
    )
    assert changed.utterances[0].status == "pending"
    assert changed.utterances[0].output_path is None
    assert changed.utterances[1].status == "generated"
    assert changed.master_path is None
