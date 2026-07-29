from __future__ import annotations

PROJECT_FILE_NAME = "project.mfp"
PROJECT_SCHEMA_VERSION = 35
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
    state TEXT NOT NULL DEFAULT 'completed',
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
    execution_owner_id TEXT,
    heartbeat_at INTEGER,
    lease_expires_at INTEGER,
    stop_request TEXT,
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
CREATE TABLE IF NOT EXISTS task_consumption (
    task_id TEXT PRIMARY KEY REFERENCES task(id) ON DELETE CASCADE,
    project_id TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    task_revision INTEGER NOT NULL,
    result_json TEXT NOT NULL,
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
