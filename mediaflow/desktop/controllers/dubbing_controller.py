from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl, Signal, Slot

from mediaflow.desktop.editor_planning import (
    current_transcription_plan,
    inferred_dialogue_track_id,
    start_current_transcription_task,
)
from mediaflow.domain.dubbing import DubbingSettings
from mediaflow.domain.task_commands import (
    CommitDubbingCommand,
    PrepareDubbingCommand,
    SynthesizeDubbingCommand,
    TranscribeSequenceCommand,
)

from .controller_facet import ControllerFacet, report_ui_errors
from .controller_scopes import CreativeControllerScope


class DubbingController(ControllerFacet[CreativeControllerScope]):
    projectStateChanged = Signal()
    tasksChanged = Signal()
    settingsChanged = Signal()

    @Slot(result="QVariantMap")
    @report_ui_errors
    def sourceReadiness(self) -> dict:
        sequence_id = self._session.state.binding.active_sequence_id
        if not sequence_id:
            return {
                "available": False,
                "active": False,
                "status": "",
                "reason": "请先打开一个项目",
            }
        matching_tasks = [
            task
            for task in self._session.state.tasks.items.values()
            if isinstance(task.command, TranscribeSequenceCommand) and task.command.sequence_id == sequence_id
        ]
        latest_task = max(
            matching_tasks,
            key=lambda item: (item.created_at, item.id),
            default=None,
        )
        try:
            plan = current_transcription_plan(self._session)
            available = plan.region_count > 0
            reason = "" if available else "主要对白轨中没有可识别的音频"
        except (RuntimeError, ValueError) as error:
            available = bool(inferred_dialogue_track_id(self._session))
            reason = "" if available else str(error)
        return {
            "available": available,
            "active": bool(latest_task and latest_task.status.is_active),
            "status": latest_task.status.value if latest_task else "",
            "reason": reason,
        }

    @Slot(str)
    @report_ui_errors
    def transcribeSource(self, language: str) -> None:
        timeline = self._session.state.binding.timeline
        dialogue_track_id = inferred_dialogue_track_id(self._session)
        if (
            timeline is not None
            and dialogue_track_id
            and not any(track.primary_dialogue for track in timeline.state.tracks)
        ):
            timeline.set_primary_dialogue_track(dialogue_track_id)
            self._session.projectors.timeline.refresh_timeline()
            self._session.updates.commit(history=True)
        selected_asr = self._session.state.service_settings.asr.model_copy(
            update={"language": language.strip() or "en"}
        )
        start_current_transcription_task(self._session, selected_asr)

    @Slot(result="QVariantList")
    @report_ui_errors
    def sourceDocuments(self) -> list[dict]:
        current = self._session.state.binding.current
        sequence_id = self._session.state.binding.active_sequence_id
        if current is None or not sequence_id:
            return []
        return [
            {
                "documentId": item.id,
                "language": item.language,
                "label": f"{item.language} · {item.id[:8]}",
            }
            for item in current.list_subtitle_documents(sequence_id=sequence_id)
            if item.is_source
        ]

    @Slot(str, result="QVariantList")
    @report_ui_errors
    def targetDocuments(self, source_document_id: str) -> list[dict]:
        current = self._session.state.binding.current
        sequence_id = self._session.state.binding.active_sequence_id
        if current is None or not sequence_id or not source_document_id:
            return []
        return [
            {
                "documentId": item.id,
                "language": item.language,
                "label": f"{item.language} · {item.id[:8]}",
            }
            for item in current.list_subtitle_documents(sequence_id=sequence_id)
            if not item.is_source and item.source_document_id == source_document_id
        ]

    @Slot(result="QVariantList")
    @report_ui_errors
    def sessions(self) -> list[dict]:
        current = self._session.state.binding.current
        sequence_id = self._session.state.binding.active_sequence_id
        if current is None or not sequence_id:
            return []
        return [self._summary(item) for item in current.list_dubbing_sessions(sequence_id=sequence_id)]

    @Slot(str, result="QVariantMap")
    @report_ui_errors
    def session(self, session_id: str) -> dict:
        current = self._session.state.binding.current
        if current is None or not session_id:
            return {}
        return self._document(current.get_dubbing_session(session_id))

    @Slot(str, str, str, int, int)
    @report_ui_errors
    def prepare(
        self,
        source_document_id: str,
        target_language: str,
        target_document_id: str,
        minimum_speakers: int,
        maximum_speakers: int,
    ) -> None:
        self._session._require_writable()
        sequence_id = self._session.state.binding.active_sequence_id
        if not source_document_id:
            raise ValueError("请先选择源字幕文档")
        minimum = minimum_speakers if minimum_speakers > 0 else None
        maximum = maximum_speakers if maximum_speakers > 0 else None
        self._session.tasks.start(
            PrepareDubbingCommand(
                sequence_id=sequence_id,
                source_document_id=source_document_id,
                target_language=target_language.strip() or "zh_CN",
                target_document_id=target_document_id or None,
                settings=DubbingSettings(
                    minimum_speakers=minimum,
                    maximum_speakers=maximum,
                ),
            )
        )

    @Slot(str, "QVariantList", bool)
    @report_ui_errors
    def synthesize(
        self,
        session_id: str,
        utterance_ids: list[str],
        regenerate: bool,
    ) -> None:
        self._session._require_writable()
        if not session_id:
            raise ValueError("请先选择配音方案")
        self._session.tasks.start(
            SynthesizeDubbingCommand(
                sequence_id=self._session.state.binding.active_sequence_id,
                session_id=session_id,
                utterance_ids=[str(item) for item in utterance_ids],
                regenerate=regenerate,
            )
        )

    @Slot(str, str, bool)
    @report_ui_errors
    def commit(
        self,
        session_id: str,
        track_name: str,
        mute_source_dialogue: bool,
    ) -> None:
        self._session._require_writable()
        self._session.tasks.start(
            CommitDubbingCommand(
                sequence_id=self._session.state.binding.active_sequence_id,
                session_id=session_id,
                track_name=track_name.strip() or "中文配音",
                mute_source_dialogue=mute_source_dialogue,
            )
        )

    @Slot(str, str, int, str, str, str)
    @report_ui_errors
    def updateSpeaker(
        self,
        session_id: str,
        speaker_id: str,
        revision: int,
        display_name: str,
        review_status: str,
        primary_reference_id: str,
    ) -> None:
        self._session._require_writable()
        current = self._session.state.binding.current
        assert current is not None
        current.update_dubbing_speaker(
            session_id,
            speaker_id,
            expected_revision=revision,
            display_name=display_name,
            review_status=review_status,
            primary_reference_id=primary_reference_id,
        )
        self._session.updates.commit(project=True)

    @Slot(str, str, str, int, str, str)
    @report_ui_errors
    def updateReference(
        self,
        session_id: str,
        speaker_id: str,
        reference_id: str,
        revision: int,
        text: str,
        language: str,
    ) -> None:
        self._session._require_writable()
        current = self._session.state.binding.current
        assert current is not None
        current.update_dubbing_reference(
            session_id,
            speaker_id,
            reference_id,
            expected_revision=revision,
            text=text,
            language=language,
        )
        self._session.updates.commit(project=True)

    @Slot(str, str, int, str, str, str)
    @report_ui_errors
    def updateUtterance(
        self,
        session_id: str,
        utterance_id: str,
        revision: int,
        target_text: str,
        speaker_id: str,
        review_status: str,
    ) -> None:
        self._session._require_writable()
        current = self._session.state.binding.current
        assert current is not None
        current.update_dubbing_utterance(
            session_id,
            utterance_id,
            expected_revision=revision,
            target_text=target_text,
            speaker_id=speaker_id,
            review_status=review_status,
        )
        self._session.updates.commit(project=True)

    @Slot(int, int)
    def previewRange(self, start_frame: int, end_frame: int) -> None:
        self._session.updates.request_preview_range(start_frame, end_frame)

    def _document(self, session) -> dict:
        return {
            **self._summary(session),
            "sourceLanguage": session.source_language,
            "targetLanguage": session.target_language,
            "speakers": [
                {
                    "speakerId": speaker.id,
                    "label": speaker.label,
                    "displayName": speaker.display_name,
                    "reviewStatus": speaker.review_status,
                    "primaryReferenceId": (
                        speaker.primary_reference.id if speaker.primary_reference is not None else ""
                    ),
                    "references": [
                        {
                            "referenceId": reference.id,
                            "text": reference.text,
                            "language": reference.language,
                            "durationSeconds": reference.duration_seconds,
                            "primary": reference.primary,
                            "audioUrl": self._artifact_url(reference.path),
                        }
                        for reference in speaker.references
                    ],
                }
                for speaker in session.speakers
            ],
            "utterances": [
                {
                    "utteranceId": item.id,
                    "speakerId": item.speaker_id,
                    "startFrame": item.start_frame,
                    "endFrame": item.end_frame,
                    "sourceText": item.source_text,
                    "targetText": item.target_text,
                    "status": item.status,
                    "reviewStatus": item.review_status,
                    "speedFactor": item.speed_factor,
                    "issues": list(item.issues),
                    "audioUrl": (self._artifact_url(item.output_path) if item.output_path else ""),
                }
                for item in session.utterances
            ],
            "masterAudioUrl": (self._artifact_url(session.master_path) if session.master_path else ""),
        }

    @staticmethod
    def _summary(session) -> dict:
        return {
            "sessionId": session.id,
            "revision": session.revision,
            "status": session.status,
            "label": (
                f"{session.source_language} → {session.target_language} · "
                f"{len(session.speakers)} 人 · {session.id[:8]}"
            ),
            "speakerCount": len(session.speakers),
            "utteranceCount": len(session.utterances),
            "hasCommittedTrack": bool(session.committed_track_id and session.committed_clip_id),
            "needsReviewCount": sum(item.review_status == "needs_review" for item in session.utterances),
        }

    def _artifact_url(self, relative_path: str) -> str:
        current = self._session.state.binding.current
        if current is None:
            return ""
        path = Path(current.project_dir) / relative_path
        return QUrl.fromLocalFile(str(path.resolve())).toString()
