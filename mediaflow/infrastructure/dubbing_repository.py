from __future__ import annotations

import json
import sqlite3
from typing import cast

from mediaflow.domain.dubbing import (
    DubbingReference,
    DubbingReviewStatus,
    DubbingSession,
    DubbingSessionStatus,
    DubbingSettings,
    DubbingSpeaker,
    DubbingSpeakerTurn,
    DubbingUtterance,
    DubbingUtteranceStatus,
)
from mediaflow.domain.enums import TrackKind
from mediaflow.domain.model_base import now_ms

from .project_repository_component import ProjectRepositoryComponent


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class DubbingRepository(ProjectRepositoryComponent):
    def create_session(self, session: DubbingSession) -> DubbingSession:
        if session.revision != 0:
            raise ValueError("New dubbing sessions must start at revision zero")
        self._validate_session(session)
        with self.transaction() as connection:
            self._insert_session(connection, session)
            self._replace_children(connection, session)
            self._touch_project(connection)
        return self.get_session(session.id)

    def save_session(
        self,
        session: DubbingSession,
        *,
        expected_revision: int,
    ) -> DubbingSession:
        if session.revision != expected_revision:
            raise ValueError("Dubbing session revision does not match the expected revision")
        self._validate_session(session)
        updated = session.model_copy(
            update={
                "revision": expected_revision + 1,
                "updated_at": now_ms(),
            }
        )
        with self.transaction() as connection:
            cursor = connection.execute(
                """UPDATE dubbing_session
                   SET sequence_id=?, source_document_id=?, target_document_id=?,
                       source_language=?, target_language=?, dialogue_track_id=?,
                       source_timeline_revision=?, status=?, settings_json=?,
                       diarization_engine=?, diarization_version=?, diarization_model=?,
                       synthesis_engine=?, synthesis_version=?, master_path=?,
                       master_sha256=?, master_duration_seconds=?, master_asset_id=?,
                       committed_track_id=?, committed_clip_id=?, revision=?, updated_at=?
                   WHERE id=? AND project_id=? AND revision=?""",
                self._session_update_values(updated, expected_revision),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(
                    "配音方案在任务执行期间已被修改，请检查新内容后重新执行"
                )
            self._replace_children(connection, updated)
            self._touch_project(connection)
        return self.get_session(session.id)

    def get_session(self, session_id: str) -> DubbingSession:
        row = self._fetchone(
            "SELECT * FROM dubbing_session WHERE id=?",
            (session_id,),
        )
        if row is None:
            raise KeyError(session_id)
        speaker_rows = self._fetchall(
            """SELECT * FROM dubbing_speaker
               WHERE session_id=? ORDER BY position""",
            (session_id,),
        )
        reference_rows = self._fetchall(
            """SELECT * FROM dubbing_reference
               WHERE session_id=? ORDER BY speaker_id, position""",
            (session_id,),
        )
        references: dict[str, list[DubbingReference]] = {}
        for item in reference_rows:
            reference = DubbingReference(
                id=str(item["id"]),
                speaker_id=str(item["speaker_id"]),
                path=str(item["path"]),
                sha256=str(item["sha256"]),
                start_frame=int(item["start_frame"]),
                end_frame=int(item["end_frame"]),
                text=str(item["text"]),
                language=str(item["language"]),
                duration_seconds=float(item["duration_seconds"]),
                primary=bool(item["primary_reference"]),
            )
            references.setdefault(reference.speaker_id, []).append(reference)
        speakers = [
            DubbingSpeaker(
                id=str(item["id"]),
                label=str(item["label"]),
                display_name=str(item["display_name"]),
                review_status=cast(DubbingReviewStatus, str(item["review_status"])),
                references=references.get(str(item["id"]), []),
            )
            for item in speaker_rows
        ]
        turns = [
            DubbingSpeakerTurn(
                id=str(item["id"]),
                speaker_id=str(item["speaker_id"]),
                start_frame=int(item["start_frame"]),
                end_frame=int(item["end_frame"]),
                confidence=(
                    None if item["confidence"] is None else float(item["confidence"])
                ),
            )
            for item in self._fetchall(
                """SELECT * FROM dubbing_speaker_turn
                   WHERE session_id=? ORDER BY position""",
                (session_id,),
            )
        ]
        utterances = [
            DubbingUtterance(
                id=str(item["id"]),
                speaker_id=str(item["speaker_id"]),
                source_segment_ids=json.loads(str(item["source_segment_ids_json"])),
                target_segment_ids=json.loads(str(item["target_segment_ids_json"])),
                start_frame=int(item["start_frame"]),
                end_frame=int(item["end_frame"]),
                source_text=str(item["source_text"]),
                target_text=str(item["target_text"]),
                status=cast(DubbingUtteranceStatus, str(item["status"])),
                review_status=cast(
                    DubbingReviewStatus,
                    str(item["review_status"]),
                ),
                output_path=(
                    None if item["output_path"] is None else str(item["output_path"])
                ),
                output_sha256=(
                    None
                    if item["output_sha256"] is None
                    else str(item["output_sha256"])
                ),
                natural_duration_seconds=(
                    None
                    if item["natural_duration_seconds"] is None
                    else float(item["natural_duration_seconds"])
                ),
                fitted_duration_seconds=(
                    None
                    if item["fitted_duration_seconds"] is None
                    else float(item["fitted_duration_seconds"])
                ),
                speed_factor=float(item["speed_factor"]),
                seed=int(item["seed"]),
                reference_sha256=(
                    None
                    if item["reference_sha256"] is None
                    else str(item["reference_sha256"])
                ),
                issues=json.loads(str(item["issues_json"])),
            )
            for item in self._fetchall(
                """SELECT * FROM dubbing_utterance
                   WHERE session_id=? ORDER BY position""",
                (session_id,),
            )
        ]
        return DubbingSession(
            id=str(row["id"]),
            project_id=str(row["project_id"]),
            sequence_id=str(row["sequence_id"]),
            source_document_id=str(row["source_document_id"]),
            target_document_id=(
                None if row["target_document_id"] is None else str(row["target_document_id"])
            ),
            source_language=str(row["source_language"]),
            target_language=str(row["target_language"]),
            dialogue_track_id=str(row["dialogue_track_id"]),
            source_timeline_revision=int(row["source_timeline_revision"]),
            status=cast(DubbingSessionStatus, str(row["status"])),
            settings=DubbingSettings.model_validate_json(str(row["settings_json"])),
            speakers=speakers,
            turns=turns,
            utterances=utterances,
            diarization_engine=str(row["diarization_engine"]),
            diarization_version=str(row["diarization_version"]),
            diarization_model=str(row["diarization_model"]),
            synthesis_engine=str(row["synthesis_engine"]),
            synthesis_version=str(row["synthesis_version"]),
            master_path=None if row["master_path"] is None else str(row["master_path"]),
            master_sha256=(
                None if row["master_sha256"] is None else str(row["master_sha256"])
            ),
            master_duration_seconds=(
                None
                if row["master_duration_seconds"] is None
                else float(row["master_duration_seconds"])
            ),
            master_asset_id=(
                None if row["master_asset_id"] is None else str(row["master_asset_id"])
            ),
            committed_track_id=(
                None
                if row["committed_track_id"] is None
                else str(row["committed_track_id"])
            ),
            committed_clip_id=(
                None
                if row["committed_clip_id"] is None
                else str(row["committed_clip_id"])
            ),
            revision=int(row["revision"]),
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
        )

    def list_sessions(self, *, sequence_id: str | None = None) -> list[DubbingSession]:
        if sequence_id is None:
            rows = self._fetchall(
                "SELECT id FROM dubbing_session ORDER BY updated_at DESC, id"
            )
        else:
            rows = self._fetchall(
                """SELECT id FROM dubbing_session
                   WHERE sequence_id=? ORDER BY updated_at DESC, id""",
                (sequence_id,),
            )
        return [self.get_session(str(row["id"])) for row in rows]

    def _validate_session(self, session: DubbingSession) -> None:
        project = self._relations.projects.get_project()
        if session.project_id != project.id:
            raise ValueError("Dubbing session belongs to another project")
        self._relations.sequences.get_sequence(session.sequence_id)
        source = self._relations.subtitles.get_subtitle_document(session.source_document_id)
        if source.project_id != project.id:
            raise ValueError("Dubbing source document belongs to another project")
        if source.sequence_id not in {None, session.sequence_id}:
            raise ValueError("Dubbing source document belongs to another sequence")
        if session.target_document_id:
            target = self._relations.subtitles.get_subtitle_document(session.target_document_id)
            if target.source_document_id != source.id:
                raise ValueError("Dubbing target document is not a translation of its source")
            if target.sequence_id not in {None, session.sequence_id}:
                raise ValueError("Dubbing target document belongs to another sequence")
        state = self._relations.timeline.load_timeline(session.sequence_id)
        track = next(
            (item for item in state.tracks if item.id == session.dialogue_track_id),
            None,
        )
        if track is None or track.kind != TrackKind.AUDIO:
            raise ValueError("Dubbing dialogue track must be an audio track in the sequence")
        for path in [
            *(reference.path for speaker in session.speakers for reference in speaker.references),
            *(item.output_path for item in session.utterances if item.output_path),
            *([session.master_path] if session.master_path else []),
        ]:
            resolved = (self.project_dir / path).resolve()
            if not resolved.is_relative_to(self.project_dir.resolve()):
                raise ValueError("Dubbing artifact escaped the project directory")

    @staticmethod
    def _insert_session(
        connection: sqlite3.Connection,
        session: DubbingSession,
    ) -> None:
        connection.execute(
            """INSERT INTO dubbing_session(
                   id, project_id, sequence_id, source_document_id, target_document_id,
                   source_language, target_language, dialogue_track_id,
                   source_timeline_revision, status, settings_json,
                   diarization_engine, diarization_version, diarization_model,
                   synthesis_engine,
                   synthesis_version, master_path, master_sha256,
                   master_duration_seconds, master_asset_id, committed_track_id,
                   committed_clip_id, revision, created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            DubbingRepository._session_insert_values(session),
        )

    @staticmethod
    def _session_insert_values(session: DubbingSession) -> tuple[object, ...]:
        return (
            session.id,
            session.project_id,
            session.sequence_id,
            session.source_document_id,
            session.target_document_id,
            session.source_language,
            session.target_language,
            session.dialogue_track_id,
            session.source_timeline_revision,
            session.status,
            session.settings.model_dump_json(),
            session.diarization_engine,
            session.diarization_version,
            session.diarization_model,
            session.synthesis_engine,
            session.synthesis_version,
            session.master_path,
            session.master_sha256,
            session.master_duration_seconds,
            session.master_asset_id,
            session.committed_track_id,
            session.committed_clip_id,
            session.revision,
            session.created_at,
            session.updated_at,
        )

    @staticmethod
    def _session_update_values(
        session: DubbingSession,
        expected_revision: int,
    ) -> tuple[object, ...]:
        return (
            session.sequence_id,
            session.source_document_id,
            session.target_document_id,
            session.source_language,
            session.target_language,
            session.dialogue_track_id,
            session.source_timeline_revision,
            session.status,
            session.settings.model_dump_json(),
            session.diarization_engine,
            session.diarization_version,
            session.diarization_model,
            session.synthesis_engine,
            session.synthesis_version,
            session.master_path,
            session.master_sha256,
            session.master_duration_seconds,
            session.master_asset_id,
            session.committed_track_id,
            session.committed_clip_id,
            session.revision,
            session.updated_at,
            session.id,
            session.project_id,
            expected_revision,
        )

    @staticmethod
    def _replace_children(
        connection: sqlite3.Connection,
        session: DubbingSession,
    ) -> None:
        for table in (
            "dubbing_utterance",
            "dubbing_speaker_turn",
            "dubbing_reference",
            "dubbing_speaker",
        ):
            connection.execute(f"DELETE FROM {table} WHERE session_id=?", (session.id,))
        for speaker_position, speaker in enumerate(session.speakers):
            connection.execute(
                """INSERT INTO dubbing_speaker(
                       session_id, id, position, label, display_name, review_status
                   ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    session.id,
                    speaker.id,
                    speaker_position,
                    speaker.label,
                    speaker.display_name,
                    speaker.review_status,
                ),
            )
            for reference_position, reference in enumerate(speaker.references):
                connection.execute(
                    """INSERT INTO dubbing_reference(
                           session_id, id, speaker_id, position, path, sha256,
                           start_frame, end_frame, text, language,
                           duration_seconds, primary_reference
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        session.id,
                        reference.id,
                        speaker.id,
                        reference_position,
                        reference.path,
                        reference.sha256,
                        reference.start_frame,
                        reference.end_frame,
                        reference.text,
                        reference.language,
                        reference.duration_seconds,
                        int(reference.primary),
                    ),
                )
        for position, turn in enumerate(session.turns):
            connection.execute(
                """INSERT INTO dubbing_speaker_turn(
                       session_id, id, position, speaker_id, start_frame,
                       end_frame, confidence
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    session.id,
                    turn.id,
                    position,
                    turn.speaker_id,
                    turn.start_frame,
                    turn.end_frame,
                    turn.confidence,
                ),
            )
        for position, utterance in enumerate(session.utterances):
            connection.execute(
                """INSERT INTO dubbing_utterance(
                       session_id, id, position, speaker_id,
                       source_segment_ids_json, target_segment_ids_json,
                       start_frame, end_frame, source_text, target_text,
                       status, review_status, output_path, output_sha256,
                       natural_duration_seconds, fitted_duration_seconds,
                       speed_factor, seed, reference_sha256, issues_json
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session.id,
                    utterance.id,
                    position,
                    utterance.speaker_id,
                    _json(utterance.source_segment_ids),
                    _json(utterance.target_segment_ids),
                    utterance.start_frame,
                    utterance.end_frame,
                    utterance.source_text,
                    utterance.target_text,
                    utterance.status,
                    utterance.review_status,
                    utterance.output_path,
                    utterance.output_sha256,
                    utterance.natural_duration_seconds,
                    utterance.fitted_duration_seconds,
                    utterance.speed_factor,
                    utterance.seed,
                    utterance.reference_sha256,
                    _json(utterance.issues),
                ),
            )
