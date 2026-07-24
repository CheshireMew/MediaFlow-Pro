from __future__ import annotations

import ast
import re
from pathlib import Path

from mediaflow.application.workflow_stage_handlers import workflow_stage_handlers
from mediaflow.domain.enums import WorkflowStage
from mediaflow.infrastructure.project_repository import ProjectRepository

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
    repository_bases = {base.__name__ for base in ProjectRepository.__mro__}
    assert {
        "ProjectCatalogRepository",
        "TimelineRepository",
        "AudioRepository",
        "SubtitleRepository",
        "HighlightRepository",
    } <= repository_bases

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
    assert publication_methods == {"__init__", "write_document_srt"}
    assert "write_document_srt" not in acquisition_methods | editing_methods


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
    for component in ("PreviewViewport", "WorkspaceNavigation"):
        assert component in workspace
        assert (qml_root / "components" / f"{component}.qml").is_file()
    assert "WorkflowBanner" not in workspace
    assert "InspectorPanel" not in workspace
    assert not (qml_root / "components" / "WorkflowBanner.qml").exists()
    assert not (qml_root / "InspectorPanel.qml").exists()
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
    automation_contract = (
        ROOT / "mediaflow" / "automation" / "contracts.py"
    ).read_text(encoding="utf-8")
    for operation in (
        "transcript.get",
        "transcript.edit.preview",
        "transcript.edit.apply",
    ):
        assert operation in automation_contract
    task_commands = (
        ROOT / "mediaflow" / "domain" / "task_commands.py"
    ).read_text(encoding="utf-8")
    assert "TranscribeSequenceCommand" in task_commands
    assert "TranscribeAssetCommand" not in task_commands
    assert "TranscribeRegionCommand" not in task_commands
    assert not (
        ROOT / "mediaflow" / "infrastructure" / "audio_region_extractor.py"
    ).exists()
    timeline = (qml_root / "TimelineView.qml").read_text(encoding="utf-8")
    assert "SequenceToolbar" in timeline
    assert 'objectName: "timelineRuler"' in timeline
    assert "rulerMajorStepFrames" in timeline
    assert "rulerSeconds" not in timeline
    assert 'index + "s"' not in timeline
    assert 'objectName: "trackControlsButton"' not in timeline
    assert "visible: tracksRepeater.count > 0" in timeline
    media_panel = (qml_root / "MediaPanel.qml").read_text(encoding="utf-8")
    assert "FileDialog.OpenFiles" in media_panel
    assert "selectedFiles" in media_panel
    assert "addAssetAtPlayhead" in media_panel
    assert "生成代理" not in media_panel
    assert "生成波形" not in media_panel
    assert "添加到时间线" not in media_panel
    assert (qml_root / "components" / "SequenceToolbar.qml").is_file()
    preview_viewport = (qml_root / "components" / "PreviewViewport.qml").read_text(
        encoding="utf-8"
    )
    assert "playPreviewFrom" in preview_viewport
    assert "pendingPlaybackMode" in preview_viewport
    assert "property int sequenceIn" not in preview_viewport
    assert "property int sequenceOut" not in preview_viewport
    assert "previewGraphRevision" not in workspace
    window_title_bar = (
        qml_root / "components" / "WindowTitleBar.qml"
    ).read_text(encoding="utf-8")
    workspace_header = (
        qml_root / "components" / "WorkspaceHeader.qml"
    ).read_text(encoding="utf-8")
    assert "WorkspaceHeader" in window_title_bar
    assert "workspaceController.projectName" not in workspace_header
    assert "toggleTaskDrawer" not in workspace_header
    task_controller = (
        ROOT / "mediaflow" / "desktop" / "controllers" / "task_controller.py"
    ).read_text(encoding="utf-8")
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
    presentation = (
        ROOT / "mediaflow" / "desktop" / "presentation_catalogs.py"
    ).read_text(encoding="utf-8")
    assert "准备流畅预览" in presentation
    assert "准备音频波形" in presentation
    assert '"生成代理"' not in presentation
    assert '"生成波形"' not in presentation


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
        if path.name not in shared_controls
        and raw_menu_control.search(path.read_text(encoding="utf-8"))
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
