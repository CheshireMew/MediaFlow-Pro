from __future__ import annotations

import ast
import re
from pathlib import Path

from mediaflow.application.workflow_stage_handlers import workflow_stage_handlers
from mediaflow.automation.operation_registry import OPERATIONS, OperationDefinition
from mediaflow.desktop.presentation_catalogs import (
    WORKSPACE_MODES,
    WORKSPACE_NAVIGATION_MODE_KEYS,
)
from mediaflow.domain.enums import WorkflowStage
from mediaflow.domain.model_base import DomainModel
from mediaflow.infrastructure.project_migrations import PROJECT_MIGRATIONS
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.project_repository_component import ProjectRepositoryComponent
from mediaflow.infrastructure.project_schema_definition import PROJECT_SCHEMA_VERSION

ROOT = Path(__file__).resolve().parents[3]
PYTHON_ROOTS = (ROOT / "mediaflow", ROOT / "scripts", ROOT / "tests")


def _python_files() -> list[Path]:
    return [
        path
        for source_root in PYTHON_ROOTS
        for path in source_root.rglob("*.py")
        if "archive" not in path.parts and path != Path(__file__).resolve()
    ]


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imported_modules(path: Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _class(path: Path, name: str) -> ast.ClassDef:
    return next(node for node in _tree(path).body if isinstance(node, ast.ClassDef) and node.name == name)


def test_removed_architecture_has_no_runtime_or_test_entry_point() -> None:
    banned_modules = {
        "mediaflow.domain." + "models",
        "mediaflow.application.subtitle_" + "service",
    }
    banned_classes = {
        "Project" + "Controller",
        "Project" + "Documents",
        "Subtitle" + "Service",
    }
    violations: list[str] = []
    for path in _python_files():
        tree = _tree(path)
        imported = _imported_modules(path)
        if imported & banned_modules:
            violations.append(f"{path.relative_to(ROOT)} imports {sorted(imported & banned_modules)}")
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name in banned_classes:
                violations.append(f"{path.relative_to(ROOT)} defines {node.name}")
            if isinstance(node, ast.ImportFrom) and node.level and node.module == "models":
                violations.append(f"{path.relative_to(ROOT)} imports the removed relative models module")
    qml_legacy_name = "project" + "Controller"
    for path in (ROOT / "mediaflow" / "desktop" / "qml").rglob("*.qml"):
        if qml_legacy_name in path.read_text(encoding="utf-8"):
            violations.append(f"{path.relative_to(ROOT)} uses {qml_legacy_name}")
    assert violations == []
    assert not (ROOT / "mediaflow" / "domain" / "models.py").exists()
    assert not (ROOT / "mediaflow" / "application" / "subtitle_service.py").exists()


def test_legacy_aggregate_settings_have_no_runtime_entry_point() -> None:
    banned_patterns = {
        "aggregate type": re.compile(r"\bGlobal" + r"Settings\b"),
        "aggregate repository": re.compile(r"\bSettings" + r"Repository\b"),
        "aggregate environment": re.compile(r"\bMEDIAFLOW_" + r"SETTINGS_PATH\b"),
        "aggregate filename": re.compile(r'(?<![-\w])settings\.json["\']'),
    }
    sources = [
        *list((ROOT / "mediaflow").rglob("*.py")),
        *[
            path
            for path in (ROOT / "scripts").glob("*.py")
            if path.name != "migrate_settings.py"
        ],
    ]
    violations = []
    for path in sources:
        if "archive" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        for label, pattern in banned_patterns.items():
            if pattern.search(source):
                violations.append(f"{path.relative_to(ROOT)} retains {label}")
    assert violations == []


def test_domain_and_application_layers_do_not_depend_on_outer_layers() -> None:
    violations: list[str] = []
    for path in (ROOT / "mediaflow" / "domain").glob("*.py"):
        for module in _imported_modules(path):
            if module.startswith(("mediaflow.application", "mediaflow.infrastructure", "mediaflow.desktop")):
                violations.append(f"{path.name} -> {module}")
    for path in (ROOT / "mediaflow" / "application").glob("*.py"):
        for module in _imported_modules(path):
            if module.startswith(("mediaflow.infrastructure", "mediaflow.desktop")):
                violations.append(f"{path.name} -> {module}")
    assert violations == []


def test_workflow_service_dispatches_to_complete_stage_registry() -> None:
    assert set(workflow_stage_handlers()) == set(WorkflowStage)
    path = ROOT / "mediaflow" / "application" / "project_workflow_service.py"
    service = _class(path, "ProjectWorkflowService")
    methods = {node.name: node for node in service.body if isinstance(node, ast.FunctionDef)}
    assert methods["continue_run"].end_lineno - methods["continue_run"].lineno < 40
    assert methods["handle_task"].end_lineno - methods["handle_task"].lineno < 40
    assert "_stage_handlers[run.stage]" in path.read_text(encoding="utf-8")


def test_repository_and_subtitle_capabilities_remain_split() -> None:
    assert ProjectRepository.__bases__ == (object,)
    repository = _class(
        ROOT / "mediaflow" / "infrastructure" / "project_repository.py",
        "ProjectRepository",
    )
    assert {
        node.name
        for node in repository.body
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
    } == {
        "create",
        "open",
        "owns_project_lock",
        "known_content_revision",
        "content_revision",
        "acknowledge_content_revision",
        "automation_result",
        "begin_automation_request",
        "save_automation_result",
        "consume_task_result_once",
        "coalesced_revision",
        "enlist_transaction_publication",
        "transaction",
        "close",
    }
    initializer = next(
        node for node in repository.body if isinstance(node, ast.FunctionDef) and node.name == "__init__"
    )
    assigned_components = {
        target.attr
        for node in ast.walk(initializer)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Attribute)
        and isinstance(target.value, ast.Name)
        and target.value.id == "self"
    }
    assert {
        "catalog",
        "timeline",
        "audio",
        "subtitles",
        "highlights",
        "web",
        "records",
    } <= assigned_components
    for component_name in (
        "ProjectCatalogRepository",
        "TimelineRepository",
        "AudioRepository",
        "SubtitleRepository",
        "HighlightRepository",
        "WebMediaRepository",
        "ProjectRecordsRepository",
    ):
        module = next(
            path
            for path in (ROOT / "mediaflow" / "infrastructure").glob("*_repository.py")
            if any(
                isinstance(node, ast.ClassDef) and node.name == component_name for node in _tree(path).body
            )
        )
        component = _class(module, component_name)
        assert any(
            isinstance(base, ast.Name) and base.id == ProjectRepositoryComponent.__name__
            for base in component.bases
        )

    acquisition = _class(
        ROOT / "mediaflow" / "application" / "subtitle_acquisition.py",
        "SubtitleAcquisitionService",
    )
    editing = _class(
        ROOT / "mediaflow" / "application" / "subtitle_editing.py",
        "SubtitleEditingService",
    )
    publication = _class(
        ROOT / "mediaflow" / "application" / "subtitle_publication.py",
        "SubtitlePublicationService",
    )
    acquisition_methods = {node.name for node in acquisition.body if isinstance(node, ast.FunctionDef)}
    editing_methods = {node.name for node in editing.body if isinstance(node, ast.FunctionDef)}
    publication_methods = {node.name for node in publication.body if isinstance(node, ast.FunctionDef)}
    assert "transcribe_asset_region" in acquisition_methods
    assert "save_sequence_transcript" in acquisition_methods
    assert "transcribe_sequence_audio" not in acquisition_methods
    assert "update_segment" in editing_methods
    assert publication_methods == {
        "__init__",
        "document_srt_path",
        "write_document_srt",
        "reconcile_document_srts",
        "commit_document_change",
        "commit_prepared_documents",
    }
    assert "write_document_srt" not in acquisition_methods | editing_methods
    assert "commit_document_change" not in acquisition_methods | editing_methods


def test_open_project_has_one_public_application_api() -> None:
    composition = _class(ROOT / "mediaflow" / "composition.py", "EditorProject")
    initializer = next(
        node for node in composition.body if isinstance(node, ast.FunctionDef) and node.name == "__init__"
    )
    public_service_names = {
        "documents",
        "assets",
        "web",
        "subtitle_publication",
        "subtitle_acquisition",
        "subtitle_editing",
        "transcript_editing",
        "highlights",
        "sequences",
        "tasks",
        "workflows",
        "task_handlers",
        "history",
    }
    assignments = {
        target.attr
        for node in ast.walk(initializer)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        if isinstance(target, ast.Attribute)
        and isinstance(target.value, ast.Name)
        and target.value.id == "self"
    }
    assert assignments.isdisjoint(public_service_names)

    production_roots = (
        ROOT / "mediaflow" / "automation",
        ROOT / "mediaflow" / "desktop",
    )
    violations = []
    for root in production_roots:
        for path in root.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            for name in public_service_names:
                if re.search(rf"\b(?:project|_project)\.{re.escape(name)}\b", source):
                    violations.append(f"{path.relative_to(ROOT)} accesses {name}")
    assert violations == []


def test_desktop_controllers_use_declared_members_only() -> None:
    controller_root = ROOT / "mediaflow" / "desktop" / "controllers"
    violations = []
    for path in controller_root.glob("*.py"):
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.FunctionDef) and node.name in {
                "__getattr__",
                "__setattr__",
            }:
                violations.append(f"{path.name}:{node.name}")
    assert violations == []

    broad_refresh_callers: set[tuple[str, str]] = set()
    for path in controller_root.glob("*.py"):
        for class_node in (node for node in _tree(path).body if isinstance(node, ast.ClassDef)):
            for method in (node for node in class_node.body if isinstance(node, ast.FunctionDef)):
                if any(
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "refresh_all"
                    for node in ast.walk(method)
                ):
                    broad_refresh_callers.add((path.name, method.name))
    assert broad_refresh_callers == set()

    timeline_qml = (ROOT / "mediaflow" / "desktop" / "qml" / "TimelineView.qml").read_text(encoding="utf-8")
    assert "allowedTrackKinds" not in timeline_qml
    assert "previewClipMove" in timeline_qml
    assert not (ROOT / "mediaflow" / "infrastructure" / "task_handlers.py").exists()
    assert (ROOT / "mediaflow" / "application" / "project_task_handlers.py").is_file()


def test_desktop_interaction_layers_do_not_reach_into_infrastructure() -> None:
    violations = []
    desktop_root = ROOT / "mediaflow" / "desktop"
    for relative_root in ("controllers", "coordinators", "presenters"):
        for path in (desktop_root / relative_root).glob("*.py"):
            for module in _imported_modules(path):
                if module.startswith("mediaflow.infrastructure"):
                    violations.append(f"{path.relative_to(ROOT)} -> {module}")
    assert violations == []


def test_removed_desktop_session_aliases_have_no_callers() -> None:
    banned_aliases = (
        "session._project",
        "session._editor",
        "session._pending_asset_batch_ids",
        "session._pending_profile_asset_id",
    )
    violations = []
    for path in _python_files():
        source = path.read_text(encoding="utf-8")
        for alias in banned_aliases:
            if alias in source:
                violations.append(f"{path.relative_to(ROOT)} uses {alias}")
    assert violations == []


def test_desktop_session_and_qml_roots_keep_focused_boundaries() -> None:
    session = _class(
        ROOT / "mediaflow" / "desktop" / "controllers" / "project_controller.py",
        "ProjectSession",
    )
    session_methods = {node.name for node in session.body if isinstance(node, ast.FunctionDef)}
    assert not any(name.startswith("_refresh_") for name in session_methods)
    assert "_schedule_preview_graph" not in session_methods
    assert "_compile_preview_graph" not in session_methods

    qml_root = ROOT / "mediaflow" / "desktop" / "qml"
    workspace = (qml_root / "Workspace.qml").read_text(encoding="utf-8")
    export_panel = (qml_root / "ExportPanel.qml").read_text(encoding="utf-8")
    assert len(workspace.splitlines()) <= 700
    assert len(export_panel.splitlines()) <= 140
    for component in (
        "PreviewViewport",
        "WorkspaceNavigation",
        "WorkspaceShortcuts",
    ):
        assert component in workspace
        assert (qml_root / "components" / f"{component}.qml").is_file()
    status_overlays = qml_root / "WorkspaceStatusOverlays.qml"
    assert "WorkspaceStatusOverlays" in workspace
    assert "WorkflowBanner" not in workspace
    assert status_overlays.is_file()
    status_overlay_source = status_overlays.read_text(encoding="utf-8")
    assert "WorkflowBanner" in status_overlay_source
    assert "DownloadProgressBanner" in status_overlay_source
    assert "InspectorPanel" in workspace
    assert (qml_root / "WorkflowBanner.qml").is_file()
    inspector = qml_root / "InspectorPanel.qml"
    assert inspector.is_file()
    inspector_source = inspector.read_text(encoding="utf-8")
    assert "EditPanel" in inspector_source
    assert "草稿参数" in inspector_source
    assert "素材参数" in inspector_source
    assert "TranscriptWorkspace" in workspace
    assert (qml_root / "TranscriptWorkspace.qml").is_file()
    assert "TaskCenterPanel" in workspace
    assert (qml_root / "TaskCenterPanel.qml").is_file()
    redundant_panel_titles = {
        "MediaPanel.qml": "素材",
        "TranscriptPanel.qml": "自动字幕",
        "SubtitlePanel.qml": "字幕编辑",
        "TranslationPanel.qml": "字幕翻译",
        "HighlightPanel.qml": "AI 高光",
        "EditPanel.qml": "片段属性",
        "AudioPanel.qml": "音频",
        "ExportPanel.qml": "导出",
        "TaskCenterPanel.qml": "任务中心",
    }
    for panel_name, redundant_title in redundant_panel_titles.items():
        panel_source = (qml_root / panel_name).read_text(encoding="utf-8")
        assert f'text: qsTr("{redundant_title}")' not in panel_source
    assert "TaskDrawer" not in workspace
    assert not (qml_root / "TaskDrawer.qml").exists()
    transcript_panel = (qml_root / "TranscriptPanel.qml").read_text(encoding="utf-8")
    assert "transcribeTimelineButton" in transcript_panel
    assert "transcribeCurrentSequence" in transcript_panel
    assert "transcribeSelectedAsset" not in transcript_panel
    assert "transcribeRegion" not in transcript_panel
    assert "transcriptWordEditor" not in transcript_panel
    assert "transcriptWordSegmentList" not in transcript_panel
    assert "rippleDeleteTranscriptWordsButton" not in transcript_panel
    assert "selectedSubtitleWordIds" not in transcript_panel
    subtitle_controller = (
        ROOT / "mediaflow" / "desktop" / "controllers" / "subtitle_controller.py"
    ).read_text(encoding="utf-8")
    assert "subtitleWordsModel" not in subtitle_controller
    assert "rippleDeleteSelectedWords" not in subtitle_controller
    automation_contract = (ROOT / "mediaflow" / "automation" / "contracts.py").read_text(encoding="utf-8")
    automation_registry = (ROOT / "mediaflow" / "automation" / "operation_registry.py").read_text(
        encoding="utf-8"
    )
    assert "OPERATIONS" in automation_contract
    for operation in (
        "transcript.get",
        "transcript.edit.preview",
        "transcript.edit.apply",
    ):
        assert operation in automation_registry
    task_commands = (ROOT / "mediaflow" / "domain" / "task_commands.py").read_text(encoding="utf-8")
    assert "TranscribeSequenceCommand" in task_commands
    assert "TranscribeAssetCommand" not in task_commands
    assert "TranscribeRegionCommand" not in task_commands
    assert not (ROOT / "mediaflow" / "infrastructure" / "audio_region_extractor.py").exists()
    timeline = (qml_root / "TimelineView.qml").read_text(encoding="utf-8")
    timeline_toolbar = (qml_root / "TimelineToolbar.qml").read_text(encoding="utf-8")
    timeline_canvas = (qml_root / "TimelineCanvas.qml").read_text(encoding="utf-8")
    assert len(timeline.splitlines()) <= 400
    assert "TimelineToolbar" in timeline
    assert "TimelineCanvas" in timeline
    assert "TimelineTrackControls" in timeline
    assert "SequenceToolbar" in timeline_toolbar
    assert 'objectName: "timelineRuler"' in timeline_canvas
    assert "rulerMajorStepFrames" in timeline
    assert "rulerSeconds" not in timeline_canvas
    assert 'index + "s"' not in timeline_canvas
    assert 'objectName: "trackControlsButton"' not in timeline
    assert "visible: trackControlsRepeater.count > 0" in (qml_root / "TimelineTrackControls.qml").read_text(
        encoding="utf-8"
    )
    for component in (
        "TimelineToolbar",
        "TimelineCanvas",
        "TimelineTrackControls",
        "TimelineClipLayer",
        "TimelineAudioClipLayer",
        "TimelineCompoundLayer",
        "TimelineSubtitleLayer",
        "TimelineTransitionLayer",
        "TimelineMarkerLayer",
    ):
        assert (qml_root / f"{component}.qml").is_file()
    media_panel = (qml_root / "MediaPanel.qml").read_text(encoding="utf-8")
    assert "FileDialog.OpenFiles" in media_panel
    assert "selectedFiles" in media_panel
    assert "addAssetAtPlayhead" in media_panel
    assert "生成代理" not in media_panel
    assert "生成波形" not in media_panel
    assert "添加到时间线" not in media_panel
    assert (qml_root / "components" / "SequenceToolbar.qml").is_file()
    preview_viewport = (qml_root / "components" / "PreviewViewport.qml").read_text(encoding="utf-8")
    assert "playPreviewFrom" in preview_viewport
    assert "pendingPlaybackMode" in preview_viewport
    assert "property int sequenceIn" not in preview_viewport
    assert "property int sequenceOut" not in preview_viewport
    assert "previewGraphRevision" not in workspace
    window_title_bar = (qml_root / "components" / "WindowTitleBar.qml").read_text(encoding="utf-8")
    workspace_header = (qml_root / "components" / "WorkspaceHeader.qml").read_text(encoding="utf-8")
    assert "WorkspaceHeader" in window_title_bar
    assert "workspaceController.projectName" not in workspace_header
    assert "toggleTaskDrawer" not in workspace_header
    task_controller = (ROOT / "mediaflow" / "desktop" / "controllers" / "task_controller.py").read_text(
        encoding="utf-8"
    )
    assert "taskDrawer" not in task_controller
    assert "openTaskCenter" in task_controller
    assert "ExportSettings" in export_panel
    assert "ExportTargetBar" in export_panel
    for component in (
        "ExportSettings",
        "ExportTargetBar",
        "ExportTechnicalSettings",
        "ExportSubtitleSettings",
        "ExportWatermarkSettings",
    ):
        assert (qml_root / "components" / f"{component}.qml").is_file()
    presentation = (ROOT / "mediaflow" / "desktop" / "presentation_catalogs.py").read_text(encoding="utf-8")
    assert "准备流畅预览" in presentation
    assert "准备音频波形" in presentation
    assert '"生成代理"' not in presentation
    assert '"生成波形"' not in presentation


def test_audited_god_files_stay_replaced_by_focused_components() -> None:
    limits = {
        ROOT / "mediaflow" / "application" / "project_task_handlers.py": 150,
        ROOT / "mediaflow" / "application" / "web_media_service.py": 50,
        ROOT / "mediaflow" / "application" / "web_package_service.py": 450,
        ROOT / "mediaflow" / "application" / "web_clip_editing_service.py": 1600,
        ROOT / "mediaflow" / "application" / "web_batch_service.py": 180,
        ROOT / "mediaflow" / "application" / "web_rebind_service.py": 850,
        ROOT / "mediaflow" / "desktop" / "controllers" / "project_controller.py": 260,
        ROOT / "mediaflow" / "desktop" / "controllers" / "web_controller.py": 700,
        ROOT / "mediaflow" / "desktop" / "controllers" / "web_timeline_controller.py": 550,
        ROOT / "mediaflow" / "desktop" / "controllers" / "web_delivery_controller.py": 300,
        ROOT / "mediaflow" / "desktop" / "qml" / "Workspace.qml": 600,
        ROOT / "mediaflow" / "desktop" / "qml" / "TimelineView.qml": 400,
        ROOT / "mediaflow" / "infrastructure" / "mlt" / "compiler.py": 300,
        ROOT / "mediaflow" / "infrastructure" / "project_migration_runner.py": 100,
    }
    assert {
        str(path.relative_to(ROOT)): len(path.read_text(encoding="utf-8").splitlines())
        for path, limit in limits.items()
        if len(path.read_text(encoding="utf-8").splitlines()) > limit
    } == {}

    task_handlers = _class(
        ROOT / "mediaflow" / "application" / "project_task_handlers.py",
        "ProjectTaskHandlers",
    )
    assert {node.name for node in task_handlers.body if isinstance(node, ast.FunctionDef)} == {
        "__init__",
        "register_with",
    }

    web_services = _class(
        ROOT / "mediaflow" / "application" / "web_media_service.py",
        "WebMediaServices",
    )
    assert {node.name for node in web_services.body if isinstance(node, ast.FunctionDef)} == {"__init__"}

    web_editor = _class(
        ROOT / "mediaflow" / "desktop" / "controllers" / "web_controller.py",
        "WebController",
    )
    web_editor_methods = {node.name for node in web_editor.body if isinstance(node, ast.FunctionDef)}
    assert web_editor_methods.isdisjoint(
        {
            "timelineItemsData",
            "keyframesData",
            "moveTimelineKeyframe",
            "createBatchVariants",
            "inspectRebind",
            "exportSelected",
        }
    )
    qml_source = "\n".join(
        path.read_text(encoding="utf-8") for path in (ROOT / "mediaflow" / "desktop" / "qml").rglob("*.qml")
    )
    assert "webTimelineController" in qml_source
    assert "webDeliveryController" in qml_source
    for old_member in (
        "webController.timelineItemsData",
        "webController.moveTimelineKeyframe",
        "webController.createBatchVariants",
        "webController.inspectRebind",
        "webController.exportSelected",
    ):
        assert old_member not in qml_source
    real_chain = (
        ROOT / "scripts" / "verify_real_user_chain.py"
    ).read_text(encoding="utf-8")
    for old_member in (
        "controller.web.setKeyframeAtFrame",
        "controller.web.updateThemeValue",
        "controller.web.updateDataValue",
    ):
        assert old_member not in real_chain

    session = _class(
        ROOT / "mediaflow" / "desktop" / "controllers" / "project_controller.py",
        "ProjectSession",
    )
    assert not any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Signal"
        for node in ast.walk(session)
    )
    session_source = (ROOT / "mediaflow" / "desktop" / "controllers" / "project_controller.py").read_text(
        encoding="utf-8"
    )
    for boundary in (
        "SessionEvents",
        "SessionModels",
        "PresentationProjectors",
        "BackgroundRequests",
        "ProjectLifecycle",
        "RuntimeToolOperations",
        "SettingsPersistence",
        "TaskOperations",
        "TimelineAssetOperations",
    ):
        assert boundary in session_source


def test_project_migrations_are_one_continuous_registered_chain() -> None:
    assert [migration.source_version for migration in PROJECT_MIGRATIONS] == list(
        range(1, PROJECT_SCHEMA_VERSION)
    )
    assert [migration.target_version for migration in PROJECT_MIGRATIONS] == list(
        range(2, PROJECT_SCHEMA_VERSION + 1)
    )
    assert len({migration.apply for migration in PROJECT_MIGRATIONS}) == len(PROJECT_MIGRATIONS)
    assert not (ROOT / "mediaflow" / "infrastructure" / "project_schema.py").exists()


def test_automation_contract_models_access_and_execution_share_one_registry() -> None:
    assert OPERATIONS
    assert all(isinstance(definition, OperationDefinition) for definition in OPERATIONS.values())
    assert all(
        definition.execution_mode == "atomic"
        or definition.project_access == "write"
        for definition in OPERATIONS.values()
    )
    assert all(
        issubclass(definition.arguments_model, DomainModel)
        and issubclass(definition.result_model, DomainModel)
        for definition in OPERATIONS.values()
    )
    registry_definitions = []
    for path in (ROOT / "mediaflow" / "automation").glob("*.py"):
        for node in ast.walk(_tree(path)):
            if (
                isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == "OPERATIONS" for target in node.targets)
            ) or (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id == "OPERATIONS"
            ):
                registry_definitions.append(path.name)
    assert registry_definitions == ["operation_registry.py"]
    for consumer in ("contracts.py",):
        assert "operation_registry import OPERATIONS" in (
            ROOT / "mediaflow" / "automation" / consumer
        ).read_text(encoding="utf-8")
    assert "operation_registry import OPERATIONS" in (
        ROOT / "mediaflow" / "service" / "sessions.py"
    ).read_text(encoding="utf-8")
    assert "operation_registry import OPERATIONS" not in (
        ROOT / "mediaflow" / "automation" / "dispatcher.py"
    ).read_text(encoding="utf-8")


def test_automation_context_uses_the_typed_application_boundary() -> None:
    context = _class(
        ROOT / "mediaflow" / "automation" / "operation_context.py",
        "OperationContext",
    )
    fields = {
        node.target.id: ast.unparse(node.annotation)
        for node in context.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
    }
    assert fields["_project"] == "EditorProject | None"
    assert fields["_application"] == "EditorApplication | None"
    assert "Any" not in fields["_project"]
    assert "Any" not in fields["_application"]


def test_ffmpeg_processes_have_one_execution_boundary() -> None:
    infrastructure = ROOT / "mediaflow" / "infrastructure"
    violations: list[str] = []
    for path in infrastructure.rglob("*.py"):
        if path.name in {"ffmpeg_runner.py", "subprocess_runner.py"}:
            continue
        source = path.read_text(encoding="utf-8")
        if re.search(r"\bsubprocess\.(?:run|Popen)\s*\(", source) and re.search(
            r"\b(?:self\.)?paths\.ffmpeg\b", source
        ):
            violations.append(str(path.relative_to(ROOT)))
        if "ffmpeg_progress_command" in source or "FfmpegProgressObserver" in source:
            violations.append(str(path.relative_to(ROOT)))
    assert violations == []
    observer_source = (infrastructure / "process_observers.py").read_text(encoding="utf-8")
    assert "Ffmpeg" not in observer_source


def test_qml_floating_controls_use_the_shared_dark_theme_boundary() -> None:
    qml_root = ROOT / "mediaflow" / "desktop" / "qml"
    components_root = qml_root / "components"
    shared_controls = {
        "AppMenu.qml": "Menu {",
        "AppMenuItem.qml": "MenuItem {",
        "AppMenuSeparator.qml": "MenuSeparator {",
    }
    for filename, base_control in shared_controls.items():
        source = (components_root / filename).read_text(encoding="utf-8")
        assert base_control in source
        assert "Theme." in source

    raw_menu_control = re.compile(r"(?<![A-Za-z])Menu(?:Item|Separator)?\s*\{")
    violations = [
        str(path.relative_to(ROOT))
        for path in qml_root.rglob("*.qml")
        if path.name not in shared_controls and raw_menu_control.search(path.read_text(encoding="utf-8"))
    ]
    assert violations == []

    main = (qml_root / "Main.qml").read_text(encoding="utf-8")
    for palette_role in (
        "light",
        "midlight",
        "mid",
        "dark",
        "shadow",
        "brightText",
        "link",
        "linkVisited",
    ):
        assert f"palette.{palette_role}:" in main


def test_workspace_modes_have_one_presentation_catalog() -> None:
    assert len(WORKSPACE_MODES) == 6
    assert len({mode.key for mode in WORKSPACE_MODES}) == len(WORKSPACE_MODES)
    assert len({mode.panel_object_name for mode in WORKSPACE_MODES}) == len(WORKSPACE_MODES)
    assert "edit" not in {mode.key for mode in WORKSPACE_MODES}
    assert "export" not in WORKSPACE_NAVIGATION_MODE_KEYS

    qml_root = ROOT / "mediaflow" / "desktop" / "qml"
    navigation = (qml_root / "components" / "WorkspaceNavigation.qml").read_text(encoding="utf-8")
    workspace = (qml_root / "Workspace.qml").read_text(encoding="utf-8")
    controller = (ROOT / "mediaflow" / "desktop" / "controllers" / "workspace_controller.py").read_text(
        encoding="utf-8"
    )
    ui_matrix = (ROOT / "scripts" / "verify_ui_matrix.py").read_text(encoding="utf-8")
    qml_smoke = (ROOT / "tests" / "v2" / "desktop" / "test_qml_smoke.py").read_text(encoding="utf-8")

    assert "workspace_mode_catalog" in controller
    assert "workspaceController.workspaceModes" in navigation
    assert "panelObjectName" in workspace
    assert "model: [" not in navigation
    assert 'activeMode === "media" ? 0' not in workspace
    assert "WORKSPACE_MODE_KEYS" in ui_matrix
    assert "WORKSPACE_NAVIGATION_MODE_KEYS" in ui_matrix
    assert "WORKSPACE_MODES" in qml_smoke
    assert "WORKSPACE_NAVIGATION_MODE_KEYS" in qml_smoke
    for mode in WORKSPACE_MODES:
        assert f'key: "{mode.key}"' not in navigation


def test_desktop_brand_and_icons_have_one_component_boundary() -> None:
    qml_root = ROOT / "mediaflow" / "desktop" / "qml"
    components = qml_root / "components"
    assert not (components / "NavIcon.qml").exists()
    for component in ("AppIcon.qml", "AppIconButton.qml", "BrandMark.qml"):
        assert (components / component).is_file()
    assert (ROOT / "mediaflow" / "resources" / "branding" / "mediaflow-mark.svg").is_file()
    assert all("NavIcon" not in path.read_text(encoding="utf-8") for path in qml_root.rglob("*.qml"))
    app_source = (ROOT / "mediaflow" / "desktop" / "app.py").read_text(encoding="utf-8")
    assert "configure_application_icon" in app_source
    assert "mediaflow-mark.svg" in app_source

    icon_source = (components / "AppIcon.qml").read_text(encoding="utf-8")
    icon_button_source = (components / "AppIconButton.qml").read_text(encoding="utf-8")
    theme_source = (qml_root / "Theme.qml").read_text(encoding="utf-8")
    assert "Theme.iconSizeToolbar" in icon_source
    assert "Theme.iconStrokeWidth" in icon_source
    assert "Theme.iconSizeToolbar" in icon_button_source
    assert "Theme.iconButtonSize" in icon_button_source
    assert "control.hovered ? Theme.dangerSoft : Theme.transparent" in icon_button_source
    for token in (
        "iconSizeToolbar: 16",
        "iconButtonSize: 32",
        "iconStrokeWidth: 1.45",
    ):
        assert token in theme_source
    required_icon_names = {
        *(mode.icon for mode in WORKSPACE_MODES),
        "large_thumbnails",
        "minus",
        "transition",
        "transition-zoom",
        "transition-black",
        "eye",
        "eye-off",
        "keyframe",
        "microphone",
        "drag",
    }
    for icon_name in required_icon_names:
        assert f'name === "{icon_name}"' in icon_source
    assert "Unknown AppIcon name:" in icon_source

    home_source = (qml_root / "HomeView.qml").read_text(encoding="utf-8")
    assert home_source.count("SignalMapArtwork {") == 1
    assert "Canvas {" not in home_source
