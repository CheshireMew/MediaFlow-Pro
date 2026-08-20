import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.domain.dubbing import (
    DubbingReference,
    DubbingSession,
    DubbingSpeaker,
    DubbingSpeakerTurn,
    DubbingUtterance,
)
from mediaflow.domain.enums import AssetKind, TrackKind
from mediaflow.domain.subtitles import SubtitleDocument, SubtitleSegment
from mediaflow.file_digest import sha256_file
from mediaflow.infrastructure.project_repository import ProjectRepository


def _session(repository: ProjectRepository, root: Path) -> DubbingSession:
    project = repository.projects.get_project()
    sequence_id = project.main_sequence_id
    editor = TimelineEditor(repository, sequence_id)
    dialogue = editor.add_track(TrackKind.AUDIO)
    editor.set_primary_dialogue_track(dialogue.id)
    subtitle_path = root / "source.srt"
    subtitle_path.write_text(
        "1\n00:00:00,000 --> 00:00:04,000\nHello there\n",
        encoding="utf-8",
    )
    subtitle_asset = repository.assets.import_external_asset(
        subtitle_path,
        AssetKind.SUBTITLE,
    )
    source = SubtitleDocument(
        project_id=project.id,
        asset_id=subtitle_asset.id,
        sequence_id=sequence_id,
        language="en",
        purpose="sequence_transcript",
    )
    source_segment = SubtitleSegment(
        document_id=source.id,
        start_frame=0,
        end_frame=120,
        text="Hello there",
    )
    repository.subtitles.create_subtitle_document(source, [source_segment])
    target = SubtitleDocument(
        project_id=project.id,
        asset_id=subtitle_asset.id,
        sequence_id=sequence_id,
        language="zh",
        source_document_id=source.id,
        is_source=False,
    )
    target_segment = SubtitleSegment(
        document_id=target.id,
        source_segment_id=source_segment.id,
        start_frame=0,
        end_frame=120,
        text="你好",
    )
    repository.subtitles.create_subtitle_document(target, [target_segment])
    return DubbingSession(
        project_id=project.id,
        sequence_id=sequence_id,
        source_document_id=source.id,
        target_document_id=target.id,
        source_language="en",
        target_language="zh",
        dialogue_track_id=dialogue.id,
        source_timeline_revision=repository.timeline.load_timeline(
            sequence_id
        ).sequence.timeline_revision,
        status="review",
        speakers=[
            DubbingSpeaker(
                id="speaker-01",
                label="SPEAKER_00",
                display_name="Alice",
                references=[
                    DubbingReference(
                        speaker_id="speaker-01",
                        path="generated/dubbing/reference.wav",
                        sha256="a" * 64,
                        start_frame=0,
                        end_frame=120,
                        text="Hello there",
                        language="en",
                        duration_seconds=4,
                        primary=True,
                    )
                ],
            )
        ],
        turns=[
            DubbingSpeakerTurn(
                speaker_id="speaker-01",
                start_frame=0,
                end_frame=120,
            )
        ],
        utterances=[
            DubbingUtterance(
                speaker_id="speaker-01",
                source_segment_ids=[source_segment.id],
                target_segment_ids=[target_segment.id],
                start_frame=0,
                end_frame=120,
                source_text="Hello there",
                target_text="你好",
            )
        ],
    )


def test_dubbing_repository_round_trips_aggregate_and_rejects_stale_save(
    tmp_path: Path,
) -> None:
    root = tmp_path / "DubbingProject"
    with ProjectRepository.create(root, "Dubbing") as repository:
        session = _session(repository, root)
        created = repository.dubbing.create_session(session)
        assert created == session
        assert repository.dubbing.list_sessions(sequence_id=session.sequence_id) == [
            session
        ]

        accepted = created.model_copy(
            update={
                "speakers": [
                    created.speakers[0].model_copy(
                        update={"review_status": "accepted"}
                    )
                ]
            }
        )
        saved = repository.dubbing.save_session(
            accepted,
            expected_revision=created.revision,
        )
        assert saved.revision == 1
        assert saved.speakers[0].review_status == "accepted"

        with pytest.raises(RuntimeError, match="已被修改"):
            repository.dubbing.save_session(
                accepted,
                expected_revision=created.revision,
            )


def test_v47_migration_adds_dubbing_documents(tmp_path: Path) -> None:
    root = tmp_path / "DubbingMigration"
    with ProjectRepository.create(root, "Dubbing migration") as repository:
        version = repository.records.create_project_version("Before dubbing")

    def downgrade(path: Path) -> None:
        with closing(sqlite3.connect(path)) as connection, connection:
            for table in (
                "dubbing_utterance",
                "dubbing_speaker_turn",
                "dubbing_reference",
                "dubbing_speaker",
                "dubbing_session",
            ):
                connection.execute(f"DROP TABLE {table}")
            connection.execute(
                "UPDATE schema_info SET version=46 WHERE component='project'"
            )

    snapshot_path = root / version.snapshot_path
    downgrade(snapshot_path)
    downgrade(root / "project.mfp")
    with closing(sqlite3.connect(root / "project.mfp")) as connection, connection:
        connection.execute(
            "UPDATE project_version SET sha256=? WHERE id=?",
            (sha256_file(snapshot_path), version.id),
        )

    with ProjectRepository.open(root, writable=True) as migrated:
        assert migrated._fetchone(
            "SELECT version FROM schema_info WHERE component='project'"
        )["version"] == 47
        tables = {
            row["name"]
            for row in migrated._fetchall(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        session_columns = {
            row["name"]
            for row in migrated._fetchall("PRAGMA table_info(dubbing_session)")
        }
        migrated.records.restore_project_version(version.id)
    assert {
        "dubbing_session",
        "dubbing_speaker",
        "dubbing_reference",
        "dubbing_speaker_turn",
        "dubbing_utterance",
    } <= tables
    assert "diarization_model" in session_columns
    with closing(sqlite3.connect(snapshot_path)) as snapshot:
        snapshot_tables = {
            row[0]
            for row in snapshot.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        snapshot_version = snapshot.execute(
            "SELECT version FROM schema_info WHERE component='project'"
        ).fetchone()[0]
        snapshot_session_columns = {
            row[1]
            for row in snapshot.execute("PRAGMA table_info(dubbing_session)")
        }
    assert snapshot_version == 47
    assert "dubbing_session" in snapshot_tables
    assert "diarization_model" in snapshot_session_columns
