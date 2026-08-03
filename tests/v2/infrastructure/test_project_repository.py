import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

import mediaflow.infrastructure.project_records_repository as project_records_module
from mediaflow.application.asset_service import AssetService
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.domain.enums import (
    AssetKind,
    ClipMediaKind,
    ColorMode,
    ExportFormat,
    SequenceKind,
    TaskStatus,
    TrackKind,
)
from mediaflow.domain.exports import ExportPreset
from mediaflow.domain.project import MediaMetadata, ProjectProfile, SequenceInOut
from mediaflow.domain.storage_names import (
    PROJECT_DIRECTORY_COMPONENT_UTF16_LIMIT,
    PROJECT_ROOT_PATH_UTF16_LIMIT,
    WINDOWS_INTEROP_PATH_UTF16_LIMIT,
    safe_child_path,
    utf16_units,
)
from mediaflow.domain.subtitles import SubtitleDocument, SubtitleSegment
from mediaflow.domain.task_commands import (
    AnalyzeDownloadCommand,
    DownloadMediaCommand,
    GenerateWaveformCommand,
    TranscribeSequenceCommand,
)
from mediaflow.domain.tasks import Task
from mediaflow.domain.timeline import (
    Clip,
    TimelineMarker,
    TimelineRange,
    TimelineRevisionConflict,
    Track,
)
from mediaflow.infrastructure.audio_repository import AudioRepository
from mediaflow.infrastructure.file_fingerprint import fingerprint_file, fingerprint_matches
from mediaflow.infrastructure.project_migration_runner import (
    ProjectSchemaMigrator,
)
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.project_schema_definition import PROJECT_SCHEMA_VERSION
from mediaflow.infrastructure.task_repository import TaskRepository


def test_project_repository_owns_the_project_root_path_boundary(
    tmp_path: Path,
    max_project_path: Path,
) -> None:
    over_budget = safe_child_path(
        tmp_path,
        "Over-Budget-Project-Root-" * 20,
        max_path_utf16_units=PROJECT_ROOT_PATH_UTF16_LIMIT + 1,
        max_component_utf16_units=PROJECT_DIRECTORY_COMPONENT_UTF16_LIMIT,
    )
    assert utf16_units(str(over_budget)) == PROJECT_ROOT_PATH_UTF16_LIMIT + 1

    with pytest.raises(ValueError, match="路径过深"):
        ProjectRepository.create(over_budget, "Must Not Exist")
    assert not over_budget.exists()

    over_budget.mkdir(parents=True)
    database_marker = over_budget / "project.mfp"
    database_marker.write_bytes(b"must not be opened")
    for writable in (False, True):
        with pytest.raises(ValueError, match="路径过深"):
            ProjectRepository.open(over_budget, writable=writable)
    assert database_marker.read_bytes() == b"must not be opened"
    assert not (over_budget / "cache").exists()

    assert utf16_units(str(max_project_path)) == PROJECT_ROOT_PATH_UTF16_LIMIT
    with ProjectRepository.create(max_project_path, "Maximum Root") as created:
        project_id = created.catalog.get_project().id
    with ProjectRepository.open(max_project_path, writable=False) as reopened:
        assert reopened.catalog.get_project().id == project_id


def test_nested_transaction_rolls_back_its_partial_writes_when_outer_recovers(
    tmp_path: Path,
) -> None:
    with ProjectRepository.create(
        tmp_path / "NestedTransaction",
        "NestedTransaction",
    ) as repository:
        with repository.transaction() as outer:
            try:
                with repository.transaction() as inner:
                    inner.execute(
                        "UPDATE project SET workflow_auto_continue=1"
                    )
                    raise ValueError("injected nested failure")
            except ValueError:
                pass
            outer.execute(
                "UPDATE project SET name=?",
                ("Outer change survived",),
            )

        project = repository.catalog.get_project()
        assert project.name == "Outer change survived"
        assert project.workflow_auto_continue is None


def test_base_exception_rolls_back_outer_transaction_and_connection_recovers(
    tmp_path: Path,
) -> None:
    class AbortTransaction(BaseException):
        pass

    with ProjectRepository.create(
        tmp_path / "InterruptedTransaction",
        "InterruptedTransaction",
    ) as repository:
        with pytest.raises(AbortTransaction):
            with repository.transaction() as connection:
                connection.execute(
                    "UPDATE project SET name=?",
                    ("Must roll back",),
                )
                raise AbortTransaction

        assert (
            repository.catalog.get_project().name
            == "InterruptedTransaction"
        )
        with repository.transaction() as connection:
            connection.execute(
                "UPDATE project SET name=?",
                ("Recovered",),
            )
        assert repository.catalog.get_project().name == "Recovered"


def test_base_exception_during_create_archives_partial_database_and_releases_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class AbortCreate(BaseException):
        pass

    root = tmp_path / "InterruptedCreate"
    original_initialize = ProjectRepository._initialize

    def abort_initialize(self, **_kwargs) -> None:
        raise AbortCreate

    monkeypatch.setattr(
        ProjectRepository,
        "_initialize",
        abort_initialize,
    )
    with pytest.raises(AbortCreate):
        ProjectRepository.create(root, "InterruptedCreate")

    assert not (root / "project.mfp").exists()
    assert len(list((root / "archive").glob("create-failed-*.mfp"))) == 1

    monkeypatch.setattr(
        ProjectRepository,
        "_initialize",
        original_initialize,
    )
    with ProjectRepository.create(
        root,
        "RecoveredCreate",
    ) as recovered:
        assert recovered.owns_project_lock is True


def test_base_exception_during_open_closes_connection_and_releases_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class AbortOpen(BaseException):
        pass

    root = tmp_path / "InterruptedOpen"
    with ProjectRepository.create(root, "InterruptedOpen"):
        pass
    original_validate = ProjectSchemaMigrator.validate

    def abort_validate(_self) -> None:
        raise AbortOpen

    monkeypatch.setattr(
        ProjectSchemaMigrator,
        "validate",
        abort_validate,
    )
    with pytest.raises(AbortOpen):
        ProjectRepository.open(root, writable=True)

    monkeypatch.setattr(
        ProjectSchemaMigrator,
        "validate",
        original_validate,
    )
    with ProjectRepository.open(
        root,
        writable=True,
    ) as recovered:
        assert recovered.read_only is False
        assert recovered.owns_project_lock is True


def test_create_project_starts_with_dynamic_empty_timeline(tmp_path: Path) -> None:
    root = tmp_path / "Demo"
    profile = ProjectProfile(
        width=3840,
        height=2160,
        fps_numerator=60_000,
        fps_denominator=1001,
        color_mode=ColorMode.HDR10_BT2020_PQ,
        bit_depth=10,
    )
    with ProjectRepository.create(root, "Demo", profile) as repository:
        project = repository.catalog.get_project()
        sequences = repository.catalog.list_sequences()
        timeline = repository.timeline.load_timeline(project.main_sequence_id)
        buses = repository.audio.list_audio_buses(project.main_sequence_id)

        assert project.name == "Demo"
        assert sequences[0].kind == SequenceKind.MAIN
        assert sequences[0].profile == profile
        assert sequences[0].profile_confirmed is True
        assert timeline.tracks == []
        assert [bus.name for bus in buses] == ["主总线", "对白", "音乐", "效果"]


def test_failed_first_creation_is_archived_and_same_name_can_be_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "AtomicCreate"
    original = AudioRepository._insert_bus_record
    calls = 0

    def fail_after_schema(connection: sqlite3.Connection, bus) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected seed failure")
        original(connection, bus)

    monkeypatch.setattr(
        AudioRepository,
        "_insert_bus_record",
        staticmethod(fail_after_schema),
    )
    with pytest.raises(OSError, match="injected seed failure"):
        ProjectRepository.create(root, "AtomicCreate")

    assert not (root / "project.mfp").exists()
    archived = list((root / "archive").glob("create-failed-*.mfp"))
    assert len(archived) == 1
    with closing(sqlite3.connect(archived[0])) as connection, connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)

    monkeypatch.setattr(
        AudioRepository,
        "_insert_bus_record",
        staticmethod(original),
    )
    with ProjectRepository.create(root, "AtomicCreate") as repository:
        assert repository.catalog.get_project().name == "AtomicCreate"


def test_schema_upgrade_rolls_back_every_version_when_a_late_step_fails(
    tmp_path: Path,
) -> None:
    root = tmp_path / "AtomicMigration"
    with ProjectRepository.create(root, "AtomicMigration") as repository:
        project = repository.catalog.get_project()
        sequence_id = project.main_sequence_id
    with closing(sqlite3.connect(root / "project.mfp")) as connection, connection:
        connection.execute(
            """INSERT INTO task(
                id, project_id, sequence_id, command_json, status, progress_json,
                input_asset_ids_json, artifacts_json, execution_trace_json,
                error, revision, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "invalid-artifact-task",
                project.id,
                sequence_id,
                json.dumps({"command_type": "generate_waveform", "asset_id": "missing"}),
                "cancelled",
                json.dumps({"mode": "indeterminate", "message_code": "cancelled"}),
                "[]",
                "{invalid-json",
                "[]",
                None,
                0,
                1,
                1,
            ),
        )
        connection.execute("UPDATE schema_info SET version=26")

    with pytest.raises(json.JSONDecodeError):
        ProjectRepository.open(root, writable=True)

    with closing(sqlite3.connect(root / "project.mfp")) as connection, connection:
        version = connection.execute("SELECT version FROM schema_info WHERE component='project'").fetchone()[
            0
        ]
        connection.execute("UPDATE task SET artifacts_json='[]' WHERE id='invalid-artifact-task'")
    assert version == 26

    # A failed open releases the project lock instead of poisoning future opens.
    with ProjectRepository.open(root, writable=True) as repository:
        assert repository.read_only is False

    assert (root / "project.mfp").is_file()
    for directory in ("generated", "proxies", "cache", "exports"):
        assert (root / directory).is_dir()
    assert not (root / "WorkSpace").exists()
    assert not (root / "downloads").exists()


def test_encoded_project_path_opens_through_every_read_only_database_boundary(
    tmp_path: Path,
) -> None:
    root = tmp_path / "客户 #100% 项目"
    with ProjectRepository.create(root, "Encoded Path") as repository:
        project_id = repository.catalog.get_project().id
        version = repository.records.create_project_version("可读取")
        assert (root / version.snapshot_path).is_file()

    with ProjectRepository.open(root, writable=False) as repository:
        assert repository.catalog.get_project().id == project_id
        assert [item.id for item in repository.records.list_project_versions()] == [
            version.id
        ]
        assert TaskRepository(repository).list() == []


def test_version_thirty_one_project_gains_automation_request_journal(
    tmp_path: Path,
) -> None:
    root = tmp_path / "MigratedV31"
    with ProjectRepository.create(root, "MigratedV31"):
        pass
    with closing(sqlite3.connect(root / "project.mfp")) as connection, connection:
        connection.execute("DROP TABLE automation_request")
        connection.execute("UPDATE schema_info SET version=31")

    with ProjectRepository.open(root, writable=True) as repository:
        stored = repository.save_automation_result(
            "request-31",
            "timeline.track.add",
            "input-hash",
            {"track_id": "track-31"},
        )

        assert stored == {"track_id": "track-31"}
        assert (
            repository.automation_result(
                "request-31",
                "timeline.track.add",
                "input-hash",
            )
            == stored
        )
        assert repository._fetchone("SELECT version FROM schema_info")["version"] == (PROJECT_SCHEMA_VERSION)


def test_version_thirty_three_project_migrates_task_leases_and_request_state(
    tmp_path: Path,
) -> None:
    root = tmp_path / "MigratedV33"
    with ProjectRepository.create(root, "MigratedV33") as repository:
        project = repository.catalog.get_project()
        task = TaskRepository(repository).create(
            Task(
                project_id=project.id,
                command=AnalyzeDownloadCommand(url="test://migration"),
            )
        )
        repository.save_automation_result(
            "completed-request",
            "task.start",
            "completed-hash",
            {"task_id": task.id},
        )

    with closing(sqlite3.connect(root / "project.mfp")) as connection, connection:
        connection.row_factory = sqlite3.Row
        connection.execute("DROP TABLE task_consumption")
        connection.execute("ALTER TABLE automation_request DROP COLUMN state")
        for column in (
            "stop_request",
            "lease_expires_at",
            "heartbeat_at",
            "execution_owner_id",
        ):
            connection.execute(f"ALTER TABLE task DROP COLUMN {column}")
        connection.execute(
            "UPDATE task SET status='running' WHERE id=?",
            (task.id,),
        )
        event = connection.execute(
            """SELECT cursor, payload_json FROM task_event
               WHERE task_id=? ORDER BY cursor DESC LIMIT 1""",
            (task.id,),
        ).fetchone()
        payload = json.loads(str(event["payload_json"]))
        payload["status"] = "running"
        for field in (
            "execution_owner_id",
            "heartbeat_at",
            "lease_expires_at",
            "stop_request",
        ):
            payload.pop(field, None)
        connection.execute(
            "UPDATE task_event SET payload_json=? WHERE cursor=?",
            (json.dumps(payload), event["cursor"]),
        )
        connection.execute(
            "UPDATE schema_info SET version=33 WHERE component='project'"
        )

    with ProjectRepository.open(root, writable=True) as repository:
        migrated = TaskRepository(repository).get(task.id)
        assert migrated.status == TaskStatus.RUNNING
        assert migrated.execution_owner_id == f"expired:migration:v34:{task.id}"
        assert migrated.heartbeat_at == 0
        assert migrated.lease_expires_at == 1
        assert [
            item.id
            for item in TaskRepository(repository).list_claimable(2)
        ] == [
            task.id
        ]
        assert repository.automation_result(
            "completed-request",
            "task.start",
            "completed-hash",
        ) == {"task_id": task.id}
        event = TaskRepository(repository).latest_event(task.id)
        assert Task.model_validate(event.payload).execution_owner_id == (
            f"expired:migration:v34:{task.id}"
        )
        assert repository._fetchone(
            "SELECT version FROM schema_info"
        )["version"] == PROJECT_SCHEMA_VERSION
        assert repository._fetchone(
            """SELECT name FROM sqlite_master
               WHERE type='table' AND name='task_consumption'"""
        ) is not None


def test_incomplete_automation_request_is_retryable_until_result_is_committed(
    tmp_path: Path,
) -> None:
    with ProjectRepository.create(
        tmp_path / "AutomationReceipt",
        "AutomationReceipt",
    ) as repository:
        first, retrying = repository.begin_automation_request(
            "request-in-flight",
            "task.resume",
            "input-hash",
        )
        assert first is None
        assert retrying is False
        repeated, retrying = repository.begin_automation_request(
            "request-in-flight",
            "task.resume",
            "input-hash",
        )
        assert repeated is None
        assert retrying is True
        assert repository.automation_result(
            "request-in-flight",
            "task.resume",
            "input-hash",
        ) is None

        stored = repository.save_automation_result(
            "request-in-flight",
            "task.resume",
            "input-hash",
            {"status": "completed"},
        )
        assert stored == {"status": "completed"}
        assert repository.begin_automation_request(
            "request-in-flight",
            "task.resume",
            "input-hash",
        ) == (stored, True)


def test_blank_project_profile_stays_provisional_until_media_or_manual_choice(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Blank"
    with ProjectRepository.create(root, "Blank") as repository:
        project = repository.catalog.get_project()
        assert repository.catalog.get_sequence(project.main_sequence_id).profile_confirmed is False

    with ProjectRepository.open(root) as repository:
        project = repository.catalog.get_project()
        assert repository.catalog.get_sequence(project.main_sequence_id).profile_confirmed is False


def test_version_eighteen_project_gains_compound_clip_storage(tmp_path: Path) -> None:
    root = tmp_path / "MigratedV18"
    with ProjectRepository.create(root, "MigratedV18"):
        pass
    with closing(sqlite3.connect(root / "project.mfp")) as connection, connection:
        connection.execute("DROP TABLE compound_clip")
        connection.execute("UPDATE schema_info SET version=18")

    with ProjectRepository.open(root, writable=True) as repository:
        tables = {
            row["name"] for row in repository._fetchall("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert "compound_clip" in tables
        assert repository._fetchone("SELECT version FROM schema_info")["version"] == (PROJECT_SCHEMA_VERSION)


def test_version_twenty_project_gains_canonical_subtitle_word_storage(tmp_path: Path) -> None:
    root = tmp_path / "MigratedV20"
    with ProjectRepository.create(root, "MigratedV20"):
        pass
    with closing(sqlite3.connect(root / "project.mfp")) as connection, connection:
        connection.execute("DROP TABLE subtitle_word")
        connection.execute("UPDATE schema_info SET version=20")

    with ProjectRepository.open(root, writable=True) as repository:
        columns = {row["name"] for row in repository._fetchall("PRAGMA table_info(subtitle_word)")}
        assert {
            "id",
            "segment_id",
            "position",
            "start_frame",
            "end_frame",
            "text",
            "confidence",
            "timing_source",
            "excluded",
        } <= columns
        assert repository._fetchone("SELECT version FROM schema_info")["version"] == (PROJECT_SCHEMA_VERSION)


def test_version_twenty_one_project_gains_export_history_and_named_versions(
    tmp_path: Path,
) -> None:
    root = tmp_path / "MigratedV21"
    with ProjectRepository.create(root, "MigratedV21"):
        pass
    with closing(sqlite3.connect(root / "project.mfp")) as connection, connection:
        connection.execute("DROP TABLE export_history")
        connection.execute("DROP TABLE project_version")
        connection.execute("UPDATE schema_info SET version=21")

    with ProjectRepository.open(root, writable=True) as repository:
        tables = {
            row["name"] for row in repository._fetchall("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {"export_history", "project_version"} <= tables
        assert repository._fetchone("SELECT version FROM schema_info")["version"] == (PROJECT_SCHEMA_VERSION)


def test_version_twenty_four_tasks_migrate_to_structured_progress(
    tmp_path: Path,
) -> None:
    root = tmp_path / "MigratedV24Progress"
    with ProjectRepository.create(root, "MigratedV24Progress") as repository:
        project = repository.catalog.get_project()
        sequence_id = project.main_sequence_id

    command_json = json.dumps(
        GenerateWaveformCommand(asset_id="legacy-asset").model_dump(mode="json"),
        ensure_ascii=False,
    )
    with closing(sqlite3.connect(root / "project.mfp")) as connection, connection:
        connection.execute("DROP INDEX idx_task_project_time")
        connection.execute("DROP TABLE task")
        connection.execute(
            """CREATE TABLE task (
                   id TEXT PRIMARY KEY,
                   project_id TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
                   sequence_id TEXT REFERENCES sequence(id) ON DELETE SET NULL,
                   command_json TEXT NOT NULL,
                   status TEXT NOT NULL,
                   progress REAL NOT NULL,
                   message_code TEXT NOT NULL,
                   input_asset_ids_json TEXT NOT NULL,
                   artifacts_json TEXT NOT NULL,
                   execution_trace_json TEXT NOT NULL DEFAULT '[]',
                   error TEXT,
                   revision INTEGER NOT NULL DEFAULT 0,
                   created_at INTEGER NOT NULL,
                   updated_at INTEGER NOT NULL
               )"""
        )
        connection.executemany(
            """INSERT INTO task(
                   id, project_id, sequence_id, command_json, status, progress,
                   message_code, input_asset_ids_json, artifacts_json,
                   execution_trace_json, error, revision, created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    "legacy-running",
                    project.id,
                    sequence_id,
                    command_json,
                    TaskStatus.RUNNING.value,
                    67.0,
                    "legacy_measuring",
                    "[]",
                    "[]",
                    "[]",
                    None,
                    0,
                    1,
                    1,
                ),
                (
                    "legacy-completed",
                    project.id,
                    sequence_id,
                    command_json,
                    TaskStatus.COMPLETED.value,
                    100.0,
                    "completed",
                    "[]",
                    "[]",
                    "[]",
                    None,
                    0,
                    2,
                    2,
                ),
            ],
        )
        connection.execute("CREATE INDEX idx_task_project_time ON task(project_id, created_at)")
        connection.execute("UPDATE schema_info SET version=24")

    with ProjectRepository.open(root, writable=True) as repository:
        columns = {row["name"] for row in repository._fetchall("PRAGMA table_info(task)")}
        tasks = {
            task.id: task
            for task in TaskRepository(repository).list()
        }

        assert "progress_json" in columns
        assert "progress" not in columns
        assert "message_code" not in columns
        assert tasks["legacy-running"].progress.mode == "indeterminate"
        assert tasks["legacy-running"].progress.message_code == "legacy_measuring"
        assert tasks["legacy-completed"].progress.mode == "determinate"
        assert tasks["legacy-completed"].progress.completed == 1
        assert tasks["legacy-completed"].progress.total == 1
        assert tasks["legacy-completed"].progress.unit == "task"
        assert repository._fetchone("SELECT version FROM schema_info")["version"] == (PROJECT_SCHEMA_VERSION)


def test_version_twenty_five_transcription_tasks_migrate_to_plan_boundary(
    tmp_path: Path,
) -> None:
    root = tmp_path / "MigratedV25Transcription"
    with ProjectRepository.create(root, "MigratedV25Transcription") as repository:
        project = repository.catalog.get_project()
        sequence_id = project.main_sequence_id

    progress = json.dumps(
        {
            "mode": "indeterminate",
            "message_code": "queued",
            "completed": None,
            "total": None,
            "unit": None,
        }
    )
    with closing(sqlite3.connect(root / "project.mfp")) as connection, connection:
        for task_id, status in (
            ("historical-transcription", "completed"),
            ("queued-transcription", "pending"),
        ):
            connection.execute(
                """INSERT INTO task(
                       id, project_id, sequence_id, command_json, status,
                       progress_json, input_asset_ids_json, artifacts_json,
                       execution_trace_json, error, revision, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, '[]', '[]', '[]', NULL, 0, 1, 1)""",
                (
                    task_id,
                    project.id,
                    sequence_id,
                    json.dumps(
                        {
                            "command_type": "transcribe_sequence",
                            "sequence_id": sequence_id,
                        }
                    ),
                    status,
                    progress,
                ),
            )
        connection.execute("UPDATE schema_info SET version=25")

    with ProjectRepository.open(root, writable=True) as repository:
        tasks = {
            task.id: task
            for task in TaskRepository(repository).list()
        }

    historical = tasks["historical-transcription"]
    queued = tasks["queued-transcription"]
    assert isinstance(historical.command, TranscribeSequenceCommand)
    assert historical.command.plan.timeline_signature == "legacy"
    assert historical.command.plan.sources == []
    assert historical.status == TaskStatus.COMPLETED
    assert queued.status == TaskStatus.CANCELLED
    assert queued.error == "旧版转录任务缺少可复现计划，请重新发起转录"


def test_named_version_restores_complete_project_and_preserves_version_catalog(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"versioned-source")
    with ProjectRepository.create(tmp_path / "Versioned", "Versioned") as repository:
        project = repository.catalog.get_project()
        asset = repository.catalog.import_external_asset(source, AssetKind.VIDEO)
        state = repository.timeline.load_timeline(project.main_sequence_id)
        track = Track(
            sequence_id=project.main_sequence_id,
            name="视频 1",
            kind=TrackKind.VIDEO,
            position=0,
        )
        state.tracks = [track]
        state.clips = [
            Clip(
                track_id=track.id,
                asset_id=asset.id,
                timeline_start=0,
                source_in=0,
                duration=60,
                media_kind=ClipMediaKind.VIDEO_ONLY,
            )
        ]
        repository.timeline.save_timeline(state)
        version = repository.records.create_project_version("客户审阅版")
        snapshot = repository.project_dir / version.snapshot_path
        assert snapshot.is_file() and snapshot.stat().st_size > 0
        assert len(version.sha256) == 64

        changed = repository.timeline.load_timeline(project.main_sequence_id)
        changed.markers = [
            TimelineMarker(
                sequence_id=project.main_sequence_id,
                frame=20,
                name="After version",
            )
        ]
        changed.clips[0] = changed.clips[0].model_copy(update={"duration": 30})
        repository.timeline.save_timeline(changed)
        assert repository.timeline.load_timeline(project.main_sequence_id).duration_frames == 30
        changed_revision = repository.content_revision()

        with ProjectRepository.open(
            repository.project_dir,
            writable=False,
        ) as observer:
            assert observer.known_content_revision == changed_revision
            restored_version = repository.records.restore_project_version(version.id)
            restored_revision = repository.content_revision()
            restored = repository.timeline.load_timeline(project.main_sequence_id)

            assert restored_version.id == version.id
            assert restored_revision > changed_revision
            assert observer.content_revision() == restored_revision
            assert observer.known_content_revision == changed_revision
            assert restored.duration_frames == 60
            assert restored.markers == []
            assert [item.id for item in repository.records.list_project_versions()] == [version.id]
            assert snapshot.is_file()


def test_failed_named_version_creation_never_publishes_a_catalog_record(
    max_project_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with ProjectRepository.create(
        max_project_path,
        "FailedVersion",
    ) as repository:
        observed_temporary: Path | None = None

        def fail_after_snapshot(_path: Path) -> str:
            nonlocal observed_temporary
            observed_temporary = _path
            raise OSError("injected digest failure")

        monkeypatch.setattr(
            project_records_module,
            "sha256_file",
            fail_after_snapshot,
        )
        with pytest.raises(OSError, match="injected digest failure"):
            repository.records.create_project_version("不能出现")

        versions_root = repository.project_dir / "generated" / "versions"
        assert observed_temporary is not None
        assert (
            utf16_units(str(observed_temporary))
            <= WINDOWS_INTEROP_PATH_UTF16_LIMIT
        )
        assert repository.records.list_project_versions() == []
        assert list(versions_root.glob("*.mfp")) == []
        archived = list((versions_root / "archive").glob("failed-*.mfp"))
        assert len(archived) == 1
        assert (
            utf16_units(str(archived[0]))
            <= WINDOWS_INTEROP_PATH_UTF16_LIMIT
        )
        with closing(sqlite3.connect(archived[0])) as snapshot:
            assert snapshot.execute("PRAGMA integrity_check").fetchone() == ("ok",)


def test_named_versions_reject_in_flight_task_state_at_the_repository_boundary(
    tmp_path: Path,
) -> None:
    with ProjectRepository.create(
        tmp_path / "BusyVersion",
        "BusyVersion",
    ) as repository:
        project = repository.catalog.get_project()
        version = repository.records.create_project_version("任务前")
        task = TaskRepository(repository).create(
            Task(
                project_id=project.id,
                command=AnalyzeDownloadCommand(url="test://busy-version"),
            )
        )

        with pytest.raises(RuntimeError, match="仍有未完成任务"):
            repository.records.create_project_version("不一致")
        with pytest.raises(RuntimeError, match="仍有未完成任务"):
            repository.records.restore_project_version(version.id)

        assert TaskRepository(repository).get(task.id).status == TaskStatus.PENDING
        assert [item.id for item in repository.records.list_project_versions()] == [
            version.id
        ]


def test_version_nineteen_video_audio_is_migrated_to_linked_dynamic_tracks(
    tmp_path: Path,
) -> None:
    root = tmp_path / "MigratedV19"
    source = tmp_path / "linked.mp4"
    source.write_bytes(b"linked-av")
    with ProjectRepository.create(root, "MigratedV19") as repository:
        asset = repository.catalog.import_external_asset(source, AssetKind.VIDEO)
        asset = repository.catalog.update_asset(
            asset.model_copy(
                update={
                    "metadata": MediaMetadata(
                        duration_frames=100,
                        has_video=True,
                        has_audio=True,
                    )
                }
            )
        )
        sequence_id = repository.catalog.get_project().main_sequence_id
        state = repository.timeline.load_timeline(sequence_id)
        video_track = Track(
            sequence_id=sequence_id,
            name="视频 1",
            kind=TrackKind.VIDEO,
            position=0,
        )
        state.tracks = [video_track]
        state.clips = [
            Clip(
                track_id=video_track.id,
                asset_id=asset.id,
                timeline_start=0,
                source_in=0,
                duration=100,
                media_kind=ClipMediaKind.VIDEO_ONLY,
            )
        ]
        repository.timeline.save_timeline(state)

    with closing(sqlite3.connect(root / "project.mfp")) as connection, connection:
        connection.execute("UPDATE schema_info SET version=19")

    with ProjectRepository.open(root, writable=True) as repository:
        state = repository.timeline.load_timeline(repository.catalog.get_project().main_sequence_id)
        migrated_video = next(track for track in state.tracks if track.kind == TrackKind.VIDEO)
        migrated_audio = next(track for track in state.tracks if track.kind == TrackKind.AUDIO)
        assert state.clips[0].media_kind == ClipMediaKind.LINKED_AV
        assert migrated_video.linked_audio_track_id == migrated_audio.id
        assert [track.position for track in state.tracks] == [0, 1]


def test_version_thirteen_project_migration_preserves_existing_profile(
    tmp_path: Path,
) -> None:
    root = tmp_path / "MigratedV13"
    with ProjectRepository.create(root, "MigratedV13"):
        pass
    with closing(sqlite3.connect(root / "project.mfp")) as connection, connection:
        connection.execute("ALTER TABLE sequence DROP COLUMN profile_confirmed")
        connection.execute("UPDATE schema_info SET version=13")

    with ProjectRepository.open(root, writable=True) as repository:
        project = repository.catalog.get_project()
        assert repository.catalog.get_sequence(project.main_sequence_id).profile_confirmed is True
        assert repository._fetchone("SELECT version FROM schema_info")["version"] == (PROJECT_SCHEMA_VERSION)


def test_version_fourteen_project_gains_persistent_subtitle_timing_overrides(
    tmp_path: Path,
) -> None:
    root = tmp_path / "MigratedV14"
    with ProjectRepository.create(root, "MigratedV14"):
        pass
    with closing(sqlite3.connect(root / "project.mfp")) as connection, connection:
        connection.execute("ALTER TABLE subtitle_placement DROP COLUMN timing_overridden")
        connection.execute("UPDATE schema_info SET version=14")

    with ProjectRepository.open(root, writable=True) as repository:
        columns = {row["name"] for row in repository._fetchall("PRAGMA table_info(subtitle_placement)")}
        assert "timing_overridden" in columns
        assert repository._fetchone("SELECT version FROM schema_info")["version"] == (PROJECT_SCHEMA_VERSION)


def test_version_fifteen_project_migrates_transcription_to_sequence_boundary(
    tmp_path: Path,
) -> None:
    root = tmp_path / "MigratedV15"
    with ProjectRepository.create(root, "MigratedV15") as repository:
        project = repository.catalog.get_project()
        sequence_id = project.main_sequence_id

    task_id = "legacy-transcription-task"
    with closing(sqlite3.connect(root / "project.mfp")) as connection, connection:
        connection.execute("ALTER TABLE subtitle_document DROP COLUMN sequence_id")
        connection.execute(
            """INSERT INTO task(
                    id, project_id, sequence_id, command_json, status, progress_json,
                    input_asset_ids_json, artifacts_json,
                    execution_trace_json, error, revision, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task_id,
                project.id,
                sequence_id,
                json.dumps(
                    {
                        "command_type": "transcribe_region",
                        "asset_id": "legacy-asset",
                        "start_frame": 12,
                        "end_frame": 48,
                    }
                ),
                "completed",
                json.dumps(
                    {
                        "mode": "determinate",
                        "message_code": "completed",
                        "completed": 1,
                        "total": 1,
                        "unit": "task",
                    }
                ),
                "[]",
                "[]",
                "[]",
                None,
                0,
                1,
                1,
            ),
        )
        connection.execute("UPDATE schema_info SET version=15")

    with ProjectRepository.open(root, writable=True) as repository:
        columns = {row["name"] for row in repository._fetchall("PRAGMA table_info(subtitle_document)")}
        task = TaskRepository(repository).get(task_id)

        assert "sequence_id" in columns
        assert isinstance(task.command, TranscribeSequenceCommand)
        assert task.command.sequence_id == sequence_id
        assert repository._fetchone("SELECT version FROM schema_info")["version"] == (PROJECT_SCHEMA_VERSION)


def test_second_writer_falls_back_to_read_only(tmp_path: Path) -> None:
    root = tmp_path / "Locked"
    first = ProjectRepository.create(root, "Locked")
    try:
        second = ProjectRepository.open(root, writable=True)
        try:
            assert second.read_only is True
            with pytest.raises(PermissionError, match="read-only"):
                second.catalog.create_short_sequence("Short")
        finally:
            second.close()
    finally:
        first.close()


def test_version_sixteen_project_gains_web_tables_and_content_revision(tmp_path: Path) -> None:
    root = tmp_path / "MigratedV16"
    with ProjectRepository.create(root, "MigratedV16"):
        pass
    with closing(sqlite3.connect(root / "project.mfp")) as connection, connection:
        connection.execute("DROP TABLE web_clip_state")
        connection.execute("DROP TABLE web_asset")
        connection.execute("ALTER TABLE project DROP COLUMN content_revision")
        connection.execute("UPDATE schema_info SET version=16")

    with ProjectRepository.open(root, writable=True) as repository:
        project_columns = {row["name"] for row in repository._fetchall("PRAGMA table_info(project)")}
        tables = {
            row["name"] for row in repository._fetchall("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert "content_revision" in project_columns
        assert {"web_asset", "web_clip_state"} <= tables
        assert repository.content_revision() == 0


def test_cooperative_writer_rejects_stale_owner_edits_until_reload(tmp_path: Path) -> None:
    root = tmp_path / "Cooperative"
    owner = ProjectRepository.create(root, "Cooperative")
    cooperative = ProjectRepository.open(root, writable=True, cooperative=True)
    try:
        cooperative.catalog.create_short_sequence("From CLI")
        assert owner.content_revision() != owner.known_content_revision
        with pytest.raises(RuntimeError, match="changed in another process"):
            owner.catalog.create_short_sequence("Stale desktop edit")
        owner.acknowledge_content_revision()
        owner.catalog.create_short_sequence("After reload")
    finally:
        cooperative.close()
        owner.close()


def test_version_ten_project_gains_recoverable_sequence_archiving(tmp_path: Path) -> None:
    root = tmp_path / "MigratedV10"
    with ProjectRepository.create(root, "MigratedV10"):
        pass
    with closing(sqlite3.connect(root / "project.mfp")) as connection, connection:
        connection.execute("ALTER TABLE sequence DROP COLUMN archived")
        connection.execute("UPDATE schema_info SET version=10")

    with ProjectRepository.open(root, writable=True) as repository:
        columns = {row["name"] for row in repository._fetchall("PRAGMA table_info(sequence)")}
        assert "archived" in columns
        assert repository._fetchone("SELECT version FROM schema_info")["version"] == PROJECT_SCHEMA_VERSION


def test_version_twenty_three_project_gains_primary_dialogue_and_source_transcript_cache(
    tmp_path: Path,
) -> None:
    root = tmp_path / "MigratedV23"
    subtitle_path = tmp_path / "legacy-transcript.srt"
    subtitle_path.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nLegacy transcript\n",
        encoding="utf-8",
    )
    with ProjectRepository.create(root, "MigratedV23") as repository:
        project = repository.catalog.get_project()
        editor = TimelineEditor(repository, project.main_sequence_id)
        audio_track = editor.add_track(TrackKind.AUDIO)
        subtitle_asset = repository.catalog.import_external_asset(
            subtitle_path,
            AssetKind.SUBTITLE,
        )
        document = SubtitleDocument(
            project_id=project.id,
            asset_id=subtitle_asset.id,
            sequence_id=project.main_sequence_id,
            language="en",
            purpose="sequence_transcript",
        )
        repository.subtitles.create_subtitle_document(
            document,
            [
                SubtitleSegment(
                    document_id=document.id,
                    start_frame=0,
                    end_frame=30,
                    text="Legacy transcript",
                )
            ],
        )

    with closing(sqlite3.connect(root / "project.mfp")) as connection, connection:
        connection.execute("DROP INDEX idx_track_primary_dialogue")
        connection.execute("DROP TABLE asset_transcript")
        connection.execute("ALTER TABLE track DROP COLUMN primary_dialogue")
        connection.execute("ALTER TABLE subtitle_document DROP COLUMN purpose")
        connection.execute("UPDATE schema_info SET version=23")

    with ProjectRepository.open(root, writable=True) as repository:
        migrated_track = next(
            track
            for track in repository.timeline.load_timeline(
                repository.catalog.get_project().main_sequence_id
            ).tracks
            if track.id == audio_track.id
        )
        migrated_document = repository.subtitles.get_subtitle_document(document.id)
        tables = {
            row["name"] for row in repository._fetchall("SELECT name FROM sqlite_master WHERE type='table'")
        }

        assert migrated_track.primary_dialogue is True
        assert migrated_document.purpose == "sequence_transcript"
        assert "asset_transcript" in tables
        assert repository._fetchone("SELECT version FROM schema_info")["version"] == PROJECT_SCHEMA_VERSION


def test_version_one_project_is_migrated_to_persisted_workflows(tmp_path: Path) -> None:
    root = tmp_path / "Migrated"
    with ProjectRepository.create(root, "Migrated"):
        pass
    database = root / "project.mfp"
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("DROP TABLE workflow_run")
        connection.execute("UPDATE schema_info SET version=1")
        connection.execute("UPDATE project SET workflow_auto_continue=0")

    with ProjectRepository.open(root, writable=True) as repository:
        assert repository.catalog.get_project().workflow_auto_continue is None
        assert repository.catalog.list_workflow_runs() == []
        version = repository._fetchone("SELECT version FROM schema_info")
        assert version["version"] == PROJECT_SCHEMA_VERSION


def test_version_three_project_gains_timeline_annotations_and_export_settings(tmp_path: Path) -> None:
    root = tmp_path / "MigratedV3"
    with ProjectRepository.create(root, "MigratedV3"):
        pass
    database = root / "project.mfp"
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("DROP TABLE sequence_export_setting")
        connection.execute("DROP TABLE timeline_range")
        connection.execute("DROP TABLE timeline_marker")
        connection.execute("UPDATE schema_info SET version=3")

    with ProjectRepository.open(root, writable=True) as repository:
        project = repository.catalog.get_project()
        assert repository.timeline.list_timeline_markers(project.main_sequence_id) == []
        assert repository.timeline.list_timeline_ranges(project.main_sequence_id) == []
        assert repository._fetchone("SELECT version FROM schema_info")["version"] == PROJECT_SCHEMA_VERSION


def test_version_five_project_gains_persisted_highlight_workspace_fields(
    tmp_path: Path,
) -> None:
    root = tmp_path / "MigratedV5"
    with ProjectRepository.create(root, "MigratedV5"):
        pass
    database = root / "project.mfp"
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("DROP TABLE highlight_candidate")
        connection.execute(
            """CREATE TABLE highlight_candidate (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                asset_id TEXT NOT NULL,
                start_frame INTEGER NOT NULL,
                end_frame INTEGER NOT NULL,
                title TEXT NOT NULL,
                reason TEXT NOT NULL,
                score REAL NOT NULL
            )"""
        )
        connection.execute("UPDATE schema_info SET version=5")

    with ProjectRepository.open(root, writable=True) as repository:
        columns = {row["name"] for row in repository._fetchall("PRAGMA table_info(highlight_candidate)")}
        assert {"document_id", "sequence_id", "selected"} <= columns
        assert repository._fetchone("SELECT version FROM schema_info")["version"] == PROJECT_SCHEMA_VERSION


def test_version_six_project_recovers_subtitle_media_relationship_and_clip_following(
    tmp_path: Path,
) -> None:
    root = tmp_path / "MigratedV6"
    video_path = tmp_path / "interview.mp4"
    subtitle_path = tmp_path / "interview.en.srt"
    video_path.write_bytes(b"video")
    subtitle_path.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nHello\n",
        encoding="utf-8",
    )
    with ProjectRepository.create(root, "MigratedV6") as repository:
        video = repository.catalog.import_external_asset(video_path, AssetKind.VIDEO)
        subtitle = repository.catalog.import_external_asset(subtitle_path, AssetKind.SUBTITLE)
        project = repository.catalog.get_project()
        state = repository.timeline.load_timeline(project.main_sequence_id)
        video_track = Track(
            sequence_id=project.main_sequence_id,
            name="视频 1",
            kind=TrackKind.VIDEO,
            position=0,
        )
        subtitle_track = Track(
            sequence_id=project.main_sequence_id,
            name="字幕 1",
            kind=TrackKind.SUBTITLE,
            position=1,
        )
        state.tracks = [video_track, subtitle_track]
        clip = Clip(
            track_id=video_track.id,
            asset_id=video.id,
            timeline_start=0,
            source_in=0,
            duration=90,
            media_kind=ClipMediaKind.VIDEO_ONLY,
        )
        state.clips.append(clip)
        repository.timeline.save_timeline(state)
        document = SubtitleDocument(
            project_id=project.id,
            asset_id=subtitle.id,
            language="en",
        )
        segment = SubtitleSegment(
            document_id=document.id,
            start_frame=0,
            end_frame=30,
            text="Hello",
        )
        repository.subtitles.create_subtitle_document(document, [segment])
        repository.subtitles.place_subtitle_document(document.id, subtitle_track.id, follow_clips=False)

    with closing(sqlite3.connect(root / "project.mfp")) as connection, connection:
        connection.execute("ALTER TABLE subtitle_document DROP COLUMN media_asset_id")
        connection.execute("UPDATE schema_info SET version=6")

    with ProjectRepository.open(root, writable=True) as repository:
        migrated = repository.subtitles.get_subtitle_document(document.id)
        assert migrated.asset_id == subtitle.id
        assert migrated.media_asset_id == video.id
        assert [item.id for item in repository.subtitles.list_subtitle_documents(video.id)] == [document.id]
        placement = repository.subtitles.list_subtitle_placements(subtitle_track.id)[0]
        assert placement.clip_id == clip.id
        assert repository._fetchone("SELECT version FROM schema_info")["version"] == PROJECT_SCHEMA_VERSION


def test_version_eight_project_migrates_download_tasks_and_workflows_to_requests(
    tmp_path: Path,
) -> None:
    root = tmp_path / "MigratedV8Downloads"
    with ProjectRepository.create(root, "MigratedV8Downloads") as repository:
        project = repository.catalog.get_project()
        sequence_id = project.main_sequence_id
    workflow_id = "download-workflow"
    task_id = "download-task"
    parameters = {
        "url": "https://x.com/outer/status/123",
        "resolution": "1080p",
        "playlist_items": "1,3",
        "download_subtitles": True,
        "subtitle_languages": ["en", "zh"],
        "codec": "avc",
        "filename": "Quoted videos",
        "workflow_run_id": workflow_id,
        "workflow_stage": "download",
    }
    with closing(sqlite3.connect(root / "project.mfp")) as connection, connection:
        connection.execute("DROP TABLE task")
        connection.execute(
            """CREATE TABLE task (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
                sequence_id TEXT REFERENCES sequence(id) ON DELETE SET NULL,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                name TEXT NOT NULL,
                progress REAL NOT NULL,
                message_code TEXT NOT NULL,
                input_asset_ids_json TEXT NOT NULL,
                parameters_json TEXT NOT NULL,
                artifacts_json TEXT NOT NULL,
                execution_trace_json TEXT NOT NULL DEFAULT '[]',
                error TEXT,
                revision INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )"""
        )
        connection.execute(
            """INSERT INTO task(
                id, project_id, sequence_id, kind, status, name, progress,
                message_code, input_asset_ids_json, parameters_json, artifacts_json,
                execution_trace_json, error, revision, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task_id,
                project.id,
                sequence_id,
                "download",
                "paused",
                "下载引用视频",
                0.0,
                "paused",
                "[]",
                json.dumps(parameters, ensure_ascii=False),
                "[]",
                "[]",
                None,
                0,
                1,
                1,
            ),
        )
        connection.execute(
            """INSERT INTO workflow_run(
                id, project_id, sequence_id, asset_ids_json, stage, status,
                auto_continue, payload_json, message_code, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                workflow_id,
                project.id,
                sequence_id,
                "[]",
                "download",
                "running",
                0,
                json.dumps({**parameters, "task_ids": [task_id]}, ensure_ascii=False),
                "workflow_download_running",
                1,
                1,
            ),
        )
        connection.execute("UPDATE schema_info SET version=8")

    with ProjectRepository.open(root, writable=True) as repository:
        assert repository._fetchone("SELECT version FROM schema_info")["version"] == PROJECT_SCHEMA_VERSION
        tasks = TaskRepository(repository).list()
        assert all(isinstance(task.command, DownloadMediaCommand) for task in tasks)
        requests = [task.command.request for task in tasks]
        workflow = repository.catalog.get_workflow_run(workflow_id)

        assert [request.entry.selector for request in requests] == [1, 3]
        assert all(task.command.workflow.run_id == workflow_id for task in tasks)
        assert [value.entry.selector for value in workflow.payload.requests] == [
            1,
            3,
        ]
        assert len(workflow.payload.task_ids) == 2


def test_timeline_annotations_and_sequence_export_preset_round_trip(tmp_path: Path) -> None:
    root = tmp_path / "Annotations"
    with ProjectRepository.create(root, "Annotations") as repository:
        sequence_id = repository.catalog.get_project().main_sequence_id
        state = repository.timeline.load_timeline(sequence_id)
        state.markers.append(TimelineMarker(sequence_id=sequence_id, frame=42, name="重点"))
        state.ranges.append(TimelineRange(sequence_id=sequence_id, start_frame=30, end_frame=90, name="候选"))
        repository.timeline.save_timeline(state)
        preset = ExportPreset(
            name="社交平台",
            format=ExportFormat.H264,
            container="mp4",
            video_codec="libx264",
            audio_codec="aac",
            pixel_format="yuv420p",
        )
        repository.catalog.save_sequence_export_preset(sequence_id, preset)

    with ProjectRepository.open(root) as reopened:
        sequence = reopened.catalog.get_sequence(reopened.catalog.get_project().main_sequence_id)
        state = reopened.timeline.load_timeline(sequence.id)
        assert [(item.frame, item.name) for item in state.markers] == [(42, "重点")]
        assert [(item.start_frame, item.end_frame) for item in state.ranges] == [(30, 90)]
        assert sequence.export_preset == preset


def test_version_nine_export_range_migrates_to_sequence_in_out(tmp_path: Path) -> None:
    root = tmp_path / "MigratedV9InOut"
    source = tmp_path / "source.mp4"
    source.write_bytes(b"media")
    with ProjectRepository.create(root, "MigratedV9InOut") as repository:
        asset = repository.catalog.import_external_asset(source, AssetKind.VIDEO)
        sequence_id = repository.catalog.get_project().main_sequence_id
        state = repository.timeline.load_timeline(sequence_id)
        video_track = Track(
            sequence_id=sequence_id,
            name="视频 1",
            kind=TrackKind.VIDEO,
            position=0,
        )
        state.tracks = [video_track]
        state.clips.append(
            Clip(
                track_id=video_track.id,
                asset_id=asset.id,
                timeline_start=0,
                source_in=0,
                duration=100,
                media_kind=ClipMediaKind.VIDEO_ONLY,
            )
        )
        repository.timeline.save_timeline(state)
        preset = ExportPreset(
            name="Legacy range",
            format=ExportFormat.H264,
            container="mp4",
            video_codec="libx264",
            audio_codec="aac",
            pixel_format="yuv420p",
        )
        repository.catalog.save_sequence_export_preset(sequence_id, preset)

    with closing(sqlite3.connect(root / "project.mfp")) as connection, connection:
        stored = json.loads(
            connection.execute(
                "SELECT preset_json FROM sequence_export_setting WHERE sequence_id=?",
                (sequence_id,),
            ).fetchone()[0]
        )
        stored["trim"] = {
            "start_frame": 5,
            "end_frame": 90,
            "auto_trim_silence": False,
            "auto_trim_leading_black": True,
        }
        connection.execute(
            "UPDATE sequence_export_setting SET preset_json=? WHERE sequence_id=?",
            (json.dumps(stored), sequence_id),
        )
        connection.execute("UPDATE schema_info SET version=9")

    with ProjectRepository.open(root, writable=True) as repository:
        migrated = repository.catalog.get_sequence(sequence_id)
        assert migrated.in_out == SequenceInOut(in_frame=5, out_frame=90)
        assert migrated.export_preset == preset
        persisted = json.loads(
            repository._fetchone(
                "SELECT preset_json FROM sequence_export_setting WHERE sequence_id=?",
                (sequence_id,),
            )["preset_json"]
        )
        assert "trim" not in persisted
        assert repository._fetchone("SELECT version FROM schema_info")["version"] == PROJECT_SCHEMA_VERSION


def test_external_asset_keeps_absolute_path_and_fingerprint(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"real-media-fixture")
    with ProjectRepository.create(tmp_path / "Project", "Project") as repository:
        asset = repository.catalog.import_external_asset(source, AssetKind.VIDEO)
        reloaded = repository.catalog.get_asset(asset.id)

        assert Path(reloaded.path).is_absolute()
        assert repository.catalog.resolve_asset_path(reloaded) == source.resolve()
        assert reloaded.fingerprint is not None
        assert fingerprint_matches(source, reloaded.fingerprint)


def test_asset_bins_persist_hierarchy_casefold_uniqueness_and_membership(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"real-media-fixture")
    root = tmp_path / "Asset Bins"
    with ProjectRepository.create(root, "Asset Bins") as repository:
        parent = repository.catalog.create_asset_bin("Scenes")
        child = repository.catalog.create_asset_bin("Street", parent.id)
        sibling = repository.catalog.create_asset_bin("People")
        asset = repository.catalog.import_external_asset(source, AssetKind.VIDEO)
        moved = repository.catalog.move_assets_to_bin([asset.id, asset.id], child.id)

        assert [item.id for item in repository.catalog.list_asset_bins()] == [
            parent.id,
            child.id,
            sibling.id,
        ]
        assert [item.id for item in moved] == [asset.id]
        assert repository.catalog.get_asset(asset.id).bin_id == child.id
        with pytest.raises(ValueError, match="重复名称"):
            repository.catalog.create_asset_bin("scenes")
        with pytest.raises(KeyError):
            repository.catalog.move_assets_to_bin([asset.id], "missing-bin")

    with ProjectRepository.open(root, writable=True) as reopened:
        bins = reopened.catalog.list_asset_bins()
        assert [(item.name, item.parent_id) for item in bins] == [
            ("Scenes", None),
            ("Street", bins[0].id),
            ("People", None),
        ]
        reloaded = reopened.catalog.list_assets()[0]
        assert reloaded.bin_id == bins[1].id
        [unfiled] = reopened.catalog.move_assets_to_bin([reloaded.id], None)
        assert unfiled.bin_id is None


def test_timeline_round_trip_persists_actual_asset_reference(tmp_path: Path) -> None:
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"fixture")
    root = tmp_path / "Project"
    with ProjectRepository.create(root, "Project") as repository:
        asset = repository.catalog.import_external_asset(source, AssetKind.VIDEO)
        project = repository.catalog.get_project()
        state = repository.timeline.load_timeline(project.main_sequence_id)
        video_track = Track(
            sequence_id=project.main_sequence_id,
            name="视频 1",
            kind=TrackKind.VIDEO,
            position=0,
        )
        state.tracks = [video_track]
        state.clips.append(
            Clip(
                track_id=video_track.id,
                asset_id=asset.id,
                timeline_start=0,
                source_in=0,
                duration=120,
                media_kind=ClipMediaKind.VIDEO_ONLY,
            )
        )
        repository.timeline.save_timeline(state)

    with ProjectRepository.open(root) as reopened:
        state = reopened.timeline.load_timeline(reopened.catalog.get_project().main_sequence_id)
        assert len(state.clips) == 1
        assert state.clips[0].asset_id == reopened.catalog.list_assets()[0].id


def test_managed_asset_cannot_escape_project(tmp_path: Path) -> None:
    from mediaflow.domain.enums import AssetOrigin
    from mediaflow.domain.project import Asset

    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"fixture")
    with ProjectRepository.create(tmp_path / "Project", "Project") as repository:
        project = repository.catalog.get_project()
        with pytest.raises(ValueError, match="inside the project"):
            repository.catalog.add_asset(
                Asset(
                    project_id=project.id,
                    name=outside.name,
                    kind=AssetKind.VIDEO,
                    origin=AssetOrigin.DOWNLOAD,
                    path=str(outside),
                    managed=True,
                )
            )


def test_changed_source_invalidates_derived_media(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"version-one")
    with ProjectRepository.create(tmp_path / "Project", "Project") as repository:
        asset = repository.catalog.import_external_asset(source, AssetKind.VIDEO)
        proxy = repository.project_dir / "proxies" / "proxy.mp4"
        sdr_proxy = repository.project_dir / "proxies" / "proxy-sdr.mp4"
        waveform = repository.project_dir / "cache" / "waveform.json"
        proxy.write_bytes(b"proxy")
        sdr_proxy.write_bytes(b"sdr-proxy")
        waveform.write_text("{}", encoding="utf-8")
        asset = repository.catalog.update_asset(
            asset.model_copy(
                update={
                    "proxy_path": str(proxy),
                    "sdr_preview_proxy_path": str(sdr_proxy),
                    "waveform_path": str(waveform),
                }
            )
        )

        source.write_bytes(b"version-two-is-different")
        refreshed = repository.catalog.refresh_asset_status(asset.id)

        assert refreshed.proxy_path is None
        assert refreshed.sdr_preview_proxy_path is None
        assert refreshed.waveform_path is None
        assert refreshed.fingerprint is not None
        assert refreshed.fingerprint.edge_sha256 != asset.fingerprint.edge_sha256


def test_derived_media_updates_merge_and_reject_stale_source_results(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"version-one")
    with ProjectRepository.create(tmp_path / "Project", "Project") as repository:
        imported = repository.catalog.import_external_asset(source, AssetKind.VIDEO)
        proxy = repository.project_dir / "proxies" / "proxy.mp4"
        waveform = repository.project_dir / "cache" / "waveform.json"

        repository.catalog.set_asset_waveform_path(
            imported.id,
            expected_fingerprint=imported.fingerprint,
            waveform_path=waveform,
        )
        merged = repository.catalog.set_asset_proxy_paths(
            imported.id,
            expected_fingerprint=imported.fingerprint,
            proxy_path=proxy,
            sdr_preview_proxy_path=None,
        )

        assert merged.proxy_path
        assert merged.waveform_path

        source.write_bytes(b"version-two-is-different")
        repository.catalog.refresh_asset_status(imported.id)
        with pytest.raises(RuntimeError, match="发生了变化"):
            repository.catalog.set_asset_waveform_path(
                imported.id,
                expected_fingerprint=imported.fingerprint,
                waveform_path=waveform,
            )
        current = repository.catalog.get_asset(imported.id)
        assert current.proxy_path is None
        assert current.waveform_path is None


def test_relink_requires_matching_content_or_explicit_confirmation(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    replacement = tmp_path / "replacement.mp4"
    source.write_bytes(b"original")
    replacement.write_bytes(b"different")
    with ProjectRepository.create(tmp_path / "Project", "Project") as repository:
        asset = repository.catalog.import_external_asset(source, AssetKind.VIDEO)
        source.unlink()
        assert repository.catalog.refresh_asset_status(asset.id).status.value == "offline"

        with pytest.raises(ValueError, match="does not match"):
            repository.catalog.relink_asset(asset.id, replacement)
        relinked = repository.catalog.relink_asset(asset.id, replacement, allow_different_content=True)
        assert repository.catalog.resolve_asset_path(relinked) == replacement.resolve()


def test_batch_relink_only_uses_exact_fingerprint_matches(tmp_path: Path) -> None:
    source_a = tmp_path / "source-a.mp4"
    source_b = tmp_path / "source-b.mp4"
    source_a.write_bytes(b"same-content")
    source_b.write_bytes(b"other-content")
    search_root = tmp_path / "relocated"
    search_root.mkdir()
    exact_match = search_root / "nested" / "source-a.mp4"
    exact_match.parent.mkdir()
    hidden_elsewhere = tmp_path / "outside-search.mp4"

    with ProjectRepository.create(tmp_path / "Project", "Project") as repository:
        asset_a = repository.catalog.import_external_asset(source_a, AssetKind.VIDEO)
        asset_b = repository.catalog.import_external_asset(source_b, AssetKind.VIDEO)
        source_a.replace(exact_match)
        source_b.replace(hidden_elsewhere)
        repository.catalog.refresh_asset_status(asset_a.id)
        repository.catalog.refresh_asset_status(asset_b.id)

        relinked, unresolved = AssetService(
            repository,
            probe=None,
            fingerprint_file=fingerprint_file,
        ).relink_offline_from_directory(search_root)
        assert [asset.id for asset in relinked] == [asset_a.id]
        assert [asset.id for asset in unresolved] == [asset_b.id]
        assert repository.catalog.resolve_asset_path(relinked[0]) == exact_match.resolve()


def test_stale_timeline_snapshot_is_rejected_without_losing_committed_changes(
    tmp_path: Path,
) -> None:
    with ProjectRepository.create(tmp_path / "Project", "Project") as repository:
        sequence_id = repository.catalog.get_project().main_sequence_id
        first = repository.timeline.load_timeline(sequence_id)
        stale = repository.timeline.load_timeline(sequence_id)
        first.markers.append(TimelineMarker(sequence_id=sequence_id, frame=10, name="producer-a"))
        repository.timeline.save_timeline(first)

        stale.markers.append(TimelineMarker(sequence_id=sequence_id, frame=20, name="producer-b"))
        with pytest.raises(TimelineRevisionConflict):
            repository.timeline.save_timeline(stale)

        persisted = repository.timeline.load_timeline(sequence_id)
        assert [(item.frame, item.name) for item in persisted.markers] == [(10, "producer-a")]
        assert persisted.sequence.timeline_revision == 1


def test_importing_the_same_external_file_reuses_the_persisted_asset(tmp_path: Path) -> None:
    source = tmp_path / "same-source.mp4"
    source.write_bytes(b"same external media")
    with ProjectRepository.create(tmp_path / "Project", "Project") as repository:
        first = repository.catalog.import_external_asset(source, AssetKind.VIDEO)
        second = repository.catalog.import_external_asset(source, AssetKind.VIDEO)

        assert second.id == first.id
        assert [asset.id for asset in repository.catalog.list_assets()] == [first.id]
