from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_desktop_components_do_not_reach_through_a_project_session() -> None:
    for folder in ("presenters", "coordinators"):
        for path in (ROOT / "mediaflow" / "desktop" / folder).glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute):
                    assert not ast.unparse(node).startswith("self._session"), path
                if isinstance(node, ast.ImportFrom):
                    assert node.module != "mediaflow.desktop.controllers.project_controller", path
    contexts = _source("mediaflow/desktop/component_contexts.py")
    assert "ProjectSession" not in contexts
    assert "PresentationProjectors" not in contexts
    assert "DesktopEditorApplication" not in contexts


def test_timeline_projection_and_preview_have_distinct_owners() -> None:
    timeline = _source("mediaflow/desktop/presenters/timeline_projector.py")
    preview = _source("mediaflow/desktop/coordinators/preview_graph.py")
    subtitles = _source("mediaflow/desktop/presenters/timeline_subtitle_projector.py")
    assert "QTimer" not in timeline
    assert "submit_background" not in timeline
    assert "RenderWebClipCommand" not in timeline
    assert "def refresh_preview_subtitles" not in timeline
    assert "def compile(" in preview
    assert "def refresh_preview_subtitles" in subtitles
    assert "project_clip_row(" in timeline
    assert '"scaleX":' not in timeline
    assert "project_subtitle_placement_row(" in subtitles
    assert '"hasOverride":' not in subtitles


def test_remote_timeline_cache_applies_commit_values_without_command_rules() -> None:
    cache = _source("mediaflow/service/remote_timeline_cache.py")
    assert "TimelineMergePolicy.merge" in cache
    for command in ("copy_clip", "split_clip", "delete_clips", "trim_clip", "ripple"):
        assert command not in cache
    service = _source("mediaflow/service/desktop_sessions.py")
    assert "TimelineStateChange.between" in service
    assert '"timeline_change": stored["timeline_change"]' in service


def test_native_keyframe_editing_uses_one_sequence_algorithm() -> None:
    visual = _source("mediaflow/application/timeline_visual_editing.py")
    assert visual.count("updated = move_timeline_keyframe(") == 3
    assert visual.count("updated = retime_timeline_keyframes(") == 3
    assert "round(anchor_offset" not in visual


def test_format_readers_translation_and_storage_have_focused_dependencies() -> None:
    loader = _source("mediaflow/infrastructure/interchange_timeline_loader.py")
    assert "FcpxmlTimelineReader().load" in loader
    assert "EdlTimelineReader().load" in loader
    assert "ET.parse" not in loader
    service = _source("mediaflow/application/translation_service.py")
    assert "TranslationBatchExecutor" in service
    assert "SubtitleTranslationStrategy" in service
    assert "ThreadPoolExecutor" not in service
    assert "complete_json" not in service
    budget = _source("mediaflow/infrastructure/storage_budget.py")
    for removed in ("os.scandir", "ProcessFileLock", "psutil.Process", "atomic_write_text"):
        assert removed not in budget


def test_qml_audio_and_video_share_one_overview_subscription() -> None:
    qml = "mediaflow/desktop/qml/"
    canvas = _source(qml + "TimelineCanvas.qml")
    assert canvas.count("clipRows: sharedOverview.rows") == 2
    for layer in ("TimelineClipLayer.qml", "TimelineAudioClipLayer.qml"):
        source = _source(qml + layer)
        assert "function patchOverview" not in source
        assert "onSourceItemsPatched" not in source
    overview = _source(qml + "components/TimelineOverview.qml")
    assert "onSourceItemsPatched" in overview
    assert "function patch(" in overview


def test_timeline_commands_are_composed_without_inherited_editing_state() -> None:
    editor = ast.parse(_source("mediaflow/application/timeline_editor.py"))
    owner = next(node for node in editor.body if isinstance(node, ast.ClassDef))
    assert owner.name == "TimelineEditor" and owner.bases == []
    for method in owner.body:
        if not isinstance(method, ast.FunctionDef) or method.name == "__init__":
            continue
        assert not any(isinstance(node, (ast.If, ast.For, ast.While, ast.Try)) for node in ast.walk(method))
    for filename in ("timeline_track_editing", "timeline_structure_editing", "timeline_marker_editing"):
        source = _source(f"mediaflow/application/{filename}.py")
        assert "TYPE_CHECKING" not in source
        assert "self._changes.commit_change(" in source


def test_focused_timeline_scopes_cannot_expose_session_aggregates() -> None:
    source = _source("mediaflow/desktop/controllers/timeline_controller_scopes.py")
    for aggregate in ("DesktopSessionState", "SessionModels", "PresentationProjectors", "ProjectSession"):
        assert aggregate not in source
    scopes = [node for node in ast.parse(source).body if isinstance(node, ast.ClassDef)]
    assert len(scopes) == 13
    for scope in scopes:
        fields = {
            node.target.id
            for node in scope.body
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
        }
        assert fields.isdisjoint({"state", "models", "projectors"})
    for path in (ROOT / "mediaflow/desktop/controllers").glob("timeline_*controller.py"):
        controller = path.read_text(encoding="utf-8")
        for aggregate in ("self._session.state", "self._session.models", "self._session.projectors"):
            assert aggregate not in controller, path


def test_task_output_journal_uses_only_the_public_publication_contract() -> None:
    tree = ast.parse(_source("mediaflow/infrastructure/task_output_journal.py"))
    assert not any(
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "transaction"
        and node.attr.startswith("_")
        for node in ast.walk(tree)
    )
