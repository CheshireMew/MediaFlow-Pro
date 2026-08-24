from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject
from PySide6.QtGui import QColor, QImage

from mediaflow.application.events import TaskEvent
from mediaflow.application.task_service import TaskCompletion
from mediaflow.application.workflow_coordinator import WorkflowCoordinator
from mediaflow.composition import EditorApplication
from mediaflow.desktop.asset_list_models import AssetFilterModel, AssetListModel
from mediaflow.desktop.controllers import EditorControllers
from mediaflow.desktop.controllers.project_controller import ProjectSession
from mediaflow.desktop.controllers.subtitle_transcription_controller import (
    SubtitleTranscriptionController,
)
from mediaflow.desktop.controllers.timeline_view_controller import (
    FILMSTRIP_BASE_IDLE_MS,
    FILMSTRIP_MAX_IDLE_MS,
    filmstrip_idle_delay_ms,
)
from mediaflow.desktop.coordinators.project_lifecycle import ProjectLifecycle
from mediaflow.desktop.coordinators.task_events import TaskOperations
from mediaflow.desktop.list_model_base import DictListModel
from mediaflow.desktop.presenters.timeline_projector import (
    PREVIEW_GRAPH_BASE_IDLE_MS,
    PREVIEW_GRAPH_MAX_IDLE_MS,
    preview_graph_idle_delay_ms,
)
from mediaflow.desktop.session_events import SessionEvents
from mediaflow.desktop.session_state import ImportDropBatch, TimelinePlacement
from mediaflow.desktop.timeline_list_models import TimelineClipViewportModel
from mediaflow.desktop.workspace_list_models import SequenceListModel
from mediaflow.domain.downloads import DownloadRequest
from mediaflow.domain.enums import (
    AssetKind,
    TaskKind,
    TaskStatus,
    TrackKind,
    WorkflowStage,
    WorkflowStatus,
)
from mediaflow.domain.project import MediaMetadata, ProjectProfile, SequenceInOut
from mediaflow.domain.sequence_bounds import SequenceBoundaryAnalysis
from mediaflow.domain.storage_names import (
    DEFAULT_HIGHLIGHT_EXPORT_RELATIVE_DIRECTORY,
    OUTPUT_WORKSPACE_COMPONENT_RESERVE_UTF16_UNITS,
    PROJECT_ROOT_PATH_UTF16_LIMIT,
    safe_child_path,
    utf16_units,
)
from mediaflow.domain.subtitles import (
    SubtitleDocument,
    SubtitlePlacement,
    SubtitleSegment,
)
from mediaflow.domain.task_commands import (
    AnalyzeDownloadCommand,
    AnalyzeSequenceBoundsCommand,
    ExportSequenceCommand,
    ImportAssetCommand,
    TrackSubjectCommand,
)
from mediaflow.domain.tasks import SequenceBoundaryTaskOutcome, Task
from mediaflow.domain.workflows import WorkflowPayload
from mediaflow.infrastructure.platform_media import PlatformMediaResolver
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.settings_repository import (
    DesktopSettingsRepository,
    ServiceSettingsRepository,
)
from mediaflow.infrastructure.task_repository import TaskRepository
from mediaflow.infrastructure.ytdlp_service import YtDlpDownloadService
from tests.v2.desktop_application_adapter import DesktopPresentationApplication
from tests.v2.real_media import generate_real_media


def test_transcription_plan_projection_is_computed_once_per_change(
    monkeypatch,
    qapp: QCoreApplication,
) -> None:
    parent = QObject()
    events = SessionEvents(parent)
    session = SimpleNamespace(parent=parent, events=events)
    calls: list[object] = []
    plan = SimpleNamespace(
        region_count=3,
        timeline_start_frame=10,
        timeline_end_frame=70,
        recognition_seconds=2.0,
        source_count=1,
        asr=SimpleNamespace(
            engine="faster_whisper",
            model="tiny.en",
            device="cpu",
            language="en",
            parallel_chunks=1,
        ),
    )

    def calculate(candidate) -> object:
        calls.append(candidate)
        return plan

    monkeypatch.setattr(
        "mediaflow.desktop.controllers.subtitle_transcription_controller.current_transcription_plan",
        calculate,
    )
    controller = SubtitleTranscriptionController(session)
    notifications: list[None] = []
    controller.planChanged.connect(lambda: notifications.append(None))

    assert controller.canTranscribeCurrentSequence is True
    assert controller.transcriptionPlanSummary["regionCount"] == 3
    assert controller.transcriptionPlanSummary["sourceCount"] == 1
    assert calls == [session]

    for change_signal in (
        events.historyChanged,
        events.settingsChanged,
        events.projectStateChanged,
    ):
        change_signal.emit()
        assert controller.canTranscribeCurrentSequence is True
        assert controller.transcriptionPlanSummary["regionCount"] == 3

    assert calls == [session, session, session, session]
    assert notifications == []
    qapp.processEvents()
    assert notifications == [None]


def test_subject_tracking_modes_do_not_reuse_each_others_active_task() -> None:
    auto_reframe = TrackSubjectCommand(
        sequence_id="sequence",
        clip_id="clip",
        mode="auto_reframe",
    )
    subject_tracking = auto_reframe.model_copy(update={"mode": "subject_tracking"})

    assert TaskOperations._active_request_scope(auto_reframe) != (
        TaskOperations._active_request_scope(subject_tracking)
    )


def test_deferred_model_update_is_readable_before_qml_notification() -> None:
    _application = QCoreApplication.instance() or QCoreApplication([])
    model = DictListModel(["id", "value"])
    model.set_items([{"id": "row", "value": 1}])
    changes: list[tuple[int, list[int]]] = []
    model.dataChanged.connect(lambda first, _last, roles: changes.append((first.row(), list(roles))))

    model.set_items_deferred([{"id": "row", "value": 2}])

    assert model.get(0)["value"] == 2
    assert changes == []
    QCoreApplication.processEvents()
    assert changes and changes[0][0] == 0


def test_deferred_model_notification_is_cancelled_when_its_qt_owner_is_deleted() -> None:
    _application = QCoreApplication.instance() or QCoreApplication([])
    owner = QObject()
    model = DictListModel(["id", "value"], owner)
    model.set_items([{"id": "row", "value": 1}])
    model.set_items_deferred([{"id": "row", "value": 2}])

    owner.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    QCoreApplication.processEvents()


def test_timeline_clip_viewport_keeps_large_fitted_timelines_bounded() -> None:
    model = TimelineClipViewportModel()
    rows = []
    for index in range(5_000):
        row = {role: "" for role in model._roles}
        row.update(
            {
                "clipId": f"clip-{index}",
                "trackPosition": index % 4,
                "audioTrackPosition": -1,
                "startFrame": index * 10,
                "durationFrames": 10,
                "endFrame": index * 10 + 10,
                "assetKind": "video",
                "trackKind": "video",
                "mediaKind": "video_only",
                "hasAudio": False,
                "compoundId": "",
            }
        )
        rows.append(row)

    model.set_source_items(rows)
    model.set_viewport(0, 50_000, 0.02)
    model.set_selected_ids([row["clipId"] for row in rows])

    assert model.rowCount() <= model._MAX_INTERACTIVE_ROWS
    assert model.findRow("clipId", "clip-4999") >= 0
    assert len(model.overview()) == 5_000

    model.set_selected_ids(["clip-4999"])
    resets: list[None] = []
    patches: list[tuple[list[dict], list[str]]] = []
    model.modelReset.connect(lambda: resets.append(None))
    model.sourceItemsPatched.connect(
        lambda changed_rows, removed_ids: patches.append((changed_rows, removed_ids))
    )
    changed = dict(rows[-1])
    changed["startFrame"] = 50_000
    changed["endFrame"] = 50_010

    assert model.update_source_items([changed]) is True
    assert resets == []
    assert patches == [([changed], [])]
    assert model.get(model.findRow("clipId", "clip-4999"))["startFrame"] == 50_000


def test_preview_graph_idle_delay_scales_with_timeline_weight() -> None:
    assert preview_graph_idle_delay_ms(0) == PREVIEW_GRAPH_BASE_IDLE_MS
    assert preview_graph_idle_delay_ms(5_000) == PREVIEW_GRAPH_MAX_IDLE_MS
    assert preview_graph_idle_delay_ms(50_000) == PREVIEW_GRAPH_MAX_IDLE_MS


def test_filmstrip_idle_delay_keeps_large_timeline_work_behind_edits() -> None:
    assert filmstrip_idle_delay_ms(0) == FILMSTRIP_BASE_IDLE_MS
    assert filmstrip_idle_delay_ms(5_000) == FILMSTRIP_MAX_IDLE_MS
    assert filmstrip_idle_delay_ms(50_000) == FILMSTRIP_MAX_IDLE_MS


def test_keyed_model_update_does_not_require_a_full_projection() -> None:
    model = DictListModel(["id", "value"])
    model.set_items(
        [
            {"id": "first", "value": 1},
            {"id": "second", "value": 2},
        ]
    )

    assert model.update_items_by_key([{"id": "second", "value": 3}]) is True
    assert model.snapshot() == [
        {"id": "first", "value": 1},
        {"id": "second", "value": 3},
    ]


def test_keyed_model_membership_patch_reuses_unchanged_rows() -> None:
    model = DictListModel(["id", "value"])
    model.set_items(
        [
            {"id": "first", "value": 1},
            {"id": "second", "value": 2},
        ]
    )

    assert model.patch_items_by_key(
        [{"id": "third", "value": 3}],
        removed_keys={"second"},
        ordered_keys=["first", "third"],
    )
    assert model.snapshot() == [
        {"id": "first", "value": 1},
        {"id": "third", "value": 3},
    ]


def test_desktop_application_is_widget_capable_for_runtime_directory_fallback() -> None:
    environment = os.environ.copy()
    environment["QT_QPA_PLATFORM"] = "offscreen"
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from PySide6.QtWidgets import QApplication;"
                "from mediaflow.desktop.app import create_desktop_application;"
                "app=create_desktop_application([]);"
                "print(isinstance(app,QApplication))"
            ),
        ],
        cwd=Path(__file__).resolve().parents[3],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.stdout.strip() == "True"


def test_long_duplicate_project_names_keep_the_numeric_suffix_inside_path_budget(
    tmp_path: Path,
) -> None:
    display_name = "很长的项目名称" * 30
    first, first_display_name = ProjectLifecycle._creation_target(
        tmp_path,
        display_name,
        ensure_unique=False,
    )
    assert first_display_name == display_name
    first.mkdir()

    second, second_display_name = ProjectLifecycle._creation_target(
        tmp_path,
        display_name,
        ensure_unique=True,
    )
    assert second != first
    assert second.name.endswith(" (2)")
    assert utf16_units(second.name) <= 120
    assert second_display_name.endswith(" (2)")
    second.mkdir()

    third, third_display_name = ProjectLifecycle._creation_target(
        tmp_path,
        display_name,
        ensure_unique=True,
    )
    assert third.name.endswith(" (3)")
    assert third != second
    assert utf16_units(third.name) <= 120
    assert third_display_name.endswith(" (3)")


def test_maximum_desktop_project_root_can_construct_default_export_paths(
    tmp_path: Path,
) -> None:
    root, _ = ProjectLifecycle._creation_target(
        tmp_path,
        "最长合法项目目录" * 100,
    )

    assert utf16_units(str(root)) == PROJECT_ROOT_PATH_UTF16_LIMIT
    main_output = safe_child_path(
        root / "exports",
        "主序列导出" * 100,
        suffix=".mp4",
        required_sibling_component_utf16_units=(OUTPUT_WORKSPACE_COMPONENT_RESERVE_UTF16_UNITS),
    )
    short_output = safe_child_path(
        root / DEFAULT_HIGHLIGHT_EXPORT_RELATIVE_DIRECTORY,
        "短视频导出" * 100,
        suffix=".mp4",
        required_sibling_component_utf16_units=(OUTPUT_WORKSPACE_COMPONENT_RESERVE_UTF16_UNITS),
    )

    assert utf16_units(str(main_output)) <= 240
    assert utf16_units(str(short_output)) <= 240


def test_desktop_main_configures_surface_and_webengine_before_application(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from mediaflow.desktop import app as desktop_app

    calls: list[object] = []
    settings_path = tmp_path / "desktop-settings.json"
    startup_settings = SimpleNamespace(recovered=False)

    class FakeSignal:
        def connect(self, _callback) -> None:
            calls.append("shutdown-connected")

    class FakeApplication:
        aboutToQuit = FakeSignal()

        def setDesktopFileName(self, _name: str) -> None:
            calls.append("desktop-file-name")

        def exec(self) -> int:
            calls.append("exec")
            return 0

    controllers = SimpleNamespace(
        shutdown=lambda: None,
        workspace=SimpleNamespace(openProject=lambda _path: None),
    )
    monkeypatch.setattr(desktop_app.multiprocessing, "freeze_support", lambda: None)
    monkeypatch.setattr(desktop_app, "startup_settings_path", lambda: settings_path)
    monkeypatch.setattr(
        desktop_app,
        "load_startup_settings",
        lambda path: calls.append(("settings", path)) or startup_settings,
    )
    monkeypatch.setattr(
        desktop_app,
        "configure_startup_surface",
        lambda settings: calls.append(("surface", settings)) or True,
    )
    monkeypatch.setattr(
        desktop_app,
        "QtWebEngineQuick",
        SimpleNamespace(initialize=lambda: calls.append("webengine")),
    )
    monkeypatch.setattr(
        desktop_app,
        "create_desktop_application",
        lambda: calls.append("application") or FakeApplication(),
    )
    monkeypatch.setattr(desktop_app, "ensure_runtime_directory", lambda: True)
    runtime_root = tmp_path / "runtime"
    monkeypatch.setattr(desktop_app, "runtime_directory", lambda: runtime_root)
    monkeypatch.setattr(
        desktop_app,
        "create_desktop_editor_application",
        lambda: calls.append("editor-service-client") or SimpleNamespace(),
    )
    monkeypatch.setattr(desktop_app, "configure_application_font", lambda _app: "")
    monkeypatch.setattr(desktop_app, "configure_application_icon", lambda _app: None)
    monkeypatch.setattr(
        desktop_app,
        "create_engine",
        lambda _app, _api, **_kwargs: (SimpleNamespace(), controllers),
    )
    monkeypatch.setattr(sys, "argv", ["mediaflow"])

    assert desktop_app.main() == 0
    assert calls.index(("settings", settings_path)) < calls.index(("surface", startup_settings))
    assert calls.index(("surface", startup_settings)) < calls.index("webengine")
    assert calls.index("webengine") < calls.index("application")
    assert calls.index("application") < calls.index("editor-service-client")
    assert (runtime_root / "logs" / "mediaflow.log").is_file()
    assert not any(
        getattr(handler, "_mediaflow_application_log", False)
        for handler in logging.getLogger("mediaflow").handlers
    )


def test_startup_settings_path_uses_env_and_bootstrap_without_runtime_discovery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from mediaflow.desktop import app as desktop_app

    explicit_settings = tmp_path / "explicit-settings.json"
    monkeypatch.setenv("MEDIAFLOW_DESKTOP_SETTINGS_PATH", str(explicit_settings))
    monkeypatch.setenv("MEDIAFLOW_RUNTIME_DIR", str(tmp_path / "ignored-runtime"))
    assert desktop_app.startup_settings_path() == explicit_settings.resolve()

    monkeypatch.delenv("MEDIAFLOW_DESKTOP_SETTINGS_PATH")
    configured_runtime = tmp_path / "configured-runtime"
    monkeypatch.setenv("MEDIAFLOW_RUNTIME_DIR", str(configured_runtime))
    assert desktop_app.startup_settings_path() == (configured_runtime / "desktop-settings.json").resolve()

    monkeypatch.delenv("MEDIAFLOW_RUNTIME_DIR")
    configured_development_root = tmp_path / "configured-development"
    monkeypatch.setenv("MEDIAFLOW_DEV_ROOT", str(configured_development_root))
    assert (
        desktop_app.startup_settings_path()
        == (configured_development_root / "runtime" / "desktop-settings.json").resolve()
    )

    monkeypatch.delenv("MEDIAFLOW_DEV_ROOT")
    saved_runtime = tmp_path / "saved-runtime"
    saved_runtime.mkdir()
    monkeypatch.setattr(
        desktop_app,
        "QSettings",
        lambda *_args: SimpleNamespace(value=lambda _key, _default="": str(saved_runtime)),
    )
    assert desktop_app.startup_settings_path() == (saved_runtime / "desktop-settings.json").resolve()

    monkeypatch.setattr(
        desktop_app,
        "QSettings",
        lambda *_args: SimpleNamespace(value=lambda _key, _default="": ""),
    )
    assert desktop_app.startup_settings_path() is None


def test_hdr_surface_format_is_set_from_explicit_settings_before_qapplication(
    tmp_path: Path,
) -> None:
    repository = DesktopSettingsRepository(tmp_path / "desktop-settings.json")
    settings = repository.default_settings()
    repository.save(settings)
    environment = os.environ.copy()
    environment["QT_QPA_PLATFORM"] = "offscreen"
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys;"
                "from pathlib import Path;"
                "from PySide6.QtGui import QColorSpace,QSurfaceFormat;"
                "from mediaflow.desktop.app import configure_startup_surface,"
                "create_desktop_application;"
                "enabled=configure_startup_surface(Path(sys.argv[1]));"
                "expected=QColorSpace(QColorSpace.SRgbLinear);"
                "configured=QSurfaceFormat.defaultFormat().colorSpace()==expected;"
                "app=create_desktop_application([]);"
                "print(enabled);"
                "print(configured)"
            ),
            str(repository.path),
        ],
        cwd=Path(__file__).resolve().parents[3],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.stdout.splitlines() == ["True", "True"]


@pytest.mark.parametrize(
    ("raw_settings", "expected_reason"),
    [
        (b'{"schema_version": 1, "ui": ', "内容无效"),
        (b'{"schema_version": 999, "ui": {}}', "schema 必须为 1"),
    ],
)
def test_startup_archives_unreadable_settings_and_continues_with_defaults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    raw_settings: bytes,
    expected_reason: str,
) -> None:
    from mediaflow.desktop import app as desktop_app

    settings_path = tmp_path / "desktop-settings.json"
    settings_path.write_bytes(raw_settings)
    monkeypatch.setenv("MEDIAFLOW_DESKTOP_SETTINGS_PATH", str(settings_path))

    loaded = desktop_app.load_startup_settings(settings_path)

    assert loaded.recovered is True
    assert loaded.archived_path is not None
    assert loaded.archived_path.parent == tmp_path / "archive"
    assert loaded.archived_path.read_bytes() == raw_settings
    assert not settings_path.exists()
    assert expected_reason in loaded.error
    assert desktop_app.configure_startup_surface(loaded) is True

    notices: list[tuple[str, str]] = []
    monkeypatch.setattr(
        desktop_app.QMessageBox,
        "warning",
        lambda _parent, title, message: notices.append((title, message)),
    )
    assert desktop_app.show_startup_settings_recovery(loaded) is True
    assert notices and str(loaded.archived_path) in notices[0][1]
    assert loaded.error in notices[0][1]

    assert DesktopSettingsRepository(settings_path).default_settings() == loaded.settings


def test_window_state_persists_normal_geometry_and_maximized_flag() -> None:
    controllers = EditorControllers()
    try:
        controllers.workspace_settings.saveWindowState(1024, 640, True)

        persisted = DesktopSettingsRepository().load().ui
        assert (persisted.window_width, persisted.window_height) == (1024, 640)
        assert persisted.window_maximized is True
        assert controllers.settings.settingsData["windowMaximized"] is True
    finally:
        controllers.shutdown()


def test_dubbing_transcription_infers_the_only_audio_track(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = EditorApplication()
    controllers = EditorControllers(application=DesktopPresentationApplication(application))
    selected_languages: list[str] = []
    monkeypatch.setattr(
        "mediaflow.desktop.controllers.dubbing_controller.start_current_transcription_task",
        lambda _session, asr: selected_languages.append(asr.language),
    )
    try:
        controllers.workspace_project.createProject(str(tmp_path), "Dubbing Audio")
        source = tmp_path / "dialogue.wav"
        with wave.open(str(source), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(48_000)
            audio.writeframes(b"\0\0" * 48_000)
        current = controllers.session.state.binding.current
        timeline = controllers.session.state.binding.timeline
        asset = current.import_external_asset(source, expected_kind=AssetKind.AUDIO)
        track = timeline.add_track(TrackKind.AUDIO)
        timeline.add_clip(
            track_id=track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=asset.metadata.duration_frames,
        )

        readiness = controllers.dubbing.sourceReadiness()
        assert readiness["available"] is True
        assert readiness["active"] is False

        controllers.dubbing.transcribeSource("en")

        selected_track = next(item for item in timeline.state.tracks if item.id == track.id)
        assert selected_track.primary_dialogue is True
        assert selected_languages == ["en"]
    finally:
        controllers.shutdown()


def test_clip_drag_preview_uses_local_index_without_remote_project_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = EditorApplication()
    controllers = EditorControllers(application=DesktopPresentationApplication(application))
    try:
        controllers.workspace_project.createProject(str(tmp_path), "Local Drag Preview")
        image_path = tmp_path / "drag-preview.png"
        image = QImage(32, 18, QImage.Format.Format_RGB32)
        image.fill(QColor("#334455"))
        assert image.save(str(image_path))
        project = controllers.session.state.binding.require_current()
        timeline = controllers.session.state.binding.require_timeline()
        asset = project.import_external_asset(image_path, expected_kind=AssetKind.IMAGE)
        track = timeline.add_track(TrackKind.VIDEO)
        clip = timeline.add_clip(
            track_id=track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=24,
        )
        controllers.session.projectors.timeline.refresh_timeline()
        monkeypatch.setattr(
            timeline,
            "preview_move_clips",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("drag preview crossed the project RPC boundary")
            ),
        )

        preview = controllers.timeline_clips.previewClipMove(
            clip.id,
            12,
            0,
            False,
        )

        assert preview["accepted"] is True
        assert preview["trackId"] == track.id
    finally:
        controllers.shutdown()


def test_clip_row_projection_preserves_metrics_until_audio_content_changes(
    tmp_path: Path,
) -> None:
    application = EditorApplication()
    controllers = EditorControllers(application=DesktopPresentationApplication(application))
    try:
        controllers.workspace_project.createProject(str(tmp_path), "Metrics Ownership")
        image_path = tmp_path / "metrics-ownership.png"
        image = QImage(32, 18, QImage.Format.Format_RGB32)
        image.fill(QColor("#334455"))
        assert image.save(str(image_path))
        project = controllers.session.state.binding.require_current()
        timeline = controllers.session.state.binding.require_timeline()
        asset = project.import_external_asset(image_path, expected_kind=AssetKind.IMAGE)
        track = timeline.add_track(TrackKind.VIDEO)
        clip = timeline.add_clip(
            track_id=track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=24,
        )
        controllers.session.projectors.timeline.refresh_timeline()
        metrics = {"integratedLufs": -24.4, "truePeakDbtp": -20.1}
        controllers.session.state.presentation.audio_metrics = metrics

        controllers.session.projectors.timeline.refresh_clip_rows(
            [clip.id],
            clips=[clip],
            refresh_relations=False,
            schedule_preview=False,
        )

        assert controllers.audio.audioMetrics == metrics

        controllers.timeline_clips.setClipAudio(clip.id, -3.0, 0.0, 0, 0)

        assert controllers.audio.audioMetrics == {}
    finally:
        controllers.shutdown()


def test_shutdown_drains_project_readers_before_releasing_project() -> None:
    controllers = EditorControllers()
    events: list[str] = []
    original_project_shutdown = controllers.session.background.shutdown_project_requests
    original_application_shutdown = controllers.session.background.shutdown_application_requests
    original_lifecycle_shutdown = controllers.session.lifecycle.shutdown

    def shutdown_project_requests() -> None:
        events.append("project-requests")
        original_project_shutdown()

    def shutdown_application_requests() -> None:
        events.append("application-requests")
        original_application_shutdown()

    def shutdown_lifecycle() -> None:
        events.append("project")
        original_lifecycle_shutdown()

    controllers.session.background.shutdown_project_requests = shutdown_project_requests
    controllers.session.background.shutdown_application_requests = shutdown_application_requests
    controllers.session.lifecycle.shutdown = shutdown_lifecycle

    controllers.shutdown()

    assert events == ["project-requests", "project", "application-requests"]


def test_shutdown_finishes_every_cleanup_stage_after_cancellation_failure() -> None:
    controllers = EditorControllers()
    events: list[str] = []

    def fail_filmstrip_cancel() -> None:
        events.append("filmstrip")
        raise RuntimeError("controlled filmstrip cancellation failure")

    controllers.session.lifecycle.cancel_filmstrip = fail_filmstrip_cancel
    controllers.session.background.shutdown_project_requests = lambda: events.append("project-requests")
    controllers.session.lifecycle.shutdown = lambda: events.append("project")
    controllers.workspace_project._finish_pending_project_close = lambda: events.append("pending-close")
    controllers.session._api.close_client_transport = lambda: events.append("transport")
    controllers.session.background.shutdown_application_requests = lambda: events.append(
        "application-requests"
    )

    with pytest.raises(RuntimeError, match="controlled filmstrip cancellation failure"):
        controllers.shutdown()

    assert controllers.session.state.requests.shutting_down is True
    assert events == [
        "filmstrip",
        "project-requests",
        "project",
        "pending-close",
        "transport",
        "application-requests",
    ]


def test_project_request_shutdown_is_bounded_for_uncooperative_reader() -> None:
    controllers = EditorControllers()
    release = threading.Event()
    started = threading.Event()
    controllers.session.background.submit(
        "timeline_filmstrip",
        (1, 1, "sequence"),
        lambda: (started.set(), release.wait(5)),
        publish_result=False,
    )
    assert started.wait(2)

    try:
        with pytest.raises(TimeoutError, match="project background request"):
            controllers.session.background.shutdown_project_requests(timeout=0.01)
    finally:
        release.set()
        controllers.shutdown()


def test_paused_import_keeps_pending_timeline_drop_until_terminal_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controllers = EditorControllers()
    session = controllers.session
    session.state.binding.project_id = "project"
    session.state.binding.generation = 7
    monkeypatch.setattr(session.projectors.tasks, "refresh_tasks", lambda: None)
    monkeypatch.setattr(session.projectors.assets, "refresh_assets", lambda: None)
    monkeypatch.setattr(
        session.projectors.workspace,
        "refresh_recent_projects",
        lambda: None,
    )
    paused = Task(
        id="import-task",
        project_id="project",
        command=ImportAssetCommand(source_path="D:/fixture.mp4"),
        status=TaskStatus.PAUSED,
        revision=1,
    )
    session.state.assets.pending_import_tasks[paused.id] = ("batch", 0)
    session.state.assets.pending_import_batches["batch"] = ImportDropBatch(
        placement=TimelinePlacement(),
        asset_ids=[None],
        pending_task_ids={paused.id},
    )

    try:
        session.tasks._on_event(
            (
                7,
                TaskEvent(
                    paused.id,
                    paused.project_id,
                    "updated",
                    paused.revision,
                    paused.model_dump(mode="json", exclude_computed_fields=True),
                    cursor=1,
                ),
            )
        )
        assert paused.id in session.state.assets.pending_import_tasks
        assert paused.id in session.state.assets.pending_import_batches["batch"].pending_task_ids

        failed = paused.model_copy(
            update={
                "status": TaskStatus.FAILED,
                "revision": 2,
                "error": "fixture failure",
            }
        )
        session.tasks._on_event(
            (
                7,
                TaskEvent(
                    failed.id,
                    failed.project_id,
                    "updated",
                    failed.revision,
                    failed.model_dump(mode="json", exclude_computed_fields=True),
                    cursor=2,
                ),
            )
        )
        assert failed.id not in session.state.assets.pending_import_tasks
        assert "batch" not in session.state.assets.pending_import_batches
    finally:
        controllers.shutdown()


def test_workspace_action_capabilities_share_one_read_only_and_closing_boundary() -> None:
    controllers = EditorControllers()
    session = controllers.session
    try:
        session.state.binding.current = SimpleNamespace(read_only=True)
        read_only = controllers.workspace.actionCapabilities
        assert read_only["canEdit"] is False
        assert read_only["canImport"] is False
        assert read_only["canStartTasks"] is False
        assert read_only["canManageTasks"] is False
        assert read_only["canManageWorkflow"] is False
        assert read_only["canCloseProject"] is True

        session.state.binding.current = None
        session.state.requests.closing_project = SimpleNamespace(project_dir=Path("D:/closing-project"))
        closing = controllers.workspace.actionCapabilities
        assert closing["canOpenProject"] is False
        assert closing["canCreateProject"] is False
        assert closing["canCloseProject"] is False
        assert closing["projectReleasePending"] is True
        assert closing["projectClosing"] is False
    finally:
        session.state.binding.current = None
        session.state.requests.closing_project = None
        controllers.shutdown()


def test_settings_form_save_merges_user_changes_with_async_runtime_updates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controllers = EditorControllers()
    try:
        session = controllers.session
        draft = controllers.settings.settingsDraft
        draft.begin()
        draft.update("theme", "high_contrast")

        installed_path = tmp_path / "runtime" / "bin" / "xxl.exe"
        runtime_update = session.state.service_settings.model_copy(deep=True)
        runtime_update.asr.cli_path = str(installed_path)
        session.settings_persistence.commit(runtime_update)

        draft.flush()

        assert session.state.desktop_settings.ui.theme == "high_contrast"
        assert session.state.service_settings.asr.cli_path == str(installed_path)
        assert ServiceSettingsRepository().load().asr.cli_path == str(installed_path)
    finally:
        controllers.shutdown()


def test_speaker_clustering_install_result_becomes_the_default_runtime(
    tmp_path: Path,
) -> None:
    controllers = EditorControllers()
    try:
        python = tmp_path / "speaker-clustering" / "venv" / "Scripts" / "python.exe"
        model = tmp_path / "speaker-clustering" / "models" / "campplus.onnx"
        python.parent.mkdir(parents=True)
        model.parent.mkdir(parents=True)
        python.touch()
        model.touch()

        controllers.session.runtime_tools._on_event(
            {
                "type": "completed",
                "operation": "install_speaker_clustering",
                "result": {"python": str(python), "model": str(model)},
            }
        )

        configured = controllers.session.state.service_settings.speaker_diarization
        persisted = ServiceSettingsRepository().load().speaker_diarization
        assert configured.backend == "transcript_clustering"
        assert configured.clustering_python_executable == str(python)
        assert configured.embedding_model_path == str(model)
        assert persisted.clustering_python_executable == str(python)
        assert persisted.embedding_model_path == str(model)
    finally:
        controllers.shutdown()


def test_subtitle_preview_fallback_reframes_main_clock_but_placement_stays_authoritative(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = EditorApplication()
    project = application.create_project(
        tmp_path / "Subtitle Clock",
        "Subtitle Clock",
        ProjectProfile(fps_numerator=24, fps_denominator=1),
    )
    project_record = project.get_project()
    short_sequence = project.create_short_sequence(
        "60 fps Short",
        ProjectProfile(
            width=1080,
            height=1920,
            fps_numerator=60,
            fps_denominator=1,
        ),
    )
    subtitle_path = tmp_path / "captions.srt"
    subtitle_path.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nClock conversion\n",
        encoding="utf-8",
    )
    subtitle_asset = project.import_external_asset(
        subtitle_path,
        expected_kind=AssetKind.SUBTITLE,
    )
    document = SubtitleDocument(
        project_id=project_record.id,
        asset_id=subtitle_asset.id,
        sequence_id=project_record.main_sequence_id,
        language="en",
    )
    segment = SubtitleSegment(
        document_id=document.id,
        start_frame=24,
        end_frame=48,
        text="Clock conversion",
    )
    project._repository.subtitles.create_subtitle_document(
        document,
        [segment],
    )
    subtitle_track = project.timeline(short_sequence.id).add_track(
        TrackKind.SUBTITLE,
    )
    desktop_application = DesktopPresentationApplication(application)
    controllers = EditorControllers(application=desktop_application)
    try:
        controllers.session.lifecycle.replace(desktop_application.adapt_project(project))
        controllers.workspace_sequence.selectSequence(short_sequence.id)
        controllers.subtitle_view.selectSubtitleDocument(document.id)
        assert controllers.subtitle_view.subtitlePlacementsModel.rowCount() == 0

        preview_ranges: list[tuple[int, int]] = []
        controllers.subtitle_view.previewRangeRequested.connect(
            lambda start, end: preview_ranges.append((start, end))
        )
        controllers.subtitle_view.previewSubtitleSegment(segment.id)

        assert preview_ranges == [(60, 120)]
        assert (
            controllers.subtitle_view.subtitleSegmentTimelineFrame(
                segment.id,
                segment.start_frame,
            )
            == 60
        )

        placement = SubtitlePlacement(
            track_id=subtitle_track.id,
            segment_id=segment.id,
            start_frame=73,
            end_frame=109,
        )
        project._repository.subtitles.add_subtitle_placements([placement])
        controllers.session.projectors.timeline.refresh_preview_subtitles()
        assert controllers.subtitle_view.subtitlePlacementsModel.rowCount() == 1

        preview_ranges.clear()
        controllers.subtitle_view.previewSubtitleSegment(segment.id)

        assert preview_ranges == [(73, 109)]
        assert (
            controllers.subtitle_view.subtitleSegmentTimelineFrame(
                segment.id,
                segment.start_frame,
            )
            == 73
        )
    finally:
        controllers.shutdown()


def test_dict_list_model_applies_structural_and_value_changes_incrementally() -> None:
    model = SequenceListModel()
    resets: list[None] = []
    inserts: list[tuple[int, int]] = []
    moves: list[tuple[int, int]] = []
    changes: list[tuple[int, int]] = []
    model.modelReset.connect(lambda: resets.append(None))
    model.rowsInserted.connect(lambda _parent, first, last: inserts.append((first, last)))
    model.rowsMoved.connect(
        lambda _source_parent, first, _last, _destination_parent, destination: moves.append(
            (first, destination)
        )
    )
    model.dataChanged.connect(lambda first, last, _roles: changes.append((first.row(), last.row())))

    model.set_items(
        [
            {
                "sequenceId": "main",
                "name": "Main",
                "displayName": "Main",
                "kind": "main",
                "profile": "1920×1080",
                "colorMode": "sdr_bt709",
            },
            {
                "sequenceId": "short-a",
                "name": "A",
                "displayName": "A",
                "kind": "short",
                "profile": "1080×1920",
                "colorMode": "sdr_bt709",
            },
        ]
    )
    model.set_items(
        [
            {
                "sequenceId": "short-a",
                "name": "A revised",
                "displayName": "A revised",
                "kind": "short",
                "profile": "1080×1920",
                "colorMode": "sdr_bt709",
            },
            {
                "sequenceId": "main",
                "name": "Main",
                "displayName": "Main",
                "kind": "main",
                "profile": "1920×1080",
                "colorMode": "sdr_bt709",
            },
            {
                "sequenceId": "short-b",
                "name": "B",
                "displayName": "B",
                "kind": "short",
                "profile": "1080×1920",
                "colorMode": "sdr_bt709",
            },
        ]
    )

    assert resets == []
    assert inserts == [(0, 1), (2, 2)]
    assert moves == [(1, 0)]
    assert changes == [(0, 0)]
    assert [model.get(row)["sequenceId"] for row in range(model.rowCount())] == [
        "short-a",
        "main",
        "short-b",
    ]
    assert model.findRow("sequenceId", "short-a") == 0
    assert model.findRow("sequenceId", "main") == 1
    assert model.findRow("sequenceId", "missing") == -1
    snapshot = model.snapshot()
    snapshot[0]["name"] = "Changed outside the model"
    assert model.get(0)["name"] == "A revised"


def test_dict_list_model_rejects_rows_that_do_not_match_qml_roles() -> None:
    model = SequenceListModel()
    with pytest.raises(ValueError, match="missing=.*colorMode"):
        model.set_items([{"sequenceId": "main", "name": "Main"}])


def test_asset_filter_model_is_the_shared_search_boundary_for_all_views() -> None:
    assets = AssetListModel()
    assets.set_items(
        [
            {
                "assetId": asset_id,
                "name": name,
                "kind": "video",
                "path": f"D:/{name}",
                "status": "online",
                "managed": False,
                "binId": "nested" if asset_id == "two" else "",
                "durationFrames": 25,
                "width": 640,
                "height": 360,
                "previewUrl": "",
                "proxyReady": False,
                "waveformReady": False,
                "searchText": f"{name} video".casefold(),
            }
            for asset_id, name in (("one", "First Video.mp4"), ("two", "Overlay.png"))
        ]
    )
    filtered = AssetFilterModel(assets)

    assert filtered.rowCount() == 2
    filtered.setSearchText(" overlay ")
    assert filtered.rowCount() == 1
    assert assets.get(filtered.mapToSource(filtered.index(0, 0)).row())["assetId"] == "two"
    filtered.setSearchText("")
    assert filtered.rowCount() == 2
    filtered.set_bin_scope("parent", {"parent", "nested"})
    assert filtered.rowCount() == 1
    assert assets.get(filtered.mapToSource(filtered.index(0, 0)).row())["assetId"] == "two"
    filtered.set_bin_scope("__unfiled__", set())
    assert filtered.rowCount() == 1
    assert assets.get(filtered.mapToSource(filtered.index(0, 0)).row())["assetId"] == "one"


def test_source_monitor_uses_real_graph_range_insertion_and_requested_frame(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = EditorApplication()
    source = tmp_path / "source.mp4"
    generate_real_media(source, application.runtime_paths, width=160, height=90)
    project = application.create_project(tmp_path / "Source Monitor", "Source Monitor")
    asset = project.import_external_asset(source, expected_kind=AssetKind.VIDEO)
    asset = project._repository.assets.update_asset(
        asset.model_copy(
            update={
                "metadata": MediaMetadata(
                    duration_frames=25,
                    width=160,
                    height=90,
                    video_codec="h264",
                    audio_codec="aac",
                    has_audio=True,
                )
            }
        )
    )
    desktop_application = DesktopPresentationApplication(application)
    controllers = EditorControllers(application=desktop_application)
    try:
        controllers.session.lifecycle.replace(desktop_application.adapt_project(project))
        controllers.media.openSourceMonitor(asset.id)
        source_state = controllers.media.sourceMonitorData
        assert source_state["assetId"] == asset.id
        assert source_state["durationFrames"] == 25
        assert Path(source_state["graphPath"]).is_file()

        controllers.media.setSourceInFrame(5)
        controllers.media.setSourceOutFrame(14)
        controllers.media.addSourceRangeToTimeline(40, 1.0, False)
        [clip] = controllers.session.state.binding.timeline.state.clips
        assert (clip.source_in, clip.duration) == (5, 10)

        controllers.media.captureSourceFrame(0)
        first_capture = controllers.session.state.binding.current.resolve_asset_path(
            controllers.session.state.binding.current.get_asset(controllers.media.selectedAssetId)
        )
        controllers.media.captureSourceFrame(12)
        second_capture = controllers.session.state.binding.current.resolve_asset_path(
            controllers.session.state.binding.current.get_asset(controllers.media.selectedAssetId)
        )
        assert first_capture.is_file() and second_capture.is_file()
        assert first_capture.read_bytes() != second_capture.read_bytes()
    finally:
        controllers.shutdown()


def test_moment_search_consumes_persisted_spoken_and_visual_producers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = EditorApplication()
    project = application.create_project(tmp_path / "Moment Search", "Moment Search")
    source = tmp_path / "interview.mp4"
    generate_real_media(source, application.runtime_paths, width=160, height=90)
    asset = project.import_external_asset(source, expected_kind=AssetKind.VIDEO)
    project_record = project.get_project()
    document = SubtitleDocument(
        project_id=project_record.id,
        asset_id=asset.id,
        media_asset_id=asset.id,
        sequence_id=project_record.main_sequence_id,
        language="zh",
    )
    segment = SubtitleSegment(
        document_id=document.id,
        start_frame=12,
        end_frame=38,
        text="城市夜景中的关键观点",
    )
    project._repository.subtitles.create_subtitle_document(document, [segment])
    highlight = project.add_manual_highlight(
        asset.id,
        start_frame=5,
        end_frame=10,
        title="人物转身",
    )
    desktop_application = DesktopPresentationApplication(application)
    controllers = EditorControllers(application=desktop_application)
    try:
        controllers.session.lifecycle.replace(desktop_application.adapt_project(project))
        moments = controllers.session.models.asset_moments.snapshot()
        assert {(row["momentId"], row["momentType"]) for row in moments} == {
            (f"spoken:{segment.id}", "spoken"),
            (f"visual:{highlight.id}", "visual"),
        }

        controllers.media.setAssetSearchText("关键观点")
        spoken = controllers.media.filteredAssetMomentsModel
        assert spoken.rowCount() == 1
        spoken_row = spoken.mapToSource(spoken.index(0, 0)).row()
        assert controllers.session.models.asset_moments.get(spoken_row)["momentId"] == (
            f"spoken:{segment.id}"
        )

        controllers.media.setAssetSearchText("人物转身")
        visual = controllers.media.filteredAssetMomentsModel
        assert visual.rowCount() == 1
        visual_row = visual.mapToSource(visual.index(0, 0)).row()
        assert controllers.session.models.asset_moments.get(visual_row)["momentId"] == (
            f"visual:{highlight.id}"
        )
    finally:
        controllers.shutdown()


def test_download_plan_queues_selected_entries_as_typed_requests(
    tmp_path: Path,
    monkeypatch,
) -> None:
    captured: list[DownloadRequest] = []
    monkeypatch.setattr(ProjectSession, "_require_writable", lambda _self: None)
    monkeypatch.setattr(
        ProjectSession,
        "_start_download_workflow",
        lambda _self, requests: captured.extend(requests),
    )
    controllers = EditorControllers()
    session = controllers.session
    plan = PlatformMediaResolver._bilibili_plan(
        "https://www.bilibili.com/video/BV1234567890",
        {
            "bvid": "BV1234567890",
            "title": "Course",
            "owner": {"name": "Teacher"},
            "ugc_season": {
                "title": "Complete Course",
                "sections": [
                    {
                        "episodes": [
                            {"bvid": "BV0000000001", "title": "One"},
                            {"bvid": "BV0000000002", "title": "Two"},
                            {"bvid": "BV0000000003", "title": "Three"},
                        ]
                    }
                ],
            },
        },
    )
    assert plan is not None
    session._set_download_plan(plan)
    controllers.download_settings.setLastDownloadUrl("https://example.com/remembered-video")
    controllers.download_settings.setDefaultProjectDirectory(str(tmp_path))
    selected_directory = tmp_path / "Selected Downloads"
    controllers.download_settings.setDefaultDownloadDirectory(str(selected_directory))

    controllers.downloads.submitDownloadPlan(
        "1080p",
        "1,3",
        False,
        "best",
        "Prefix",
    )
    controllers.download_settings.resetDefaultDownloadDirectory()

    assert [request.entry.download_url for request in captured] == [
        "https://www.bilibili.com/video/BV0000000001",
        "https://www.bilibili.com/video/BV0000000003",
    ]
    assert {request.collection_title for request in captured} == {"Complete Course"}
    assert {request.filename_prefix for request in captured} == {"Prefix"}
    assert {request.output_directory for request in captured} == {str(selected_directory.resolve())}
    assert controllers.settings.settingsData["downloadResolution"] == "1080p"
    assert controllers.settings.settingsData["downloadSubtitles"] is False
    assert controllers.settings.settingsData["downloadCodec"] == "best"
    assert (
        controllers.settings.settingsData["downloadDirectory"]
        == controllers.download_settings.builtInMediaDirectory
    )
    persisted = ServiceSettingsRepository().load()
    assert persisted.download.last_url == "https://example.com/remembered-video"
    assert persisted.default_project_directory == str(tmp_path.resolve())
    assert persisted.download.resolution == "1080p"


def test_download_entry_model_exposes_unavailable_collection_slots() -> None:
    controllers = EditorControllers()
    plan = YtDlpDownloadService._plan_from_info(
        {
            "_type": "playlist",
            "id": "course",
            "title": "Course",
            "extractor_key": "YoutubeTab",
            "entries": [
                {
                    "id": "one",
                    "title": "One",
                    "webpage_url": "https://www.youtube.com/watch?v=one",
                },
                None,
            ],
        },
        "https://www.youtube.com/playlist?list=course",
    )

    controllers.session._set_download_plan(plan)

    model = controllers.downloads.downloadEntriesModel
    assert model.rowCount() == 2
    assert model.get(0)["selected"] is True
    assert model.get(1)["available"] is False
    assert model.get(1)["selected"] is False
    assert "无权访问" in model.get(1)["unavailableReason"]


def test_download_plan_preserves_source_profile_and_real_available_heights() -> None:
    plan = YtDlpDownloadService._plan_from_info(
        {
            "id": "profiled-video",
            "title": "Profiled Video",
            "extractor_key": "Fixture",
            "width": 3840,
            "height": 2160,
            "fps": 29.97002997,
            "formats": [
                {"height": 720, "vcodec": "h264"},
                {"height": 2160, "vcodec": "vp9"},
                {"height": 1080, "vcodec": "h264"},
                {"height": 1080, "vcodec": "av1"},
                {"height": None, "vcodec": "none"},
            ],
        },
        "https://example.com/profiled-video",
    )

    assert (plan.width, plan.height) == (3840, 2160)
    assert plan.fps == pytest.approx(29.97002997)
    assert plan.available_heights == [2160, 1080, 720]


def test_project_switch_rolls_back_to_live_previous_session_when_binding_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    application = EditorApplication()
    desktop_application = DesktopPresentationApplication(application)
    controllers = EditorControllers(application=desktop_application)
    session = controllers.session
    try:
        first = application.create_project(tmp_path / "First", "First")
        session.lifecycle.replace(desktop_application.adapt_project(first))
        first_id = session.state.binding.current.get_project().id
        second = application.create_project(tmp_path / "Second", "Second")
        original_refresh = session.projectors.refresh_project
        attempts = 0

        def fail_candidate_refresh_once(_projectors) -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("binding failed")
            original_refresh()

        monkeypatch.setattr(
            type(session.projectors),
            "refresh_project",
            fail_candidate_refresh_once,
        )
        with pytest.raises(RuntimeError, match="binding failed"):
            session.lifecycle.replace(desktop_application.adapt_project(second))

        assert session.state.binding.current.get_project().id == first_id
        assert session.state.binding.current.project_dir == (tmp_path / "First").resolve()
        assert controllers.workspace.sequencesModel.rowCount() == 1
        with application.open_project(tmp_path / "Second", writable=True) as reopened:
            assert reopened.get_project().name == "Second"
    finally:
        controllers.shutdown()


def test_read_only_desktop_open_preserves_interrupted_workflows_and_writable_open_reconciles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "Workflow Read Only"
    holder = ProjectRepository.create(root, "Workflow Read Only")
    application = EditorApplication()
    desktop_application = DesktopPresentationApplication(application)
    controllers = EditorControllers(application=desktop_application)
    try:
        project = holder.projects.get_project()
        paused_task = TaskRepository(holder).create(
            Task(
                project_id=project.id,
                sequence_id=project.main_sequence_id,
                command=AnalyzeDownloadCommand(url="https://example.invalid/paused"),
                status=TaskStatus.PAUSED,
            )
        )
        coordinator = WorkflowCoordinator(holder, global_auto_continue=False)
        empty = coordinator.begin(
            sequence_id=project.main_sequence_id,
            stage=WorkflowStage.DOWNLOAD,
            running=True,
        )
        missing = coordinator.begin(
            sequence_id=project.main_sequence_id,
            stage=WorkflowStage.PREPARE_MEDIA,
            payload=WorkflowPayload(task_ids=["missing-task"]),
            running=True,
        )
        paused = coordinator.begin(
            sequence_id=project.main_sequence_id,
            stage=WorkflowStage.TRANSCRIBE,
            payload=WorkflowPayload(task_ids=[paused_task.id]),
            running=True,
        )
        workflow_ids = {empty.id, missing.id, paused.id}
        before = {run.id: run.model_dump(mode="json") for run in holder.projects.list_workflow_runs()}
        revision_before = holder.content_revision()

        observer = application.open_project(root, writable=True)
        assert observer.read_only is True
        controllers.session.lifecycle.replace(desktop_application.adapt_project(observer))

        bound = controllers.session.state.binding.current
        assert bound.project_dir == observer.project_dir
        assert bound.read_only is True
        with pytest.raises(PermissionError, match="只读"):
            bound.reconcile_workflow()
        assert {run.id: run.model_dump(mode="json") for run in holder.projects.list_workflow_runs()} == before
        assert holder.content_revision() == revision_before

        controllers.session.lifecycle.close(close_in_background=False)
        holder.close()
        writable = application.open_project(root, writable=True)
        assert writable.read_only is False
        controllers.session.lifecycle.replace(desktop_application.adapt_project(writable))

        reconciled = {run.id: run for run in writable.list_workflow_runs() if run.id in workflow_ids}
        assert set(reconciled) == workflow_ids
        assert {run.status for run in reconciled.values()} == {WorkflowStatus.BLOCKED}
        assert {run.message_code for run in reconciled.values()} == {"workflow_interrupted"}
    finally:
        if holder.owns_project_lock:
            holder.close()
        controllers.shutdown()


def test_default_export_uses_project_folder_and_avoids_existing_names(
    tmp_path: Path,
    monkeypatch,
) -> None:
    application = EditorApplication()
    controllers = EditorControllers(application=DesktopPresentationApplication(application))
    captured: list[ExportSequenceCommand] = []

    def capture_task(command, *, sequence_id=None):
        del sequence_id
        captured.append(command)
        return None

    try:
        controllers.workspace_project.createProject(
            str(tmp_path),
            "Export Defaults",
        )
        image_path = tmp_path / "export-content.png"
        image = QImage(16, 16, QImage.Format.Format_RGB32)
        image.fill(QColor("#56d6cb"))
        assert image.save(str(image_path))
        current = controllers.session.state.binding.current
        asset = current.import_external_asset(
            image_path,
            expected_kind=AssetKind.IMAGE,
        )
        track = controllers.session.state.binding.timeline.add_track(TrackKind.VIDEO)
        controllers.session.state.binding.timeline.add_clip(
            track_id=track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=24,
        )
        monkeypatch.setattr(controllers.session.tasks, "start", capture_task)

        controllers.export.exportSequenceToDefaultLocation("h264", "mp4", {})
        first_output = Path(captured[-1].output_path)
        assert first_output == tmp_path / "Export Defaults" / "exports" / "主序列.mp4"
        assert captured[-1].overwrite is False
        first_output.touch()

        controllers.export.exportSequenceToDefaultLocation("h264", "mp4", {})
        assert Path(captured[-1].output_path) == (tmp_path / "Export Defaults" / "exports" / "主序列 (2).mp4")
        assert captured[-1].overwrite is False

        confirmed_output = tmp_path / "confirmed-overwrite.mp4"
        controllers.export.exportSequenceWithOptions(
            "h264",
            str(confirmed_output),
            {},
        )
        assert Path(captured[-1].output_path) == confirmed_output
        assert captured[-1].overwrite is True
    finally:
        controllers.shutdown()


def test_double_loudness_trigger_reuses_the_same_active_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = EditorApplication()
    controllers = EditorControllers(application=DesktopPresentationApplication(application))
    handler_started = threading.Event()
    release_handler = threading.Event()
    executions = 0

    def block_loudness(_context) -> TaskCompletion:
        nonlocal executions
        executions += 1
        handler_started.set()
        assert release_handler.wait(5)
        return TaskCompletion()

    try:
        controllers.workspace_project.createProject(str(tmp_path), "Double Trigger")
        current = controllers.session.state.binding.current
        current._tasks._execution.handlers[TaskKind.ANALYZE] = block_loudness

        controllers.audio.analyzeLoudness()
        assert handler_started.wait(5)
        controllers.audio.analyzeLoudness()

        tasks = [task for task in current.list_tasks() if task.kind == TaskKind.ANALYZE]
        assert len(tasks) == 1
        assert executions == 1
    finally:
        release_handler.set()
        controllers.shutdown()


def test_open_desktop_consumes_persisted_task_events_from_project_service(
    tmp_path: Path,
) -> None:
    controllers = EditorControllers(application=DesktopPresentationApplication(EditorApplication()))
    try:
        controllers.workspace_project.createProject(str(tmp_path), "External Task")
        session = controllers.session
        assert controllers.tasks.tasksModel.rowCount() == 0
        project_revision = session.state.binding.current.content_revision()

        task = session.state.binding.current.start_task(
            AnalyzeDownloadCommand(url="https://example.invalid/media")
        )
        external = session.state.binding.current.wait_for_task(task.id, timeout=5)
        assert external.status == TaskStatus.FAILED
        assert session.state.binding.current.content_revision() == project_revision

        session.lifecycle.reconcile_task_events()

        assert controllers.tasks.tasksModel.rowCount() == 1
        row = controllers.tasks.tasksModel.get(0)
        assert row["taskId"] == external.id
        assert row["status"] == "failed"
        assert row["error"] == external.error
        assert row["error"]
        assert session.state.binding.current.committed_task_result(external.id) is not None
    finally:
        controllers.shutdown()


def _create_completed_sequence_boundary_task(
    project,
    source: Path,
) -> tuple[Task, str]:
    sequence_id = project.get_project().main_sequence_id
    source.write_bytes(b"desktop sequence boundary source")
    asset = project._repository.assets.import_external_asset(
        source,
        AssetKind.VIDEO,
    )
    asset = project._repository.assets.update_asset(
        asset.model_copy(
            update={
                "metadata": asset.metadata.model_copy(
                    update={
                        "duration_frames": 100,
                        "width": 1920,
                        "height": 1080,
                    }
                )
            }
        )
    )
    editor = project.timeline(sequence_id)
    track = editor.add_track(TrackKind.VIDEO)
    editor.add_clip(
        track_id=track.id,
        asset_id=asset.id,
        timeline_start=0,
        source_in=0,
        duration=100,
    )
    snapshot_hash = project.sequence_boundary_snapshot_hash(sequence_id)
    outcome = SequenceBoundaryTaskOutcome(
        analysis=SequenceBoundaryAnalysis(
            sequence_id=sequence_id,
            snapshot_hash=snapshot_hash,
            duration_frames=100,
            suggested=SequenceInOut(
                in_frame=10,
                out_frame=90,
            ),
            speech_in_frame=10,
            speech_out_frame=90,
        )
    )
    project._tasks._execution.handlers[TaskKind.ANALYZE] = lambda _context: TaskCompletion(outcome=outcome)
    task = project.start_task(
        AnalyzeSequenceBoundsCommand(
            sequence_id=sequence_id,
            snapshot_hash=snapshot_hash,
        ),
        sequence_id=sequence_id,
    )
    return project.wait_for_task(task.id, timeout=5), sequence_id


def test_open_desktop_projects_service_committed_terminal_task_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = EditorApplication()
    root = tmp_path / "Offline Completed Task"
    with application.create_project(root, "Offline Completed Task") as project:
        task, sequence_id = _create_completed_sequence_boundary_task(
            project,
            tmp_path / "offline-boundary.mp4",
        )
        assert project.load_timeline(sequence_id).sequence.in_out == (
            SequenceInOut(in_frame=10, out_frame=90)
        )
        assert (
            project._repository._fetchone(
                "SELECT task_id FROM task_consumption WHERE task_id=?",
                (task.id,),
            )["task_id"]
            == task.id
        )

    controllers = EditorControllers(application=DesktopPresentationApplication(application))
    try:
        controllers.workspace_project.openProject(str(root))
        current = controllers.session.state.binding.current

        assert current.load_timeline(sequence_id).sequence.in_out == (
            SequenceInOut(in_frame=10, out_frame=90)
        )
        assert (
            current._repository._fetchone(
                "SELECT task_id FROM task_consumption WHERE task_id=?",
                (task.id,),
            )["task_id"]
            == task.id
        )
    finally:
        controllers.shutdown()


def test_desktop_retries_terminal_task_projection_after_transient_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controllers = EditorControllers(application=DesktopPresentationApplication(EditorApplication()))
    errors: list[str] = []
    controllers.session.events.errorOccurred.connect(errors.append)
    try:
        controllers.workspace_project.createProject(
            str(tmp_path),
            "Retry Task Consumption",
        )
        current = controllers.session.state.binding.current
        task, sequence_id = _create_completed_sequence_boundary_task(
            current,
            tmp_path / "retry-boundary.mp4",
        )
        original_read = current.committed_task_result
        attempts = 0

        def fail_twice(task_id: str):
            nonlocal attempts
            attempts += 1
            if attempts <= 2:
                raise RuntimeError("injected transient projection failure")
            return original_read(task_id)

        monkeypatch.setattr(current, "committed_task_result", fail_twice)
        controllers.session.lifecycle.reconcile_task_events()
        assert attempts == 2
        assert any("将自动重试" in message for message in errors)

        controllers.session.lifecycle.reconcile_task_events()

        assert attempts == 3
        assert current.load_timeline(sequence_id).sequence.in_out == (
            SequenceInOut(in_frame=10, out_frame=90)
        )
        assert (
            current._repository._fetchone(
                "SELECT task_id FROM task_consumption WHERE task_id=?",
                (task.id,),
            )["task_id"]
            == task.id
        )
    finally:
        controllers.shutdown()
