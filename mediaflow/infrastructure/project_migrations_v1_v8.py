from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Literal

from mediaflow.domain.downloads import DownloadEntry, DownloadRequest
from mediaflow.domain.enums import AssetKind, WorkflowStage
from mediaflow.domain.media_association import related_media_stem
from mediaflow.domain.model_base import new_id
from mediaflow.infrastructure.project_schema_definition import (
    SUBTITLE_TRACK_DOCUMENT_TABLE_SQL,
    TIMELINE_ANNOTATION_TABLES_SQL,
    WORKFLOW_RUN_TABLE_SQL,
)
from mediaflow.infrastructure.project_serialization import json_value as _json
from mediaflow.infrastructure.storage_paths import default_media_root


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


def migrate_v1_to_v2(workspace) -> None:
    with workspace.transaction() as connection:
        connection.execute(WORKFLOW_RUN_TABLE_SQL)
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_workflow_project_time ON workflow_run(project_id, updated_at)"
        )
        connection.execute("UPDATE project SET workflow_auto_continue=-1")
        connection.execute(
            "UPDATE schema_info SET version=? WHERE component='project'",
            (2,),
        )


def migrate_v2_to_v3(workspace) -> None:
    with workspace.transaction() as connection:
        connection.execute(SUBTITLE_TRACK_DOCUMENT_TABLE_SQL)
        document_columns = {
            item["name"] for item in connection.execute("PRAGMA table_info(subtitle_document)").fetchall()
        }
        if "media_asset_id" not in document_columns:
            connection.execute(
                "ALTER TABLE subtitle_document ADD COLUMN media_asset_id TEXT "
                "REFERENCES asset(id) ON DELETE SET NULL"
            )
        columns = {
            item["name"] for item in connection.execute("PRAGMA table_info(subtitle_placement)").fetchall()
        }
        if "clip_id" not in columns:
            connection.execute(
                "ALTER TABLE subtitle_placement ADD COLUMN clip_id TEXT REFERENCES clip(id) ON DELETE CASCADE"
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
        sequence_ids = [item["id"] for item in connection.execute("SELECT id FROM sequence").fetchall()]
        for sequence_id in sequence_ids:
            workspace.subtitles.sync_subtitle_placements(
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


def migrate_v3_to_v4(workspace) -> None:
    with workspace.transaction() as connection:
        for statement in TIMELINE_ANNOTATION_TABLES_SQL.split(";"):
            if statement.strip():
                connection.execute(statement)
        connection.execute(
            "UPDATE schema_info SET version=? WHERE component='project'",
            (4,),
        )


def migrate_v4_to_v5(workspace) -> None:
    with workspace.transaction() as connection:
        columns = {item["name"] for item in connection.execute("PRAGMA table_info(asset)").fetchall()}
        if "sdr_preview_proxy_path" not in columns:
            connection.execute("ALTER TABLE asset ADD COLUMN sdr_preview_proxy_path TEXT")
        connection.execute(
            "UPDATE schema_info SET version=? WHERE component='project'",
            (5,),
        )


def migrate_v5_to_v6(workspace) -> None:
    with workspace.transaction() as connection:
        columns = {
            item["name"] for item in connection.execute("PRAGMA table_info(highlight_candidate)").fetchall()
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


def migrate_v6_to_v7(workspace) -> None:
    with workspace.transaction() as connection:
        columns = {
            item["name"] for item in connection.execute("PRAGMA table_info(subtitle_document)").fetchall()
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
                candidate = workspace.project_dir / candidate
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
        sequence_ids = [item["id"] for item in connection.execute("SELECT id FROM sequence").fetchall()]
        for sequence_id in sequence_ids:
            workspace.subtitles.sync_subtitle_placements(
                connection,
                sequence_id,
            )
        connection.execute(
            "UPDATE schema_info SET version=? WHERE component='project'",
            (7,),
        )


def migrate_v7_to_v8(workspace) -> None:
    with workspace.transaction() as connection:
        columns = {item["name"] for item in connection.execute("PRAGMA table_info(task)").fetchall()}
        if "execution_trace_json" not in columns:
            connection.execute("ALTER TABLE task ADD COLUMN execution_trace_json TEXT NOT NULL DEFAULT '[]'")
        connection.execute(
            "UPDATE schema_info SET version=? WHERE component='project'",
            (8,),
        )


def migrate_v8_to_v9(workspace) -> None:
    task_columns = {item["name"] for item in workspace._fetchall("PRAGMA table_info(task)")}
    if "command_json" in task_columns:
        with workspace.transaction() as connection:
            connection.execute(
                "UPDATE schema_info SET version=? WHERE component='project'",
                (9,),
            )
        return
    with workspace.transaction() as connection:
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
