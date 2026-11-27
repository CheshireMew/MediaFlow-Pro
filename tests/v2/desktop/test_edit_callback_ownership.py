from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, Qt, QUrl, Slot
from PySide6.QtQuick import QQuickView
from PySide6.QtTest import QTest

from mediaflow.composition import EditorProject
from mediaflow.desktop.app import _qml_dll_search_path
from mediaflow.desktop.controllers.subtitle_translation_controller import SubtitleTranslationController
from mediaflow.desktop.controllers.web_controller import WebController
from mediaflow.desktop.session_events import SessionEvents
from mediaflow.domain.collaboration import ActorIdentity
from mediaflow.domain.enums import TrackKind
from mediaflow.domain.settings import ServiceSettings
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.runtime_context import RuntimeContext
from mediaflow.service.remote_project import RemoteEditorProject


@pytest.mark.parametrize("component", ["AppTextField.qml", "AppTextArea.qml"])
def test_actual_text_control_keeps_unsaved_draft_after_focus_loss(
    qapp, qtbot, tmp_path: Path, component: str,
) -> None:
    project = RemoteEditorProject(
        {"project": str(tmp_path), "project_id": "draft-project", "project_revision": 1},
        actor=ActorIdentity(kind="human", id="draft-desktop"),
        workspace_session_id="draft-workspace",
    )

    class DraftBridge(QObject):
        @Slot(str)
        def beginCollaborationDraft(self, path: str) -> None:
            project.begin_draft(path)

        @Slot(str, result=bool)
        def hasCollaborationDraft(self, path: str) -> bool:
            return path in project.draft_paths

    bridge = DraftBridge()
    view = QQuickView()
    view.engine().rootContext().setContextProperty("applicationMonospaceFontFamily", "Consolas")
    view.engine().rootContext().setContextProperty("mediaflow", {
        "workspaceProjectController": bridge,
        "settingsController": {"settingsData": {"theme": "dark"}},
    })
    component_path = Path(__file__).resolve().parents[3] / "mediaflow/desktop/qml/components" / component
    with _qml_dll_search_path():
        view.setSource(QUrl.fromLocalFile(str(component_path)))
    assert view.status() == QQuickView.Status.Ready, [error.toString() for error in view.errors()]
    field = view.rootObject()
    assert field is not None
    path = "/subtitles/document/segments/segment/text"
    field.setProperty("collaborationPath", path)
    field.setProperty("sourceText", "server")
    view.resize(400, 120)
    view.show()
    try:
        view.requestActivate()
        field.forceActiveFocus()
        qtbot.waitUntil(lambda: bool(field.property("activeFocus")))
        QTest.keyClick(view, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        for key in (Qt.Key.Key_L, Qt.Key.Key_O, Qt.Key.Key_C, Qt.Key.Key_A, Qt.Key.Key_L):
            QTest.keyClick(view, key)
        assert field.property("text") == "local"
        assert project._drafts[path] == 1
        field.setFocus(False)
        qtbot.waitUntil(lambda: not field.property("activeFocus"))
        project._known_content_revision = 2
        field.setProperty("sourceText", "remote update")
        assert field.property("text") == "local"
        assert project._drafts[path] == 1
        project.end_draft(path)
        field.setProperty("sourceText", "accepted update")
        assert field.property("text") == "accepted update"
    finally:
        view.close()
        view.engine().rootContext().setContextProperty("mediaflow", None)


def test_save_ack_only_clears_its_submitted_draft_and_project_session() -> None:
    scope = SimpleNamespace(parent=QObject(), state=SimpleNamespace(binding=SimpleNamespace(
        generation=1, require_current=lambda: SimpleNamespace(begin_draft=lambda _path: None),
    )))
    controller = SubtitleTranslationController(scope)
    calls = []
    scope.projectors = SimpleNamespace(
        subtitles=SimpleNamespace(refresh_documents=lambda: calls.append("refresh")),
        timeline_subtitles=SimpleNamespace(refresh_preview_subtitles=lambda: None),
    )
    scope.preview = SimpleNamespace(schedule=lambda: None)
    scope.updates = SimpleNamespace(commit=lambda **kwargs: None)
    scope._set_status = lambda *args: None
    controller._comparison["targetDocumentId"] = "document"
    key = ("document", "segment")
    controller.storeTranslationDraft("segment", "submitted")
    submitted_version = controller._draft_versions[key]
    controller.storeTranslationDraft("segment", "NEW UNSAVED TEXT")
    controller._translation_segment_saved(key, 1, submitted_version)
    assert controller._drafts[key] == "NEW UNSAVED TEXT"
    scope.state.binding.generation = 2
    controller._translation_segment_saved(key, 1, controller._draft_versions[key])
    assert calls == ["refresh"]
    controller._ensure_draft_session()
    assert controller._drafts == {}


def test_browser_commit_is_bound_to_original_clip_and_page_session(tmp_path: Path) -> None:
    paths = RuntimeContext.discover().paths
    repository = ProjectRepository.create(tmp_path / "Web", "Web")
    project = EditorProject(repository, settings=ServiceSettings(), paths=paths)
    try:
        starter = Path(__file__).resolve().parents[2] / "fixtures" / "editable-media-v6"
        asset = project.import_web_package(starter)
        editor = project.timeline(project.get_project().main_sequence_id)
        track = editor.add_track(TrackKind.VIDEO, "Web")
        first = editor.add_clip(
            track_id=track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=30,
        )
        second = editor.copy_clip(first.id, timeline_start=30)
        scope = SimpleNamespace(
            parent=QObject(),
            events=SessionEvents(),
            state=SimpleNamespace(
                binding=SimpleNamespace(
                    current=project,
                    active_sequence_id=editor.sequence_id,
                    generation=1,
                )
            ),
            _require_writable=lambda: None,
        )
        controller = WebController(scope)
        controller._web_clip_id = first.id
        controller._web_state = project.get_web_clip(first.id).model_dump(mode="json")
        payload = project.web_runtime_state(editor.sequence_id, first.id)
        scene = payload["scene_id"]
        payload["scenes"][scene]["layers"]["title"]["x"] = 77
        old_binding = json.loads(controller.browser_binding_json())
        controller._web_clip_id = second.id
        controller._web_state = project.get_web_clip(second.id).model_dump(mode="json")
        controller._runtime_web_state = project.web_runtime_state(editor.sequence_id, second.id)
        # An in-flight message keeps the identity it had at event creation.
        WebController.commitBrowserState.__wrapped__(
            controller,
            json.dumps(
                {
                    "binding": old_binding,
                    "state": payload,
                }
            ),
        )
        assert project.get_web_clip(second.id).revision == 0
        previous_page = json.loads(controller.browser_binding_json())
        controller.beginBrowserPage()
        WebController.commitBrowserState.__wrapped__(
            controller,
            json.dumps(
                {
                    "binding": previous_page,
                    "state": payload,
                }
            ),
        )
        assert project.get_web_clip(second.id).revision == 0
        accepted = []
        controller._accept_state = accepted.append
        WebController.commitBrowserState.__wrapped__(
            controller,
            json.dumps(
                {
                    "binding": json.loads(controller.browser_binding_json()),
                    "state": payload,
                }
            ),
        )
        assert accepted[0].clip_id == second.id
        assert accepted[0].scenes[scene].layers["title"].x == 77
        assert project.get_web_clip(first.id).revision == 0
    finally:
        project.close(timeout=10)
