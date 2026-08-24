from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

from mediaflow.application.desktop_mutation_adapter import plan_desktop_project_mutation

ROOT = Path(__file__).resolve().parents[3]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _class_methods(relative: str, class_name: str) -> set[str]:
    tree = ast.parse(_source(relative))
    class_node = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    return {
        node.name for node in class_node.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _function_call_names(
    relative: str,
    function_name: str,
    *,
    class_name: str | None = None,
) -> set[str]:
    tree = ast.parse(_source(relative))
    nodes: list[ast.stmt] = tree.body
    if class_name is not None:
        class_node = next(
            node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name
        )
        nodes = class_node.body
    function = next(
        node
        for node in nodes
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name
    )
    return {
        node.func.id
        if isinstance(node.func, ast.Name)
        else node.func.attr
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute))
    }


def test_removed_god_modules_have_no_runtime_or_test_entry_point() -> None:
    removed = (
        "mediaflow/domain/web_media.py",
        "mediaflow/desktop/controllers/timeline_controller.py",
        "mediaflow/desktop/controllers/subtitle_controller.py",
    )
    assert all(not (ROOT / relative).exists() for relative in removed)
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for root in (ROOT / "mediaflow", ROOT / "scripts", ROOT / "tests")
        for path in root.rglob("*.py")
        if path != Path(__file__)
    )
    for removed_import in (
        "domain.web_media import",
        "controllers.timeline_controller import",
        "controllers.subtitle_controller import",
        "ProjectSessionManager",
        "class TimelineController",
        "class SubtitleController",
    ):
        assert removed_import not in sources


def test_compatibility_facades_cannot_grow_business_logic() -> None:
    for relative in (
        "mediaflow/infrastructure/project_catalog_repository.py",
        "mediaflow/desktop/presentation_catalogs.py",
        "mediaflow/service/desktop_proxy.py",
        "mediaflow/automation/operation_models.py",
        "mediaflow/desktop/models.py",
    ):
        tree = ast.parse(_source(relative))
        definitions = [
            node
            for node in tree.body
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        assert definitions == []


def test_project_repository_exposes_focused_document_owners() -> None:
    repository = _source("mediaflow/infrastructure/project_repository.py")
    assert "self.projects =" in repository
    assert "self.sequences =" in repository
    assert "self.assets =" in repository
    assert "self.catalog" not in repository

    runtime_and_verification = "\n".join(
        path.read_text(encoding="utf-8")
        for root in (ROOT / "mediaflow", ROOT / "scripts")
        for path in root.rglob("*.py")
    )
    assert "repository.catalog" not in runtime_and_verification
    assert "ProjectCatalogRepository" not in runtime_and_verification

    architecture = _source("ARCHITECTURE.md")
    assert "`projects`、`sequences`、`assets`" in architecture
    assert "由 `catalog`、" not in architecture


def test_presentation_catalog_generation_tracks_every_focused_source() -> None:
    script = _source("scripts/update_qm_translations.py")
    assert 'desktop.glob("presentation_*.py")' in script
    assert "presentation_sources" in script
    for source in (ROOT / "mediaflow" / "desktop").glob("presentation_*.py"):
        if source.name == "presentation_catalogs.py":
            continue
        assert "QCoreApplication.translate" in source.read_text(encoding="utf-8") or source.name in {
            "presentation_workspace.py",
        }


def test_web_capture_responsibilities_have_one_owner() -> None:
    owners = {
        "class _BrowserWorker": "mediaflow/infrastructure/web_capture_worker.py",
        "class _FrameScheduler": "mediaflow/infrastructure/web_capture_scheduler.py",
        "def _compare_fast_capture": "mediaflow/infrastructure/web_capture_quality.py",
        "def _seek_frame": "mediaflow/infrastructure/web_capture_page.py",
    }
    capture_sources = {
        path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in (ROOT / "mediaflow" / "infrastructure").glob("web_capture_*.py")
    }
    for definition, owner in owners.items():
        assert capture_sources[owner].count(definition) == 1
        assert sum(source.count(definition) for source in capture_sources.values()) == 1


def test_desktop_proxy_commands_are_installed_from_the_command_registry() -> None:
    project = _source("mediaflow/service/remote_project.py")
    timeline = _source("mediaflow/service/remote_timeline.py")
    assert "for (target, name), _definition in DESKTOP_COMMANDS.items()" in project
    assert "for (target, name), _definition in DESKTOP_COMMANDS.items()" in timeline
    assert "setattr(RemoteEditorProject, name, descriptor)" in project
    assert "setattr(RemoteTimelineEditor, name, descriptor)" in timeline
    assert "__getattr__" not in project + timeline


def test_duplicate_media_layout_and_source_algorithms_stay_converged() -> None:
    audio = _source("mediaflow/infrastructure/mlt/audio_graph.py")
    video = _source("mediaflow/infrastructure/mlt/video_graph.py")
    assert "plan_playlist_layout(" in audio
    assert "plan_playlist_layout(" in video
    assert "visible_start =" not in audio + video

    resolver = _source("mediaflow/infrastructure/visual_source_resolver.py")
    thumbnail = _source("mediaflow/infrastructure/media_thumbnail_service.py")
    filmstrip = _source("mediaflow/infrastructure/timeline_filmstrip.py")
    assert resolver.count("def resolve_visual_source(") == 1
    assert 'resolve_visual_source(repository, asset, prefer="original")' in thumbnail
    assert 'resolve_visual_source(self.repository, asset, prefer="proxy")' in filmstrip


def test_qml_clip_interaction_and_number_fields_have_one_implementation() -> None:
    qml_root = ROOT / "mediaflow" / "desktop" / "qml"
    video_clip = (qml_root / "TimelineClipLayer.qml").read_text(encoding="utf-8")
    audio_clip = (qml_root / "TimelineAudioClipLayer.qml").read_text(encoding="utf-8")
    interaction = (qml_root / "components" / "TimelineClipInteraction.qml").read_text(encoding="utf-8")
    assert "TimelineClipInteraction {" in video_clip
    assert "TimelineClipInteraction {" in audio_clip
    assert video_clip.count("onPressed:") == 1
    assert audio_clip.count("onPressed:") == 0
    assert interaction.count("onPressed:") == 1

    audio_panel = (qml_root / "AudioPanel.qml").read_text(encoding="utf-8")
    visual_panel = (qml_root / "components" / "VisualEffectStackPanel.qml").read_text(encoding="utf-8")
    editor = (qml_root / "components" / "EditorFieldControl.qml").read_text(encoding="utf-8")
    assert "EditorFieldControl {" in audio_panel
    assert "EditorFieldControl {" in visual_panel
    assert (
        sum(path.read_text(encoding="utf-8").count("id: numberEditor") for path in qml_root.rglob("*.qml"))
        == 1
    )
    assert "id: numberEditor" in editor


def test_public_verifiers_use_the_focused_workspace_project_controller() -> None:
    performance = _source("scripts/verify_performance.py")
    assert "controllers.workspace_project.openProject(" in performance
    assert "controllers.workspace.openProject(" not in performance

    ui_matrix = _source("scripts/verify_ui_matrix.py")
    assert "workspace_project_controller = controllers.workspace_project" in ui_matrix
    for method in ("createProject", "openProject", "closeProject"):
        assert f"workspace_project_controller.{method}(" in ui_matrix
        assert f"workspace_controller.{method}(" not in ui_matrix


def test_automation_package_initialization_has_no_import_cycle() -> None:
    package = ast.parse(_source("mediaflow/automation/__init__.py"))
    assert not any(isinstance(node, (ast.Import, ast.ImportFrom)) for node in package.body)
    registry = _source("mediaflow/automation/operation_registry.py")
    for focused_models in (
        "operation_delivery_models",
        "operation_language_models",
        "operation_model_common",
        "operation_project_models",
        "operation_timeline_models",
        "operation_web_models",
    ):
        assert f"import mediaflow.automation.{focused_models}" in registry
    assert "operation_models as models" not in registry
    assert "from mediaflow.automation import" not in registry


def test_audited_desktop_controllers_keep_one_responsibility() -> None:
    moved_settings_methods = {
        "saveWindowState",
        "setWorkspaceLayoutPreset",
        "saveWorkspaceLayout",
        "setAssetViewMode",
        "setDefaultDownloadDirectory",
        "setDefaultProjectDirectory",
        "setLastDownloadUrl",
        "inspectManagedCookies",
        "saveManagedCookies",
        "clearManagedCookies",
        "inspectRuntimeTools",
        "installRuntimeComponents",
        "scheduleRuntimeDirectoryChange",
        "saveLlmProvider",
        "saveGlossaryTerm",
    }
    assert _class_methods(
        "mediaflow/desktop/controllers/settings_controller.py",
        "SettingsController",
    ).isdisjoint(moved_settings_methods)
    focused_settings = {
        "WorkspaceSettingsController": {
            "saveWindowState",
            "setWorkspaceLayoutPreset",
            "saveWorkspaceLayout",
            "setAssetViewMode",
        },
        "DownloadSettingsController": {
            "setDefaultDownloadDirectory",
            "setDefaultProjectDirectory",
            "setLastDownloadUrl",
            "inspectManagedCookies",
            "saveManagedCookies",
            "clearManagedCookies",
        },
        "RuntimeSettingsController": {
            "inspectRuntimeTools",
            "installRuntimeComponents",
            "scheduleRuntimeDirectoryChange",
        },
        "LanguageSettingsController": {
            "saveLlmProvider",
            "saveGlossaryTerm",
        },
    }
    for class_name, methods in focused_settings.items():
        relative = (
            "mediaflow/desktop/controllers/"
            + class_name.removesuffix("Controller")
            .replace("Settings", "_settings")
            .lower()
            + "_controller.py"
        )
        assert methods <= _class_methods(relative, class_name)

    download_methods = {
        "analyzeDownloadUrl",
        "submitDownloadPlan",
        "createProjectAndDownload",
        "setDownloadEntrySelected",
        "selectAllDownloadEntries",
        "dismissDownloadPlan",
    }
    assert _class_methods(
        "mediaflow/desktop/controllers/task_controller.py",
        "TaskController",
    ).isdisjoint(download_methods)
    assert download_methods <= _class_methods(
        "mediaflow/desktop/controllers/download_controller.py",
        "DownloadController",
    )


def test_background_results_are_routed_by_focused_handlers() -> None:
    request_calls = _function_call_names(
        "mediaflow/desktop/coordinators/background_requests.py",
        "_on_result",
        class_name="BackgroundRequests",
    )
    assert "dispatch" in request_calls
    router_methods = _class_methods(
        "mediaflow/desktop/coordinators/background_result_router.py",
        "BackgroundResultRouter",
    )
    assert {
        "_handle_recent_projects",
        "_handle_encoder_policies",
        "_handle_runtime_status",
        "_handle_download_plan",
        "_handle_waveform",
        "_handle_asset_thumbnails",
        "_handle_audio_metrics",
        "_handle_timeline_filmstrip",
        "_handle_project_close",
        "_handle_preview",
    } <= router_methods


def test_audited_shared_algorithms_have_one_owner() -> None:
    runtime_sources = {
        path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in (ROOT / "mediaflow").rglob("*.py")
    }
    unique_definitions = {
        "def estimate_subtitle_words(": "mediaflow/application/subtitle_word_timing.py",
        "def upsert_web_keyframe(": "mediaflow/application/web_keyframe_operations.py",
        "def open_editable_media_page(": "mediaflow/infrastructure/editable_media_page.py",
        "def open_project_database(": "mediaflow/infrastructure/sqlite_connections.py",
    }
    for definition, owner in unique_definitions.items():
        assert runtime_sources[owner].count(definition) == 1
        assert sum(source.count(definition) for source in runtime_sources.values()) == 1
    application_sources = {
        relative: source
        for relative, source in runtime_sources.items()
        if relative.startswith("mediaflow/application/")
    }
    for definition in ("def remove_web_keyframe(", "def move_web_keyframe("):
        assert runtime_sources["mediaflow/application/web_keyframe_operations.py"].count(
            definition
        ) == 1
        assert sum(source.count(definition) for source in application_sources.values()) == 1

    for browser_consumer in (
        "mediaflow/infrastructure/web_capture_worker.py",
        "mediaflow/infrastructure/web_direct_h264_browser.py",
        "mediaflow/infrastructure/web_browser.py",
    ):
        assert "open_editable_media_page(" in runtime_sources[browser_consumer]
    assert runtime_sources["mediaflow/application/subtitle_acquisition.py"].count(
        "estimate_subtitle_words("
    ) == 1
    assert runtime_sources["mediaflow/application/subtitle_editing.py"].count(
        "estimate_subtitle_words("
    ) == 1


def test_frame_clock_and_subtitle_projection_have_focused_owners() -> None:
    timeline_methods = _class_methods(
        "mediaflow/infrastructure/timeline_repository.py",
        "TimelineRepository",
    )
    assert timeline_methods.isdisjoint(
        {
            "capture_main_frame_clock",
            "change_main_frame_clock",
            "restore_main_frame_clock",
        }
    )
    assert {
        "capture_main_frame_clock",
        "change_main_frame_clock",
        "restore_main_frame_clock",
    } <= _class_methods(
        "mediaflow/infrastructure/main_frame_clock_repository.py",
        "MainFrameClockRepository",
    )
    subtitle_repository = _source("mediaflow/infrastructure/subtitle_repository.py")
    assert "reconcile_placements(" in subtitle_repository
    assert "def _map_segment_to_clip(" not in subtitle_repository


def test_large_qml_surfaces_compose_focused_components() -> None:
    qml_root = ROOT / "mediaflow" / "desktop" / "qml"
    subtitle_panel = (qml_root / "SubtitlePanel.qml").read_text(encoding="utf-8")
    for component in ("SubtitleDocumentEditor", "SubtitleSequenceEditor"):
        assert f"{component} {{" in subtitle_panel
    document_editor = (qml_root / "components" / "SubtitleDocumentEditor.qml").read_text(
        encoding="utf-8"
    )
    for component in ("SubtitleSearchPanel", "SubtitleSegmentList", "SubtitleSegmentEditor"):
        assert f"{component} {{" in document_editor

    preview = (qml_root / "components" / "PreviewViewport.qml").read_text(encoding="utf-8")
    assert "PreviewExportOverlay {" in preview
    assert "PreviewTransportControls {" in preview
    assert 'objectName: "previewControlBar"' not in preview
    assert 'objectName: "exportSubtitlePreview"' not in preview


def test_retired_model_aggregates_have_no_consumers() -> None:
    sources = {
        path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in (ROOT / "mediaflow").rglob("*.py")
    }
    active_sources = "\n".join(
        source
        for relative, source in sources.items()
        if relative
        not in {
            "mediaflow/automation/operation_models.py",
            "mediaflow/desktop/models.py",
        }
    )
    assert "automation.operation_models" not in active_sources
    assert "desktop.models" not in active_sources


def test_audited_long_methods_delegate_to_focused_operations() -> None:
    expected_calls = {
        (
            "mediaflow/application/transcription_task_handler.py",
            "handle",
            "TranscriptionTaskHandler",
        ): {"_validated_selection", "_transcribe_sources", "_defer_transcript_commit"},
        (
            "mediaflow/application/sequence_service.py",
            "_prepare_copy_selection",
            "SequenceService",
        ): {"prepare"},
        (
            "mediaflow/application/dubbing_task_handler.py",
            "_synthesize_with_outputs",
            "DubbingTaskHandler",
        ): {"_synthesis_targets", "_synthesize_utterances", "_assemble_master"},
        (
            "mediaflow/infrastructure/segmented_export_service.py",
            "build",
            "SegmentedExportService",
        ): {"_prepare_build", "_build_visual_units", "_build_audio_master", "_build_result"},
        (
            "mediaflow/infrastructure/web_browser.py",
            "validate_editable_media_page",
            None,
        ): {
            "_validate_frame_protocol",
            "_validate_runtime_manifests",
            "_validate_runtime_state",
            "_validate_variants_and_seek",
        },
        (
            "mediaflow/infrastructure/web_capture_engine.py",
            "render_frames",
            "WebCaptureEngine",
        ): {"_create_run", "_dispatch_workers", "_consume_frames", "_record_metrics"},
    }
    for (relative, function_name, class_name), required_calls in expected_calls.items():
        assert required_calls <= _function_call_names(
            relative,
            function_name,
            class_name=class_name,
        )


def test_runtime_paths_and_llm_models_have_one_configured_source() -> None:
    runtime_source = "\n".join(
        path.read_text(encoding="utf-8") for path in (ROOT / "mediaflow").rglob("*.py")
    )
    for embedded_path in (
        "venv/Scripts/python.exe",
        "venv\\\\Scripts\\\\python.exe",
    ):
        assert embedded_path not in runtime_source

    qml_source = "\n".join(
        path.read_text(encoding="utf-8") for path in (ROOT / "mediaflow" / "desktop" / "qml").rglob("*.qml")
    )
    assert "deepseek-chat" not in qml_source
    assert "deepseek-reasoner" not in qml_source

    environment = _source("mediaflow/environment.py")
    environment_consumers = {
        "mediaflow/desktop/bootstrap.py": "SERVICE_STATE_DIRECTORY_VARIABLE",
        "mediaflow/desktop/app.py": "DESKTOP_SETTINGS_PATH_VARIABLE",
        "mediaflow/desktop/runtime_directory_management.py": "RUNTIME_DIRECTORY_VARIABLE",
    }
    for relative, constant in environment_consumers.items():
        assert constant in environment
        source = _source(relative)
        assert constant in source
    assert '"MEDIAFLOW_SERVICE_STATE_DIR"' not in _source("mediaflow/desktop/bootstrap.py")
    assert '"MEDIAFLOW_DESKTOP_SETTINGS_PATH"' not in _source("mediaflow/desktop/app.py")
    assert '"MEDIAFLOW_RUNTIME_DIR"' not in _source(
        "mediaflow/desktop/runtime_directory_management.py"
    )


def test_project_sqlite_connections_share_one_contract() -> None:
    connection_owner = _source("mediaflow/infrastructure/sqlite_connections.py")
    assert connection_owner.count("sqlite3.connect(") == 2
    assert "PROJECT_DATABASE_TIMEOUT_SECONDS" in connection_owner
    assert "PROJECT_DATABASE_BUSY_TIMEOUT_MS" in connection_owner
    for relative in (
        "mediaflow/infrastructure/project_repository.py",
        "mediaflow/infrastructure/task_repository.py",
    ):
        source = _source(relative)
        assert "open_project_database(" in source
        assert "sqlite3.connect(" not in source
    records = _source("mediaflow/infrastructure/project_records_repository.py")
    assert "open_project_database(" in records
    assert records.count("sqlite3.connect(") == 1
    assert "sqlite3.connect(temporary)" in records


def test_editor_service_routes_to_focused_services_without_a_forwarding_manager() -> None:
    composition_methods = _class_methods(
        "mediaflow/service/sessions.py",
        "EditorServiceOperations",
    )
    assert composition_methods == {
        "__init__",
        "service_status",
        "prepare_shutdown",
        "close",
    }
    dispatcher = _source("mediaflow/service/request_dispatcher.py")
    for direct_boundary in (
        "operations.automation.execute",
        "operations.desktop.execute_desktop_command",
        "runtime.execute_application_command",
        "operations.registry.create_desktop_project",
    ):
        assert direct_boundary in dispatcher
    server = _source("mediaflow/service/server.py")
    assert "ServiceRequestDispatcher" in server
    assert "self._sessions" not in server
    assert "self._sessions" not in dispatcher


def test_desktop_qml_uses_focused_timeline_and_subtitle_contexts() -> None:
    qml = "\n".join(
        path.read_text(encoding="utf-8") for path in (ROOT / "mediaflow" / "desktop" / "qml").rglob("*.qml")
    )
    for context in (
        "timelineViewController",
        "timelineClipController",
        "timelineStructureController",
        "timelineEffectsController",
        "timelineAnalysisController",
        "subtitleViewController",
        "subtitlePlacementController",
        "subtitleTranscriptionController",
        "subtitleTranslationController",
        "subtitleEditingController",
    ):
        assert context in qml
    assert "timelineController" not in qml.replace("webTimelineController", "")
    assert "subtitleController" not in qml


def test_shared_algorithms_have_one_implementation_and_explicit_consumers() -> None:
    expectations = {
        "mediaflow/application/project_revision_policy.py": "def resolve_project_revision(",
        "mediaflow/application/timeline_integrity.py": "def validate_timeline_integrity(",
        "mediaflow/infrastructure/resumable_download.py": "def download_with_resume(",
        "mediaflow/domain/clip_transform_projection.py": "def project_clip_transform_points(",
        "mediaflow/application/web_package_contract.py": "def resolve_media_bindings(",
        "mediaflow/infrastructure/web_render_ffmpeg.py": "def build_web_render_ffmpeg_command(",
    }
    runtime_sources = "\n".join(
        path.read_text(encoding="utf-8") for path in (ROOT / "mediaflow").rglob("*.py")
    )
    for owner, definition in expectations.items():
        assert _source(owner).count(definition) == 1
        assert runtime_sources.count(definition) == 1

    consumers = {
        "resolve_project_revision": {
            "mediaflow/project_collaboration.py",
            "mediaflow/service/session_registry.py",
        },
        "validate_timeline_integrity": {
            "mediaflow/application/timeline_validator.py",
            "mediaflow/infrastructure/timeline_repository.py",
        },
        "download_with_resume": {
            "mediaflow/infrastructure/runtime_components.py",
            "mediaflow/infrastructure/runtime_tools.py",
        },
        "project_clip_transform_points": {
            "mediaflow/infrastructure/fcpxml_export.py",
            "mediaflow/infrastructure/mlt/clip_graph.py",
        },
        "resolve_media_bindings": {
            "mediaflow/infrastructure/web_native_media.py",
        },
    }
    for symbol, expected_consumers in consumers.items():
        actual = {
            path.relative_to(ROOT).as_posix()
            for path in (ROOT / "mediaflow").rglob("*.py")
            if path.relative_to(ROOT).as_posix() not in expectations
            and symbol in path.read_text(encoding="utf-8")
            and not path.name.endswith("__init__.py")
        }
        assert expected_consumers <= actual


def test_automation_controller_has_no_private_controller_dependency() -> None:
    source = _source("mediaflow/desktop/controllers/automation_controller.py")
    for private_peer in (
        "._export",
        "._subtitle",
        "._web_timeline",
        "._web_delivery",
    ):
        assert private_peer not in source
    init = next(
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and node.name == "__init__"
    )
    parameters = {argument.arg for argument in init.args.args + init.args.kwonlyargs}
    assert parameters == {"self", "session", "web"}


def test_project_web_command_binds_its_real_positional_sequence_before_planning() -> None:
    calls: list[tuple[str, str, str | None]] = []

    class Project:
        def get_project(self):
            return SimpleNamespace(main_sequence_id="main")

        def describe_web_clip_editing(
            self,
            sequence_id: str,
            clip_id: str,
            *,
            scene_id: str | None = None,
        ):
            calls.append((sequence_id, clip_id, scene_id))
            return SimpleNamespace(
                fields=[
                    SimpleNamespace(
                        target="parameter",
                        source_id="spring",
                        path="parameters.spring",
                    )
                ]
            )

    plan = plan_desktop_project_mutation(
        "project",
        "update_web_parameter",
        sequence_id="",
        args=["sequence-real", "clip", "spring", 0.5],
        kwargs={"scene_id": "scene"},
        project=Project(),
    )

    assert calls == [("sequence-real", "clip", "scene")]
    assert plan.conflict_set == ["/web/clips/clip/parameters/spring"]
