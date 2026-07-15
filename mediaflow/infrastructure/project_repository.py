from __future__ import annotations

import json
import sqlite3
import threading
from bisect import bisect_left, bisect_right
from collections.abc import Iterator
from contextlib import contextmanager
from fractions import Fraction
from pathlib import Path
from typing import Any

from mediaflow.domain.enums import (
    AssetKind,
    AssetOrigin,
    AssetStatus,
    ColorMode,
    SequenceKind,
    TrackKind,
    WorkflowStage,
    WorkflowStatus,
)
from mediaflow.domain.models import (
    Asset,
    AssetFingerprint,
    AudioBus,
    AudioEffect,
    Clip,
    ClipAudio,
    ClipTransform,
    ExportPreset,
    HighlightCandidate,
    MediaMetadata,
    Project,
    ProjectProfile,
    Sequence,
    SubtitleDocument,
    SubtitlePlacement,
    SubtitleSegment,
    TimelineMarker,
    TimelineRange,
    TimelineState,
    Track,
    Transition,
    WorkflowRun,
    new_id,
    now_ms,
)
from mediaflow.domain.timebase import frames_to_seconds, seconds_to_frames

from .file_fingerprint import fingerprint_file, fingerprint_matches
from .project_lock import ProjectWriteLock

PROJECT_FILE_NAME = "project.mfp"
PROJECT_SCHEMA_VERSION = 5
MANAGED_DIRECTORIES = ("downloads", "generated", "proxies", "cache", "exports")


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_info (
    component TEXT PRIMARY KEY,
    version INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS project (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    root_path TEXT NOT NULL,
    main_sequence_id TEXT NOT NULL,
    workflow_auto_continue INTEGER NOT NULL DEFAULT -1,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS sequence (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('main', 'short')),
    position INTEGER NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    fps_numerator INTEGER NOT NULL,
    fps_denominator INTEGER NOT NULL,
    color_mode TEXT NOT NULL,
    bit_depth INTEGER NOT NULL,
    audio_sample_rate INTEGER NOT NULL,
    audio_channels INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS asset (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    origin TEXT NOT NULL,
    path TEXT NOT NULL,
    managed INTEGER NOT NULL,
    proxy_path TEXT,
    sdr_preview_proxy_path TEXT,
    waveform_path TEXT,
    status TEXT NOT NULL,
    fingerprint_json TEXT,
    metadata_json TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS audio_bus (
    id TEXT PRIMARY KEY,
    sequence_id TEXT NOT NULL REFERENCES sequence(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    parent_bus_id TEXT REFERENCES audio_bus(id) ON DELETE RESTRICT,
    position INTEGER NOT NULL,
    gain_db REAL NOT NULL,
    muted INTEGER NOT NULL,
    solo INTEGER NOT NULL,
    channel_layout TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS track (
    id TEXT PRIMARY KEY,
    sequence_id TEXT NOT NULL REFERENCES sequence(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    position INTEGER NOT NULL,
    enabled INTEGER NOT NULL,
    locked INTEGER NOT NULL,
    muted INTEGER NOT NULL,
    solo INTEGER NOT NULL,
    audio_bus_id TEXT REFERENCES audio_bus(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS clip (
    id TEXT PRIMARY KEY,
    track_id TEXT NOT NULL REFERENCES track(id) ON DELETE CASCADE,
    asset_id TEXT NOT NULL REFERENCES asset(id) ON DELETE RESTRICT,
    timeline_start INTEGER NOT NULL,
    source_in INTEGER NOT NULL,
    duration INTEGER NOT NULL,
    speed_numerator INTEGER NOT NULL,
    speed_denominator INTEGER NOT NULL,
    pitch_compensation INTEGER NOT NULL,
    transform_json TEXT NOT NULL,
    audio_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS transition (
    id TEXT PRIMARY KEY,
    track_id TEXT NOT NULL REFERENCES track(id) ON DELETE CASCADE,
    left_clip_id TEXT NOT NULL REFERENCES clip(id) ON DELETE CASCADE,
    right_clip_id TEXT NOT NULL REFERENCES clip(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    duration INTEGER NOT NULL,
    parameters_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS timeline_marker (
    id TEXT PRIMARY KEY,
    sequence_id TEXT NOT NULL REFERENCES sequence(id) ON DELETE CASCADE,
    frame INTEGER NOT NULL,
    name TEXT NOT NULL,
    color TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS timeline_range (
    id TEXT PRIMARY KEY,
    sequence_id TEXT NOT NULL REFERENCES sequence(id) ON DELETE CASCADE,
    start_frame INTEGER NOT NULL,
    end_frame INTEGER NOT NULL,
    name TEXT NOT NULL,
    color TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sequence_export_setting (
    sequence_id TEXT PRIMARY KEY REFERENCES sequence(id) ON DELETE CASCADE,
    preset_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS subtitle_document (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    asset_id TEXT NOT NULL REFERENCES asset(id) ON DELETE CASCADE,
    language TEXT NOT NULL,
    source_document_id TEXT REFERENCES subtitle_document(id) ON DELETE SET NULL,
    is_source INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS subtitle_segment (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES subtitle_document(id) ON DELETE CASCADE,
    source_segment_id TEXT,
    start_frame INTEGER NOT NULL,
    end_frame INTEGER NOT NULL,
    text TEXT NOT NULL,
    speaker TEXT,
    confidence REAL
);
CREATE TABLE IF NOT EXISTS subtitle_placement (
    id TEXT PRIMARY KEY,
    track_id TEXT NOT NULL REFERENCES track(id) ON DELETE CASCADE,
    segment_id TEXT NOT NULL REFERENCES subtitle_segment(id) ON DELETE CASCADE,
    clip_id TEXT REFERENCES clip(id) ON DELETE CASCADE,
    start_frame INTEGER NOT NULL,
    end_frame INTEGER NOT NULL,
    text_override TEXT
);
CREATE TABLE IF NOT EXISTS subtitle_track_document (
    track_id TEXT NOT NULL REFERENCES track(id) ON DELETE CASCADE,
    document_id TEXT NOT NULL REFERENCES subtitle_document(id) ON DELETE CASCADE,
    follow_clips INTEGER NOT NULL,
    offset_frames INTEGER NOT NULL DEFAULT 0,
    source_start_frame INTEGER,
    source_end_frame INTEGER,
    PRIMARY KEY(track_id, document_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_subtitle_placement_source
ON subtitle_placement(track_id, segment_id, COALESCE(clip_id, ''));
CREATE TABLE IF NOT EXISTS audio_effect (
    id TEXT PRIMARY KEY,
    bus_id TEXT NOT NULL REFERENCES audio_bus(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    position INTEGER NOT NULL,
    enabled INTEGER NOT NULL,
    parameters_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS highlight_candidate (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    asset_id TEXT NOT NULL REFERENCES asset(id) ON DELETE CASCADE,
    start_frame INTEGER NOT NULL,
    end_frame INTEGER NOT NULL,
    title TEXT NOT NULL,
    reason TEXT NOT NULL,
    score REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS export_preset (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    data_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS task (
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
    error TEXT,
    revision INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS workflow_run (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    sequence_id TEXT NOT NULL REFERENCES sequence(id) ON DELETE CASCADE,
    asset_ids_json TEXT NOT NULL,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    auto_continue INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    message_code TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_asset_project ON asset(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_sequence_project ON sequence(project_id, position);
CREATE INDEX IF NOT EXISTS idx_track_sequence ON track(sequence_id, position);
CREATE INDEX IF NOT EXISTS idx_clip_track_time ON clip(track_id, timeline_start);
CREATE INDEX IF NOT EXISTS idx_marker_sequence_time ON timeline_marker(sequence_id, frame);
CREATE INDEX IF NOT EXISTS idx_range_sequence_time ON timeline_range(sequence_id, start_frame);
CREATE INDEX IF NOT EXISTS idx_task_project_time ON task(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_workflow_project_time ON workflow_run(project_id, updated_at);
"""

WORKFLOW_RUN_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS workflow_run (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    sequence_id TEXT NOT NULL REFERENCES sequence(id) ON DELETE CASCADE,
    asset_ids_json TEXT NOT NULL,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    auto_continue INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    message_code TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
)
"""

SUBTITLE_TRACK_DOCUMENT_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS subtitle_track_document (
    track_id TEXT NOT NULL REFERENCES track(id) ON DELETE CASCADE,
    document_id TEXT NOT NULL REFERENCES subtitle_document(id) ON DELETE CASCADE,
    follow_clips INTEGER NOT NULL,
    offset_frames INTEGER NOT NULL DEFAULT 0,
    source_start_frame INTEGER,
    source_end_frame INTEGER,
    PRIMARY KEY(track_id, document_id)
)
"""

TIMELINE_ANNOTATION_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS timeline_marker (
    id TEXT PRIMARY KEY,
    sequence_id TEXT NOT NULL REFERENCES sequence(id) ON DELETE CASCADE,
    frame INTEGER NOT NULL,
    name TEXT NOT NULL,
    color TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS timeline_range (
    id TEXT PRIMARY KEY,
    sequence_id TEXT NOT NULL REFERENCES sequence(id) ON DELETE CASCADE,
    start_frame INTEGER NOT NULL,
    end_frame INTEGER NOT NULL,
    name TEXT NOT NULL,
    color TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sequence_export_setting (
    sequence_id TEXT PRIMARY KEY REFERENCES sequence(id) ON DELETE CASCADE,
    preset_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_marker_sequence_time ON timeline_marker(sequence_id, frame);
CREATE INDEX IF NOT EXISTS idx_range_sequence_time ON timeline_range(sequence_id, start_frame);
"""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _model_json(model: Any) -> str:
    return _json(model.model_dump(mode="json"))


class ProjectRepository:
    def __init__(
        self,
        project_dir: Path,
        connection: sqlite3.Connection,
        *,
        read_only: bool,
        write_lock: ProjectWriteLock | None,
    ):
        self.project_dir = project_dir
        self.database_path = project_dir / PROJECT_FILE_NAME
        self._connection = connection
        self._connection_lock = threading.RLock()
        self.read_only = read_only
        self._write_lock = write_lock

    @classmethod
    def create(
        cls,
        project_dir: str | Path,
        name: str,
        profile: ProjectProfile | None = None,
    ) -> ProjectRepository:
        root = Path(project_dir).resolve()
        root.mkdir(parents=True, exist_ok=True)
        database_path = root / PROJECT_FILE_NAME
        if database_path.exists():
            raise FileExistsError(f"Project already exists: {database_path}")
        for directory in MANAGED_DIRECTORIES:
            (root / directory).mkdir(exist_ok=True)

        lock = ProjectWriteLock(root / "cache" / "project.lock")
        if not lock.acquire():
            raise RuntimeError(f"Project directory is already locked: {root}")
        try:
            connection = cls._connect(database_path, read_only=False)
            repository = cls(root, connection, read_only=False, write_lock=lock)
            repository._initialize(name=name, profile=profile or ProjectProfile())
            return repository
        except Exception:
            lock.release()
            raise

    @classmethod
    def open(cls, project_dir: str | Path, *, writable: bool = True) -> ProjectRepository:
        root = Path(project_dir).resolve(strict=True)
        database_path = root / PROJECT_FILE_NAME
        if not database_path.is_file():
            raise FileNotFoundError(database_path)

        lock: ProjectWriteLock | None = None
        read_only = not writable
        if writable:
            candidate = ProjectWriteLock(root / "cache" / "project.lock")
            if candidate.acquire():
                lock = candidate
            else:
                read_only = True
        connection = cls._connect(database_path, read_only=read_only)
        repository = cls(root, connection, read_only=read_only, write_lock=lock)
        repository._validate_schema()
        return repository

    @staticmethod
    def _connect(database_path: Path, *, read_only: bool) -> sqlite3.Connection:
        if read_only:
            connection = sqlite3.connect(
                f"file:{database_path.as_posix()}?mode=ro",
                uri=True,
                timeout=5.0,
                check_same_thread=False,
            )
        else:
            connection = sqlite3.connect(database_path, timeout=5.0, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        if not read_only:
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("PRAGMA synchronous=FULL")
        return connection

    def _initialize(self, *, name: str, profile: ProjectProfile) -> None:
        project_id = new_id()
        main_sequence_id = new_id()
        created_at = now_ms()
        project = Project(
            id=project_id,
            name=name,
            root_path=str(self.project_dir),
            main_sequence_id=main_sequence_id,
            created_at=created_at,
            updated_at=created_at,
        )
        sequence = Sequence(
            id=main_sequence_id,
            project_id=project_id,
            name="主序列",
            kind=SequenceKind.MAIN,
            profile=profile,
            created_at=created_at,
        )
        master_bus = AudioBus(sequence_id=main_sequence_id, name="主总线", position=0)
        dialogue_bus = AudioBus(
            sequence_id=main_sequence_id,
            name="对白",
            parent_bus_id=master_bus.id,
            position=1,
        )
        music_bus = AudioBus(
            sequence_id=main_sequence_id,
            name="音乐",
            parent_bus_id=master_bus.id,
            position=2,
        )
        effects_bus = AudioBus(
            sequence_id=main_sequence_id,
            name="效果",
            parent_bus_id=master_bus.id,
            position=3,
        )
        tracks = [
            Track(
                sequence_id=main_sequence_id,
                name="视频 1",
                kind=TrackKind.VIDEO,
                position=0,
                audio_bus_id=dialogue_bus.id,
            ),
            Track(
                sequence_id=main_sequence_id,
                name="音频 1",
                kind=TrackKind.AUDIO,
                position=1,
                audio_bus_id=dialogue_bus.id,
            ),
            Track(sequence_id=main_sequence_id, name="字幕 1", kind=TrackKind.SUBTITLE, position=2),
        ]
        with self.transaction() as connection:
            connection.executescript(SCHEMA_SQL)
            connection.execute(
                "INSERT INTO schema_info(component, version) VALUES('project', ?)",
                (PROJECT_SCHEMA_VERSION,),
            )
            connection.execute(
                """INSERT INTO project(
                    id, name, root_path, main_sequence_id, workflow_auto_continue,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    project.id,
                    project.name,
                    project.root_path,
                    project.main_sequence_id,
                    -1 if project.workflow_auto_continue is None else int(project.workflow_auto_continue),
                    project.created_at,
                    project.updated_at,
                ),
            )
            self._insert_sequence(connection, sequence)
            for bus in (master_bus, dialogue_bus, music_bus, effects_bus):
                self._insert_audio_bus(connection, bus)
            for track in tracks:
                self._insert_track(connection, track)

    def _validate_schema(self) -> None:
        try:
            row = self._fetchone("SELECT version FROM schema_info WHERE component='project'")
        except sqlite3.Error as error:
            raise RuntimeError(f"Invalid MediaFlow Pro project: {self.database_path}") from error
        if row is not None and int(row["version"]) == 1 and not self.read_only:
            with self.transaction() as connection:
                connection.execute(WORKFLOW_RUN_TABLE_SQL)
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_workflow_project_time "
                    "ON workflow_run(project_id, updated_at)"
                )
                connection.execute("UPDATE project SET workflow_auto_continue=-1")
                connection.execute(
                    "UPDATE schema_info SET version=? WHERE component='project'",
                    (2,),
                )
            row = self._fetchone("SELECT version FROM schema_info WHERE component='project'")
        if row is not None and int(row["version"]) == 2 and not self.read_only:
            with self.transaction() as connection:
                connection.execute(SUBTITLE_TRACK_DOCUMENT_TABLE_SQL)
                columns = {
                    item["name"]
                    for item in connection.execute("PRAGMA table_info(subtitle_placement)").fetchall()
                }
                if "clip_id" not in columns:
                    connection.execute(
                        "ALTER TABLE subtitle_placement ADD COLUMN clip_id TEXT "
                        "REFERENCES clip(id) ON DELETE CASCADE"
                    )
                connection.execute(
                    """INSERT OR IGNORE INTO subtitle_track_document(
                           track_id, document_id, follow_clips, offset_frames,
                           source_start_frame, source_end_frame
                       )
                       SELECT DISTINCT placement.track_id, segment.document_id,
                           CASE WHEN EXISTS (
                               SELECT 1
                               FROM clip
                               JOIN track clip_track ON clip_track.id=clip.track_id
                               JOIN subtitle_document document
                                   ON document.id=segment.document_id
                               WHERE clip_track.sequence_id=placement_track.sequence_id
                                 AND clip.asset_id=document.asset_id
                           ) THEN 1 ELSE 0 END,
                           0, NULL, NULL
                       FROM subtitle_placement placement
                       JOIN subtitle_segment segment ON segment.id=placement.segment_id
                       JOIN track placement_track ON placement_track.id=placement.track_id"""
                )
                sequence_ids = [
                    item["id"] for item in connection.execute("SELECT id FROM sequence").fetchall()
                ]
                for sequence_id in sequence_ids:
                    self._sync_subtitle_placements(connection, sequence_id)
                connection.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_subtitle_placement_source "
                    "ON subtitle_placement(track_id, segment_id, COALESCE(clip_id, ''))"
                )
                connection.execute(
                    "UPDATE schema_info SET version=? WHERE component='project'",
                    (3,),
                )
            row = self._fetchone("SELECT version FROM schema_info WHERE component='project'")
        if row is not None and int(row["version"]) == 3 and not self.read_only:
            with self.transaction() as connection:
                connection.executescript(TIMELINE_ANNOTATION_TABLES_SQL)
                connection.execute(
                    "UPDATE schema_info SET version=? WHERE component='project'",
                    (4,),
                )
            row = self._fetchone("SELECT version FROM schema_info WHERE component='project'")
        if row is not None and int(row["version"]) == 4 and not self.read_only:
            with self.transaction() as connection:
                columns = {
                    item["name"] for item in connection.execute("PRAGMA table_info(asset)").fetchall()
                }
                if "sdr_preview_proxy_path" not in columns:
                    connection.execute("ALTER TABLE asset ADD COLUMN sdr_preview_proxy_path TEXT")
                connection.execute(
                    "UPDATE schema_info SET version=? WHERE component='project'",
                    (PROJECT_SCHEMA_VERSION,),
                )
            row = self._fetchone("SELECT version FROM schema_info WHERE component='project'")
        if row is None or int(row["version"]) != PROJECT_SCHEMA_VERSION:
            raise RuntimeError(f"Unsupported project schema: {None if row is None else row['version']}")

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        if self.read_only:
            raise PermissionError("Project is open read-only")
        with self._connection_lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                yield self._connection
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise

    def close(self) -> None:
        with self._connection_lock:
            self._connection.close()
        if self._write_lock is not None:
            self._write_lock.release()
            self._write_lock = None

    def __enter__(self) -> ProjectRepository:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def get_project(self) -> Project:
        row = self._fetchone("SELECT * FROM project LIMIT 1")
        if row is None:
            raise RuntimeError("Project record is missing")
        return Project(
            id=row["id"],
            name=row["name"],
            root_path=str(self.project_dir),
            main_sequence_id=row["main_sequence_id"],
            workflow_auto_continue=(
                None if row["workflow_auto_continue"] < 0 else bool(row["workflow_auto_continue"])
            ),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def set_workflow_auto_continue(self, value: bool | None) -> Project:
        stored = -1 if value is None else int(value)
        with self.transaction() as connection:
            connection.execute(
                "UPDATE project SET workflow_auto_continue=?, updated_at=?",
                (stored, now_ms()),
            )
        return self.get_project()

    def save_workflow_run(self, run: WorkflowRun) -> WorkflowRun:
        project = self.get_project()
        if run.project_id != project.id:
            raise ValueError("Workflow run belongs to another project")
        self.get_sequence(run.sequence_id)
        if any(self.get_asset(asset_id).project_id != project.id for asset_id in run.asset_ids):
            raise ValueError("Workflow run contains an asset from another project")
        updated = run.model_copy(update={"updated_at": now_ms()})
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO workflow_run(
                    id, project_id, sequence_id, asset_ids_json, stage, status,
                    auto_continue, payload_json, message_code, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    sequence_id=excluded.sequence_id,
                    asset_ids_json=excluded.asset_ids_json,
                    stage=excluded.stage,
                    status=excluded.status,
                    auto_continue=excluded.auto_continue,
                    payload_json=excluded.payload_json,
                    message_code=excluded.message_code,
                    updated_at=excluded.updated_at""",
                (
                    updated.id,
                    updated.project_id,
                    updated.sequence_id,
                    _json(updated.asset_ids),
                    updated.stage.value,
                    updated.status.value,
                    int(updated.auto_continue),
                    _json(updated.payload),
                    updated.message_code,
                    updated.created_at,
                    updated.updated_at,
                ),
            )
            self._touch_project(connection)
        return self.get_workflow_run(updated.id)

    def get_workflow_run(self, run_id: str) -> WorkflowRun:
        row = self._fetchone("SELECT * FROM workflow_run WHERE id=?", (run_id,))
        if row is None:
            raise KeyError(run_id)
        return self._workflow_run_from_row(row)

    def list_workflow_runs(self, *, active_only: bool = False) -> list[WorkflowRun]:
        sql = "SELECT * FROM workflow_run"
        parameters: tuple = ()
        if active_only:
            sql += " WHERE status NOT IN ('completed', 'cancelled')"
        sql += " ORDER BY updated_at DESC, id"
        return [self._workflow_run_from_row(row) for row in self._fetchall(sql, parameters)]

    @staticmethod
    def _workflow_run_from_row(row: sqlite3.Row) -> WorkflowRun:
        return WorkflowRun(
            id=row["id"],
            project_id=row["project_id"],
            sequence_id=row["sequence_id"],
            asset_ids=json.loads(row["asset_ids_json"]),
            stage=WorkflowStage(row["stage"]),
            status=WorkflowStatus(row["status"]),
            auto_continue=bool(row["auto_continue"]),
            payload=json.loads(row["payload_json"]),
            message_code=row["message_code"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def list_sequences(self) -> list[Sequence]:
        rows = self._fetchall("SELECT * FROM sequence ORDER BY position, created_at")
        presets = {
            row["sequence_id"]: row["preset_json"]
            for row in self._fetchall("SELECT sequence_id, preset_json FROM sequence_export_setting")
        }
        return [self._sequence_from_row(row, presets.get(row["id"])) for row in rows]

    def get_sequence(self, sequence_id: str) -> Sequence:
        row = self._fetchone("SELECT * FROM sequence WHERE id=?", (sequence_id,))
        if row is None:
            raise KeyError(sequence_id)
        preset = self._fetchone(
            "SELECT preset_json FROM sequence_export_setting WHERE sequence_id=?",
            (sequence_id,),
        )
        return self._sequence_from_row(row, preset["preset_json"] if preset else None)

    def save_sequence_export_preset(self, sequence_id: str, preset: ExportPreset) -> Sequence:
        self.get_sequence(sequence_id)
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO sequence_export_setting(sequence_id, preset_json)
                   VALUES (?, ?)
                   ON CONFLICT(sequence_id) DO UPDATE SET preset_json=excluded.preset_json""",
                (sequence_id, _model_json(preset)),
            )
            self._touch_project(connection)
        return self.get_sequence(sequence_id)

    def create_short_sequence(
        self,
        name: str,
        profile: ProjectProfile | None = None,
    ) -> Sequence:
        project = self.get_project()
        position = len(self.list_sequences())
        sequence = Sequence(
            project_id=project.id,
            name=name,
            kind=SequenceKind.SHORT,
            position=position,
            profile=profile or ProjectProfile(width=1080, height=1920, fps_numerator=30, fps_denominator=1),
        )
        master = AudioBus(sequence_id=sequence.id, name="主总线", position=0)
        dialogue = AudioBus(
            sequence_id=sequence.id,
            name="对白",
            parent_bus_id=master.id,
            position=1,
        )
        music = AudioBus(
            sequence_id=sequence.id,
            name="音乐",
            parent_bus_id=master.id,
            position=2,
        )
        effects = AudioBus(
            sequence_id=sequence.id,
            name="效果",
            parent_bus_id=master.id,
            position=3,
        )
        with self.transaction() as connection:
            self._insert_sequence(connection, sequence)
            for bus in (master, dialogue, music, effects):
                self._insert_audio_bus(connection, bus)
            self._insert_track(
                connection,
                Track(
                    sequence_id=sequence.id,
                    name="视频 1",
                    kind=TrackKind.VIDEO,
                    position=0,
                    audio_bus_id=dialogue.id,
                ),
            )
            self._insert_track(
                connection,
                Track(
                    sequence_id=sequence.id,
                    name="音频 1",
                    kind=TrackKind.AUDIO,
                    position=1,
                    audio_bus_id=dialogue.id,
                ),
            )
            self._insert_track(
                connection,
                Track(sequence_id=sequence.id, name="字幕 1", kind=TrackKind.SUBTITLE, position=2),
            )
            self._touch_project(connection)
        return sequence

    def add_asset(self, asset: Asset) -> Asset:
        project = self.get_project()
        if asset.project_id != project.id:
            raise ValueError("Asset belongs to a different project")
        stored_path = self._stored_path(asset.path, managed=asset.managed)
        proxy_path = self._stored_optional_path(asset.proxy_path)
        sdr_preview_proxy_path = self._stored_optional_path(asset.sdr_preview_proxy_path)
        waveform_path = self._stored_optional_path(asset.waveform_path)
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO asset(
                    id, project_id, name, kind, origin, path, managed, proxy_path,
                    sdr_preview_proxy_path, waveform_path, status, fingerprint_json,
                    metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    asset.id,
                    asset.project_id,
                    asset.name,
                    asset.kind.value,
                    asset.origin.value,
                    stored_path,
                    int(asset.managed),
                    proxy_path,
                    sdr_preview_proxy_path,
                    waveform_path,
                    asset.status.value,
                    _model_json(asset.fingerprint) if asset.fingerprint else None,
                    _model_json(asset.metadata),
                    asset.created_at,
                ),
            )
            self._touch_project(connection)
        return self.get_asset(asset.id)

    def import_external_asset(self, path: str | Path, kind: AssetKind) -> Asset:
        source = Path(path).resolve(strict=True)
        project = self.get_project()
        return self.add_asset(
            Asset(
                project_id=project.id,
                name=source.name,
                kind=kind,
                origin=AssetOrigin.EXTERNAL,
                path=str(source),
                managed=False,
                fingerprint=fingerprint_file(source),
            )
        )

    def get_asset(self, asset_id: str) -> Asset:
        row = self._fetchone("SELECT * FROM asset WHERE id=?", (asset_id,))
        if row is None:
            raise KeyError(asset_id)
        return self._asset_from_row(row)

    def list_assets(self) -> list[Asset]:
        rows = self._fetchall("SELECT * FROM asset ORDER BY created_at, name")
        return [self._asset_from_row(row) for row in rows]

    def update_asset(self, asset: Asset) -> Asset:
        current = self.get_asset(asset.id)
        if current.project_id != asset.project_id:
            raise ValueError("Asset project cannot change")
        stored_path = self._stored_path(asset.path, managed=asset.managed)
        with self.transaction() as connection:
            connection.execute(
                """UPDATE asset SET
                    name=?, kind=?, origin=?, path=?, managed=?, proxy_path=?,
                    sdr_preview_proxy_path=?, waveform_path=?, status=?,
                    fingerprint_json=?, metadata_json=?
                WHERE id=?""",
                (
                    asset.name,
                    asset.kind.value,
                    asset.origin.value,
                    stored_path,
                    int(asset.managed),
                    self._stored_optional_path(asset.proxy_path),
                    self._stored_optional_path(asset.sdr_preview_proxy_path),
                    self._stored_optional_path(asset.waveform_path),
                    asset.status.value,
                    _model_json(asset.fingerprint) if asset.fingerprint else None,
                    _model_json(asset.metadata),
                    asset.id,
                ),
            )
            self._touch_project(connection)
        return self.get_asset(asset.id)

    def refresh_asset_status(self, asset_id: str) -> Asset:
        asset = self.get_asset(asset_id)
        source = self.resolve_asset_path(asset)
        if not source.is_file():
            return self.update_asset(asset.model_copy(update={"status": AssetStatus.OFFLINE}))
        if asset.fingerprint is None:
            return self.update_asset(
                asset.model_copy(
                    update={
                        "status": AssetStatus.ONLINE,
                        "fingerprint": fingerprint_file(source),
                    }
                )
            )
        if fingerprint_matches(source, asset.fingerprint):
            if asset.status != AssetStatus.ONLINE:
                return self.update_asset(asset.model_copy(update={"status": AssetStatus.ONLINE}))
            return asset
        return self.update_asset(
            asset.model_copy(
                update={
                    "status": AssetStatus.ONLINE,
                    "fingerprint": fingerprint_file(source),
                    "proxy_path": None,
                    "sdr_preview_proxy_path": None,
                    "waveform_path": None,
                    "metadata": MediaMetadata(),
                }
            )
        )

    def relink_asset(
        self,
        asset_id: str,
        replacement: str | Path,
        *,
        allow_different_content: bool = False,
    ) -> Asset:
        asset = self.get_asset(asset_id)
        if asset.managed:
            raise ValueError("Managed project assets cannot be relinked as external files")
        candidate = Path(replacement).resolve(strict=True)
        matches = asset.fingerprint is not None and fingerprint_matches(candidate, asset.fingerprint)
        if not matches and not allow_different_content:
            raise ValueError("Replacement content does not match the missing asset")
        replacement_fingerprint = fingerprint_file(candidate)
        changed = (
            asset.fingerprint is None or replacement_fingerprint.edge_sha256 != asset.fingerprint.edge_sha256
        )
        return self.update_asset(
            asset.model_copy(
                update={
                    "name": candidate.name,
                    "path": str(candidate),
                    "status": AssetStatus.ONLINE,
                    "fingerprint": replacement_fingerprint,
                    "proxy_path": None if changed else asset.proxy_path,
                    "sdr_preview_proxy_path": (
                        None if changed else asset.sdr_preview_proxy_path
                    ),
                    "waveform_path": None if changed else asset.waveform_path,
                    "metadata": MediaMetadata() if changed else asset.metadata,
                }
            )
        )

    def resolve_asset_path(self, asset: Asset) -> Path:
        path = Path(asset.path)
        return (self.project_dir / path).resolve() if asset.managed else path.resolve()

    def load_timeline(self, sequence_id: str) -> TimelineState:
        sequence = self.get_sequence(sequence_id)
        track_rows = self._fetchall(
            "SELECT * FROM track WHERE sequence_id=? ORDER BY position, id",
            (sequence_id,),
        )
        tracks = [self._track_from_row(row) for row in track_rows]
        track_ids = [track.id for track in tracks]
        if not track_ids:
            return TimelineState(
                sequence=sequence,
                markers=self.list_timeline_markers(sequence_id),
                ranges=self.list_timeline_ranges(sequence_id),
            )
        placeholders = ",".join("?" for _ in track_ids)
        clip_rows = self._fetchall(
            f"SELECT * FROM clip WHERE track_id IN ({placeholders}) ORDER BY timeline_start, id",
            track_ids,
        )
        transition_rows = self._fetchall(
            f"SELECT * FROM transition WHERE track_id IN ({placeholders}) ORDER BY id",
            track_ids,
        )
        return TimelineState(
            sequence=sequence,
            tracks=tracks,
            clips=[self._clip_from_row(row) for row in clip_rows],
            transitions=[self._transition_from_row(row) for row in transition_rows],
            markers=self.list_timeline_markers(sequence_id),
            ranges=self.list_timeline_ranges(sequence_id),
        )

    def list_timeline_markers(self, sequence_id: str) -> list[TimelineMarker]:
        self.get_sequence(sequence_id)
        return [
            TimelineMarker(
                id=row["id"],
                sequence_id=row["sequence_id"],
                frame=row["frame"],
                name=row["name"],
                color=row["color"],
            )
            for row in self._fetchall(
                "SELECT * FROM timeline_marker WHERE sequence_id=? ORDER BY frame, id",
                (sequence_id,),
            )
        ]

    def list_timeline_ranges(self, sequence_id: str) -> list[TimelineRange]:
        self.get_sequence(sequence_id)
        return [
            TimelineRange(
                id=row["id"],
                sequence_id=row["sequence_id"],
                start_frame=row["start_frame"],
                end_frame=row["end_frame"],
                name=row["name"],
                color=row["color"],
            )
            for row in self._fetchall(
                "SELECT * FROM timeline_range WHERE sequence_id=? ORDER BY start_frame, id",
                (sequence_id,),
            )
        ]

    def save_timeline(self, state: TimelineState) -> None:
        existing = self.get_sequence(state.sequence.id)
        if existing.project_id != state.sequence.project_id:
            raise ValueError("Sequence project cannot change")
        track_ids = {track.id for track in state.tracks}
        if any(track.sequence_id != state.sequence.id for track in state.tracks):
            raise ValueError("Timeline contains a track from another sequence")
        if any(clip.track_id not in track_ids for clip in state.clips):
            raise ValueError("Timeline clip references an unknown track")
        clip_ids = {clip.id for clip in state.clips}
        if any(
            transition.track_id not in track_ids
            or transition.left_clip_id not in clip_ids
            or transition.right_clip_id not in clip_ids
            for transition in state.transitions
        ):
            raise ValueError("Timeline transition references an unknown clip or track")
        if any(marker.sequence_id != state.sequence.id for marker in state.markers):
            raise ValueError("Timeline marker belongs to another sequence")
        if any(item.sequence_id != state.sequence.id for item in state.ranges):
            raise ValueError("Timeline range belongs to another sequence")

        frame_clock_changed = (
            existing.profile.fps_numerator != state.sequence.profile.fps_numerator
            or existing.profile.fps_denominator != state.sequence.profile.fps_denominator
        )

        def reframe(value: int) -> int:
            return seconds_to_frames(
                frames_to_seconds(
                    value,
                    existing.profile.fps_numerator,
                    existing.profile.fps_denominator,
                ),
                state.sequence.profile.fps_numerator,
                state.sequence.profile.fps_denominator,
            )

        with self.transaction() as connection:
            self._update_sequence(connection, state.sequence)
            existing_track_ids = {
                row["id"]
                for row in connection.execute(
                    "SELECT id FROM track WHERE sequence_id=?", (state.sequence.id,)
                ).fetchall()
            }
            existing_clip_ids: set[str] = set()
            if existing_track_ids:
                placeholders = ",".join("?" for _ in existing_track_ids)
                existing_clip_ids = {
                    row["id"]
                    for row in connection.execute(
                        f"SELECT id FROM clip WHERE track_id IN ({placeholders})",
                        tuple(existing_track_ids),
                    ).fetchall()
                }

            self._delete_missing(
                connection,
                "transition",
                "id",
                self._ids_for_sequence_transitions(state.sequence.id),
                {item.id for item in state.transitions},
            )
            self._delete_missing(connection, "clip", "id", existing_clip_ids, clip_ids)
            self._delete_missing(connection, "track", "id", existing_track_ids, track_ids)

            for track in state.tracks:
                self._upsert_track(connection, track)
            for clip in state.clips:
                self._upsert_clip(connection, clip)
            for transition in state.transitions:
                self._upsert_transition(connection, transition)
            self._delete_missing(
                connection,
                "timeline_marker",
                "id",
                {
                    row["id"]
                    for row in connection.execute(
                        "SELECT id FROM timeline_marker WHERE sequence_id=?",
                        (state.sequence.id,),
                    ).fetchall()
                },
                {item.id for item in state.markers},
            )
            self._delete_missing(
                connection,
                "timeline_range",
                "id",
                {
                    row["id"]
                    for row in connection.execute(
                        "SELECT id FROM timeline_range WHERE sequence_id=?",
                        (state.sequence.id,),
                    ).fetchall()
                },
                {item.id for item in state.ranges},
            )
            for marker in state.markers:
                self._upsert_timeline_marker(connection, marker)
            for item in state.ranges:
                self._upsert_timeline_range(connection, item)
            self._store_sequence_export_preset(connection, state.sequence)
            if frame_clock_changed:
                project = self.get_project()
                if state.sequence.id == project.main_sequence_id:
                    for row in connection.execute("SELECT id, metadata_json FROM asset").fetchall():
                        metadata = MediaMetadata.model_validate_json(row["metadata_json"])
                        metadata = metadata.model_copy(
                            update={"duration_frames": reframe(metadata.duration_frames)}
                        )
                        connection.execute(
                            """UPDATE asset SET proxy_path=NULL, sdr_preview_proxy_path=NULL,
                               metadata_json=? WHERE id=?""",
                            (_model_json(metadata), row["id"]),
                        )
                    for table in ("subtitle_segment", "highlight_candidate"):
                        rows = connection.execute(
                            f"SELECT id, start_frame, end_frame FROM {table}"
                        ).fetchall()
                        for row in rows:
                            start_frame = reframe(row["start_frame"])
                            connection.execute(
                                f"UPDATE {table} SET start_frame=?, end_frame=? WHERE id=?",
                                (
                                    start_frame,
                                    max(start_frame + 1, reframe(row["end_frame"])),
                                    row["id"],
                                ),
                            )
            if (
                state.sequence.profile != existing.profile
                and state.sequence.id == self.get_project().main_sequence_id
                and not frame_clock_changed
            ):
                connection.execute(
                    "UPDATE asset SET proxy_path=NULL, sdr_preview_proxy_path=NULL"
                )
            self._sync_subtitle_placements(connection, state.sequence.id)
            self._touch_project(connection)

    def save_clip_changes(self, state: TimelineState, clip_ids: set[str]) -> None:
        """Persist in-place clip edits without rewriting an unchanged timeline graph."""
        if not clip_ids:
            return
        clips = {clip.id: clip for clip in state.clips if clip.id in clip_ids}
        if set(clips) != clip_ids:
            raise ValueError("Clip delta references a missing clip")
        track_ids = {
            row["id"]
            for row in self._fetchall("SELECT id FROM track WHERE sequence_id=?", (state.sequence.id,))
        }
        if any(clip.track_id not in track_ids for clip in clips.values()):
            raise ValueError("Clip delta references a track outside the sequence")
        with self.transaction() as connection:
            for clip in clips.values():
                self._upsert_clip(connection, clip)
            self._sync_subtitle_placements(
                connection,
                state.sequence.id,
                clip_ids=clip_ids,
            )
            self._touch_project(connection)

    def apply_main_profile_change(
        self,
        state: TimelineState,
        assets: list[Asset],
        *,
        old_profile: ProjectProfile,
    ) -> None:
        """Atomically replace the main frame clock and every value owned by it."""
        project = self.get_project()
        if state.sequence.id != project.main_sequence_id:
            raise ValueError("Only the main sequence owns the shared project frame clock")
        if {item.id for item in assets} != {item.id for item in self.list_assets()}:
            raise ValueError("Profile changes must update metadata for every project asset")
        new_profile = state.sequence.profile

        def reframe(value: int) -> int:
            return seconds_to_frames(
                frames_to_seconds(
                    value,
                    old_profile.fps_numerator,
                    old_profile.fps_denominator,
                ),
                new_profile.fps_numerator,
                new_profile.fps_denominator,
            )

        with self.transaction() as connection:
            self._update_sequence(connection, state.sequence)
            for clip in state.clips:
                connection.execute(
                    """UPDATE clip SET timeline_start=?, source_in=?, duration=?,
                       speed_numerator=?, speed_denominator=?, pitch_compensation=?,
                       transform_json=?, audio_json=? WHERE id=?""",
                    (
                        clip.timeline_start,
                        clip.source_in,
                        clip.duration,
                        clip.speed_numerator,
                        clip.speed_denominator,
                        int(clip.pitch_compensation),
                        _model_json(clip.transform),
                        _model_json(clip.audio),
                        clip.id,
                    ),
                )
            for transition in state.transitions:
                connection.execute(
                    "UPDATE transition SET duration=?, parameters_json=? WHERE id=?",
                    (transition.duration, _json(transition.parameters), transition.id),
                )
            for marker in state.markers:
                connection.execute(
                    "UPDATE timeline_marker SET frame=? WHERE id=? AND sequence_id=?",
                    (marker.frame, marker.id, state.sequence.id),
                )
            for item in state.ranges:
                connection.execute(
                    """UPDATE timeline_range SET start_frame=?, end_frame=?
                       WHERE id=? AND sequence_id=?""",
                    (item.start_frame, item.end_frame, item.id, state.sequence.id),
                )
            for asset in assets:
                connection.execute(
                    """UPDATE asset SET proxy_path=?, sdr_preview_proxy_path=?,
                       metadata_json=? WHERE id=?""",
                    (
                        self._stored_optional_path(asset.proxy_path),
                        self._stored_optional_path(asset.sdr_preview_proxy_path),
                        _model_json(asset.metadata),
                        asset.id,
                    ),
                )

            for row in connection.execute(
                "SELECT id, start_frame, end_frame FROM subtitle_segment"
            ).fetchall():
                start_frame = reframe(row["start_frame"])
                connection.execute(
                    "UPDATE subtitle_segment SET start_frame=?, end_frame=? WHERE id=?",
                    (
                        start_frame,
                        max(start_frame + 1, reframe(row["end_frame"])),
                        row["id"],
                    ),
                )
            for row in connection.execute(
                "SELECT id, start_frame, end_frame FROM highlight_candidate"
            ).fetchall():
                start_frame = reframe(row["start_frame"])
                connection.execute(
                    "UPDATE highlight_candidate SET start_frame=?, end_frame=? WHERE id=?",
                    (
                        start_frame,
                        max(start_frame + 1, reframe(row["end_frame"])),
                        row["id"],
                    ),
                )
            self._sync_subtitle_placements(connection, state.sequence.id)
            self._touch_project(connection)

    def list_audio_buses(self, sequence_id: str) -> list[AudioBus]:
        rows = self._fetchall(
            "SELECT * FROM audio_bus WHERE sequence_id=? ORDER BY position, id",
            (sequence_id,),
        )
        return [
            AudioBus(
                id=row["id"],
                sequence_id=row["sequence_id"],
                name=row["name"],
                parent_bus_id=row["parent_bus_id"],
                position=row["position"],
                gain_db=row["gain_db"],
                muted=bool(row["muted"]),
                solo=bool(row["solo"]),
                channel_layout=row["channel_layout"],
            )
            for row in rows
        ]

    def save_audio_bus(self, bus: AudioBus) -> AudioBus:
        sequence = self.get_sequence(bus.sequence_id)
        del sequence
        buses = {item.id: item for item in self.list_audio_buses(bus.sequence_id)}
        if bus.parent_bus_id == bus.id:
            raise ValueError("Audio bus cannot route to itself")
        if bus.parent_bus_id:
            parent = buses.get(bus.parent_bus_id)
            if parent is None:
                raise ValueError("Audio bus parent does not exist in this sequence")
            seen = {bus.id}
            cursor: AudioBus | None = parent
            while cursor is not None:
                if cursor.id in seen:
                    raise ValueError("Audio bus routing cannot contain a cycle")
                seen.add(cursor.id)
                cursor = buses.get(cursor.parent_bus_id) if cursor.parent_bus_id else None
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO audio_bus(
                    id, sequence_id, name, parent_bus_id, position, gain_db,
                    muted, solo, channel_layout
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name, parent_bus_id=excluded.parent_bus_id,
                    position=excluded.position, gain_db=excluded.gain_db,
                    muted=excluded.muted, solo=excluded.solo,
                    channel_layout=excluded.channel_layout""",
                (
                    bus.id,
                    bus.sequence_id,
                    bus.name,
                    bus.parent_bus_id,
                    bus.position,
                    bus.gain_db,
                    int(bus.muted),
                    int(bus.solo),
                    bus.channel_layout,
                ),
            )
            self._touch_project(connection)
        return next(item for item in self.list_audio_buses(bus.sequence_id) if item.id == bus.id)

    def save_audio_effect(self, effect: AudioEffect) -> AudioEffect:
        with self.transaction() as connection:
            bus = connection.execute(
                "SELECT sequence_id FROM audio_bus WHERE id=?", (effect.bus_id,)
            ).fetchone()
            if bus is None:
                raise KeyError(effect.bus_id)
            connection.execute(
                """INSERT INTO audio_effect(
                    id, bus_id, kind, position, enabled, parameters_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    bus_id=excluded.bus_id, kind=excluded.kind,
                    position=excluded.position, enabled=excluded.enabled,
                    parameters_json=excluded.parameters_json""",
                (
                    effect.id,
                    effect.bus_id,
                    effect.kind.value,
                    effect.position,
                    int(effect.enabled),
                    _json(effect.parameters),
                ),
            )
            self._touch_project(connection)
        return effect

    def list_audio_effects(self, bus_id: str) -> list[AudioEffect]:
        rows = self._fetchall("SELECT * FROM audio_effect WHERE bus_id=? ORDER BY position, id", (bus_id,))
        return [
            AudioEffect(
                id=row["id"],
                bus_id=row["bus_id"],
                kind=row["kind"],
                position=row["position"],
                enabled=bool(row["enabled"]),
                parameters=json.loads(row["parameters_json"]),
            )
            for row in rows
        ]

    def save_audio_effect_chain(self, bus_id: str, effects: list[AudioEffect]) -> list[AudioEffect]:
        existing_ids = {effect.id for effect in self.list_audio_effects(bus_id)}
        if {effect.id for effect in effects} != existing_ids:
            raise ValueError("Audio effect reordering must preserve the complete chain")
        if any(effect.bus_id != bus_id for effect in effects):
            raise ValueError("Audio effect chain contains an effect from another bus")
        if [effect.position for effect in effects] != list(range(len(effects))):
            raise ValueError("Audio effect positions must be contiguous")
        with self.transaction() as connection:
            for effect in effects:
                connection.execute(
                    """UPDATE audio_effect SET position=?, enabled=?, parameters_json=?
                       WHERE id=? AND bus_id=?""",
                    (
                        effect.position,
                        int(effect.enabled),
                        _json(effect.parameters),
                        effect.id,
                        bus_id,
                    ),
                )
            self._touch_project(connection)
        return self.list_audio_effects(bus_id)

    def remove_audio_effect(self, effect_id: str) -> None:
        row = self._fetchone("SELECT bus_id FROM audio_effect WHERE id=?", (effect_id,))
        if row is None:
            raise KeyError(effect_id)
        bus_id = row["bus_id"]
        with self.transaction() as connection:
            connection.execute("DELETE FROM audio_effect WHERE id=?", (effect_id,))
            remaining = connection.execute(
                "SELECT id FROM audio_effect WHERE bus_id=? ORDER BY position, id",
                (bus_id,),
            ).fetchall()
            for position, effect in enumerate(remaining):
                connection.execute(
                    "UPDATE audio_effect SET position=? WHERE id=?",
                    (position, effect["id"]),
                )
            self._touch_project(connection)

    def create_subtitle_document(
        self,
        document: SubtitleDocument,
        segments: list[SubtitleSegment],
    ) -> SubtitleDocument:
        project = self.get_project()
        if document.project_id != project.id:
            raise ValueError("Subtitle document belongs to another project")
        self.get_asset(document.asset_id)
        if any(segment.document_id != document.id for segment in segments):
            raise ValueError("Subtitle segment belongs to another document")
        if document.source_document_id:
            source = self.get_subtitle_document(document.source_document_id)
            if source.asset_id != document.asset_id:
                raise ValueError("Translation source must belong to the same asset")
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO subtitle_document(
                    id, project_id, asset_id, language, source_document_id,
                    is_source, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    document.id,
                    document.project_id,
                    document.asset_id,
                    document.language,
                    document.source_document_id,
                    int(document.is_source),
                    document.created_at,
                ),
            )
            for segment in segments:
                self._insert_subtitle_segment(connection, segment)
            self._touch_project(connection)
        return document

    def get_subtitle_document(self, document_id: str) -> SubtitleDocument:
        row = self._fetchone("SELECT * FROM subtitle_document WHERE id=?", (document_id,))
        if row is None:
            raise KeyError(document_id)
        return self._subtitle_document_from_row(row)

    def list_subtitle_documents(self, asset_id: str | None = None) -> list[SubtitleDocument]:
        if asset_id:
            rows = self._fetchall(
                "SELECT * FROM subtitle_document WHERE asset_id=? ORDER BY created_at, id",
                (asset_id,),
            )
        else:
            rows = self._fetchall("SELECT * FROM subtitle_document ORDER BY created_at, id")
        return [self._subtitle_document_from_row(row) for row in rows]

    def list_subtitle_segments(self, document_id: str) -> list[SubtitleSegment]:
        rows = self._fetchall(
            "SELECT * FROM subtitle_segment WHERE document_id=? ORDER BY start_frame, id",
            (document_id,),
        )
        return [self._subtitle_segment_from_row(row) for row in rows]

    def replace_subtitle_segments(
        self,
        document_id: str,
        segments: list[SubtitleSegment],
    ) -> None:
        self.get_subtitle_document(document_id)
        if any(segment.document_id != document_id for segment in segments):
            raise ValueError("Subtitle segment belongs to another document")
        with self.transaction() as connection:
            connection.execute("DELETE FROM subtitle_segment WHERE document_id=?", (document_id,))
            for segment in segments:
                self._insert_subtitle_segment(connection, segment)
            sequence_ids = [
                row["sequence_id"]
                for row in connection.execute(
                    """SELECT DISTINCT track.sequence_id
                       FROM subtitle_track_document link
                       JOIN track ON track.id=link.track_id
                       WHERE link.document_id=?""",
                    (document_id,),
                ).fetchall()
            ]
            for sequence_id in sequence_ids:
                self._sync_subtitle_placements(connection, sequence_id)
            self._touch_project(connection)

    def place_subtitle_document(
        self,
        document_id: str,
        track_id: str,
        *,
        offset_frames: int = 0,
        source_start_frame: int | None = None,
        source_end_frame: int | None = None,
        follow_clips: bool | None = None,
    ) -> list[SubtitlePlacement]:
        track_row = self._fetchone("SELECT * FROM track WHERE id=?", (track_id,))
        if track_row is None:
            raise KeyError(track_id)
        if track_row["kind"] != TrackKind.SUBTITLE.value:
            raise ValueError("Subtitle documents can only be placed on subtitle tracks")
        self.get_subtitle_document(document_id)
        if follow_clips is None:
            matching = self._fetchone(
                """SELECT clip.id
                   FROM clip
                   JOIN track clip_track ON clip_track.id=clip.track_id
                   JOIN subtitle_document document ON document.id=?
                   WHERE clip_track.sequence_id=? AND clip.asset_id=document.asset_id
                   LIMIT 1""",
                (document_id, track_row["sequence_id"]),
            )
            follow_clips = matching is not None
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO subtitle_track_document(
                       track_id, document_id, follow_clips, offset_frames,
                       source_start_frame, source_end_frame
                   ) VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(track_id, document_id) DO UPDATE SET
                       follow_clips=excluded.follow_clips,
                       offset_frames=excluded.offset_frames,
                       source_start_frame=excluded.source_start_frame,
                       source_end_frame=excluded.source_end_frame""",
                (
                    track_id,
                    document_id,
                    int(follow_clips),
                    offset_frames,
                    source_start_frame,
                    source_end_frame,
                ),
            )
            self._sync_subtitle_placements(connection, track_row["sequence_id"])
            self._touch_project(connection)
        segment_ids = {item.id for item in self.list_subtitle_segments(document_id)}
        return [
            item for item in self.list_subtitle_placements(track_id) if item.segment_id in segment_ids
        ]

    def list_subtitle_placements(self, track_id: str) -> list[SubtitlePlacement]:
        rows = self._fetchall(
            "SELECT * FROM subtitle_placement WHERE track_id=? ORDER BY start_frame, id",
            (track_id,),
        )
        return [
            SubtitlePlacement(
                id=row["id"],
                track_id=row["track_id"],
                segment_id=row["segment_id"],
                clip_id=row["clip_id"],
                start_frame=row["start_frame"],
                end_frame=row["end_frame"],
                text_override=row["text_override"],
            )
            for row in rows
        ]

    def update_subtitle_placement_text(
        self,
        placement_id: str,
        text_override: str | None,
    ) -> SubtitlePlacement:
        row = self._fetchone("SELECT * FROM subtitle_placement WHERE id=?", (placement_id,))
        if row is None:
            raise KeyError(placement_id)
        normalized = None if text_override is None else text_override.strip()
        if normalized == "":
            raise ValueError("Subtitle text cannot be empty")
        with self.transaction() as connection:
            connection.execute(
                "UPDATE subtitle_placement SET text_override=? WHERE id=?",
                (normalized, placement_id),
            )
            self._touch_project(connection)
        return next(
            item
            for item in self.list_subtitle_placements(row["track_id"])
            if item.id == placement_id
        )

    def add_subtitle_placements(
        self,
        placements: list[SubtitlePlacement],
    ) -> list[SubtitlePlacement]:
        if not placements:
            return []
        track_ids = {item.track_id for item in placements}
        rows = self._fetchall(
            f"SELECT id, kind FROM track WHERE id IN ({','.join('?' for _ in track_ids)})",
            tuple(track_ids),
        )
        if len(rows) != len(track_ids) or any(row["kind"] != TrackKind.SUBTITLE.value for row in rows):
            raise ValueError("Subtitle placements require subtitle tracks")
        with self.transaction() as connection:
            for item in placements:
                connection.execute(
                    """INSERT INTO subtitle_placement(
                           id, track_id, segment_id, clip_id, start_frame, end_frame,
                           text_override
                       ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        item.id,
                        item.track_id,
                        item.segment_id,
                        item.clip_id,
                        item.start_frame,
                        item.end_frame,
                        item.text_override,
                    ),
                )
            self._touch_project(connection)
        by_track = {
            item.id: item
            for track_id in track_ids
            for item in self.list_subtitle_placements(track_id)
        }
        return [by_track[item.id] for item in placements]

    def apply_subtitle_placement_to_document(
        self,
        placement_id: str,
        text: str,
    ) -> SubtitleSegment:
        value = text.strip()
        if not value:
            raise ValueError("Subtitle text cannot be empty")
        row = self._fetchone("SELECT * FROM subtitle_placement WHERE id=?", (placement_id,))
        if row is None:
            raise KeyError(placement_id)
        with self.transaction() as connection:
            connection.execute(
                "UPDATE subtitle_segment SET text=? WHERE id=?",
                (value, row["segment_id"]),
            )
            connection.execute(
                "UPDATE subtitle_placement SET text_override=NULL WHERE id=?",
                (placement_id,),
            )
            self._touch_project(connection)
        segment_row = self._fetchone("SELECT * FROM subtitle_segment WHERE id=?", (row["segment_id"],))
        if segment_row is None:
            raise RuntimeError("Subtitle segment disappeared during update")
        return self._subtitle_segment_from_row(segment_row)

    @staticmethod
    def _round_fraction(value: Fraction) -> int:
        quotient, remainder = divmod(value.numerator, value.denominator)
        return quotient + (1 if remainder * 2 >= value.denominator else 0)

    def _sync_subtitle_placements(
        self,
        connection: sqlite3.Connection,
        sequence_id: str,
        *,
        clip_ids: set[str] | None = None,
    ) -> None:
        """Compile track/document links into clip-aware, editable placements."""
        project_row = connection.execute(
            "SELECT main_sequence_id FROM project LIMIT 1"
        ).fetchone()
        if project_row is None:
            return
        main_profile = connection.execute(
            "SELECT fps_numerator, fps_denominator FROM sequence WHERE id=?",
            (project_row["main_sequence_id"],),
        ).fetchone()
        target_profile = connection.execute(
            "SELECT fps_numerator, fps_denominator FROM sequence WHERE id=?",
            (sequence_id,),
        ).fetchone()
        if main_profile is None or target_profile is None:
            return

        links = connection.execute(
            """SELECT link.*, document.asset_id
               FROM subtitle_track_document link
               JOIN track ON track.id=link.track_id
               JOIN subtitle_document document ON document.id=link.document_id
               WHERE track.sequence_id=?""",
            (sequence_id,),
        ).fetchall()
        for link in links:
            if clip_ids is not None and not bool(link["follow_clips"]):
                continue
            clips = []
            if bool(link["follow_clips"]):
                clip_filter = ""
                parameters: list[Any] = [sequence_id, link["asset_id"]]
                if clip_ids is not None:
                    placeholders = ",".join("?" for _ in clip_ids)
                    clip_filter = f" AND clip.id IN ({placeholders})"
                    parameters.extend(sorted(clip_ids))
                clips = connection.execute(
                    """SELECT clip.*
                       FROM clip
                       JOIN track ON track.id=clip.track_id
                       WHERE track.sequence_id=? AND clip.asset_id=?"""
                    + clip_filter
                    + " ORDER BY clip.timeline_start, clip.id",
                    parameters,
                ).fetchall()
                if clip_ids is not None and not clips:
                    continue

            segment_sql = "SELECT * FROM subtitle_segment WHERE document_id=?"
            segment_parameters: list[Any] = [link["document_id"]]
            if clip_ids is not None and clips:
                target_to_main = Fraction(
                    main_profile["fps_numerator"] * target_profile["fps_denominator"],
                    main_profile["fps_denominator"] * target_profile["fps_numerator"],
                )
                ranges = [self._clip_source_range(clip) for clip in clips]
                main_start = min(item[0] for item in ranges) * target_to_main
                main_end = max(item[1] for item in ranges) * target_to_main
                lower = main_start.numerator // main_start.denominator - 1
                upper = -(-main_end.numerator // main_end.denominator) + 1
                segment_sql += " AND end_frame>? AND start_frame<?"
                segment_parameters.extend([lower, upper])
            segments = connection.execute(
                segment_sql + " ORDER BY start_frame, id",
                segment_parameters,
            ).fetchall()

            converted_segments: list[tuple[sqlite3.Row, int, int]] = []
            for segment in segments:
                source_start = seconds_to_frames(
                    frames_to_seconds(
                        segment["start_frame"],
                        main_profile["fps_numerator"],
                        main_profile["fps_denominator"],
                    ),
                    target_profile["fps_numerator"],
                    target_profile["fps_denominator"],
                )
                source_end = seconds_to_frames(
                    frames_to_seconds(
                        segment["end_frame"],
                        main_profile["fps_numerator"],
                        main_profile["fps_denominator"],
                    ),
                    target_profile["fps_numerator"],
                    target_profile["fps_denominator"],
                )
                converted_segments.append((segment, source_start, source_end))

            desired: dict[tuple[str, str | None], tuple[int, int]] = {}
            if bool(link["follow_clips"]):
                starts = [item[1] for item in converted_segments]
                maximum_end = -1
                prefix_maximum_ends: list[int] = []
                for _, _, source_end in converted_segments:
                    maximum_end = max(maximum_end, source_end)
                    prefix_maximum_ends.append(maximum_end)
                for clip in clips:
                    clip_start, clip_end = self._clip_source_range(clip)
                    first = bisect_right(prefix_maximum_ends, clip_start)
                    last = bisect_left(starts, clip_end)
                    for segment, source_start, source_end in converted_segments[first:last]:
                        mapped = self._map_segment_to_clip(source_start, source_end, clip)
                        if mapped is not None:
                            desired[(segment["id"], clip["id"])] = mapped
            else:
                for segment, source_start, source_end in converted_segments:
                    start = source_start
                    end = source_end
                    if link["source_start_frame"] is not None:
                        if end <= link["source_start_frame"]:
                            continue
                        start = max(start, link["source_start_frame"])
                    if link["source_end_frame"] is not None:
                        if start >= link["source_end_frame"]:
                            continue
                        end = min(end, link["source_end_frame"])
                    start += link["offset_frames"]
                    end += link["offset_frames"]
                    if end > 0:
                        desired[(segment["id"], None)] = (max(0, start), max(1, end))

            placement_filter = ""
            placement_parameters: list[Any] = [link["track_id"], link["document_id"]]
            if clip_ids is not None:
                placeholders = ",".join("?" for _ in clip_ids)
                placement_filter = f" AND placement.clip_id IN ({placeholders})"
                placement_parameters.extend(sorted(clip_ids))
            existing_rows = connection.execute(
                """SELECT placement.*
                   FROM subtitle_placement placement
                   JOIN subtitle_segment segment ON segment.id=placement.segment_id
                   WHERE placement.track_id=? AND segment.document_id=?"""
                + placement_filter
                + " ORDER BY placement.id",
                placement_parameters,
            ).fetchall()
            existing: dict[tuple[str, str | None], sqlite3.Row] = {}
            duplicate_ids: list[str] = []
            for row in existing_rows:
                key = (row["segment_id"], row["clip_id"])
                if key in existing:
                    duplicate_ids.append(row["id"])
                else:
                    existing[key] = row
            stale_ids = duplicate_ids + [row["id"] for key, row in existing.items() if key not in desired]
            if stale_ids:
                placeholders = ",".join("?" for _ in stale_ids)
                connection.execute(
                    f"DELETE FROM subtitle_placement WHERE id IN ({placeholders})",
                    stale_ids,
                )
            for key, (start, end) in desired.items():
                row = existing.get(key)
                if row is not None:
                    if row["start_frame"] != start or row["end_frame"] != end:
                        connection.execute(
                            "UPDATE subtitle_placement SET start_frame=?, end_frame=? WHERE id=?",
                            (start, end, row["id"]),
                        )
                else:
                    connection.execute(
                        """INSERT INTO subtitle_placement(
                               id, track_id, segment_id, clip_id,
                               start_frame, end_frame, text_override
                           ) VALUES (?, ?, ?, ?, ?, ?, NULL)""",
                        (new_id(), link["track_id"], key[0], key[1], start, end),
                    )

    @staticmethod
    def _clip_source_range(clip: sqlite3.Row) -> tuple[Fraction, Fraction]:
        speed = Fraction(abs(clip["speed_numerator"]), clip["speed_denominator"])
        source_in = Fraction(clip["source_in"])
        consumed = Fraction(clip["duration"]) * speed
        if clip["speed_numerator"] > 0:
            return source_in, source_in + consumed
        return source_in - consumed, source_in

    def _map_segment_to_clip(
        self,
        segment_start: int,
        segment_end: int,
        clip: sqlite3.Row,
    ) -> tuple[int, int] | None:
        speed = Fraction(abs(clip["speed_numerator"]), clip["speed_denominator"])
        source_in = Fraction(clip["source_in"])
        consumed = Fraction(clip["duration"]) * speed
        if clip["speed_numerator"] > 0:
            start = max(Fraction(segment_start), source_in)
            end = min(Fraction(segment_end), source_in + consumed)
            if end <= start:
                return None
            timeline_start = clip["timeline_start"] + self._round_fraction((start - source_in) / speed)
            timeline_end = clip["timeline_start"] + self._round_fraction((end - source_in) / speed)
        else:
            start = max(Fraction(segment_start), source_in - consumed)
            end = min(Fraction(segment_end), source_in)
            if end <= start:
                return None
            timeline_start = clip["timeline_start"] + self._round_fraction((source_in - end) / speed)
            timeline_end = clip["timeline_start"] + self._round_fraction((source_in - start) / speed)
        timeline_start = max(
            clip["timeline_start"],
            min(clip["timeline_start"] + clip["duration"] - 1, timeline_start),
        )
        timeline_end = max(
            timeline_start + 1,
            min(clip["timeline_start"] + clip["duration"], timeline_end),
        )
        return timeline_start, timeline_end

    def save_highlights(self, candidates: list[HighlightCandidate]) -> None:
        project = self.get_project()
        if any(candidate.project_id != project.id for candidate in candidates):
            raise ValueError("Highlight belongs to another project")
        with self.transaction() as connection:
            for candidate in candidates:
                connection.execute(
                    """INSERT INTO highlight_candidate(
                        id, project_id, asset_id, start_frame, end_frame,
                        title, reason, score
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        start_frame=excluded.start_frame, end_frame=excluded.end_frame,
                        title=excluded.title, reason=excluded.reason, score=excluded.score""",
                    (
                        candidate.id,
                        candidate.project_id,
                        candidate.asset_id,
                        candidate.start_frame,
                        candidate.end_frame,
                        candidate.title,
                        candidate.reason,
                        candidate.score,
                    ),
                )
            self._touch_project(connection)

    def list_highlights(self, asset_id: str | None = None) -> list[HighlightCandidate]:
        if asset_id:
            rows = self._fetchall(
                "SELECT * FROM highlight_candidate WHERE asset_id=? ORDER BY score DESC, start_frame",
                (asset_id,),
            )
        else:
            rows = self._fetchall("SELECT * FROM highlight_candidate ORDER BY score DESC, start_frame")
        return [
            HighlightCandidate(
                id=row["id"],
                project_id=row["project_id"],
                asset_id=row["asset_id"],
                start_frame=row["start_frame"],
                end_frame=row["end_frame"],
                title=row["title"],
                reason=row["reason"],
                score=row["score"],
            )
            for row in rows
        ]

    @staticmethod
    def _insert_subtitle_segment(
        connection: sqlite3.Connection,
        segment: SubtitleSegment,
    ) -> None:
        connection.execute(
            """INSERT INTO subtitle_segment(
                id, document_id, source_segment_id, start_frame, end_frame,
                text, speaker, confidence
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                segment.id,
                segment.document_id,
                segment.source_segment_id,
                segment.start_frame,
                segment.end_frame,
                segment.text,
                segment.speaker,
                segment.confidence,
            ),
        )

    @staticmethod
    def _subtitle_document_from_row(row: sqlite3.Row) -> SubtitleDocument:
        return SubtitleDocument(
            id=row["id"],
            project_id=row["project_id"],
            asset_id=row["asset_id"],
            language=row["language"],
            source_document_id=row["source_document_id"],
            is_source=bool(row["is_source"]),
            created_at=row["created_at"],
        )

    @staticmethod
    def _subtitle_segment_from_row(row: sqlite3.Row) -> SubtitleSegment:
        return SubtitleSegment(
            id=row["id"],
            document_id=row["document_id"],
            source_segment_id=row["source_segment_id"],
            start_frame=row["start_frame"],
            end_frame=row["end_frame"],
            text=row["text"],
            speaker=row["speaker"],
            confidence=row["confidence"],
        )

    def _stored_path(self, path: str, *, managed: bool) -> str:
        candidate = Path(path)
        resolved = (
            (self.project_dir / candidate).resolve()
            if managed and not candidate.is_absolute()
            else candidate.resolve()
        )
        if not managed:
            return str(resolved)
        try:
            relative = resolved.relative_to(self.project_dir)
        except ValueError as error:
            raise ValueError("Managed asset must be inside the project directory") from error
        if not relative.parts or relative.parts[0] not in MANAGED_DIRECTORIES:
            raise ValueError("Managed asset must be stored in a managed project directory")
        return relative.as_posix()

    def _stored_optional_path(self, path: str | None) -> str | None:
        if not path:
            return None
        candidate = Path(path)
        resolved = (
            (self.project_dir / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
        )
        try:
            return resolved.relative_to(self.project_dir).as_posix()
        except ValueError:
            return str(resolved)

    def _asset_from_row(self, row: sqlite3.Row) -> Asset:
        fingerprint = (
            AssetFingerprint.model_validate_json(row["fingerprint_json"]) if row["fingerprint_json"] else None
        )
        return Asset(
            id=row["id"],
            project_id=row["project_id"],
            name=row["name"],
            kind=AssetKind(row["kind"]),
            origin=AssetOrigin(row["origin"]),
            path=row["path"],
            managed=bool(row["managed"]),
            proxy_path=row["proxy_path"],
            sdr_preview_proxy_path=row["sdr_preview_proxy_path"],
            waveform_path=row["waveform_path"],
            status=AssetStatus(row["status"]),
            fingerprint=fingerprint,
            metadata=MediaMetadata.model_validate_json(row["metadata_json"]),
            created_at=row["created_at"],
        )

    @staticmethod
    def _insert_sequence(connection: sqlite3.Connection, sequence: Sequence) -> None:
        profile = sequence.profile
        connection.execute(
            """INSERT INTO sequence(
                id, project_id, name, kind, position, width, height,
                fps_numerator, fps_denominator, color_mode, bit_depth,
                audio_sample_rate, audio_channels, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                sequence.id,
                sequence.project_id,
                sequence.name,
                sequence.kind.value,
                sequence.position,
                profile.width,
                profile.height,
                profile.fps_numerator,
                profile.fps_denominator,
                profile.color_mode.value,
                profile.bit_depth,
                profile.audio_sample_rate,
                profile.audio_channels,
                sequence.created_at,
            ),
        )

    @staticmethod
    def _update_sequence(connection: sqlite3.Connection, sequence: Sequence) -> None:
        profile = sequence.profile
        connection.execute(
            """UPDATE sequence SET
                name=?, kind=?, position=?, width=?, height=?, fps_numerator=?,
                fps_denominator=?, color_mode=?, bit_depth=?, audio_sample_rate=?,
                audio_channels=? WHERE id=?""",
            (
                sequence.name,
                sequence.kind.value,
                sequence.position,
                profile.width,
                profile.height,
                profile.fps_numerator,
                profile.fps_denominator,
                profile.color_mode.value,
                profile.bit_depth,
                profile.audio_sample_rate,
                profile.audio_channels,
                sequence.id,
            ),
        )

    @staticmethod
    def _sequence_from_row(row: sqlite3.Row, preset_json: str | None = None) -> Sequence:
        return Sequence(
            id=row["id"],
            project_id=row["project_id"],
            name=row["name"],
            kind=SequenceKind(row["kind"]),
            position=row["position"],
            export_preset=ExportPreset.model_validate_json(preset_json) if preset_json else None,
            profile=ProjectProfile(
                width=row["width"],
                height=row["height"],
                fps_numerator=row["fps_numerator"],
                fps_denominator=row["fps_denominator"],
                color_mode=ColorMode(row["color_mode"]),
                bit_depth=row["bit_depth"],
                audio_sample_rate=row["audio_sample_rate"],
                audio_channels=row["audio_channels"],
            ),
            created_at=row["created_at"],
        )

    @staticmethod
    def _store_sequence_export_preset(
        connection: sqlite3.Connection,
        sequence: Sequence,
    ) -> None:
        if sequence.export_preset is None:
            connection.execute(
                "DELETE FROM sequence_export_setting WHERE sequence_id=?",
                (sequence.id,),
            )
            return
        connection.execute(
            """INSERT INTO sequence_export_setting(sequence_id, preset_json)
               VALUES (?, ?)
               ON CONFLICT(sequence_id) DO UPDATE SET preset_json=excluded.preset_json""",
            (sequence.id, _model_json(sequence.export_preset)),
        )

    @staticmethod
    def _upsert_timeline_marker(
        connection: sqlite3.Connection,
        marker: TimelineMarker,
    ) -> None:
        connection.execute(
            """INSERT INTO timeline_marker(id, sequence_id, frame, name, color)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET frame=excluded.frame,
                   name=excluded.name, color=excluded.color""",
            (marker.id, marker.sequence_id, marker.frame, marker.name, marker.color),
        )

    @staticmethod
    def _upsert_timeline_range(
        connection: sqlite3.Connection,
        item: TimelineRange,
    ) -> None:
        connection.execute(
            """INSERT INTO timeline_range(
                   id, sequence_id, start_frame, end_frame, name, color
               ) VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET start_frame=excluded.start_frame,
                   end_frame=excluded.end_frame, name=excluded.name, color=excluded.color""",
            (
                item.id,
                item.sequence_id,
                item.start_frame,
                item.end_frame,
                item.name,
                item.color,
            ),
        )

    @staticmethod
    def _insert_audio_bus(connection: sqlite3.Connection, bus: AudioBus) -> None:
        connection.execute(
            """INSERT INTO audio_bus(
                id, sequence_id, name, parent_bus_id, position, gain_db,
                muted, solo, channel_layout
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                bus.id,
                bus.sequence_id,
                bus.name,
                bus.parent_bus_id,
                bus.position,
                bus.gain_db,
                int(bus.muted),
                int(bus.solo),
                bus.channel_layout,
            ),
        )

    @staticmethod
    def _insert_track(connection: sqlite3.Connection, track: Track) -> None:
        connection.execute(
            """INSERT INTO track(
                id, sequence_id, name, kind, position, enabled, locked,
                muted, solo, audio_bus_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                track.id,
                track.sequence_id,
                track.name,
                track.kind.value,
                track.position,
                int(track.enabled),
                int(track.locked),
                int(track.muted),
                int(track.solo),
                track.audio_bus_id,
            ),
        )

    @staticmethod
    def _upsert_track(connection: sqlite3.Connection, track: Track) -> None:
        connection.execute(
            """INSERT INTO track(
                id, sequence_id, name, kind, position, enabled, locked,
                muted, solo, audio_bus_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name, kind=excluded.kind, position=excluded.position,
                enabled=excluded.enabled, locked=excluded.locked, muted=excluded.muted,
                solo=excluded.solo, audio_bus_id=excluded.audio_bus_id""",
            (
                track.id,
                track.sequence_id,
                track.name,
                track.kind.value,
                track.position,
                int(track.enabled),
                int(track.locked),
                int(track.muted),
                int(track.solo),
                track.audio_bus_id,
            ),
        )

    @staticmethod
    def _track_from_row(row: sqlite3.Row) -> Track:
        return Track(
            id=row["id"],
            sequence_id=row["sequence_id"],
            name=row["name"],
            kind=TrackKind(row["kind"]),
            position=row["position"],
            enabled=bool(row["enabled"]),
            locked=bool(row["locked"]),
            muted=bool(row["muted"]),
            solo=bool(row["solo"]),
            audio_bus_id=row["audio_bus_id"],
        )

    @staticmethod
    def _upsert_clip(connection: sqlite3.Connection, clip: Clip) -> None:
        connection.execute(
            """INSERT INTO clip(
                id, track_id, asset_id, timeline_start, source_in, duration,
                speed_numerator, speed_denominator, pitch_compensation,
                transform_json, audio_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                track_id=excluded.track_id, asset_id=excluded.asset_id,
                timeline_start=excluded.timeline_start, source_in=excluded.source_in,
                duration=excluded.duration, speed_numerator=excluded.speed_numerator,
                speed_denominator=excluded.speed_denominator,
                pitch_compensation=excluded.pitch_compensation,
                transform_json=excluded.transform_json, audio_json=excluded.audio_json""",
            (
                clip.id,
                clip.track_id,
                clip.asset_id,
                clip.timeline_start,
                clip.source_in,
                clip.duration,
                clip.speed_numerator,
                clip.speed_denominator,
                int(clip.pitch_compensation),
                _model_json(clip.transform),
                _model_json(clip.audio),
            ),
        )

    @staticmethod
    def _clip_from_row(row: sqlite3.Row) -> Clip:
        return Clip(
            id=row["id"],
            track_id=row["track_id"],
            asset_id=row["asset_id"],
            timeline_start=row["timeline_start"],
            source_in=row["source_in"],
            duration=row["duration"],
            speed_numerator=row["speed_numerator"],
            speed_denominator=row["speed_denominator"],
            pitch_compensation=bool(row["pitch_compensation"]),
            transform=ClipTransform.model_validate_json(row["transform_json"]),
            audio=ClipAudio.model_validate_json(row["audio_json"]),
        )

    @staticmethod
    def _upsert_transition(connection: sqlite3.Connection, transition: Transition) -> None:
        connection.execute(
            """INSERT INTO transition(
                id, track_id, left_clip_id, right_clip_id, kind, duration, parameters_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                track_id=excluded.track_id, left_clip_id=excluded.left_clip_id,
                right_clip_id=excluded.right_clip_id, kind=excluded.kind,
                duration=excluded.duration, parameters_json=excluded.parameters_json""",
            (
                transition.id,
                transition.track_id,
                transition.left_clip_id,
                transition.right_clip_id,
                transition.kind.value,
                transition.duration,
                _json(transition.parameters),
            ),
        )

    @staticmethod
    def _transition_from_row(row: sqlite3.Row) -> Transition:
        return Transition(
            id=row["id"],
            track_id=row["track_id"],
            left_clip_id=row["left_clip_id"],
            right_clip_id=row["right_clip_id"],
            kind=row["kind"],
            duration=row["duration"],
            parameters=json.loads(row["parameters_json"]),
        )

    def _ids_for_sequence_transitions(self, sequence_id: str) -> set[str]:
        rows = self._fetchall(
            """SELECT transition.id FROM transition
            JOIN track ON track.id=transition.track_id
            WHERE track.sequence_id=?""",
            (sequence_id,),
        )
        return {row["id"] for row in rows}

    def _fetchone(self, sql: str, parameters: tuple | list = ()) -> sqlite3.Row | None:
        with self._connection_lock:
            return self._connection.execute(sql, parameters).fetchone()

    def _fetchall(self, sql: str, parameters: tuple | list = ()) -> list[sqlite3.Row]:
        with self._connection_lock:
            return self._connection.execute(sql, parameters).fetchall()

    @staticmethod
    def _delete_missing(
        connection: sqlite3.Connection,
        table: str,
        column: str,
        existing_ids: set[str],
        retained_ids: set[str],
    ) -> None:
        for item_id in existing_ids - retained_ids:
            connection.execute(f"DELETE FROM {table} WHERE {column}=?", (item_id,))

    @staticmethod
    def _touch_project(connection: sqlite3.Connection) -> None:
        connection.execute("UPDATE project SET updated_at=?", (now_ms(),))
