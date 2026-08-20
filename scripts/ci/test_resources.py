from __future__ import annotations

from collections.abc import Sequence

RUNTIME_TEST_SOURCES = frozenset(
    {
        "tests/v2/infrastructure/test_asr_cli.py",
        "tests/v2/infrastructure/test_dubbing_runtime.py",
        "tests/v2/infrastructure/test_editable_media_v6_runtime.py",
        "tests/v2/infrastructure/test_editable_media_project_migration.py",
        "tests/v2/infrastructure/test_encoder_discovery.py",
        "tests/v2/infrastructure/test_font_assets.py",
        "tests/v2/infrastructure/test_loudness_service.py",
        "tests/v2/infrastructure/test_media_pipeline.py",
        "tests/v2/infrastructure/test_mlt_export.py",
        "tests/v2/infrastructure/test_proxy_commit_boundary.py",
        "tests/v2/infrastructure/test_reference_video_comparison.py",
        "tests/v2/infrastructure/test_release_runtime.py",
        "tests/v2/infrastructure/test_segmented_export.py",
        "tests/v2/infrastructure/test_web_capture_engine.py",
        "tests/v2/infrastructure/test_web_media.py",
        "tests/v2/application/test_portable_timeline_import.py",
    }
)
RUNTIME_TEST_PREFIXES = ("tests/v2/desktop/", "tests/v2/integration/")
RUNTIME_TEST_NODE_PREFIXES = (
    "tests/v2/application/test_editor_api.py::test_cli_and_desktop_composition_api_share_real_persisted_task_chain",
    "tests/v2/application/test_editor_api.py::test_fcpxml_export_runs_through_the_public_cli_contract",
    "tests/v2/application/test_editor_api.py::test_preview_snapshots_are_content_addressed_and_never_overwrite_active_graph",
    "tests/v2/application/test_editor_api.py::test_public_batch_clip_add_is_atomic_and_idempotent",
    "tests/v2/application/test_editor_service.py::test_async_task_receipt_recovers_after_scheduling_persistence_fault",
    "tests/v2/application/test_editor_service.py::test_task_request_releases_foreground_gate_but_keeps_real_writes_serialized",
    "tests/v2/application/test_editor_service.py::test_websocket_stream_delivers_real_task_and_committed_project_events",
    "tests/v2/application/test_subtitle_services.py::test_imported_subtitle_auto_imports_adjacent_media_and_follows_its_clip",
    "tests/v2/application/test_subtitle_services.py::test_new_translation_commit_failure_leaves_no_document_or_visible_srt",
    "tests/v2/application/test_subtitle_services.py::test_overlap_fix_and_clipboard_replacement_persist_through_srt_boundary",
    "tests/v2/application/test_subtitle_services.py::test_sequence_subtitle_timing_edit_persists_through_document_sync_and_undo",
    "tests/v2/application/test_subtitle_services.py::test_srt_import_edit_place_compile_and_export_use_one_document_boundary",
    "tests/v2/application/test_subtitle_services.py::test_smart_split_and_delete_preserve_existing_placement_identity",
    "tests/v2/application/test_subtitle_services.py::test_subtitle_edit_database_commit_failure_restores_database_and_visible_srt",
    "tests/v2/application/test_subtitle_services.py::test_subtitle_import_commit_failure_with_related_media_rolls_back_everything",
    "tests/v2/application/test_subtitle_services.py::test_subtitle_import_cancellation_after_related_media_probe_has_no_side_effects",
    "tests/v2/application/test_subtitle_services.py::test_timeline_and_subtitle_edits_share_one_chronological_undo_history",
    "tests/v2/application/test_subtitle_services.py::test_webvtt_ass_and_ssa_import_share_the_same_subtitle_document_boundary",
    "tests/v2/infrastructure/test_download_features.py::test_real_browser_sniffer_observes_page_media_request_and_title",
    "tests/v2/infrastructure/test_openchatcut_extensions.py::test_fcpxml_exports_real_media_timing_markers_and_captions",
    "tests/v2/infrastructure/test_openchatcut_extensions.py::test_scene_and_subject_tasks_write_observable_timeline_results",
)
RESOURCE_PROFILES = ("all", "lightweight", "runtime")


def source_file_for_node(node_id: str) -> str:
    source, separator, _ = node_id.partition("::")
    if not separator:
        raise ValueError(f"Invalid pytest node id: {node_id}")
    return source.replace("\\", "/")


def requires_reviewed_runtime(node_id: str) -> bool:
    source = source_file_for_node(node_id)
    return (
        source in RUNTIME_TEST_SOURCES
        or source.startswith(RUNTIME_TEST_PREFIXES)
        or any(
            node_id == prefix or node_id.startswith(f"{prefix}[")
            for prefix in RUNTIME_TEST_NODE_PREFIXES
        )
    )


def select_resource_profile(nodes: Sequence[str], profile: str) -> tuple[str, ...]:
    if profile not in RESOURCE_PROFILES:
        raise ValueError(f"Unsupported test resource profile: {profile}")
    if profile == "all":
        return tuple(nodes)
    needs_runtime = profile == "runtime"
    return tuple(node for node in nodes if requires_reviewed_runtime(node) is needs_runtime)
