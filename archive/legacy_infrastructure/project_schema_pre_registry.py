from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Literal

from mediaflow.domain.downloads import DownloadEntry, DownloadRequest
from mediaflow.domain.enums import AssetKind, TrackKind, WorkflowStage
from mediaflow.domain.media_association import related_media_stem
from mediaflow.domain.model_base import new_id
from mediaflow.domain.settings import AsrSettings
from mediaflow.infrastructure.project_serialization import (
    json_value as _json,
)
from mediaflow.infrastructure.project_serialization import (
    model_json as _model_json,
)
from mediaflow.infrastructure.storage_paths import default_media_root
from mediaflow.infrastructure.task_command_migrations import migrate_stored_task_command

PROJECT_FILE_NAME = "project.mfp"
PROJECT_SCHEMA_VERSION = 33
MANAGED_DIRECTORIES = ("sources", "generated", "proxies", "cache", "exports")


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_info (
    component TEXT PRIMARY KEY,
    version INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS project (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    main_sequence_id TEXT NOT NULL,
    workflow_auto_continue INTEGER NOT NULL DEFAULT -1,
    content_revision INTEGER NOT NULL DEFAULT 0,
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
    profile_confirmed INTEGER NOT NULL DEFAULT 1,
    in_frame INTEGER,
    out_frame INTEGER,
    archived INTEGER NOT NULL DEFAULT 0,
    timeline_revision INTEGER NOT NULL DEFAULT 0,
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
CREATE TABLE IF NOT EXISTS web_asset (
    asset_id TEXT PRIMARY KEY REFERENCES asset(id) ON DELETE CASCADE,
    manifest_json TEXT NOT NULL,
    source_hash TEXT NOT NULL
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
    audio_bus_id TEXT REFERENCES audio_bus(id) ON DELETE SET NULL,
    linked_audio_track_id TEXT REFERENCES track(id) ON DELETE SET NULL,
    primary_dialogue INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS clip (
    id TEXT PRIMARY KEY,
    track_id TEXT NOT NULL REFERENCES track(id) ON DELETE CASCADE,
    asset_id TEXT NOT NULL REFERENCES asset(id) ON DELETE RESTRICT,
    timeline_start INTEGER NOT NULL,
    source_in INTEGER NOT NULL,
    duration INTEGER NOT NULL,
    media_kind TEXT NOT NULL CHECK(media_kind IN ('linked_av', 'video_only', 'audio_only')),
    speed_numerator INTEGER NOT NULL,
    speed_denominator INTEGER NOT NULL,
    pitch_compensation INTEGER NOT NULL,
    transform_json TEXT NOT NULL,
    transform_keyframes_json TEXT NOT NULL DEFAULT '[]',
    audio_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS compound_clip (
    id TEXT PRIMARY KEY,
    sequence_id TEXT NOT NULL REFERENCES sequence(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    clip_ids_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS web_clip_state (
    clip_id TEXT PRIMARY KEY REFERENCES clip(id) ON DELETE CASCADE,
    state_json TEXT NOT NULL,
    revision INTEGER NOT NULL
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
    media_asset_id TEXT REFERENCES asset(id) ON DELETE SET NULL,
    sequence_id TEXT REFERENCES sequence(id) ON DELETE CASCADE,
    language TEXT NOT NULL,
    source_document_id TEXT REFERENCES subtitle_document(id) ON DELETE SET NULL,
    is_source INTEGER NOT NULL,
    purpose TEXT NOT NULL DEFAULT 'subtitle'
        CHECK(purpose IN ('subtitle', 'sequence_transcript')),
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS asset_transcript (
    asset_id TEXT NOT NULL REFERENCES asset(id) ON DELETE CASCADE,
    signature TEXT NOT NULL,
    language TEXT NOT NULL,
    duration_seconds REAL NOT NULL,
    result_json TEXT NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY(asset_id, signature)
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
CREATE TABLE IF NOT EXISTS subtitle_word (
    id TEXT PRIMARY KEY,
    segment_id TEXT NOT NULL REFERENCES subtitle_segment(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    start_frame INTEGER NOT NULL,
    end_frame INTEGER NOT NULL,
    text TEXT NOT NULL,
    confidence REAL,
    timing_source TEXT NOT NULL CHECK(timing_source IN ('recognized', 'estimated')),
    excluded INTEGER NOT NULL DEFAULT 0,
    UNIQUE(segment_id, position)
);
CREATE TABLE IF NOT EXISTS subtitle_placement (
    id TEXT PRIMARY KEY,
    track_id TEXT NOT NULL REFERENCES track(id) ON DELETE CASCADE,
    segment_id TEXT NOT NULL REFERENCES subtitle_segment(id) ON DELETE CASCADE,
    clip_id TEXT REFERENCES clip(id) ON DELETE CASCADE,
    start_frame INTEGER NOT NULL,
    end_frame INTEGER NOT NULL,
    text_override TEXT,
    timing_overridden INTEGER NOT NULL DEFAULT 0
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
    document_id TEXT REFERENCES subtitle_document(id) ON DELETE SET NULL,
    sequence_id TEXT REFERENCES sequence(id) ON DELETE SET NULL,
    start_frame INTEGER NOT NULL,
    end_frame INTEGER NOT NULL,
    title TEXT NOT NULL,
    reason TEXT NOT NULL,
    score REAL NOT NULL,
    selected INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS export_preset (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    data_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS export_history (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    sequence_id TEXT NOT NULL REFERENCES sequence(id) ON DELETE CASCADE,
    output_path TEXT NOT NULL,
    format TEXT NOT NULL,
    preset_json TEXT NOT NULL,
    quality_json TEXT NOT NULL,
    content_revision INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS project_version (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    snapshot_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    content_revision INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS automation_request (
    request_id TEXT PRIMARY KEY,
    operation TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS task (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    sequence_id TEXT REFERENCES sequence(id) ON DELETE SET NULL,
    idempotency_key TEXT,
    command_json TEXT NOT NULL,
    status TEXT NOT NULL,
    progress_json TEXT NOT NULL,
    input_asset_ids_json TEXT NOT NULL,
    artifacts_json TEXT NOT NULL,
    outcome_json TEXT,
    execution_trace_json TEXT NOT NULL DEFAULT '[]',
    error TEXT,
    revision INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS task_event (
    cursor INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    task_id TEXT NOT NULL,
    task_revision INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at INTEGER NOT NULL
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
CREATE UNIQUE INDEX IF NOT EXISTS idx_track_primary_dialogue
ON track(sequence_id) WHERE primary_dialogue=1;
CREATE INDEX IF NOT EXISTS idx_clip_track_time ON clip(track_id, timeline_start);
CREATE INDEX IF NOT EXISTS idx_marker_sequence_time ON timeline_marker(sequence_id, frame);
CREATE INDEX IF NOT EXISTS idx_range_sequence_time ON timeline_range(sequence_id, start_frame);
CREATE INDEX IF NOT EXISTS idx_task_project_time ON task(project_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_task_project_idempotency
ON task(project_id, idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_task_event_project_cursor
ON task_event(project_id, cursor);
CREATE INDEX IF NOT EXISTS idx_workflow_project_time ON workflow_run(project_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_export_history_sequence_time
ON export_history(sequence_id, created_at);
CREATE INDEX IF NOT EXISTS idx_project_version_project_time
ON project_version(project_id, created_at);
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


def _download_selectors(value: object) -> list[int | None]:
    spec = str(value or "").strip()
    if not spec:
        return [None]
    selectors: list[int] = []
    for raw_token in spec.split(","):
        token = raw_token.strip()
        if not token:
            continue
        if "-" in token:
            start_text, end_text = token.split("-", maxsplit=1)
            start = int(start_text.strip())
            end = int(end_text.strip())
            if start < 1 or end < start or end - start > 10_000:
                raise ValueError(f"Invalid persisted download selector: {token}")
            selectors.extend(range(start, end + 1))
        else:
            selector = int(token)
            if selector < 1:
                raise ValueError(f"Invalid persisted download selector: {token}")
            selectors.append(selector)
    return list(dict.fromkeys(selectors)) or [None]


def _download_requests_from_parameters(
    parameters: dict[str, Any],
    task_name: str,
) -> list[DownloadRequest]:
    if "request" in parameters:
        return [DownloadRequest.model_validate(parameters["request"])]
    url = str(parameters.get("url") or "").strip()
    if not url:
        raise ValueError("Persisted download task has no URL")
    filename = str(parameters.get("filename") or "").strip()
    collection_title = str(parameters.get("playlist_title") or "").strip()
    raw_codec = str(parameters.get("codec") or "best")
    codec: Literal["best", "avc"] = "avc" if raw_codec == "avc" else "best"
    requests: list[DownloadRequest] = []
    for selector in _download_selectors(parameters.get("playlist_items")):
        index = selector or 1
        requests.append(
            DownloadRequest(
                entry=DownloadEntry(
                    index=index,
                    title=(filename or task_name if selector is None else f"Item {index}"),
                    page_url=url,
                    download_url=url,
                    selector=selector,
                ),
                collection_title=collection_title,
                resolution=str(parameters.get("resolution") or "best"),
                codec=codec,
                download_subtitles=bool(parameters.get("download_subtitles", False)),
                subtitle_languages=[
                    str(value) for value in (parameters.get("subtitle_languages") or ["en", "zh"])
                ],
                filename_prefix=filename,
                output_directory=default_media_root(),
            )
        )
    return requests


class ProjectSchemaMigrator:
    def __init__(self, workspace: Any):
        self.workspace = workspace

    def validate(self) -> None:
        if self.workspace.read_only:
            self._validate()
            return
        with self.workspace.transaction():
            self._validate()

    def _validate(self) -> None:
        try:
            row = self.workspace._fetchone("SELECT version FROM schema_info WHERE component='project'")
        except sqlite3.Error as error:
            raise RuntimeError(f"Invalid MediaFlow Pro project: {self.workspace.database_path}") from error
        if row is not None and int(row["version"]) == 1 and not self.workspace.read_only:
            with self.workspace.transaction() as connection:
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
            row = self.workspace._fetchone("SELECT version FROM schema_info WHERE component='project'")
        if row is not None and int(row["version"]) == 2 and not self.workspace.read_only:
            with self.workspace.transaction() as connection:
                connection.execute(SUBTITLE_TRACK_DOCUMENT_TABLE_SQL)
                document_columns = {
                    item["name"]
                    for item in connection.execute("PRAGMA table_info(subtitle_document)").fetchall()
                }
                if "media_asset_id" not in document_columns:
                    connection.execute(
                        "ALTER TABLE subtitle_document ADD COLUMN media_asset_id TEXT "
                        "REFERENCES asset(id) ON DELETE SET NULL"
                    )
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
                    self.workspace.subtitles._sync_subtitle_placements(
                        connection,
                        sequence_id,
                    )
                connection.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_subtitle_placement_source "
                    "ON subtitle_placement(track_id, segment_id, COALESCE(clip_id, ''))"
                )
                connection.execute(
                    "UPDATE schema_info SET version=? WHERE component='project'",
                    (3,),
                )
            row = self.workspace._fetchone("SELECT version FROM schema_info WHERE component='project'")
        if row is not None and int(row["version"]) == 3 and not self.workspace.read_only:
            with self.workspace.transaction() as connection:
                for statement in TIMELINE_ANNOTATION_TABLES_SQL.split(";"):
                    if statement.strip():
                        connection.execute(statement)
                connection.execute(
                    "UPDATE schema_info SET version=? WHERE component='project'",
                    (4,),
                )
            row = self.workspace._fetchone("SELECT version FROM schema_info WHERE component='project'")
        if row is not None and int(row["version"]) == 4 and not self.workspace.read_only:
            with self.workspace.transaction() as connection:
                columns = {item["name"] for item in connection.execute("PRAGMA table_info(asset)").fetchall()}
                if "sdr_preview_proxy_path" not in columns:
                    connection.execute("ALTER TABLE asset ADD COLUMN sdr_preview_proxy_path TEXT")
                connection.execute(
                    "UPDATE schema_info SET version=? WHERE component='project'",
                    (5,),
                )
            row = self.workspace._fetchone("SELECT version FROM schema_info WHERE component='project'")
        if row is not None and int(row["version"]) == 5 and not self.workspace.read_only:
            with self.workspace.transaction() as connection:
                columns = {
                    item["name"]
                    for item in connection.execute("PRAGMA table_info(highlight_candidate)").fetchall()
                }
                if "document_id" not in columns:
                    connection.execute(
                        "ALTER TABLE highlight_candidate ADD COLUMN document_id TEXT "
                        "REFERENCES subtitle_document(id) ON DELETE SET NULL"
                    )
                if "sequence_id" not in columns:
                    connection.execute(
                        "ALTER TABLE highlight_candidate ADD COLUMN sequence_id TEXT "
                        "REFERENCES sequence(id) ON DELETE SET NULL"
                    )
                if "selected" not in columns:
                    connection.execute(
                        "ALTER TABLE highlight_candidate ADD COLUMN selected INTEGER NOT NULL DEFAULT 1"
                    )
                connection.execute(
                    "UPDATE schema_info SET version=? WHERE component='project'",
                    (6,),
                )
            row = self.workspace._fetchone("SELECT version FROM schema_info WHERE component='project'")
        if row is not None and int(row["version"]) == 6 and not self.workspace.read_only:
            with self.workspace.transaction() as connection:
                columns = {
                    item["name"]
                    for item in connection.execute("PRAGMA table_info(subtitle_document)").fetchall()
                }
                if "media_asset_id" not in columns:
                    connection.execute(
                        "ALTER TABLE subtitle_document ADD COLUMN media_asset_id TEXT "
                        "REFERENCES asset(id) ON DELETE SET NULL"
                    )
                subtitle_documents = connection.execute(
                    """SELECT document.id, asset.path, asset.managed
                       FROM subtitle_document document
                       JOIN asset ON asset.id=document.asset_id
                       WHERE document.media_asset_id IS NULL AND asset.kind=?""",
                    (AssetKind.SUBTITLE.value,),
                ).fetchall()
                media_assets = connection.execute(
                    "SELECT id, path, managed, kind FROM asset WHERE kind IN (?, ?)",
                    (AssetKind.VIDEO.value, AssetKind.AUDIO.value),
                ).fetchall()

                def resolved_asset_path(item: sqlite3.Row) -> Path:
                    candidate = Path(item["path"])
                    if bool(item["managed"]) and not candidate.is_absolute():
                        candidate = self.workspace.project_dir / candidate
                    return candidate.resolve()

                resolved_media = [(item, resolved_asset_path(item)) for item in media_assets]
                for document in subtitle_documents:
                    subtitle_path = resolved_asset_path(document)
                    matches = [
                        (item, media_path)
                        for item, media_path in resolved_media
                        if media_path.parent == subtitle_path.parent
                        and related_media_stem(media_path) == related_media_stem(subtitle_path)
                    ]
                    if not matches:
                        continue
                    matches.sort(
                        key=lambda match: (
                            match[0]["kind"] != AssetKind.VIDEO.value,
                            str(match[1]).casefold(),
                        )
                    )
                    connection.execute(
                        "UPDATE subtitle_document SET media_asset_id=? WHERE id=?",
                        (matches[0][0]["id"], document["id"]),
                    )
                connection.execute(
                    """UPDATE subtitle_track_document AS link
                       SET follow_clips=1
                       WHERE EXISTS (
                           SELECT 1
                           FROM subtitle_document document
                           JOIN track subtitle_track ON subtitle_track.id=link.track_id
                           JOIN track clip_track ON clip_track.sequence_id=subtitle_track.sequence_id
                           JOIN clip ON clip.track_id=clip_track.id
                           WHERE document.id=link.document_id
                             AND clip.asset_id=COALESCE(
                                 document.media_asset_id,
                                 document.asset_id
                             )
                       )"""
                )
                sequence_ids = [
                    item["id"] for item in connection.execute("SELECT id FROM sequence").fetchall()
                ]
                for sequence_id in sequence_ids:
                    self.workspace.subtitles._sync_subtitle_placements(
                        connection,
                        sequence_id,
                    )
                connection.execute(
                    "UPDATE schema_info SET version=? WHERE component='project'",
                    (7,),
                )
            row = self.workspace._fetchone("SELECT version FROM schema_info WHERE component='project'")
        if row is not None and int(row["version"]) == 7 and not self.workspace.read_only:
            with self.workspace.transaction() as connection:
                columns = {item["name"] for item in connection.execute("PRAGMA table_info(task)").fetchall()}
                if "execution_trace_json" not in columns:
                    connection.execute(
                        "ALTER TABLE task ADD COLUMN execution_trace_json TEXT NOT NULL DEFAULT '[]'"
                    )
                connection.execute(
                    "UPDATE schema_info SET version=? WHERE component='project'",
                    (8,),
                )
            row = self.workspace._fetchone("SELECT version FROM schema_info WHERE component='project'")
        if row is not None and int(row["version"]) == 8 and not self.workspace.read_only:
            task_columns = {item["name"] for item in self.workspace._fetchall("PRAGMA table_info(task)")}
            if "command_json" in task_columns:
                with self.workspace.transaction() as connection:
                    connection.execute(
                        "UPDATE schema_info SET version=? WHERE component='project'",
                        (9,),
                    )
                row = self.workspace._fetchone("SELECT version FROM schema_info WHERE component='project'")
        if row is not None and int(row["version"]) == 8 and not self.workspace.read_only:
            with self.workspace.transaction() as connection:
                contract_keys = {
                    "url",
                    "resolution",
                    "playlist_title",
                    "playlist_items",
                    "download_subtitles",
                    "subtitle_languages",
                    "codec",
                    "filename",
                }
                split_task_ids: dict[str, list[str]] = {}
                task_rows = connection.execute(
                    "SELECT * FROM task WHERE kind=? ORDER BY created_at, id",
                    ("download",),
                ).fetchall()
                for task_row in task_rows:
                    parameters = json.loads(task_row["parameters_json"])
                    if "request" in parameters:
                        continue
                    requests = _download_requests_from_parameters(
                        parameters,
                        str(task_row["name"]),
                    )
                    extras = {key: value for key, value in parameters.items() if key not in contract_keys}
                    first_parameters = {
                        "request": requests[0].model_dump(mode="json"),
                        **extras,
                    }
                    connection.execute(
                        "UPDATE task SET parameters_json=? WHERE id=?",
                        (_json(first_parameters), task_row["id"]),
                    )
                    migrated_ids = [str(task_row["id"])]
                    if task_row["status"] != "completed":
                        for request in requests[1:]:
                            clone_id = new_id()
                            clone_parameters = {
                                "request": request.model_dump(mode="json"),
                                **extras,
                            }
                            connection.execute(
                                """INSERT INTO task(
                                    id, project_id, sequence_id, kind, status, name, progress,
                                    message_code, input_asset_ids_json, parameters_json,
                                    artifacts_json, execution_trace_json, error, revision,
                                    created_at, updated_at
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                                (
                                    clone_id,
                                    task_row["project_id"],
                                    task_row["sequence_id"],
                                    task_row["kind"],
                                    task_row["status"],
                                    (f"{task_row['name']} · {request.entry.index:03d} {request.entry.title}"),
                                    task_row["progress"],
                                    task_row["message_code"],
                                    task_row["input_asset_ids_json"],
                                    _json(clone_parameters),
                                    "[]",
                                    "[]",
                                    task_row["error"],
                                    0,
                                    task_row["created_at"] + len(migrated_ids),
                                    max(
                                        task_row["updated_at"],
                                        task_row["created_at"] + len(migrated_ids),
                                    ),
                                ),
                            )
                            migrated_ids.append(clone_id)
                    split_task_ids[str(task_row["id"])] = migrated_ids

                workflow_rows = connection.execute(
                    "SELECT id, payload_json FROM workflow_run WHERE stage=?",
                    (WorkflowStage.DOWNLOAD.value,),
                ).fetchall()
                for workflow_row in workflow_rows:
                    payload = json.loads(workflow_row["payload_json"])
                    if "requests" in payload:
                        continue
                    original_task_ids = [str(value) for value in payload.get("task_ids", [])]
                    task_ids = [
                        migrated_id
                        for task_id in original_task_ids
                        for migrated_id in split_task_ids.get(task_id, [task_id])
                    ]
                    request_values: list[dict[str, Any]] = []
                    for task_id in task_ids:
                        migrated_task = connection.execute(
                            "SELECT parameters_json FROM task WHERE id=?",
                            (task_id,),
                        ).fetchone()
                        if migrated_task is None:
                            continue
                        migrated_parameters = json.loads(migrated_task["parameters_json"])
                        if "request" in migrated_parameters:
                            request_values.append(migrated_parameters["request"])
                    if not request_values:
                        request_values = [
                            request.model_dump(mode="json")
                            for request in _download_requests_from_parameters(
                                payload,
                                "下载视频",
                            )
                        ]
                    migrated_payload = {
                        key: value
                        for key, value in payload.items()
                        if key not in contract_keys
                        and key
                        not in {
                            "request",
                            "task_ids",
                            "workflow_run_id",
                            "workflow_stage",
                        }
                    }
                    migrated_payload["requests"] = request_values
                    if task_ids:
                        migrated_payload["task_ids"] = task_ids
                    connection.execute(
                        "UPDATE workflow_run SET payload_json=? WHERE id=?",
                        (_json(migrated_payload), workflow_row["id"]),
                    )
                connection.execute(
                    "UPDATE schema_info SET version=? WHERE component='project'",
                    (9,),
                )
            row = self.workspace._fetchone("SELECT version FROM schema_info WHERE component='project'")
        if row is not None and int(row["version"]) == 9 and not self.workspace.read_only:
            with self.workspace.transaction() as connection:
                columns = {
                    item["name"] for item in connection.execute("PRAGMA table_info(sequence)").fetchall()
                }
                if "in_frame" not in columns:
                    connection.execute("ALTER TABLE sequence ADD COLUMN in_frame INTEGER")
                if "out_frame" not in columns:
                    connection.execute("ALTER TABLE sequence ADD COLUMN out_frame INTEGER")

                preset_rows = connection.execute(
                    "SELECT sequence_id, preset_json FROM sequence_export_setting"
                ).fetchall()
                for preset_row in preset_rows:
                    preset = json.loads(preset_row["preset_json"])
                    legacy_trim = preset.pop("trim", None)
                    if isinstance(legacy_trim, dict):
                        duration_row = connection.execute(
                            """SELECT COALESCE(MAX(clip.timeline_start + clip.duration), 1) AS duration
                               FROM clip
                               JOIN track ON track.id=clip.track_id
                               WHERE track.sequence_id=?""",
                            (preset_row["sequence_id"],),
                        ).fetchone()
                        duration = max(1, int(duration_row["duration"]))
                        start = max(0, min(duration, int(legacy_trim.get("start_frame") or 0)))
                        end = max(1, min(duration, int(legacy_trim.get("end_frame") or duration)))
                        if legacy_trim.get("auto_trim_silence"):
                            speech = connection.execute(
                                """SELECT MIN(placement.start_frame) AS first_speech,
                                          MAX(placement.end_frame) AS last_speech
                                   FROM subtitle_placement placement
                                   JOIN track ON track.id=placement.track_id
                                   WHERE track.sequence_id=? AND track.kind=? AND track.enabled=1""",
                                (preset_row["sequence_id"], TrackKind.SUBTITLE.value),
                            ).fetchone()
                            if speech["first_speech"] is not None:
                                start = max(start, int(speech["first_speech"]))
                                end = min(end, int(speech["last_speech"]))
                        if end > start and (
                            legacy_trim.get("start_frame") is not None
                            or legacy_trim.get("end_frame") is not None
                            or legacy_trim.get("auto_trim_silence")
                        ):
                            connection.execute(
                                "UPDATE sequence SET in_frame=?, out_frame=? WHERE id=?",
                                (start, end, preset_row["sequence_id"]),
                            )
                    connection.execute(
                        "UPDATE sequence_export_setting SET preset_json=? WHERE sequence_id=?",
                        (_json(preset), preset_row["sequence_id"]),
                    )

                for preset_row in connection.execute("SELECT id, data_json FROM export_preset").fetchall():
                    preset = json.loads(preset_row["data_json"])
                    preset.pop("trim", None)
                    connection.execute(
                        "UPDATE export_preset SET data_json=? WHERE id=?",
                        (_json(preset), preset_row["id"]),
                    )
                connection.execute(
                    "UPDATE schema_info SET version=? WHERE component='project'",
                    (10,),
                )
            row = self.workspace._fetchone("SELECT version FROM schema_info WHERE component='project'")
        if row is not None and int(row["version"]) == 10 and not self.workspace.read_only:
            with self.workspace.transaction() as connection:
                columns = {
                    item["name"] for item in connection.execute("PRAGMA table_info(sequence)").fetchall()
                }
                if "archived" not in columns:
                    connection.execute("ALTER TABLE sequence ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")
                connection.execute(
                    "UPDATE schema_info SET version=? WHERE component='project'",
                    (11,),
                )
            row = self.workspace._fetchone("SELECT version FROM schema_info WHERE component='project'")
        if row is not None and int(row["version"]) == 11 and not self.workspace.read_only:
            task_columns = {item["name"] for item in self.workspace._fetchall("PRAGMA table_info(task)")}
            if "command_json" in task_columns:
                with self.workspace.transaction() as connection:
                    connection.execute(
                        "UPDATE schema_info SET version=? WHERE component='project'",
                        (12,),
                    )
                row = self.workspace._fetchone("SELECT version FROM schema_info WHERE component='project'")
        if row is not None and int(row["version"]) == 11 and not self.workspace.read_only:
            with self.workspace.transaction() as connection:
                connection.execute(
                    """CREATE TABLE task_v12 (
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
                rows = connection.execute("SELECT * FROM task ORDER BY created_at, id").fetchall()
                connection.executemany(
                    """INSERT INTO task_v12(
                        id, project_id, sequence_id, command_json, status, progress,
                        message_code, input_asset_ids_json, artifacts_json,
                        execution_trace_json, error, revision, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [
                        (
                            task_row["id"],
                            task_row["project_id"],
                            task_row["sequence_id"],
                            _model_json(
                                migrate_stored_task_command(
                                    str(task_row["kind"]),
                                    json.loads(task_row["parameters_json"]),
                                    sequence_id=task_row["sequence_id"],
                                )
                            ),
                            task_row["status"],
                            task_row["progress"],
                            task_row["message_code"],
                            task_row["input_asset_ids_json"],
                            task_row["artifacts_json"],
                            task_row["execution_trace_json"],
                            task_row["error"],
                            task_row["revision"],
                            task_row["created_at"],
                            task_row["updated_at"],
                        )
                        for task_row in rows
                    ],
                )
                connection.execute("DROP TABLE task")
                connection.execute("ALTER TABLE task_v12 RENAME TO task")
                connection.execute("CREATE INDEX idx_task_project_time ON task(project_id, created_at)")
                connection.execute(
                    "UPDATE schema_info SET version=? WHERE component='project'",
                    (12,),
                )
            row = self.workspace._fetchone("SELECT version FROM schema_info WHERE component='project'")
        if row is not None and int(row["version"]) == 12 and not self.workspace.read_only:
            media_root = default_media_root()
            with self.workspace.transaction() as connection:
                for task_row in connection.execute("SELECT id, command_json FROM task").fetchall():
                    command = json.loads(task_row["command_json"])
                    if command.get("command_type") != "download_media":
                        continue
                    request = command.get("request")
                    if isinstance(request, dict) and not str(request.get("output_directory") or "").strip():
                        request["output_directory"] = media_root
                        connection.execute(
                            "UPDATE task SET command_json=? WHERE id=?",
                            (_json(command), task_row["id"]),
                        )
                for run_row in connection.execute("SELECT id, payload_json FROM workflow_run").fetchall():
                    payload = json.loads(run_row["payload_json"])
                    changed = False
                    for request in payload.get("requests") or []:
                        if (
                            isinstance(request, dict)
                            and not str(request.get("output_directory") or "").strip()
                        ):
                            request["output_directory"] = media_root
                            changed = True
                    if changed:
                        connection.execute(
                            "UPDATE workflow_run SET payload_json=? WHERE id=?",
                            (_json(payload), run_row["id"]),
                        )
                connection.execute(
                    "UPDATE schema_info SET version=? WHERE component='project'",
                    (13,),
                )
            row = self.workspace._fetchone("SELECT version FROM schema_info WHERE component='project'")
        if row is not None and int(row["version"]) == 13 and not self.workspace.read_only:
            with self.workspace.transaction() as connection:
                columns = {
                    item["name"] for item in connection.execute("PRAGMA table_info(sequence)").fetchall()
                }
                if "profile_confirmed" not in columns:
                    connection.execute(
                        "ALTER TABLE sequence ADD COLUMN profile_confirmed INTEGER NOT NULL DEFAULT 1"
                    )
                connection.execute(
                    "UPDATE schema_info SET version=? WHERE component='project'",
                    (14,),
                )
            row = self.workspace._fetchone("SELECT version FROM schema_info WHERE component='project'")
        if row is not None and int(row["version"]) == 14 and not self.workspace.read_only:
            with self.workspace.transaction() as connection:
                columns = {
                    item["name"]
                    for item in connection.execute("PRAGMA table_info(subtitle_placement)").fetchall()
                }
                if "timing_overridden" not in columns:
                    connection.execute(
                        "ALTER TABLE subtitle_placement ADD COLUMN timing_overridden "
                        "INTEGER NOT NULL DEFAULT 0"
                    )
                connection.execute(
                    "UPDATE schema_info SET version=? WHERE component='project'",
                    (15,),
                )
            row = self.workspace._fetchone("SELECT version FROM schema_info WHERE component='project'")
        if row is not None and int(row["version"]) == 15 and not self.workspace.read_only:
            with self.workspace.transaction() as connection:
                document_columns = {
                    item["name"]
                    for item in connection.execute("PRAGMA table_info(subtitle_document)").fetchall()
                }
                if "sequence_id" not in document_columns:
                    connection.execute(
                        "ALTER TABLE subtitle_document ADD COLUMN sequence_id TEXT "
                        "REFERENCES sequence(id) ON DELETE CASCADE"
                    )
                project_row = connection.execute("SELECT main_sequence_id FROM project LIMIT 1").fetchone()
                main_sequence_id = str(project_row["main_sequence_id"]) if project_row else ""
                for task_row in connection.execute(
                    "SELECT id, sequence_id, command_json FROM task"
                ).fetchall():
                    command = json.loads(task_row["command_json"])
                    if command.get("command_type") not in {
                        "transcribe_asset",
                        "transcribe_region",
                    }:
                        continue
                    sequence_id = str(task_row["sequence_id"] or main_sequence_id)
                    migrated: dict[str, Any] = {
                        "command_type": "transcribe_sequence",
                        "sequence_id": sequence_id,
                    }
                    if command.get("workflow"):
                        migrated["workflow"] = command["workflow"]
                    connection.execute(
                        "UPDATE task SET command_json=? WHERE id=?",
                        (_json(migrated), task_row["id"]),
                    )
                for run_row in connection.execute("SELECT id, payload_json FROM workflow_run").fetchall():
                    payload = json.loads(run_row["payload_json"])
                    if "document_ids_before_transcribe" not in payload:
                        continue
                    payload.pop("document_ids_before_transcribe", None)
                    connection.execute(
                        "UPDATE workflow_run SET payload_json=? WHERE id=?",
                        (_json(payload), run_row["id"]),
                    )
                connection.execute(
                    "UPDATE schema_info SET version=? WHERE component='project'",
                    (16,),
                )
            row = self.workspace._fetchone("SELECT version FROM schema_info WHERE component='project'")
        if row is not None and int(row["version"]) == 16 and not self.workspace.read_only:
            with self.workspace.transaction() as connection:
                project_columns = {
                    item["name"] for item in connection.execute("PRAGMA table_info(project)").fetchall()
                }
                if "content_revision" not in project_columns:
                    connection.execute(
                        "ALTER TABLE project ADD COLUMN content_revision INTEGER NOT NULL DEFAULT 0"
                    )
                connection.execute(
                    """CREATE TABLE IF NOT EXISTS web_asset (
                           asset_id TEXT PRIMARY KEY REFERENCES asset(id) ON DELETE CASCADE,
                           manifest_json TEXT NOT NULL,
                           source_hash TEXT NOT NULL
                       )"""
                )
                connection.execute(
                    """CREATE TABLE IF NOT EXISTS web_clip_state (
                           clip_id TEXT PRIMARY KEY REFERENCES clip(id) ON DELETE CASCADE,
                           state_json TEXT NOT NULL,
                           revision INTEGER NOT NULL
                       )"""
                )
                connection.execute(
                    "UPDATE schema_info SET version=? WHERE component='project'",
                    (17,),
                )
            row = self.workspace._fetchone("SELECT version FROM schema_info WHERE component='project'")
        if row is not None and int(row["version"]) == 17 and not self.workspace.read_only:
            with self.workspace.transaction() as connection:
                manifests: dict[str, dict[str, Any]] = {}
                source_hashes: dict[str, str] = {}
                for asset_row in connection.execute(
                    "SELECT asset_id, manifest_json, source_hash FROM web_asset"
                ).fetchall():
                    manifest = json.loads(asset_row["manifest_json"])
                    for layer in manifest.get("layers") or []:
                        layer["editable"] = [
                            field for field in layer.get("editable") or [] if field != "locked"
                        ]
                        constraints = layer.get("constraints")
                        if isinstance(constraints, dict):
                            constraints.pop("locked", None)
                    manifests[str(asset_row["asset_id"])] = manifest
                    source_hashes[str(asset_row["asset_id"])] = str(asset_row["source_hash"])
                    connection.execute(
                        "UPDATE web_asset SET manifest_json=? WHERE asset_id=?",
                        (_json(manifest), asset_row["asset_id"]),
                    )
                state_rows = connection.execute(
                    """SELECT state.clip_id, state.state_json, clip.asset_id
                       FROM web_clip_state AS state
                       JOIN clip ON clip.id=state.clip_id"""
                ).fetchall()
                for state_row in state_rows:
                    asset_id = str(state_row["asset_id"])
                    manifest = manifests.get(asset_id, {})
                    editable_by_layer = {
                        str(layer.get("id")): tuple(layer.get("editable") or [])
                        for layer in manifest.get("layers") or []
                    }
                    layers = json.loads(state_row["state_json"])
                    locks: dict[str, list[str]] = {}
                    for layer_id, values in layers.items():
                        if not isinstance(values, dict):
                            continue
                        locked = values.pop("locked", None)
                        if locked is True:
                            locks[str(layer_id)] = list(editable_by_layer.get(str(layer_id), ()))
                    migrated = {
                        "layers": layers,
                        "layout_overrides": {},
                        "animations": {},
                        "theme": {},
                        "data_snapshot": {"source_kind": "inline", "values": {}},
                        "locks": locks,
                        "source_hash": source_hashes.get(asset_id, ""),
                        "variant_name": "",
                    }
                    connection.execute(
                        "UPDATE web_clip_state SET state_json=? WHERE clip_id=?",
                        (_json(migrated), state_row["clip_id"]),
                    )
                connection.execute(
                    "UPDATE schema_info SET version=? WHERE component='project'",
                    (18,),
                )
            row = self.workspace._fetchone("SELECT version FROM schema_info WHERE component='project'")
        if row is not None and int(row["version"]) == 18 and not self.workspace.read_only:
            with self.workspace.transaction() as connection:
                connection.execute(
                    """CREATE TABLE IF NOT EXISTS compound_clip (
                           id TEXT PRIMARY KEY,
                           sequence_id TEXT NOT NULL REFERENCES sequence(id) ON DELETE CASCADE,
                           name TEXT NOT NULL,
                           clip_ids_json TEXT NOT NULL
                       )"""
                )
                connection.execute(
                    "UPDATE schema_info SET version=? WHERE component='project'",
                    (19,),
                )
            row = self.workspace._fetchone("SELECT version FROM schema_info WHERE component='project'")
        if row is not None and int(row["version"]) == 19 and not self.workspace.read_only:
            with self.workspace.transaction() as connection:
                track_columns = {item["name"] for item in connection.execute("PRAGMA table_info(track)")}
                if "linked_audio_track_id" not in track_columns:
                    connection.execute(
                        "ALTER TABLE track ADD COLUMN linked_audio_track_id TEXT "
                        "REFERENCES track(id) ON DELETE SET NULL"
                    )
                clip_columns = {item["name"] for item in connection.execute("PRAGMA table_info(clip)")}
                if "media_kind" not in clip_columns:
                    connection.execute(
                        "ALTER TABLE clip ADD COLUMN media_kind TEXT NOT NULL "
                        "DEFAULT 'video_only' CHECK(media_kind IN "
                        "('linked_av', 'video_only', 'audio_only'))"
                    )
                clip_rows = connection.execute(
                    """SELECT clip.id, asset.kind AS asset_kind,
                              asset.metadata_json, track.kind AS track_kind
                       FROM clip
                       JOIN asset ON asset.id=clip.asset_id
                       JOIN track ON track.id=clip.track_id"""
                ).fetchall()
                for clip_row in clip_rows:
                    asset_kind = str(clip_row["asset_kind"])
                    track_kind = str(clip_row["track_kind"])
                    metadata = json.loads(str(clip_row["metadata_json"]))
                    if track_kind == TrackKind.AUDIO.value or asset_kind == AssetKind.AUDIO.value:
                        media_kind = "audio_only"
                    elif asset_kind == AssetKind.VIDEO.value and bool(metadata.get("has_audio")):
                        media_kind = "linked_av"
                    else:
                        media_kind = "video_only"
                    connection.execute(
                        "UPDATE clip SET media_kind=? WHERE id=?",
                        (media_kind, clip_row["id"]),
                    )
                sequence_rows = connection.execute("SELECT id FROM sequence ORDER BY position, id").fetchall()
                for sequence_row in sequence_rows:
                    sequence_id = str(sequence_row["id"])
                    video_tracks = connection.execute(
                        """SELECT track.* FROM track
                           WHERE track.sequence_id=? AND track.kind=?
                             AND EXISTS(
                                 SELECT 1 FROM clip
                                 WHERE clip.track_id=track.id AND clip.media_kind='linked_av'
                             )
                           ORDER BY track.position, track.id""",
                        (sequence_id, TrackKind.VIDEO.value),
                    ).fetchall()
                    audio_tracks = list(
                        connection.execute(
                            """SELECT * FROM track
                               WHERE sequence_id=? AND kind=?
                               ORDER BY position, id""",
                            (sequence_id, TrackKind.AUDIO.value),
                        ).fetchall()
                    )
                    used_audio_ids: set[str] = set()
                    next_position = int(
                        connection.execute(
                            "SELECT COALESCE(MAX(position), -1) + 1 FROM track WHERE sequence_id=?",
                            (sequence_id,),
                        ).fetchone()[0]
                    )
                    audio_count = len(audio_tracks)
                    for video_track in video_tracks:
                        audio_track = next(
                            (
                                item
                                for item in audio_tracks
                                if str(item["id"]) not in used_audio_ids
                                and item["audio_bus_id"] == video_track["audio_bus_id"]
                            ),
                            None,
                        )
                        if audio_track is None:
                            audio_track = next(
                                (item for item in audio_tracks if str(item["id"]) not in used_audio_ids),
                                None,
                            )
                        if audio_track is None:
                            audio_count += 1
                            audio_track_id = new_id()
                            connection.execute(
                                """INSERT INTO track(
                                       id, sequence_id, name, kind, position, enabled,
                                       locked, muted, solo, audio_bus_id, linked_audio_track_id
                                   ) VALUES (?, ?, ?, ?, ?, 1, 0, 0, 0, ?, NULL)""",
                                (
                                    audio_track_id,
                                    sequence_id,
                                    f"音频 {audio_count}",
                                    TrackKind.AUDIO.value,
                                    next_position,
                                    video_track["audio_bus_id"],
                                ),
                            )
                            next_position += 1
                        else:
                            audio_track_id = str(audio_track["id"])
                        used_audio_ids.add(audio_track_id)
                        connection.execute(
                            "UPDATE track SET linked_audio_track_id=? WHERE id=?",
                            (audio_track_id, video_track["id"]),
                        )
                connection.execute(
                    "UPDATE schema_info SET version=? WHERE component='project'",
                    (20,),
                )
            row = self.workspace._fetchone("SELECT version FROM schema_info WHERE component='project'")
        if row is not None and int(row["version"]) == 20 and not self.workspace.read_only:
            with self.workspace.transaction() as connection:
                connection.execute(
                    """CREATE TABLE IF NOT EXISTS subtitle_word (
                           id TEXT PRIMARY KEY,
                           segment_id TEXT NOT NULL REFERENCES subtitle_segment(id) ON DELETE CASCADE,
                           position INTEGER NOT NULL,
                           start_frame INTEGER NOT NULL,
                           end_frame INTEGER NOT NULL,
                           text TEXT NOT NULL,
                           confidence REAL,
                           timing_source TEXT NOT NULL
                               CHECK(timing_source IN ('recognized', 'estimated')),
                           excluded INTEGER NOT NULL DEFAULT 0,
                           UNIQUE(segment_id, position)
                       )"""
                )
                connection.execute(
                    "UPDATE schema_info SET version=? WHERE component='project'",
                    (21,),
                )
            row = self.workspace._fetchone("SELECT version FROM schema_info WHERE component='project'")
        if row is not None and int(row["version"]) == 21 and not self.workspace.read_only:
            with self.workspace.transaction() as connection:
                connection.execute(
                    """CREATE TABLE IF NOT EXISTS export_history (
                           id TEXT PRIMARY KEY,
                           task_id TEXT NOT NULL,
                           sequence_id TEXT NOT NULL REFERENCES sequence(id) ON DELETE CASCADE,
                           output_path TEXT NOT NULL,
                           format TEXT NOT NULL,
                           preset_json TEXT NOT NULL,
                           quality_json TEXT NOT NULL,
                           content_revision INTEGER NOT NULL,
                           created_at INTEGER NOT NULL
                       )"""
                )
                connection.execute(
                    """CREATE TABLE IF NOT EXISTS project_version (
                           id TEXT PRIMARY KEY,
                           project_id TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
                           name TEXT NOT NULL,
                           snapshot_path TEXT NOT NULL,
                           sha256 TEXT NOT NULL,
                           content_revision INTEGER NOT NULL,
                           created_at INTEGER NOT NULL
                       )"""
                )
                connection.execute(
                    """CREATE INDEX IF NOT EXISTS idx_export_history_sequence_time
                       ON export_history(sequence_id, created_at)"""
                )
                connection.execute(
                    """CREATE INDEX IF NOT EXISTS idx_project_version_project_time
                       ON project_version(project_id, created_at)"""
                )
                connection.execute(
                    "UPDATE schema_info SET version=? WHERE component='project'",
                    (22,),
                )
            row = self.workspace._fetchone("SELECT version FROM schema_info WHERE component='project'")
        if row is not None and int(row["version"]) == 22 and not self.workspace.read_only:
            with self.workspace.transaction() as connection:
                clip_columns = {
                    str(item["name"]) for item in connection.execute("PRAGMA table_info(clip)").fetchall()
                }
                if "transform_keyframes_json" not in clip_columns:
                    connection.execute(
                        "ALTER TABLE clip ADD COLUMN transform_keyframes_json TEXT NOT NULL DEFAULT '[]'"
                    )
                connection.execute(
                    "UPDATE schema_info SET version=? WHERE component='project'",
                    (23,),
                )
            row = self.workspace._fetchone("SELECT version FROM schema_info WHERE component='project'")
        if row is not None and int(row["version"]) == 23 and not self.workspace.read_only:
            with self.workspace.transaction() as connection:
                track_columns = {
                    str(item["name"]) for item in connection.execute("PRAGMA table_info(track)").fetchall()
                }
                if "primary_dialogue" not in track_columns:
                    connection.execute(
                        "ALTER TABLE track ADD COLUMN primary_dialogue INTEGER NOT NULL DEFAULT 0"
                    )
                sequence_ids = [
                    str(item["id"])
                    for item in connection.execute("SELECT id FROM sequence ORDER BY position, id").fetchall()
                ]
                for sequence_id in sequence_ids:
                    candidate = connection.execute(
                        """SELECT track.id
                           FROM track
                           LEFT JOIN audio_bus ON audio_bus.id=track.audio_bus_id
                           WHERE track.sequence_id=? AND track.kind=?
                           ORDER BY
                               CASE WHEN audio_bus.name='对白' THEN 0 ELSE 1 END,
                               CASE WHEN EXISTS(
                                   SELECT 1
                                   FROM clip
                                   WHERE clip.track_id=track.id
                               ) THEN 0 ELSE 1 END,
                               track.position,
                               track.id
                           LIMIT 1""",
                        (sequence_id, TrackKind.AUDIO.value),
                    ).fetchone()
                    if candidate is not None:
                        connection.execute(
                            "UPDATE track SET primary_dialogue=1 WHERE id=?",
                            (candidate["id"],),
                        )
                connection.execute(
                    """CREATE UNIQUE INDEX IF NOT EXISTS idx_track_primary_dialogue
                       ON track(sequence_id) WHERE primary_dialogue=1"""
                )
                document_columns = {
                    str(item["name"])
                    for item in connection.execute("PRAGMA table_info(subtitle_document)").fetchall()
                }
                if "purpose" not in document_columns:
                    connection.execute(
                        "ALTER TABLE subtitle_document ADD COLUMN purpose "
                        "TEXT NOT NULL DEFAULT 'subtitle' "
                        "CHECK(purpose IN ('subtitle', 'sequence_transcript'))"
                    )
                connection.execute(
                    """UPDATE subtitle_document
                       SET purpose='sequence_transcript'
                       WHERE sequence_id IS NOT NULL
                         AND is_source=1
                         AND source_document_id IS NULL"""
                )
                connection.execute(
                    """CREATE TABLE IF NOT EXISTS asset_transcript (
                           asset_id TEXT NOT NULL
                               REFERENCES asset(id) ON DELETE CASCADE,
                           signature TEXT NOT NULL,
                           language TEXT NOT NULL,
                           duration_seconds REAL NOT NULL,
                           result_json TEXT NOT NULL,
                           updated_at INTEGER NOT NULL,
                           PRIMARY KEY(asset_id, signature)
                       )"""
                )
                connection.execute(
                    "UPDATE schema_info SET version=? WHERE component='project'",
                    (24,),
                )
            row = self.workspace._fetchone("SELECT version FROM schema_info WHERE component='project'")
        if row is not None and int(row["version"]) == 24 and not self.workspace.read_only:
            with self.workspace.transaction() as connection:
                task_columns = {
                    str(item["name"]) for item in connection.execute("PRAGMA table_info(task)").fetchall()
                }
                if "progress_json" not in task_columns:
                    task_rows = connection.execute(
                        """SELECT id, project_id, sequence_id, command_json, status,
                                  message_code, input_asset_ids_json, artifacts_json,
                                  execution_trace_json, error, revision, created_at, updated_at
                           FROM task"""
                    ).fetchall()
                    connection.execute("DROP INDEX IF EXISTS idx_task_project_time")
                    connection.execute("ALTER TABLE task RENAME TO task_progress_v24")
                    connection.execute(
                        """CREATE TABLE task (
                               id TEXT PRIMARY KEY,
                               project_id TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
                               sequence_id TEXT REFERENCES sequence(id) ON DELETE SET NULL,
                               command_json TEXT NOT NULL,
                               status TEXT NOT NULL,
                               progress_json TEXT NOT NULL,
                               input_asset_ids_json TEXT NOT NULL,
                               artifacts_json TEXT NOT NULL,
                               execution_trace_json TEXT NOT NULL DEFAULT '[]',
                               error TEXT,
                               revision INTEGER NOT NULL DEFAULT 0,
                               created_at INTEGER NOT NULL,
                               updated_at INTEGER NOT NULL
                           )"""
                    )
                    for task_row in task_rows:
                        status = str(task_row["status"])
                        message_code = str(task_row["message_code"])
                        progress = (
                            {
                                "mode": "determinate",
                                "message_code": "completed",
                                "completed": 1.0,
                                "total": 1.0,
                                "unit": "task",
                            }
                            if status == "completed"
                            else {
                                "mode": "indeterminate",
                                "message_code": message_code,
                                "completed": None,
                                "total": None,
                                "unit": None,
                            }
                        )
                        connection.execute(
                            """INSERT INTO task(
                                   id, project_id, sequence_id, command_json, status,
                                   progress_json, input_asset_ids_json, artifacts_json,
                                   execution_trace_json, error, revision, created_at, updated_at
                               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                task_row["id"],
                                task_row["project_id"],
                                task_row["sequence_id"],
                                task_row["command_json"],
                                status,
                                _json(progress),
                                task_row["input_asset_ids_json"],
                                task_row["artifacts_json"],
                                task_row["execution_trace_json"],
                                task_row["error"],
                                task_row["revision"],
                                task_row["created_at"],
                                task_row["updated_at"],
                            ),
                        )
                    connection.execute("DROP TABLE task_progress_v24")
                    connection.execute("CREATE INDEX idx_task_project_time ON task(project_id, created_at)")
                connection.execute(
                    "UPDATE schema_info SET version=? WHERE component='project'",
                    (25,),
                )
            row = self.workspace._fetchone("SELECT version FROM schema_info WHERE component='project'")
        if row is not None and int(row["version"]) == 25 and not self.workspace.read_only:
            with self.workspace.transaction() as connection:
                task_rows = connection.execute(
                    """SELECT id, sequence_id, command_json, status
                       FROM task"""
                ).fetchall()
                for task_row in task_rows:
                    command = json.loads(str(task_row["command_json"]))
                    if command.get("command_type") != "transcribe_sequence":
                        continue
                    if "plan" not in command:
                        sequence_id = str(command.pop("sequence_id", None) or task_row["sequence_id"] or "")
                        sequence_row = connection.execute(
                            """SELECT fps_numerator, fps_denominator
                               FROM sequence WHERE id=?""",
                            (sequence_id,),
                        ).fetchone()
                        command["plan"] = {
                            "sequence_id": sequence_id,
                            "timeline_signature": "legacy",
                            "dialogue_track_id": "",
                            "timeline_start_frame": 0,
                            "timeline_end_frame": 0,
                            "fps_numerator": (
                                int(sequence_row["fps_numerator"]) if sequence_row is not None else 30
                            ),
                            "fps_denominator": (
                                int(sequence_row["fps_denominator"]) if sequence_row is not None else 1
                            ),
                            "sources": [],
                            "asr": AsrSettings().model_dump(mode="json"),
                        }
                    status = str(task_row["status"])
                    update_values: list[object] = [_json(command)]
                    update_clause = "command_json=?"
                    if status in {"pending", "running", "paused"} and not command["plan"].get("sources"):
                        update_clause += ", status='cancelled', progress_json=?, error=?"
                        update_values.extend(
                            [
                                _json(
                                    {
                                        "mode": "indeterminate",
                                        "message_code": "cancelled",
                                        "completed": None,
                                        "total": None,
                                        "unit": None,
                                    }
                                ),
                                "旧版转录任务缺少可复现计划，请重新发起转录",
                            ]
                        )
                    update_values.append(task_row["id"])
                    connection.execute(
                        f"UPDATE task SET {update_clause} WHERE id=?",
                        tuple(update_values),
                    )
                connection.execute(
                    "UPDATE schema_info SET version=? WHERE component='project'",
                    (26,),
                )
            row = self.workspace._fetchone("SELECT version FROM schema_info WHERE component='project'")
        if row is not None and int(row["version"]) == 26 and not self.workspace.read_only:
            with self.workspace.transaction() as connection:
                sequence_columns = {
                    item["name"] for item in connection.execute("PRAGMA table_info(sequence)").fetchall()
                }
                if "timeline_revision" not in sequence_columns:
                    connection.execute(
                        "ALTER TABLE sequence ADD COLUMN timeline_revision INTEGER NOT NULL DEFAULT 0"
                    )
                connection.execute(
                    "UPDATE schema_info SET version=? WHERE component='project'",
                    (27,),
                )
            row = self.workspace._fetchone("SELECT version FROM schema_info WHERE component='project'")
        if row is not None and int(row["version"]) == 27 and not self.workspace.read_only:
            with self.workspace.transaction() as connection:
                connection.execute(
                    """CREATE TABLE IF NOT EXISTS task_event (
                        cursor INTEGER PRIMARY KEY AUTOINCREMENT,
                        project_id TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
                        task_id TEXT NOT NULL,
                        task_revision INTEGER NOT NULL,
                        event_type TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        created_at INTEGER NOT NULL
                    )"""
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_task_event_project_cursor "
                    "ON task_event(project_id, cursor)"
                )
                connection.execute(
                    "UPDATE schema_info SET version=? WHERE component='project'",
                    (28,),
                )
            row = self.workspace._fetchone("SELECT version FROM schema_info WHERE component='project'")
        if row is not None and int(row["version"]) == 28 and not self.workspace.read_only:
            with self.workspace.transaction() as connection:
                project_dir = Path(self.workspace.project_dir).resolve()
                for task_row in connection.execute("SELECT id, artifacts_json FROM task").fetchall():
                    values = json.loads(str(task_row["artifacts_json"]))
                    references: list[dict[str, str]] = []
                    for value in values:
                        path = Path(str(value))
                        if path.is_absolute():
                            try:
                                relative = path.resolve().relative_to(project_dir)
                            except ValueError:
                                references.append({"scope": "external", "path": str(path.resolve())})
                            else:
                                references.append({"scope": "project", "path": relative.as_posix()})
                        else:
                            references.append({"scope": "project", "path": path.as_posix()})
                    connection.execute(
                        "UPDATE task SET artifacts_json=? WHERE id=?",
                        (_json(references), task_row["id"]),
                    )
                connection.execute(
                    "UPDATE schema_info SET version=? WHERE component='project'",
                    (29,),
                )
            row = self.workspace._fetchone("SELECT version FROM schema_info WHERE component='project'")
        if row is not None and int(row["version"]) == 29 and not self.workspace.read_only:
            with self.workspace.transaction() as connection:
                project_columns = {
                    item["name"] for item in connection.execute("PRAGMA table_info(project)").fetchall()
                }
                if "root_path" in project_columns:
                    connection.execute("ALTER TABLE project DROP COLUMN root_path")
                connection.execute(
                    "UPDATE schema_info SET version=? WHERE component='project'",
                    (30,),
                )
            row = self.workspace._fetchone("SELECT version FROM schema_info WHERE component='project'")
        if row is not None and int(row["version"]) == 30 and not self.workspace.read_only:
            with self.workspace.transaction() as connection:
                task_columns = {
                    item["name"] for item in connection.execute("PRAGMA table_info(task)").fetchall()
                }
                if "idempotency_key" not in task_columns:
                    connection.execute("ALTER TABLE task ADD COLUMN idempotency_key TEXT")
                connection.execute(
                    """CREATE UNIQUE INDEX IF NOT EXISTS idx_task_project_idempotency
                       ON task(project_id, idempotency_key)
                       WHERE idempotency_key IS NOT NULL"""
                )
                connection.execute(
                    "UPDATE schema_info SET version=? WHERE component='project'",
                    (31,),
                )
            row = self.workspace._fetchone("SELECT version FROM schema_info WHERE component='project'")
        if row is not None and int(row["version"]) == 31 and not self.workspace.read_only:
            with self.workspace.transaction() as connection:
                connection.execute(
                    """CREATE TABLE IF NOT EXISTS automation_request (
                           request_id TEXT PRIMARY KEY,
                           operation TEXT NOT NULL,
                           input_hash TEXT NOT NULL,
                           result_json TEXT NOT NULL,
                           created_at INTEGER NOT NULL
                       )"""
                )
                connection.execute(
                    "UPDATE schema_info SET version=? WHERE component='project'",
                    (32,),
                )
            row = self.workspace._fetchone("SELECT version FROM schema_info WHERE component='project'")
        if row is not None and int(row["version"]) == 32 and not self.workspace.read_only:
            with self.workspace.transaction() as connection:
                task_columns = {
                    item["name"] for item in connection.execute("PRAGMA table_info(task)").fetchall()
                }
                if "outcome_json" not in task_columns:
                    connection.execute("ALTER TABLE task ADD COLUMN outcome_json TEXT")
                connection.execute(
                    "UPDATE schema_info SET version=? WHERE component='project'",
                    (33,),
                )
            row = self.workspace._fetchone("SELECT version FROM schema_info WHERE component='project'")
        if row is None or int(row["version"]) != PROJECT_SCHEMA_VERSION:
            raise RuntimeError(f"Unsupported project schema: {None if row is None else row['version']}")
