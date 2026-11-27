from __future__ import annotations

import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import (
    Q_ARG,
    QCoreApplication,
    QEvent,
    QMetaObject,
    QObject,
    QPointF,
    QSize,
    Qt,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent, QQmlPropertyMap
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest
from shiboken6 import getCppPointer, wrapInstance

from mediaflow.application.asset_service import AssetService
from mediaflow.application.export_task_handlers import ExportTaskHandlers
from mediaflow.application.project_file_task_handlers import ProjectFileTaskHandlers
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.composition import EditorApplication
from mediaflow.desktop.app import _qml_dll_search_path, configure_application_font, create_engine
from mediaflow.desktop.controllers import EditorControllers
from mediaflow.desktop.coordinators.preview_graph import PreviewGraphCoordinator
from mediaflow.desktop.presenters.task_projector import TaskProjector
from mediaflow.desktop.task_list_models import TaskListModel
from mediaflow.domain.enums import AssetKind, ClipMediaKind, TaskStatus, TrackKind
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.project import ProjectProfile
from mediaflow.domain.task_commands import GenerateWaveformCommand, RenderWebClipCommand
from mediaflow.domain.tasks import ProjectArchiveTaskOutcome, ProjectCollectionTaskOutcome, Task
from mediaflow.domain.timeline import Clip
from mediaflow.domain.web_state import WebClipState
from mediaflow.infrastructure.media_probe import MediaProbe
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.runtime_context import RuntimeContext
from scripts.verify_performance import visual_clip_delegates
from tests.v2.desktop_application_adapter import DesktopPresentationApplication


def _wait(predicate, seconds: float = 10) -> None:
    deadline = time.monotonic() + seconds
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(10)
    assert predicate()


def _visual_items(item: QQuickItem):
    for child in item.childItems():
        yield child
        yield from _visual_items(child)


@pytest.fixture
def desktop(tmp_path: Path, qapp: QGuiApplication):
    paths = RuntimeContext.discover().paths
    source = Path(__file__).resolve().parents[2] / "fixtures/media-timeline-v1-project/sources/moving.mp4"
    project_dir = tmp_path / "Editor"
    with ProjectRepository.create(project_dir, "Editor") as repository:
        asset = AssetService(repository, MediaProbe(paths)).import_external(source)
        editor = TimelineEditor(repository, repository.projects.get_project().main_sequence_id)
        track = editor.add_track(TrackKind.VIDEO)
        clip = editor.add_clip(
            track_id=track.id,
            asset_id=asset.id,
            timeline_start=60,
            source_in=0,
            duration=min(30, asset.metadata.duration_frames),
        )
    configure_application_font(qapp)
    engine, controllers = create_engine(qapp)
    try:
        controllers.workspace_project.openProject(str(project_dir))
        window = wrapInstance(getCppPointer(engine.rootObjects()[0])[0], QQuickWindow)
        _wait(lambda: window.findChild(QQuickItem, "workspace") is not None)
        _wait(lambda: bool(visual_clip_delegates(window)))
        yield window, controllers, asset.id, clip.id
    finally:
        controllers.shutdown()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QCoreApplication.processEvents()


def test_selection_updates_the_actual_visual_delegates(desktop) -> None:
    window, controllers, asset_id, clip_id = desktop
    clip_item = next(item for item in visual_clip_delegates(window) if item.property("clipId") == clip_id)
    asset_item = next(
        item
        for item in _visual_items(window.contentItem())
        if item.objectName() == "mediaAssetDelegate" and item.property("assetId") == asset_id
    )
    controllers.timeline_view.selectClip(clip_id)
    controllers.media.selectAsset(asset_id, False)
    _wait(lambda: clip_item.property("selected") and asset_item.property("selected"))
    controllers.timeline_view.clearSelection()
    _wait(lambda: not clip_item.property("selected"))
    assert len(visual_clip_delegates(window)) == controllers.timeline_viewport.visibleClipsModel.rowCount()


def test_project_switch_resets_source_and_filters_and_keeps_current_status(desktop, tmp_path: Path) -> None:
    window, controllers, asset_id, _clip_id = desktop
    dropdown = window.findChild(QObject, "assetBinFilter")
    assert dropdown.property("count") == 2
    controllers.media.createAssetBin("New bin", "")
    _wait(lambda: dropdown.property("count") == 3)
    bin_id = controllers.media.assetBinsModel.get(0)["binId"]
    controllers.media.setAssetBinFilter(bin_id)
    assert controllers.media.filteredAssetsModel.rowCount() == 0
    controllers.media.openSourceMonitor(asset_id)
    assert controllers.media.sourceMonitorData["assetId"] == asset_id
    next_dir = tmp_path / "Next"
    source = controllers.session.state.binding.current.resolve_asset_path(
        controllers.session.state.binding.current.get_asset(asset_id)
    )
    with ProjectRepository.create(next_dir, "Next") as repository:
        AssetService(repository, MediaProbe(RuntimeContext.discover().paths)).import_external(source)
    controllers.workspace_project.openProject(str(next_dir))
    _wait(lambda: controllers.session.state.requests.closing_project is None)
    assert controllers.media.sourceMonitorData["assetId"] == ""
    assert controllers.media.assetBinId == ""
    assert controllers.media.filteredAssetsModel.rowCount() == 1
    assert dropdown.property("currentIndex") == 0
    assert controllers.workspace.statusMessage == "项目已打开"


def test_source_and_program_monitors_keep_separate_edit_positions(desktop) -> None:
    window, controllers, asset_id, clip_id = desktop
    workspace = window.findChild(QObject, "workspace")
    panel = window.findChild(QObject, "previewPanel")
    _wait(lambda: controllers.workspace.previewStatus == "ready")
    controllers.workspace_playback.remoteSeekRequested.emit(70)
    _wait(lambda: panel.property("programFrame") == 70)
    controllers.media.openSourceMonitor(asset_id)
    workspace.setProperty("previewMode", "source")
    player = window.findChild(QObject, "previewPlayer")
    _wait(lambda: player.property("duration") > 0)
    player.seek(5)
    _wait(lambda: panel.property("sourceFrame") == 5)
    assert panel.property("programFrame") == 70
    timeline = window.findChild(QObject, "timelinePanel")
    assert timeline.property("visiblePlayheadFrame") == 70
    controllers.timeline_view.selectClip(clip_id)
    workspace.forceActiveFocus()
    window.requestActivate()
    QTest.keyClick(window, Qt.Key.Key_I)
    assert controllers.media.sourceMonitorData["inFrame"] == 5
    assert controllers.session.state.binding.timeline.state.sequence.in_out is None
    QTest.keyClick(window, Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier)
    clips = controllers.session.state.binding.timeline.state.clips
    assert len(clips) == 2 and sorted(clip.timeline_start for clip in clips) == [60, 70]
    workspace.setProperty("previewMode", "program")
    _wait(lambda: player.property("position") == 70)


def test_small_workspaces_keep_visible_panels_inside_window(desktop, tmp_path) -> None:
    window, controllers, asset_id, _clip_id = desktop
    workspace = window.findChild(QQuickItem, "workspace")
    window.setMinimumSize(QSize(0, 0))
    for width, height in ((960, 540), (640, 360)):
        window.setWidth(width)
        window.setHeight(height)
        QTest.qWait(50)
        window.setWidth(width)
        window.setHeight(height)
        QTest.qWait(50)
        for name in ("toolPanelContainer", "previewPanel", "inspectorPanel", "timelinePanel"):
            item = workspace.findChild(QQuickItem, name)
            if not item.isVisible():
                continue
            origin = item.mapToScene(QPointF())
            assert 0 <= origin.x() < width and 0 <= origin.y() < height
            assert origin.x() + item.width() <= width + 1
            assert origin.y() + item.height() <= height + 1
        close = window.findChild(QQuickItem, "closeWindowButton")
        assert close.mapToScene(QPointF()).x() + close.width() <= width + 1
        asset_list = window.findChild(QQuickItem, "mediaAssetListView")
        if asset_list.isVisible():
            assert asset_list.height() >= 30
        assert window.grabWindow().save(str(tmp_path / f"layout-{width}x{height}.png"))
        controllers.media.openSourceMonitor(asset_id)
        workspace.setProperty("previewMode", "source")
        QTest.qWait(30)
        for name in (
            "programMonitorTab",
            "sourceMonitorTab",
            "sourceInsertRangeButton",
            "sourceActionsMenuButton",
        ):
            item = window.findChild(QQuickItem, name)
            if item.isVisible():
                assert item.mapToScene(QPointF()).x() + item.width() <= width + 1
        workspace.setProperty("previewMode", "program")
        for panel in ("tool", "inspector", "timeline") if width == 640 else ("timeline",):
            assert QMetaObject.invokeMethod(workspace, "toggleWorkspacePanel", Q_ARG("QVariant", panel))
            assert workspace.property(f"{panel}Shown")
            assert QMetaObject.invokeMethod(workspace, "toggleWorkspacePanel", Q_ARG("QVariant", panel))
            assert workspace.property("previewShown")


def test_focused_volume_slider_owns_arrow_keys_and_keeps_global_commands(desktop) -> None:
    window, controllers, _asset_id, _clip_id = desktop
    workspace = window.findChild(QQuickItem, "workspace")
    panel = window.findChild(QObject, "previewPanel")
    _wait(lambda: controllers.workspace.previewStatus == "ready")
    controllers.workspace_playback.remoteSeekRequested.emit(70)
    _wait(lambda: panel.property("programFrame") == 70)
    slider = next(
        item
        for item in _visual_items(window.contentItem())
        if item.metaObject().indexOfProperty("stepSize") >= 0 and item.property("stepSize") == 0.01
    )
    slider.setProperty("value", 0.5)
    slider.forceActiveFocus()
    window.requestActivate()
    _wait(lambda: slider.hasActiveFocus())
    QTest.keyClick(window, Qt.Key.Key_Right)
    assert slider.property("value") > 0.5
    assert panel.property("programFrame") == 70
    QTest.keyClick(window, Qt.Key.Key_M, Qt.KeyboardModifier.ControlModifier)
    assert workspace.property("activeMode") == "export"
    workspace.forceActiveFocus()
    QTest.keyClick(window, Qt.Key.Key_Right)
    _wait(lambda: panel.property("programFrame") == 71)


def test_preview_preparing_and_failure_states_are_visible_and_retryable(desktop) -> None:
    window, controllers, _asset_id, _clip_id = desktop
    _wait(lambda: controllers.workspace.previewStatus == "ready")
    preview = controllers.session.preview
    preview.stop()
    preview._set_preview_status("preparing")
    message = window.findChild(QQuickItem, "previewStateMessage")
    assert message.isVisible() and "正在准备" in message.property("text")
    preview._set_preview_status("failed", "Capture failed")
    assert message.isVisible() and "Capture failed" in message.property("text")
    retry = window.findChild(QQuickItem, "retryPreviewButton")
    assert retry.isVisible()
    assert QMetaObject.invokeMethod(retry, "clicked")
    _wait(lambda: controllers.workspace.previewStatus == "ready")


@pytest.mark.parametrize("status", [TaskStatus.CANCELLED, TaskStatus.FAILED])
def test_web_preview_respects_stop_until_content_changes_or_explicit_retry(qapp, status) -> None:
    clip = Clip(
        track_id="track",
        asset_id="asset",
        duration=30,
        timeline_start=0,
        source_in=0,
        media_kind=ClipMediaKind.VIDEO_ONLY,
    )
    timeline = SimpleNamespace(
        state=SimpleNamespace(
            sequence=SimpleNamespace(id="sequence", profile=ProjectProfile()),
            clips=[clip],
            web_states={clip.id: WebClipState(clip_id=clip.id, source_hash="a" * 64)},
        )
    )
    current = SimpleNamespace(
        list_assets=lambda: [SimpleNamespace(id="asset", kind=AssetKind.WEB)],
        web_render_cache_ready=lambda *_args: False,
    )
    binding = SimpleNamespace(
        current=current,
        timeline=timeline,
        generation=1,
        require_current=lambda: current,
        require_timeline=lambda: timeline,
    )
    state = SimpleNamespace(
        binding=binding,
        tasks=SimpleNamespace(items={}),
        requests=SimpleNamespace(preview_id=0),
        presentation=SimpleNamespace(preview_status="empty", preview_error=""),
    )
    created = []

    def create(command, *_args, **_kwargs):
        task = Task(project_id="project", command=command, created_at=len(created))
        state.tasks.items[task.id] = task
        created.append(task)
        return task

    context = SimpleNamespace(
        parent=QObject(),
        state=state,
        create=create,
        updates=SimpleNamespace(commit=lambda **_kwargs: None, report_error=lambda error: pytest.fail(error)),
    )
    preview = PreviewGraphCoordinator(context)
    preview.compile()
    assert len(created) == 1
    task = created[0]
    state.tasks.items[task.id] = task.model_copy(update={"status": status})
    preview.schedule()
    preview.stop()
    preview.compile()
    assert len(created) == 1 and state.presentation.preview_status == status.value
    state.tasks.items.clear()
    preview.compile()
    assert len(created) == 1
    preview.retry()
    preview.stop()
    preview.compile()
    assert len(created) == 2
    state.tasks.items[created[-1].id] = created[-1].model_copy(update={"status": status})
    timeline.state.web_states[clip.id] = WebClipState(clip_id=clip.id, source_hash="b" * 64)
    preview.compile()
    assert len(created) == 3
    state.tasks.items[created[-1].id] = created[-1].model_copy(update={"status": TaskStatus.COMPLETED})
    preview.compile()
    assert len(created) == 4
    preview.compile()
    assert len(created) == 4


def test_progress_updates_only_the_changed_task_and_queue_positions_remain_correct(qapp, monkeypatch) -> None:
    tasks = [
        Task(
            id=str(index),
            project_id="project",
            command=GenerateWaveformCommand(asset_id="asset"),
            created_at=index,
        )
        for index in range(1000)
    ]
    state = SimpleNamespace(
        binding=SimpleNamespace(current=SimpleNamespace(project_dir=Path.cwd())),
        tasks=SimpleNamespace(items={task.id: task for task in tasks}),
    )
    model = TaskListModel()
    context = SimpleNamespace(
        state=state,
        models=SimpleNamespace(tasks=model),
        updates=SimpleNamespace(commit=lambda **_kwargs: None),
    )
    projector = TaskProjector(context)
    projector.refresh_tasks()
    task = tasks[500].model_copy(
        update={
            "revision": 1,
            "progress": OperationProgress.determinate(
                "waveform_decoding", completed=1, total=10, unit="media_seconds"
            ),
        }
    )
    state.tasks.items[task.id] = task
    with monkeypatch.context() as patch:
        patch.setattr(
            model, "set_items", lambda *_args: pytest.fail("Progress rebuilt the entire task model")
        )
        projector.refresh_tasks(task_ids=[task.id])
    assert model.get(model.findRow("taskId", task.id))["progressValue"] == 10
    state.tasks.items["0"] = tasks[0].model_copy(update={"status": TaskStatus.COMPLETED, "revision": 1})
    projector.refresh_tasks(task_ids=["0"])
    assert model.get(model.findRow("taskId", "1"))["queuePosition"] == 1
    assert model.get(model.findRow("taskId", "0"))["queuePosition"] == 0


@pytest.mark.parametrize("operation", ["collection", "archive", "fcpxml"])
def test_long_desktop_file_operations_return_immediately_and_can_be_cancelled(
    qapp, tmp_path, monkeypatch, operation
):
    entered = threading.Event()
    release = threading.Event()

    def hold(_self, context):
        entered.set()
        while not release.wait(0.01):
            context.cancellation.raise_if_requested()
        context.cancellation.raise_if_requested()
        raise AssertionError("Test hold was released without cancellation")

    monkeypatch.setattr(
        ProjectFileTaskHandlers if operation != "fcpxml" else ExportTaskHandlers, "handle", hold
    )
    application = DesktopPresentationApplication(EditorApplication())
    controllers = EditorControllers(application=application)
    try:
        controllers.workspace_project.createProject(str(tmp_path), "Task admission")
        if operation == "fcpxml":
            monkeypatch.setattr(
                type(controllers.export._session), "_require_exportable_sequence", lambda _self: None
            )
        started = time.perf_counter()
        if operation == "collection":
            controllers.workspace_project.collectProjectAssets()
        elif operation == "archive":
            controllers.workspace_project.createPortableProjectArchive(str(tmp_path))
        else:
            controllers.export.exportFcpxml(str(tmp_path / "output.fcpxml"))
        assert time.perf_counter() - started < 0.5
        assert entered.wait(3)
        current = controllers.session.state.binding.current
        task = current.list_tasks()[-1]
        controllers.tasks.cancelTask(task.id)
        _wait(lambda: current.get_task(task.id).status == TaskStatus.CANCELLED)
        assert controllers.session.state.binding.current is current
    finally:
        release.set()
        controllers.shutdown()


def test_background_file_operations_publish_their_real_results(desktop, tmp_path) -> None:
    _window, controllers, asset_id, _clip_id = desktop
    current = controllers.session.state.binding.require_current()
    original = current.resolve_asset_path(current.get_asset(asset_id)).read_bytes()

    def completed_task(command_type):
        def finished():
            return any(
                task.command.command_type == command_type and task.status.is_terminal
                for task in controllers.session.state.tasks.items.values()
            )

        _wait(finished)
        task = next(
            task
            for task in controllers.session.state.tasks.items.values()
            if task.command.command_type == command_type
        )
        assert task.status == TaskStatus.COMPLETED, task.error
        return task

    controllers.workspace_project.collectProjectAssets()
    collection = completed_task("collect_project_assets")
    assert isinstance(collection.outcome, ProjectCollectionTaskOutcome)
    collected_path = current.resolve_asset_path(current.get_asset(asset_id))
    assert collected_path.is_relative_to(current.project_dir)
    assert collected_path.read_bytes() == original
    assert "已归集" in controllers.workspace.statusMessage

    controllers.workspace_project.createPortableProjectArchive(str(tmp_path))
    archive = completed_task("archive_project")
    assert isinstance(archive.outcome, ProjectArchiveTaskOutcome)
    with ProjectRepository.open(archive.outcome.result.destination, writable=False) as repository:
        archived_asset = repository.assets.get_asset(asset_id)
        archived_path = Path(archive.outcome.result.destination) / archived_asset.path
        assert archived_path.read_bytes() == original
        assert (
            len(repository.timeline.load_timeline(repository.projects.get_project().main_sequence_id).clips)
            == 1
        )
    assert "可迁移项目已归档" in controllers.workspace.statusMessage

    output = tmp_path / "timeline.fcpxml"
    controllers.export.exportFcpxml(str(output))
    completed_task("export_fcpxml")
    document = ET.parse(output)
    assert document.getroot().tag == "fcpxml"
    assert document.find("resources/asset") is not None
    video = document.find(".//clip/video")
    assert video is not None and video.get("ref") == document.find("resources/asset").get("id")
    assert "已导出 FCPXML" in controllers.workspace.statusMessage


def test_real_web_preview_stays_cancelled_then_renders_after_explicit_retry(desktop, tmp_path) -> None:
    window, controllers, _asset_id, _clip_id = desktop
    current = controllers.session.state.binding.require_current()
    package = Path(__file__).resolve().parents[2] / "fixtures/editable-media-v6"
    asset = current.import_web_package(package)
    timeline = controllers.session.state.binding.require_timeline()
    timeline.set_sequence_profile(ProjectProfile(width=320, height=180))
    clip = timeline.add_clip(
        track_id=timeline.state.tracks[0].id, asset_id=asset.id, timeline_start=0, source_in=0, duration=30
    )
    controllers.session.projectors.refresh_project()
    controllers.session.preview.stop()
    controllers.session.preview.compile()

    def renders():
        return [
            task
            for task in controllers.session.state.tasks.items.values()
            if isinstance(task.command, RenderWebClipCommand) and task.command.clip_id == clip.id
        ]

    _wait(lambda: bool(renders()))
    first = renders()[0]
    controllers.tasks.cancelTask(first.id)
    _wait(lambda: renders()[0].status.is_terminal)
    assert renders()[0].status == TaskStatus.CANCELLED
    for _ in range(2):
        controllers.session.preview.schedule()
        controllers.session.preview.stop()
        controllers.session.preview.compile()
    assert len(renders()) == 1
    assert controllers.workspace.previewStatus == "cancelled"
    controllers.workspace_sequence.retryPreview()
    _wait(lambda: len(renders()) == 2)
    _wait(lambda: all(task.status.is_terminal for task in renders()), seconds=60)
    assert any(task.status == TaskStatus.COMPLETED for task in renders()), [task.error for task in renders()]
    _wait(lambda: controllers.workspace.previewStatus == "ready")
    assert current.web_render_cache_ready(timeline.state, clip.id)
    controllers.workspace_playback.remoteSeekRequested.emit(10)
    player = window.findChild(QObject, "previewPlayer")
    _wait(lambda: player.property("position") == 10)
    QTest.qWait(100)
    assert window.grabWindow().save(str(tmp_path / "web-preview-retried.png"))


def test_dubbing_drafts_survive_other_results_refresh_recreation_and_failed_save(qapp) -> None:
    class Dubbing(QObject):
        projectStateChanged = Signal()
        tasksChanged = Signal()
        settingsChanged = Signal()
        revision = 1
        save_succeeds = False

        @Slot(result="QVariantMap")
        def sourceReadiness(self):
            return {"available": True, "active": False}

        @Slot(result="QVariantList")
        def sourceDocuments(self):
            return [{"documentId": "source", "label": "English"}]

        @Slot(str, result="QVariantList")
        def targetDocuments(self, _source):
            return []

        @Slot(result="QVariantList")
        def sessions(self):
            return [{"sessionId": "session", "label": "Review"}]

        @Slot(str, result="QVariantMap")
        def session(self, session_id):
            return {
                "sessionId": session_id,
                "revision": self.revision,
                "status": "review",
                "needsReviewCount": 2,
                "masterAudioUrl": "",
                "speakers": [
                    {
                        "speakerId": "speaker",
                        "displayName": "Speaker",
                        "reviewStatus": "needs_review",
                        "primaryReferenceId": "reference",
                        "references": [
                            {
                                "referenceId": "reference",
                                "text": "Reference",
                                "language": "en",
                                "durationSeconds": 3,
                                "audioUrl": "",
                            }
                        ],
                    }
                ],
                "utterances": [
                    {
                        "utteranceId": str(i),
                        "speakerId": "speaker",
                        "startFrame": i * 30,
                        "endFrame": (i + 1) * 30,
                        "sourceText": "Source",
                        "targetText": f"Target {i}",
                        "status": "generated" if self.revision > 1 and i == 0 else "pending",
                        "reviewStatus": "needs_review",
                        "speedFactor": 1,
                        "issues": [],
                        "audioUrl": "",
                    }
                    for i in range(2)
                ],
            }

        @Slot(str, str, int, str, str, str, result=bool)
        def updateUtterance(self, *_args):
            return self.save_succeeds

    engine = QQmlApplicationEngine()
    dubbing = Dubbing()
    settings = QQmlPropertyMap()
    settings.insert("settingsData", {"theme": "dark"})
    context = QQmlPropertyMap()
    context.insert("dubbingController", dubbing)
    context.insert("settingsController", settings)
    engine.rootContext().setContextProperty("mediaflow", context)
    engine.rootContext().setContextProperty("applicationMonospaceFontFamily", "Consolas")
    component = QQmlComponent(engine)
    qml = b"""import QtQuick
import QtQuick.Window
import "."
Window { width: 800; height: 1100; visible: true
    DubbingPanel { objectName: "panel"; anchors.fill: parent }
}"""
    url = QUrl.fromLocalFile(
        str(Path(__file__).resolve().parents[3] / "mediaflow/desktop/qml/in-memory-test.qml")
    )
    with _qml_dll_search_path():
        component.setData(qml, url)
        _wait(lambda: not component.isLoading())
        assert not component.isError(), [error.toString() for error in component.errors()]
        window = component.create()
        assert window is not None
        try:
            panel = window.findChild(QObject, "panel")
            _wait(
                lambda: any(
                    item.objectName() == "dubbingSpeakerNameEditor"
                    for item in _visual_items(window.contentItem())
                )
            )

            def edit(name, value, index=0):
                fields = [item for item in _visual_items(window.contentItem()) if item.objectName() == name]
                fields[index].setProperty("text", value)
                assert QMetaObject.invokeMethod(fields[index], "textEdited")

            edit("dubbingSpeakerNameEditor", "Unsaved speaker")
            edit("dubbingReferenceTextEditor", "Unsaved reference")
            tabs = next(
                item
                for item in _visual_items(window.contentItem())
                if "TabBar" in item.metaObject().className()
            )
            tabs.setProperty("currentIndex", 1)
            _wait(
                lambda: sum(
                    item.objectName() == "dubbingTargetTextEditor"
                    for item in _visual_items(window.contentItem())
                )
                >= 2
            )
            edit("dubbingTargetTextEditor", "Unsaved second sentence", 1)
            dubbing.revision += 1
            dubbing.tasksChanged.emit()
            QTest.qWait(20)
            texts = {
                item.property("text")
                for item in _visual_items(window.contentItem())
                if item.metaObject().indexOfProperty("text") >= 0
            }
            assert {"Unsaved speaker", "Unsaved reference", "Unsaved second sentence"} <= texts
            assert QMetaObject.invokeMethod(
                panel,
                "saveUtterance",
                Q_ARG("QVariant", "1"),
                Q_ARG("QVariant", "Unsaved second sentence"),
                Q_ARG("QVariant", "speaker"),
            )
            drafts = panel.property("drafts").toVariant()
            assert drafts["session/utterance/1"]["targetText"] == "Unsaved second sentence"
            panel.setProperty("selectedSessionId", "other")
            assert QMetaObject.invokeMethod(panel, "refreshSession")
            panel.setProperty("selectedSessionId", "session")
            assert QMetaObject.invokeMethod(panel, "refreshSession")
            QTest.qWait(20)
            texts = [
                item.property("text")
                for item in _visual_items(window.contentItem())
                if item.objectName() == "dubbingTargetTextEditor"
            ]
            assert "Unsaved second sentence" in texts
        finally:
            window.close()
            window.deleteLater()
            engine.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
