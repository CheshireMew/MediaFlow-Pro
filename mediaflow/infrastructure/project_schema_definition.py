from __future__ import annotations

PROJECT_FILE_NAME = "project.mfp"
PROJECT_SCHEMA_VERSION = 49
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
CREATE TABLE IF NOT EXISTS asset_bin (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    parent_id TEXT REFERENCES asset_bin(id) ON DELETE CASCADE,
    position INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS asset (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    origin TEXT NOT NULL,
    path TEXT NOT NULL,
    managed INTEGER NOT NULL,
    bin_id TEXT REFERENCES asset_bin(id) ON DELETE SET NULL,
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
    primary_dialogue INTEGER NOT NULL DEFAULT 0,
    subtitle_style_json TEXT
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
    freeze_source_frame INTEGER,
    transform_json TEXT NOT NULL,
    transform_keyframes_json TEXT NOT NULL DEFAULT '[]',
    audio_json TEXT NOT NULL,
    visual_effects_json TEXT NOT NULL DEFAULT '[]'
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
CREATE TABLE IF NOT EXISTS editable_media_upgrade (
    asset_id TEXT PRIMARY KEY REFERENCES asset(id) ON DELETE CASCADE,
    source_version INTEGER NOT NULL,
    target_version INTEGER NOT NULL,
    old_source_hash TEXT NOT NULL,
    new_source_hash TEXT NOT NULL,
    old_package_path TEXT NOT NULL,
    new_package_path TEXT NOT NULL,
    archive_package_path TEXT NOT NULL,
    migrated_at INTEGER NOT NULL
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
CREATE TABLE IF NOT EXISTS dubbing_session (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    sequence_id TEXT NOT NULL REFERENCES sequence(id) ON DELETE CASCADE,
    source_document_id TEXT NOT NULL REFERENCES subtitle_document(id) ON DELETE CASCADE,
    target_document_id TEXT REFERENCES subtitle_document(id) ON DELETE SET NULL,
    source_language TEXT NOT NULL,
    target_language TEXT NOT NULL,
    dialogue_track_id TEXT NOT NULL REFERENCES track(id) ON DELETE RESTRICT,
    source_timeline_revision INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN (
        'preparing', 'review', 'synthesizing', 'synthesized', 'committed'
    )),
    settings_json TEXT NOT NULL,
    diarization_engine TEXT NOT NULL,
    diarization_version TEXT NOT NULL,
    diarization_model TEXT NOT NULL,
    synthesis_engine TEXT NOT NULL,
    synthesis_version TEXT NOT NULL,
    master_path TEXT,
    master_sha256 TEXT,
    master_duration_seconds REAL,
    master_asset_id TEXT REFERENCES asset(id) ON DELETE SET NULL,
    committed_track_id TEXT REFERENCES track(id) ON DELETE SET NULL,
    committed_clip_id TEXT REFERENCES clip(id) ON DELETE SET NULL,
    revision INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS dubbing_speaker (
    session_id TEXT NOT NULL REFERENCES dubbing_session(id) ON DELETE CASCADE,
    id TEXT NOT NULL,
    position INTEGER NOT NULL,
    label TEXT NOT NULL,
    display_name TEXT NOT NULL,
    review_status TEXT NOT NULL CHECK(review_status IN (
        'automatic', 'accepted', 'needs_review'
    )),
    PRIMARY KEY(session_id, id),
    UNIQUE(session_id, position)
);
CREATE TABLE IF NOT EXISTS dubbing_reference (
    session_id TEXT NOT NULL,
    id TEXT NOT NULL,
    speaker_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    start_frame INTEGER NOT NULL,
    end_frame INTEGER NOT NULL,
    text TEXT NOT NULL,
    language TEXT NOT NULL,
    duration_seconds REAL NOT NULL,
    primary_reference INTEGER NOT NULL,
    PRIMARY KEY(session_id, id),
    UNIQUE(session_id, speaker_id, position),
    FOREIGN KEY(session_id, speaker_id)
        REFERENCES dubbing_speaker(session_id, id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS dubbing_speaker_turn (
    session_id TEXT NOT NULL REFERENCES dubbing_session(id) ON DELETE CASCADE,
    id TEXT NOT NULL,
    position INTEGER NOT NULL,
    speaker_id TEXT NOT NULL,
    start_frame INTEGER NOT NULL,
    end_frame INTEGER NOT NULL,
    confidence REAL,
    PRIMARY KEY(session_id, id),
    UNIQUE(session_id, position),
    FOREIGN KEY(session_id, speaker_id)
        REFERENCES dubbing_speaker(session_id, id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS dubbing_utterance (
    session_id TEXT NOT NULL REFERENCES dubbing_session(id) ON DELETE CASCADE,
    id TEXT NOT NULL,
    position INTEGER NOT NULL,
    speaker_id TEXT NOT NULL,
    source_segment_ids_json TEXT NOT NULL,
    target_segment_ids_json TEXT NOT NULL,
    start_frame INTEGER NOT NULL,
    end_frame INTEGER NOT NULL,
    source_text TEXT NOT NULL,
    target_text TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN (
        'pending', 'generated', 'needs_review', 'failed'
    )),
    review_status TEXT NOT NULL CHECK(review_status IN (
        'automatic', 'accepted', 'needs_review'
    )),
    output_path TEXT,
    output_sha256 TEXT,
    natural_duration_seconds REAL,
    fitted_duration_seconds REAL,
    speed_factor REAL NOT NULL,
    seed INTEGER NOT NULL,
    reference_sha256 TEXT,
    issues_json TEXT NOT NULL,
    PRIMARY KEY(session_id, id),
    UNIQUE(session_id, position),
    FOREIGN KEY(session_id, speaker_id)
        REFERENCES dubbing_speaker(session_id, id) ON DELETE RESTRICT
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
CREATE TABLE IF NOT EXISTS project_event (
    cursor INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    base_revision INTEGER NOT NULL,
    project_revision INTEGER NOT NULL,
    operation TEXT NOT NULL,
    actor_json TEXT NOT NULL,
    request_id TEXT NOT NULL,
    undo_group_id TEXT NOT NULL,
    write_set_json TEXT NOT NULL,
    changes_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    inverse_command_json TEXT,
    created_at INTEGER NOT NULL,
    UNIQUE(project_id, project_revision)
);
CREATE TABLE IF NOT EXISTS undo_group (
    id TEXT NOT NULL,
    project_id TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    source_revision INTEGER NOT NULL,
    state_revision INTEGER NOT NULL,
    label TEXT NOT NULL,
    actor_json TEXT NOT NULL,
    write_set_json TEXT NOT NULL,
    command_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('applied', 'undone', 'discarded')),
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY(project_id, id)
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
CREATE INDEX IF NOT EXISTS idx_asset_bin_project ON asset_bin(project_id, parent_id, position);
CREATE UNIQUE INDEX IF NOT EXISTS idx_asset_bin_unique_name
ON asset_bin(project_id, COALESCE(parent_id, ''), name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_sequence_project ON sequence(project_id, position);
CREATE INDEX IF NOT EXISTS idx_track_sequence ON track(sequence_id, position);
CREATE UNIQUE INDEX IF NOT EXISTS idx_track_primary_dialogue
ON track(sequence_id) WHERE primary_dialogue=1;
CREATE INDEX IF NOT EXISTS idx_clip_track_time ON clip(track_id, timeline_start);
CREATE INDEX IF NOT EXISTS idx_subtitle_segment_document_time
ON subtitle_segment(document_id, start_frame, id);
CREATE INDEX IF NOT EXISTS idx_marker_sequence_time ON timeline_marker(sequence_id, frame);
CREATE INDEX IF NOT EXISTS idx_range_sequence_time ON timeline_range(sequence_id, start_frame);
CREATE INDEX IF NOT EXISTS idx_task_project_time ON task(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_task_claimable_pending
ON task(created_at, id)
WHERE status='pending' AND execution_owner_id IS NULL;
CREATE INDEX IF NOT EXISTS idx_task_claimable_running
ON task(lease_expires_at, created_at, id)
WHERE status='running';
CREATE INDEX IF NOT EXISTS idx_project_event_project_cursor
ON project_event(project_id, cursor);
CREATE INDEX IF NOT EXISTS idx_undo_group_project_state_revision
ON undo_group(project_id, state, state_revision);
CREATE UNIQUE INDEX IF NOT EXISTS idx_task_project_idempotency
ON task(project_id, idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_task_event_project_cursor
ON task_event(project_id, cursor);
CREATE INDEX IF NOT EXISTS idx_workflow_project_time ON workflow_run(project_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_export_history_sequence_time
ON export_history(sequence_id, created_at);
CREATE INDEX IF NOT EXISTS idx_project_version_project_time
ON project_version(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_dubbing_session_project_time
ON dubbing_session(project_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_dubbing_session_sequence
ON dubbing_session(sequence_id, updated_at);
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
