from __future__ import annotations

import ast
import inspect
import re
import tomllib
from pathlib import Path

from mediaflow.application.desktop_mutation_adapter import plan_desktop_project_mutation
from mediaflow.application.project_mutation_planning import plan_automation_project_mutation
from mediaflow.application.settings_form import SettingsForm
from mediaflow.application.workflow_stage_handlers import workflow_stage_handlers
from mediaflow.automation.operation_registry import OPERATIONS, OperationDefinition
from mediaflow.desktop.presentation_catalogs import (
    WORKSPACE_MODES,
    WORKSPACE_NAVIGATION_MODE_KEYS,
)
from mediaflow.domain.enums import WorkflowStage
from mediaflow.domain.model_base import DomainModel
from mediaflow.domain.settings import ServiceSettings
from mediaflow.infrastructure.project_migrations import PROJECT_MIGRATIONS
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.project_repository_component import ProjectRepositoryComponent
from mediaflow.infrastructure.project_schema_definition import PROJECT_SCHEMA_VERSION
from mediaflow.service.commands import DESKTOP_COMMANDS, DesktopCommand, desktop_command
from mediaflow.service.remote_project import RemoteEditorProject
from mediaflow.service.remote_timeline import RemoteTimelineEditor

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


def test_mypy_covers_the_complete_mediaflow_package() -> None:
    configuration = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert configuration["tool"]["mypy"]["files"] == ["mediaflow"]
    strict_flags = {
        "disallow_any_generics",
        "disallow_subclassing_any",
        "disallow_untyped_calls",
        "disallow_untyped_defs",
        "disallow_incomplete_defs",
        "disallow_untyped_decorators",
        "warn_unused_ignores",
        "warn_return_any",
        "strict_equality",
        "extra_checks",
    }
    strict_modules = {
        module
        for override in configuration["tool"]["mypy"]["overrides"]
        if all(override.get(flag) is True for flag in strict_flags)
        and override.get("implicit_reexport") is False
        for module in ([override["module"]] if isinstance(override["module"], str) else override["module"])
    }
    assert {
        "mediaflow.infrastructure.project_repository",
        "mediaflow.editor_project_delivery_commands",
        "mediaflow.editor_project_document_commands",
        "mediaflow.editor_project_media_commands",
        "mediaflow.editor_project_script_timeline_commands",
        "mediaflow.editor_project_task_commands",
        "mediaflow.editor_project_web_commands",
        "mediaflow.service.client",
        "mediaflow.service.commands",
        "mediaflow.service.execution",
        "mediaflow.service.remote_project",
        "mediaflow.service.request_dispatcher",
        "mediaflow.service.session_registry",
    }.issubset(strict_modules)


def test_runtime_status_messages_use_the_registered_translation_boundary() -> None:
    presentation_path = ROOT / "mediaflow" / "desktop" / "presentation_messages.py"
    status_function = next(
        node
        for node in _tree(presentation_path).body
        if isinstance(node, ast.FunctionDef) and node.name == "status_message"
    )
    registered = {
        key.value
        for node in ast.walk(status_function)
        if isinstance(node, ast.Dict)
        for key in node.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }
    used: set[str] = set()
    dynamic_bridges: set[tuple[str, str]] = set()
    source_positions = {
        "_set_status": 0,
        "_finish_subtitle_edit": 1,
        "_finish_sequence_in_out_edit": 0,
        "_finish_subtitle_placement_edit": 1,
        "_after_visual_effect_change": 0,
        "commit": 1,
        "commit_pair": 2,
        "remember_default_project_directory": 1,
    }
    mediaflow_root = ROOT / "mediaflow"
    for path in mediaflow_root.rglob("*.py"):
        if path == presentation_path:
            continue
        tree = _tree(path)
        for function in (
            node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ):
            for call in (node for node in ast.walk(function) if isinstance(node, ast.Call)):
                name = call.func.attr if isinstance(call.func, ast.Attribute) else ""
                position = source_positions.get(name)
                if position is not None and len(call.args) > position:
                    source = call.args[position]
                    if isinstance(source, ast.Constant) and isinstance(source.value, str):
                        if source.value:
                            used.add(source.value)
                            placeholders = {int(value) for value in re.findall(r"%(\d+)", source.value)}
                            supplied = len(call.args) - position - 1
                            assert placeholders == set(range(1, supplied + 1))
                    else:
                        dynamic_bridges.add((path.relative_to(ROOT).as_posix(), function.name))
                if isinstance(call.func, ast.Name) and call.func.id == "WorkflowUpdate":
                    keyword = next(
                        (item for item in call.keywords if item.arg == "status_source"),
                        None,
                    )
                    if keyword is not None:
                        if isinstance(keyword.value, ast.Constant):
                            assert isinstance(keyword.value.value, str)
                            used.add(keyword.value.value)
                            arguments = next(
                                (item for item in call.keywords if item.arg == "status_arguments"),
                                None,
                            )
                            supplied = (
                                len(arguments.value.elts)
                                if arguments is not None and isinstance(arguments.value, ast.Tuple)
                                else 0
                            )
                            placeholders = {
                                int(value) for value in re.findall(r"%(\d+)", keyword.value.value)
                            }
                            assert placeholders == set(range(1, supplied + 1))
                        else:
                            dynamic_bridges.add((path.relative_to(ROOT).as_posix(), function.name))

    assert dynamic_bridges == {
        ("mediaflow/project_task_settlement.py", "from_dict"),
        ("mediaflow/desktop/controllers/project_controller.py", "_apply_workflow_update"),
        ("mediaflow/desktop/controllers/project_controller.py", "_finish_sequence_in_out_edit"),
        ("mediaflow/desktop/controllers/project_controller.py", "_finish_subtitle_edit"),
        (
            "mediaflow/desktop/controllers/subtitle_placement_controller.py",
            "_finish_subtitle_placement_edit",
        ),
        (
            "mediaflow/desktop/controllers/timeline_effects_controller.py",
            "_after_visual_effect_change",
        ),
        ("mediaflow/desktop/coordinators/settings_persistence.py", "commit"),
        ("mediaflow/desktop/coordinators/settings_persistence.py", "commit_pair"),
        (
            "mediaflow/desktop/coordinators/settings_persistence.py",
            "remember_default_project_directory",
        ),
        ("mediaflow/application/workflow_models.py", "merge"),
    }
    assert used == registered


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
        *[path for path in (ROOT / "scripts").glob("*.py") if path.name != "migrate_settings.py"],
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


def test_automation_operation_handlers_do_not_construct_infrastructure() -> None:
    violations: list[str] = []
    for path in (ROOT / "mediaflow" / "automation").glob("*_operations.py"):
        for module in _imported_modules(path):
            if module.startswith("mediaflow.infrastructure"):
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


def test_task_service_is_a_composed_facade_without_polling_or_worker_ownership() -> None:
    service_path = ROOT / "mediaflow" / "application" / "task_service.py"
    service = _class(service_path, "TaskService")
    methods = {node.name for node in service.body if isinstance(node, ast.FunctionDef)}
    assert methods.isdisjoint(
        {
            "_run",
            "_heartbeat_loop",
            "_persist_owned",
            "_persist_completion",
            "_persist_settlement",
            "_persist_failure_or_stop",
        }
    )
    assert service.end_lineno - service.lineno < 270
    assert {
        "task_execution.py",
        "task_execution_types.py",
        "task_lifecycle.py",
        "task_persistence.py",
        "task_waiter.py",
    } <= {path.name for path in (ROOT / "mediaflow" / "application").glob("task_*.py")}
    wait_method = next(
        node for node in service.body if isinstance(node, ast.FunctionDef) and node.name == "wait"
    )
    assert "sleep" not in ast.unparse(wait_method)


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
        "coalesced_revision",
        "enlist_transaction_publication",
        "transaction",
        "task_transaction",
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
        "projects",
        "sequences",
        "assets",
        "timeline",
        "frame_clock",
        "audio",
        "subtitles",
        "highlights",
        "web",
        "records",
        "operations",
        "observations",
    } <= assigned_components
    for component_name in (
        "ProjectMetadataRepository",
        "SequenceCatalogRepository",
        "AssetCatalogRepository",
        "TimelineRepository",
        "AudioRepository",
        "SubtitleRepository",
        "HighlightRepository",
        "WebMediaRepository",
        "ProjectRecordsRepository",
        "ProjectOperationRepository",
        "ProjectObservationRepository",
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

    component_files = []
    for path in (ROOT / "mediaflow" / "infrastructure").glob("*_repository.py"):
        tree = _tree(path)
        if any(
            isinstance(node, ast.ClassDef)
            and any(
                isinstance(base, ast.Name) and base.id == "ProjectRepositoryComponent" for base in node.bases
            )
            for node in tree.body
        ):
            component_files.append(path)
    assert component_files
    for path in component_files:
        source = path.read_text(encoding="utf-8")
        assert "self._owner" not in source
        assert re.search(r"_relations\.[A-Za-z_]+\._[A-Za-z_]", source) is None

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


def test_desktop_session_has_one_mutable_state_tree() -> None:
    controller = ROOT / "mediaflow" / "desktop" / "controllers" / "project_controller.py"
    session = _class(controller, "ProjectSession")
    initializer = next(
        node for node in session.body if isinstance(node, ast.FunctionDef) and node.name == "__init__"
    )
    assignments = {
        target.attr
        for node in ast.walk(initializer)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        if isinstance(target, ast.Attribute)
        and isinstance(target.value, ast.Name)
        and target.value.id == "self"
    }
    old_members = {
        "binding",
        "selection",
        "task_state",
        "presentation",
        "asset_state",
        "download_state",
        "runtime_state",
        "requests",
        "service_settings",
        "desktop_settings",
    }
    assert "state" in assignments
    assert assignments.isdisjoint(old_members)
    violations = []
    for path in _python_files():
        source = path.read_text(encoding="utf-8")
        for member in old_members:
            if re.search(rf"\b(?:_?session|session)\.{member}\b", source):
                violations.append(f"{path.relative_to(ROOT)} reads legacy session.{member}")
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
    for component in (
        "PreviewViewport",
        "WorkspaceNavigation",
        "WorkspaceChrome",
    ):
        assert component in workspace
        assert (qml_root / "components" / f"{component}.qml").is_file()
    status_overlays = qml_root / "WorkspaceStatusOverlays.qml"
    workspace_chrome = (qml_root / "components" / "WorkspaceChrome.qml").read_text(encoding="utf-8")
    assert "WorkspaceStatusOverlays" in workspace_chrome
    assert "WorkspaceShortcuts" in workspace_chrome
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
    assert "项目参数" in inspector_source
    assert "草稿参数" not in inspector_source
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

    shortcuts_source = (qml_root / "components" / "WorkspaceShortcuts.qml").read_text(
        encoding="utf-8"
    )
    assert 'sequence: "Ctrl+S"' not in shortcuts_source
    assert "saveProject" not in shortcuts_source

    hardcoded_dense_text = re.compile(r"font\.pixelSize\s*:\s*(?:9|10)\b")
    dense_text_violations = [
        path.relative_to(qml_root).as_posix()
        for path in qml_root.rglob("*.qml")
        if hardcoded_dense_text.search(path.read_text(encoding="utf-8"))
    ]
    assert dense_text_violations == []

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
    subtitle_controllers = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "mediaflow" / "desktop" / "controllers").glob("subtitle_*_controller.py")
    )
    assert "subtitleWordsModel" not in subtitle_controllers
    assert "rippleDeleteSelectedWords" not in subtitle_controllers
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
    task_presentation = (ROOT / "mediaflow" / "desktop" / "presentation_tasks.py").read_text(encoding="utf-8")
    assert "准备流畅预览" in task_presentation
    assert "准备音频波形" in task_presentation
    assert '"生成代理"' not in task_presentation
    assert '"生成波形"' not in task_presentation


def test_desktop_qml_uses_one_root_and_focused_workspace_boundaries() -> None:
    app_source = (ROOT / "mediaflow" / "desktop" / "app.py").read_text(encoding="utf-8")
    assert set(re.findall(r'setContextProperty\(\s*"([^"]+)"', app_source)) == {
        "applicationMonospaceFontFamily",
        "mediaflow",
    }

    qml_source = "\n".join(
        path.read_text(encoding="utf-8") for path in (ROOT / "mediaflow" / "desktop" / "qml").rglob("*.qml")
    )
    assert "workspaceController" not in qml_source
    for controller in (
        "workspaceViewController",
        "workspaceProjectController",
        "workspaceSequenceController",
        "workspaceWorkflowController",
        "workspacePlaybackController",
        "settingsController",
        "mediaController",
        "timelineViewController",
        "taskController",
        "webController",
    ):
        assert not re.search(rf"(?<!mediaflow\.)\b{controller}\b", qml_source)

    main_qml = (ROOT / "mediaflow" / "desktop" / "qml" / "Main.qml").read_text(encoding="utf-8")
    assert "target: mediaflow.downloadController" in main_qml
    assert "function onDownloadPlanChanged()" in main_qml
    web_editor_canvas = (ROOT / "mediaflow" / "desktop" / "qml" / "WebEditorCanvas.qml").read_text(
        encoding="utf-8"
    )
    assert 'WebChannel.id: "mediaflowWebController"' in web_editor_canvas
    assert "channel.objects.mediaflowWebController" in web_editor_canvas
    assert "channel.objects.mediaflow.webController" not in web_editor_canvas

    view = _class(
        ROOT / "mediaflow" / "desktop" / "controllers" / "workspace_controller.py",
        "WorkspaceViewController",
    )
    view_methods = {node.name for node in view.body if isinstance(node, ast.FunctionDef)}
    assert view_methods.isdisjoint(
        {
            "createProject",
            "openProject",
            "closeProject",
            "selectSequence",
            "updateSequenceProfile",
            "continueWorkflow",
            "shutdown",
        }
    )
    facet_source = (ROOT / "mediaflow" / "desktop" / "controllers" / "controller_facet.py").read_text(
        encoding="utf-8"
    )
    assert "ProjectSession" not in facet_source

    direct_event_emits = []
    for path in (ROOT / "mediaflow" / "desktop").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if re.search(r"\.events\.[A-Za-z_][A-Za-z0-9_]*\.emit\(", source):
            direct_event_emits.append(str(path.relative_to(ROOT)))
    assert direct_event_emits == []


def test_settings_dialog_uses_the_typed_backend_draft() -> None:
    qml_root = ROOT / "mediaflow" / "desktop" / "qml"
    settings_files = (
        qml_root / "SettingsDialog.qml",
        qml_root / "SettingsGeneralPage.qml",
        qml_root / "SettingsMediaPage.qml",
        qml_root / "SettingsEditorPage.qml",
        qml_root / "SettingsAiPage.qml",
    )
    qml_source = "\n".join(path.read_text(encoding="utf-8") for path in settings_files)
    controller_source = (ROOT / "mediaflow" / "desktop" / "controllers" / "settings_controller.py").read_text(
        encoding="utf-8"
    )
    for removed_qml_path in (
        "settingsPayload",
        "settingsBaseline",
        "settingsSaveTimer",
        "JSON.stringify",
        "saveSettings(",
    ):
        assert removed_qml_path not in qml_source
    assert "def saveSettings(" not in controller_source
    assert "SettingsDraft(" in controller_source
    draft_fields = set(re.findall(r'updateDraft\(\s*"([^"]+)"', qml_source))
    form_fields = {field.alias or name for name, field in SettingsForm.model_fields.items()}
    assert draft_fields == form_fields
    ai_source = settings_files[-1].read_text(encoding="utf-8")
    assert "required property bool providerEnabled" in ai_source
    assert "required property bool enabled" not in ai_source
    assert "visible: !providerEnabled" in ai_source
    assert "model.enabled" not in ai_source
    for model_id in ("deepseek-chat", "deepseek-reasoner"):
        assert model_id not in ai_source


def test_audited_large_components_keep_semantic_boundaries() -> None:
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
    real_chain = (ROOT / "scripts" / "verify_real_user_chain.py").read_text(encoding="utf-8")
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
        "DesktopSessionState",
    ):
        assert boundary in session_source

    timeline_editor = _class(
        ROOT / "mediaflow" / "application" / "timeline_editor.py",
        "TimelineEditor",
    )
    timeline_methods = {node.name for node in timeline_editor.body if isinstance(node, ast.FunctionDef)}
    assert timeline_methods.isdisjoint(
        {"_apply_history_action", "_apply_change", "_persist_change", "_canonical_state"}
    )
    web_editor = _class(
        ROOT / "mediaflow" / "application" / "web_clip_editing_service.py",
        "WebClipEditingService",
    )
    web_methods = {node.name for node in web_editor.body if isinstance(node, ast.FunctionDef)}
    assert web_methods.isdisjoint({"validated_field_value", "validate_data_value", "validate_constraint"})


def test_audited_orchestrators_delegate_to_their_focused_components() -> None:
    expected_calls = {
        (ROOT / "mediaflow" / "domain" / "web_manifest.py", "valid_contract"): {
            "validate_manifest_contract",
        },
        (
            ROOT / "mediaflow" / "application" / "web_edit_document_builder.py",
            "build_web_edit_document",
        ): {"_layer_fields", "_parameter_fields", "_theme_fields", "_data_fields"},
        (
            ROOT / "mediaflow" / "application" / "web_rebind_service.py",
            "_rebind_conflicts",
        ): {"WebRebindConflictDetector", "detect"},
        (ROOT / "mediaflow" / "service" / "server.py", "_dispatch"): {"dispatch"},
        (ROOT / "mediaflow" / "mcp_server.py", "build_mcp_server"): {
            "_ContractMCPServer",
            "register_mediaflow_tools",
        },
        (
            ROOT / "mediaflow" / "infrastructure" / "proxy_service.py",
            "_prepare_outputs",
        ): {
            "content_addressed_child_path",
            "output_set_transaction",
            "build_proxy_command",
            "_encode_proxy_output",
        },
    }
    for (path, function_name), required_calls in expected_calls.items():
        functions = [
            node
            for node in ast.walk(_tree(path))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name
        ]
        assert len(functions) == 1
        function = functions[0]
        actual_calls = {
            node.func.id if isinstance(node.func, ast.Name) else node.func.attr
            for node in ast.walk(function)
            if isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute))
        }
        assert required_calls <= actual_calls


def test_project_migrations_are_one_continuous_registered_chain() -> None:
    assert [migration.source_version for migration in PROJECT_MIGRATIONS] == list(
        range(1, PROJECT_SCHEMA_VERSION)
    )
    assert [migration.target_version for migration in PROJECT_MIGRATIONS] == list(
        range(2, PROJECT_SCHEMA_VERSION + 1)
    )
    implementations = [migration.apply or migration.apply_with_runtime for migration in PROJECT_MIGRATIONS]
    assert all(implementation is not None for implementation in implementations)
    assert len(set(implementations)) == len(PROJECT_MIGRATIONS)
    assert not (ROOT / "mediaflow" / "infrastructure" / "project_schema.py").exists()


def test_automation_contract_models_access_and_execution_share_one_registry() -> None:
    assert OPERATIONS
    assert all(isinstance(definition, OperationDefinition) for definition in OPERATIONS.values())
    assert all(
        definition.execution_mode == "atomic" or definition.project_access == "write"
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
        ROOT / "mediaflow" / "service" / "automation_sessions.py"
    ).read_text(encoding="utf-8")
    assert "operation_registry import OPERATIONS" not in (
        ROOT / "mediaflow" / "automation" / "dispatcher.py"
    ).read_text(encoding="utf-8")


def test_desktop_transport_uses_one_typed_command_registry() -> None:
    assert DESKTOP_COMMANDS
    assert all(isinstance(value, DesktopCommand) for value in DESKTOP_COMMANDS.values())
    assert all(key == (value.target, value.name) for key, value in DESKTOP_COMMANDS.items())
    assert all(desktop_command(*key) is value for key, value in DESKTOP_COMMANDS.items())
    assert desktop_command("project", "cancel_task").workload == "control"
    assert desktop_command("project", "pause_task").workload == "control"
    assert desktop_command("project", "import_asset").workload == "project"
    assert len({value.schema_id for value in DESKTOP_COMMANDS.values()}) == len(DESKTOP_COMMANDS)
    assert all(value.request_model is not None for value in DESKTOP_COMMANDS.values())
    assert all(value.result_model is not None for value in DESKTOP_COMMANDS.values())
    assert len({value.request_model for value in DESKTOP_COMMANDS.values()}) == len(DESKTOP_COMMANDS)
    assert len({value.result_model for value in DESKTOP_COMMANDS.values()}) == len(DESKTOP_COMMANDS)
    assert all(
        "args" not in value.request_model.model_fields and "kwargs" not in value.request_model.model_fields
        for value in DESKTOP_COMMANDS.values()
    )
    assert all(
        value.result_model.model_fields["value"].annotation is not object
        for value in DESKTOP_COMMANDS.values()
    )
    assert all(
        not any(
            parameter.kind in {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}
            for parameter in value.signature.parameters.values()
        )
        for value in DESKTOP_COMMANDS.values()
    )
    settings = ServiceSettings()
    settings_request = desktop_command("project", "update_settings").validate_request(
        [settings],
        {},
    )
    assert (
        desktop_command("project", "update_settings").request_arguments(settings_request)["settings"]
        is settings
    )
    find_request = desktop_command("project", "find_subtitle_matches").validate_request(
        ["document", "term"],
        {},
    )
    assert desktop_command("project", "find_subtitle_matches").request_arguments(find_request) == {
        "document_id": "document",
        "search": "term",
    }
    copy_request = desktop_command("timeline", "copy_clip").validate_request(
        ["clip"],
        {"timeline_start": 10, "snap_targets": (1, 9, 20)},
    )
    assert desktop_command("timeline", "copy_clip").request_arguments(copy_request)["snap_targets"] == [
        1,
        9,
        20,
    ]
    assert all(
        value.mutation_plan(sequence_id="sequence", args=[], kwargs={}).change_scopes
        for value in DESKTOP_COMMANDS.values()
        if value.access == "write"
    )

    service_root = ROOT / "mediaflow" / "service"
    sources = "\n".join(path.read_text(encoding="utf-8") for path in service_root.glob("*.py"))
    for removed in (
        "PROJECT_READ_COMMANDS",
        "PROJECT_RUNTIME_COMMANDS",
        "PROJECT_WRITE_COMMANDS",
    ):
        assert removed not in sources
    for removed_helper in (
        "project_command",
        "timeline_command",
        "command_mutation_plan",
    ):
        assert not re.search(rf"\b{removed_helper}\s*\(", sources)
    commands_source = (service_root / "commands.py").read_text(encoding="utf-8")
    assert "/desktop/" not in commands_source
    mutation_source = (ROOT / "mediaflow" / "application" / "project_mutation_planning.py").read_text(
        encoding="utf-8"
    )
    assert "Project write operation has no mutation boundary" in mutation_source
    assert "plan_desktop_project_mutation" in commands_source

    assert "__getattr__" not in RemoteEditorProject.__dict__
    assert "__getattr__" not in RemoteTimelineEditor.__dict__
    for (target, name), definition in DESKTOP_COMMANDS.items():
        remote_type = RemoteEditorProject if target == "project" else RemoteTimelineEditor
        assert hasattr(remote_type, name), definition.schema_id

    codec_path = service_root / "codec.py"
    assert "importlib" not in _imported_modules(codec_path)
    assert "_load_mediaflow_class" not in codec_path.read_text(encoding="utf-8")

    assert desktop_command("project", "import_web_package").mutation_plan(
        sequence_id="sequence",
        args=[],
        kwargs={},
    ).change_scopes == ["/assets"]
    assert desktop_command("project", "create_web_variants").mutation_plan(
        sequence_id="sequence",
        args=[],
        kwargs={},
    ).change_scopes == ["/sequences"]
    assert desktop_command("project", "set_workflow_mode").mutation_plan(
        sequence_id="sequence",
        args=[],
        kwargs={},
    ).change_scopes == ["/project/workflow_auto_continue"]
    assert desktop_command("project", "create_asset_bin").mutation_plan(
        sequence_id="sequence",
        args=[],
        kwargs={},
    ).change_scopes == ["/asset-bins"]
    assert desktop_command("project", "update_web_data").mutation_plan(
        sequence_id="sequence",
        args=["sequence", "clip"],
        kwargs={"values": {"title": "value"}},
    ).change_scopes == ["/web/clips/clip"]
    assert desktop_command("timeline", "move_clip").mutation_plan(
        sequence_id="sequence",
        args=["clip"],
        kwargs={"timeline_start": 10},
    ).change_scopes == [
        "/sequences/sequence/clips/clip",
        "/sequences/sequence/tracks",
        "/sequences/sequence/transitions",
    ]
    assert desktop_command("timeline", "set_sequence_profile").mutation_plan(
        sequence_id="sequence",
        args=[],
        kwargs={},
    ).change_scopes == [
        "/assets",
        "/highlights",
        "/sequences/sequence",
        "/subtitles",
    ]


def test_desktop_and_automation_share_one_project_mutation_planner() -> None:
    planner_path = ROOT / "mediaflow" / "application" / "project_mutation_planning.py"
    planner = _tree(planner_path)
    assert [
        node.name
        for node in planner.body
        if isinstance(node, ast.FunctionDef) and node.name == "_plan_project_mutation"
    ] == ["_plan_project_mutation"]
    for consumer in (
        ROOT / "mediaflow" / "automation" / "operation_registry.py",
        ROOT / "mediaflow" / "service" / "commands.py",
    ):
        source = consumer.read_text(encoding="utf-8")
        assert "_operation_change_scopes" not in source
        assert "_command_change_scopes" not in source

    pairs = (
        (
            "timeline.clip.move",
            {"sequence_id": "sequence", "clip_id": "clip", "track_id": "track"},
            "timeline",
            "move_clip",
            ["clip"],
            {"timeline_start": 10, "track_id": "track"},
        ),
        (
            "transcript.edit.apply",
            {"sequence_id": "sequence"},
            "project",
            "apply_transcript_edit",
            [],
            {},
        ),
        (
            "web.asset.rebind.commit",
            {"asset_id": "asset"},
            "project",
            "commit_web_asset_rebind",
            ["asset"],
            {},
        ),
        (
            "audio.effect.remove",
            {"effect_id": "effect"},
            "project",
            "remove_audio_effect",
            ["effect"],
            {},
        ),
    )
    for operation, arguments, target, command, args, kwargs in pairs:
        automation = plan_automation_project_mutation(
            operation,
            arguments,
            default_sequence_id="sequence",
        )
        desktop = plan_desktop_project_mutation(
            target,
            command,
            sequence_id="sequence",
            args=args,
            kwargs=kwargs,
        )
        assert desktop == automation


def test_project_events_can_only_store_actual_change_actions() -> None:
    collaboration_source = (ROOT / "mediaflow" / "project_collaboration.py").read_text(encoding="utf-8")
    event_source = (ROOT / "mediaflow" / "infrastructure" / "project_event_repository.py").read_text(
        encoding="utf-8"
    )
    domain_source = (ROOT / "mediaflow" / "domain" / "collaboration.py").read_text(encoding="utf-8")
    assert 'action="invoke"' not in collaboration_source
    assert '"invoke"' not in domain_source
    assert "observations.capture" in collaboration_source
    assert "before.changes_to" in event_source
    assert "Project mutation scope has no observable state reader" in (
        ROOT / "mediaflow" / "infrastructure" / "project_observation_repository.py"
    ).read_text(encoding="utf-8")


def test_automation_context_uses_the_typed_application_boundary() -> None:
    context = _class(
        ROOT / "mediaflow" / "automation" / "operation_context.py",
        "OperationContext",
    )
    fields = {
        node.target.id: ast.unparse(node.annotation)
        for node in context.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    assert fields["_project"] == "EditorProject | None"
    assert fields["_application"] == "EditorApplication | None"
    assert "Any" not in fields["_project"]
    assert "Any" not in fields["_application"]


def test_ffmpeg_and_ffprobe_processes_have_one_execution_boundary() -> None:
    infrastructure = ROOT / "mediaflow" / "infrastructure"
    violations: list[str] = []
    for path in infrastructure.rglob("*.py"):
        if path.name in {"ffmpeg_runner.py", "ffprobe_runner.py", "subprocess_runner.py"}:
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}

        def runner_owns_reference(
            node: ast.Attribute,
            parent_map: dict[ast.AST, ast.AST] = parents,
        ) -> bool:
            current: ast.AST | None = node
            while current is not None and not isinstance(current, ast.stmt):
                if (
                    isinstance(current, ast.Call)
                    and isinstance(current.func, ast.Name)
                    and current.func.id in {"FfmpegRunner", "FfprobeRunner"}
                ):
                    return True
                current = parent_map.get(current)
            return False

        imports_process_runner = any(
            isinstance(node, ast.ImportFrom)
            and node.module == "mediaflow.infrastructure.subprocess_runner"
            and any(alias.name in {"run_cancellable", "run_cancellable_streaming"} for alias in node.names)
            for node in ast.walk(tree)
        )
        references_media_executable = any(
            isinstance(node, ast.Attribute)
            and node.attr in {"ffmpeg", "ffprobe"}
            and (
                (isinstance(node.value, ast.Name) and node.value.id == "paths")
                or (isinstance(node.value, ast.Attribute) and node.value.attr == "paths")
            )
            and not runner_owns_reference(node)
            for node in ast.walk(tree)
        )
        starts_subprocess = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "subprocess"
            and node.func.attr in {"run", "Popen"}
            for node in ast.walk(tree)
        )
        if references_media_executable and (imports_process_runner or starts_subprocess):
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
    assert len(WORKSPACE_MODES) == 7
    assert len({mode.key for mode in WORKSPACE_MODES}) == len(WORKSPACE_MODES)
    assert len({mode.panel_object_name for mode in WORKSPACE_MODES}) == len(WORKSPACE_MODES)
    assert "edit" not in {mode.key for mode in WORKSPACE_MODES}
    assert "export" in WORKSPACE_NAVIGATION_MODE_KEYS
    mode_labels = {mode.key: mode.label_source for mode in WORKSPACE_MODES}
    assert mode_labels["transcript"] == "字幕"
    assert mode_labels["highlight"] == "高光"
    assert mode_labels["resources"] == "资源"

    qml_root = ROOT / "mediaflow" / "desktop" / "qml"
    navigation = (qml_root / "components" / "WorkspaceNavigation.qml").read_text(encoding="utf-8")
    workspace = (qml_root / "Workspace.qml").read_text(encoding="utf-8")
    controller = (ROOT / "mediaflow" / "desktop" / "controllers" / "workspace_controller.py").read_text(
        encoding="utf-8"
    )
    ui_matrix = (ROOT / "scripts" / "verify_ui_matrix.py").read_text(encoding="utf-8")
    qml_smoke = (ROOT / "tests" / "v2" / "desktop" / "test_qml_smoke.py").read_text(encoding="utf-8")

    assert "workspace_mode_catalog" in controller
    assert "workspaceViewController.workspaceModes" in navigation
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
