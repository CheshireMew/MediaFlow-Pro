from __future__ import annotations

import json

from pydantic import ValidationError

from mediaflow.domain.enums import AssetKind, TrackKind
from mediaflow.domain.model_base import new_id
from mediaflow.domain.web_state import WebClipState
from mediaflow.infrastructure.editable_media_project_migration import (
    migrate_editable_media_manifest_to_v6,
)
from mediaflow.infrastructure.project_serialization import json_value as _json


def migrate_v17_to_v18(workspace) -> None:
    with workspace.transaction() as connection:
        for asset_row in connection.execute("SELECT asset_id, manifest_json FROM web_asset").fetchall():
            try:
                migrate_editable_media_manifest_to_v6(json.loads(str(asset_row["manifest_json"])))
            except (TypeError, ValueError, ValidationError) as error:
                raise RuntimeError(
                    "项目中的历史 editable-media 网页素材不符合最终 v4 合同，无法进入一次性 v6 项目升级流程。"
                ) from error
        state_rows = connection.execute(
            """SELECT state.clip_id, state.state_json, state.revision
               FROM web_clip_state AS state
               JOIN clip ON clip.id=state.clip_id"""
        ).fetchall()
        for state_row in state_rows:
            try:
                WebClipState.model_validate(
                    {
                        **json.loads(str(state_row["state_json"])),
                        "clip_id": str(state_row["clip_id"]),
                        "revision": int(state_row["revision"]),
                    }
                )
            except (TypeError, ValueError, ValidationError) as error:
                raise RuntimeError(
                    "项目中的历史 editable-media 网页片段状态无法进入一次性 v6 项目升级流程。"
                ) from error
        connection.execute(
            "UPDATE schema_info SET version=? WHERE component='project'",
            (18,),
        )


def migrate_v18_to_v19(workspace) -> None:
    with workspace.transaction() as connection:
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


def migrate_v19_to_v20(workspace) -> None:
    with workspace.transaction() as connection:
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


def migrate_v20_to_v21(workspace) -> None:
    with workspace.transaction() as connection:
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


def migrate_v21_to_v22(workspace) -> None:
    with workspace.transaction() as connection:
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


def migrate_v22_to_v23(workspace) -> None:
    with workspace.transaction() as connection:
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


def migrate_v23_to_v24(workspace) -> None:
    with workspace.transaction() as connection:
        track_columns = {
            str(item["name"]) for item in connection.execute("PRAGMA table_info(track)").fetchall()
        }
        if "primary_dialogue" not in track_columns:
            connection.execute("ALTER TABLE track ADD COLUMN primary_dialogue INTEGER NOT NULL DEFAULT 0")
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


def migrate_v24_to_v25(workspace) -> None:
    with workspace.transaction() as connection:
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
