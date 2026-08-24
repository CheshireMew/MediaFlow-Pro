from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mediaflow.application.ports import DubbingDocuments
from mediaflow.domain.dubbing import DubbingSession, DubbingUtterance


class DubbingEditingService:
    """Review dubbing plans and invalidate only the derived audio they affect."""

    def __init__(
        self,
        documents: DubbingDocuments,
        require_writable: Callable[[], None],
    ) -> None:
        self.documents = documents
        self.require_writable = require_writable

    def get_session(self, session_id: str) -> DubbingSession:
        return self.documents.get_session(session_id)

    def list_sessions(self, *, sequence_id: str | None = None) -> list[DubbingSession]:
        return self.documents.list_sessions(sequence_id=sequence_id)

    def update_speaker(
        self,
        session_id: str,
        speaker_id: str,
        *,
        expected_revision: int,
        display_name: str,
        review_status: str,
        primary_reference_id: str,
    ) -> DubbingSession:
        self.require_writable()
        session = self.documents.get_session(session_id)
        speaker = next(item for item in session.speakers if item.id == speaker_id)
        if review_status not in {"automatic", "accepted", "needs_review"}:
            raise ValueError("说话人审校状态无效")
        name = display_name.strip()
        if not name:
            raise ValueError("说话人名称不能为空")
        if primary_reference_id not in {item.id for item in speaker.references}:
            raise KeyError(primary_reference_id)
        primary_changed = (
            speaker.primary_reference is None
            or speaker.primary_reference.id != primary_reference_id
        )
        speakers = [
            item.model_copy(
                update={
                    "display_name": name,
                    "review_status": review_status,
                    "references": [
                        reference.model_copy(
                            update={"primary": reference.id == primary_reference_id}
                        )
                        for reference in item.references
                    ],
                }
            )
            if item.id == speaker_id
            else item
            for item in session.speakers
        ]
        utterances = session.utterances
        if primary_changed:
            utterances = [
                self._reset_utterance(item)
                if item.speaker_id == speaker_id
                else item
                for item in utterances
            ]
        updated = session.model_copy(
            update={
                "speakers": speakers,
                "utterances": utterances,
                **(self._reset_master() if primary_changed else {}),
            }
        )
        return self.documents.save_session(
            updated,
            expected_revision=expected_revision,
        )

    def update_reference(
        self,
        session_id: str,
        speaker_id: str,
        reference_id: str,
        *,
        expected_revision: int,
        text: str,
        language: str,
    ) -> DubbingSession:
        self.require_writable()
        session = self.documents.get_session(session_id)
        normalized_text = text.strip()
        normalized_language = language.strip()
        if not normalized_text or not normalized_language:
            raise ValueError("参考音频原文和语言不能为空")
        found = False
        primary_changed = False
        speakers = []
        for speaker in session.speakers:
            if speaker.id != speaker_id:
                speakers.append(speaker)
                continue
            references = []
            for reference in speaker.references:
                if reference.id == reference_id:
                    found = True
                    primary_changed = reference.primary and (
                        reference.text != normalized_text
                        or reference.language != normalized_language
                    )
                    references.append(
                        reference.model_copy(
                            update={
                                "text": normalized_text,
                                "language": normalized_language,
                            }
                        )
                    )
                else:
                    references.append(reference)
            speakers.append(speaker.model_copy(update={"references": references}))
        if not found:
            raise KeyError(reference_id)
        utterances = [
            self._reset_utterance(item)
            if primary_changed and item.speaker_id == speaker_id
            else item
            for item in session.utterances
        ]
        updated = session.model_copy(
            update={
                "speakers": speakers,
                "utterances": utterances,
                **(self._reset_master() if primary_changed else {}),
            }
        )
        return self.documents.save_session(
            updated,
            expected_revision=expected_revision,
        )

    def update_utterance(
        self,
        session_id: str,
        utterance_id: str,
        *,
        expected_revision: int,
        target_text: str,
        speaker_id: str,
        review_status: str,
    ) -> DubbingSession:
        self.require_writable()
        session = self.documents.get_session(session_id)
        text = target_text.strip()
        if not text:
            raise ValueError("配音译文不能为空")
        if speaker_id not in {item.id for item in session.speakers}:
            raise KeyError(speaker_id)
        if review_status not in {"automatic", "accepted", "needs_review"}:
            raise ValueError("配音句子审校状态无效")
        found = False
        changed = False
        utterances = []
        for utterance in session.utterances:
            if utterance.id != utterance_id:
                utterances.append(utterance)
                continue
            found = True
            changed = (
                utterance.target_text != text
                or utterance.speaker_id != speaker_id
            )
            candidate = utterance.model_copy(
                update={
                    "target_text": text,
                    "speaker_id": speaker_id,
                    "review_status": review_status,
                }
            )
            utterances.append(
                self._reset_utterance(candidate) if changed else candidate
            )
        if not found:
            raise KeyError(utterance_id)
        updated = session.model_copy(
            update={
                "utterances": utterances,
                **(self._reset_master() if changed else {}),
            }
        )
        return self.documents.save_session(
            updated,
            expected_revision=expected_revision,
        )

    @staticmethod
    def _reset_utterance(utterance: DubbingUtterance) -> DubbingUtterance:
        return utterance.model_copy(
            update={
                "status": "pending",
                "output_path": None,
                "output_sha256": None,
                "natural_duration_seconds": None,
                "fitted_duration_seconds": None,
                "speed_factor": 1.0,
                "reference_sha256": None,
                "issues": [
                    issue
                    for issue in utterance.issues
                    if issue != "译文在最大语速下仍超出可用时长，请缩短译文"
                ],
            }
        )

    @staticmethod
    def _reset_master() -> dict[str, Any]:
        return {
            "status": "review",
            "master_path": None,
            "master_sha256": None,
            "master_duration_seconds": None,
            "master_asset_id": None,
        }
