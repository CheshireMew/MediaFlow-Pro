from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Literal

from mediaflow.domain.downloads import DownloadEntry, DownloadRequest
from mediaflow.domain.enums import AssetKind, TrackKind, WorkflowStage
from mediaflow.domain.media_association import related_media_stem
from mediaflow.domain.model_base import new_id
from mediaflow.domain.settings import default_media_root
from mediaflow.infrastructure.legacy_task_commands import legacy_task_command
from mediaflow.infrastructure.project_serialization import (
    json_value as _json,
)
from mediaflow.infrastructure.project_serialization import (
    model_json as _model_json,
)

PROJECT_FILE_NAME = "project.mfp"
PROJECT_SCHEMA_VERSION = 14
MANAGED_DIRECTORIES = ("generated", "proxies", "cache", "exports")


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
    profile_confirmed INTEGER NOT NULL DEFAULT 1,
    in_frame INTEGER,
    out_frame INTEGER,
    archived INTEGER NOT NULL DEFAULT 0,
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
    media_asset_id TEXT REFERENCES asset(id) ON DELETE SET NULL,
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
CREATE TABLE IF NOT EXISTS task (
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
            )
        )
    return requests


class ProjectSchemaMigrator:
    def __init__(self, workspace: Any):
        self.workspace = workspace

    def validate(self) -> None:
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
                    self.workspace._sync_subtitle_placements(connection, sequence_id)
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
                connection.executescript(TIMELINE_ANNOTATION_TABLES_SQL)
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
                    self.workspace._sync_subtitle_placements(connection, sequence_id)
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
                                legacy_task_command(
                                    str(task_row["kind"]),
                                    json.loads(task_row["parameters_json"]),
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
                for task_row in connection.execute(
                    "SELECT id, command_json FROM task"
                ).fetchall():
                    command = json.loads(task_row["command_json"])
                    if command.get("command_type") != "download_media":
                        continue
                    request = command.get("request")
                    if isinstance(request, dict) and not str(
                        request.get("output_directory") or ""
                    ).strip():
                        request["output_directory"] = media_root
                        connection.execute(
                            "UPDATE task SET command_json=? WHERE id=?",
                            (_json(command), task_row["id"]),
                        )
                for run_row in connection.execute(
                    "SELECT id, payload_json FROM workflow_run"
                ).fetchall():
                    payload = json.loads(run_row["payload_json"])
                    changed = False
                    for request in payload.get("requests") or []:
                        if isinstance(request, dict) and not str(
                            request.get("output_directory") or ""
                        ).strip():
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
                        "ALTER TABLE sequence ADD COLUMN profile_confirmed "
                        "INTEGER NOT NULL DEFAULT 1"
                    )
                connection.execute(
                    "UPDATE schema_info SET version=? WHERE component='project'",
                    (PROJECT_SCHEMA_VERSION,),
                )
            row = self.workspace._fetchone("SELECT version FROM schema_info WHERE component='project'")
        if row is None or int(row["version"]) != PROJECT_SCHEMA_VERSION:
            raise RuntimeError(f"Unsupported project schema: {None if row is None else row['version']}")
