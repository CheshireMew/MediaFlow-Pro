from __future__ import annotations

import os
import threading
import time
from ctypes import WinDLL, create_unicode_buffer
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickItem

from mediaflow.desktop.app import create_engine, load_application_font
from mediaflow.domain.enums import TaskKind, TaskStatus
from mediaflow.domain.models import HighlightCandidate, Task
from mediaflow.infrastructure.settings_repository import SettingsRepository
from mediaflow.infrastructure.task_repository import TaskRepository
from tests.v2.infrastructure.test_media_pipeline import generate_real_media


def _windows_environment(name: str) -> str | None:
    buffer = create_unicode_buffer(32768)
    length = WinDLL("kernel32", use_last_error=True).GetEnvironmentVariableW(
        name,
        buffer,
        len(buffer),
    )
    return buffer.value if length else None


def _windows_dll_directory() -> str | None:
    buffer = create_unicode_buffer(32768)
    length = WinDLL("kernel32", use_last_error=True).GetDllDirectoryW(len(buffer), buffer)
    return buffer.value if length else None


def test_qml_real_project_chain_is_visible_in_models(tmp_path: Path, monkeypatch) -> None:
    mlt_environment_before = {
        name: _windows_environment(name) for name in ("MLT_DATA", "MLT_REPOSITORY", "MLT_REPOSITORY_DENY")
    }
    dll_directory_before = _windows_dll_directory()
    monkeypatch.setenv("MEDIAFLOW_RUNTIME_DIR", str(tmp_path / "runtime"))
    app = QGuiApplication.instance() or QGuiApplication([])
    load_application_font(app)
    engine, controller = create_engine(app)
    try:
        controller.createProject(QUrl.fromLocalFile(str(tmp_path)).toString(), "UI Test")
        assert controller.hasProject is True
        assert controller.sequencesModel.rowCount() == 1
        assert controller.tracksModel.rowCount() == 3
        assert controller.tracksModel.get(0)["displayName"].startswith("视频 ")

        controller.settings.ui.language = "en"
        controller._refresh_timeline()
        controller._refresh_audio_buses()
        assert controller.tracksModel.get(0)["displayName"].startswith("Video ")
        assert {controller.audioBusesModel.get(index)["displayName"] for index in range(4)} == {
            "Master",
            "Dialogue",
            "Music",
            "Effects",
        }
        assert controller._localized_task_name("导出 1080p") == "Export 1080p"
        assert controller._localized_task_status("failed") == "Failed"
        assert controller._localized_task_message("export_verifying") == "Verifying export"
        assert next(
            option["label"]
            for option in controller.videoEncoderOptions
            if option["value"] == "libx264"
        ) == "H.264 Software"
        assert controller.subtitleTrackOptions[0]["label"] == "Do not burn in"
        controller.settings.ui.language = "zh_CN"
        controller._refresh_timeline()
        controller._refresh_audio_buses()

        source = tmp_path / "ui-source.mp4"
        generate_real_media(source, controller.paths, width=320, height=180)
        handler = partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        try:
            download_url = f"http://127.0.0.1:{server.server_address[1]}/{source.name}"
            controller.analyzeDownloadUrl(download_url)
            analyze_deadline = time.monotonic() + 20
            while time.monotonic() < analyze_deadline and not controller.downloadAnalysisReady:
                QCoreApplication.processEvents()
                time.sleep(0.02)
            assert controller.downloadAnalysisReady is True
            assert controller.downloadAnalysisData["title"] == "ui-source"
            assert controller.downloadAnalysisData["url"] == download_url
            controller.dismissDownloadAnalysis()
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=5)
        controller.importMedia(QUrl.fromLocalFile(str(source)).toString())
        assert controller.assetsModel.rowCount() == 1
        assert controller.workflowStage == "prepare_media"
        assert controller.workflowStatus == "awaiting_confirmation"

        asset_id = controller.assetsModel.get(0)["assetId"]
        controller.addAssetToTimeline(asset_id)
        QCoreApplication.processEvents()

        assert controller.clipsModel.rowCount() == 1
        assert controller.selectedClipId == controller.clipsModel.get(0)["clipId"]
        assert engine.rootObjects()[0].title() == "UI Test — MediaFlow Pro"

        first_clip = controller.clipsModel.get(0)
        controller.copyClip(first_clip["clipId"], 3.0, first_clip["endFrame"])
        assert controller.clipsModel.rowCount() == 2
        controller.addTransitionAfter(first_clip["clipId"], "dissolve", 8)
        assert controller.transitionsModel.rowCount() == 1
        transition_id = controller.transitionsModel.get(0)["transitionId"]
        controller.updateTransition(transition_id, "fade", 6)
        assert controller.transitionsModel.get(0)["durationFrames"] == 6
        controller.addTimelineMarker(12)
        controller.setRangeIn(10)
        controller.commitTimelineRange(24)
        assert controller.timelineMarkersModel.rowCount() == 1
        assert controller.timelineRangesModel.get(0)["endFrame"] == 24
        controller.undo()
        assert controller.timelineRangesModel.rowCount() == 0
        controller.redo()
        assert controller.timelineRangesModel.rowCount() == 1
        QCoreApplication.processEvents()

        controller.updateSequenceProfile(1080, 1920, 30, 1, "sdr_bt709", 2)
        assert (controller.profileWidth, controller.profileHeight) == (1080, 1920)
        assert (controller.profileFpsNumerator, controller.profileFpsDenominator) == (30, 1)

        highlight = HighlightCandidate(
            project_id=controller._repository.get_project().id,
            asset_id=asset_id,
            start_frame=2,
            end_frame=10,
            title="UI 高光",
            reason="验证区间预览",
            score=0.9,
        )
        controller._repository.save_highlights([highlight])
        controller._refresh_highlights()
        preview_ranges: list[tuple[int, int]] = []
        controller.previewRangeRequested.connect(
            lambda start, end: preview_ranges.append((start, end))
        )
        controller.previewHighlight(highlight.id)
        preview_deadline = time.monotonic() + 10
        while time.monotonic() < preview_deadline and not preview_ranges:
            QCoreApplication.processEvents()
            time.sleep(0.02)
        assert preview_ranges == [(2, 10)]

        root = engine.rootObjects()[0]
        page_loader = root.findChild(QQuickItem, "pageLoader")
        assert page_loader is not None
        workspace = page_loader.property("item")
        assert workspace is not None
        controller.savePanelLayout(340, 360, 380)
        controller.saveWindowSize(1440, 900)
        persisted_ui = SettingsRepository(tmp_path / "runtime" / "settings.json").load().ui
        assert (
            persisted_ui.left_panel_width,
            persisted_ui.inspector_width,
            persisted_ui.timeline_height,
        ) == (340, 360, 380)
        assert (persisted_ui.window_width, persisted_ui.window_height) == (1440, 900)
        workflow_banner = workspace.findChild(QQuickItem, "workflowBanner")
        assert workflow_banner is not None and workflow_banner.isVisible()
        controller.setProjectWorkflowMode("auto")
        assert controller.projectWorkflowMode == "auto"
        controller.setProjectWorkflowMode("confirm")
        assert controller.projectWorkflowMode == "confirm"
        workflow_run_id = controller.workflowRunId
        controller.continueWorkflow(workflow_run_id, "")
        workflow_deadline = time.monotonic() + 20
        while time.monotonic() < workflow_deadline:
            QCoreApplication.processEvents()
            if (
                controller.workflowStage == "transcribe"
                and controller.workflowStatus == "awaiting_confirmation"
            ):
                break
            time.sleep(0.02)
        assert controller.workflowStage == "transcribe"
        assert controller.workflowStatus == "awaiting_confirmation"
        assert controller.assetsModel.get(0)["waveformReady"] is True
        controller.cancelWorkflow(workflow_run_id)
        assert controller.workflowPending is False
        for mode in ("transcript", "translate", "highlight", "edit", "audio", "export", "media"):
            workspace.setProperty("activeMode", mode)
            QCoreApplication.processEvents()
            assert workspace.property("activeMode") == mode

        master_bus = next(
            controller.audioBusesModel.get(index)
            for index in range(controller.audioBusesModel.rowCount())
            if controller.audioBusesModel.get(index)["name"] == "主总线"
        )
        controller.addAudioBus("旁白")
        custom_bus = next(
            controller.audioBusesModel.get(index)
            for index in range(controller.audioBusesModel.rowCount())
            if controller.audioBusesModel.get(index)["name"] == "旁白"
        )
        assert custom_bus["parentBusId"] == master_bus["busId"]
        controller.updateAudioBus(
            custom_bus["busId"], -1.0, False, False, master_bus["busId"], "mono"
        )
        assert controller.audioBusesModel.get(
            controller.audioBusesModel.findRow("busId", custom_bus["busId"])
        )["channelLayout"] == "mono"
        controller.selectAudioBus(master_bus["busId"])
        controller.updateAudioBus(master_bus["busId"], -3.0, False, False)
        controller.addAudioEffect(master_bus["busId"], "limiter")
        assert controller.audioEffectsModel.rowCount() == 1
        assert controller.audioEffectsModel.get(0)["kind"] == "limiter"
        limiter_id = controller.audioEffectsModel.get(0)["effectId"]
        controller.selectAudioEffect(limiter_id)
        assert controller.audioEffectParametersModel.get(0)["key"] == "ceiling_db"
        controller.setAudioEffectParameter(limiter_id, "ceiling_db", -2.0)
        assert controller.audioEffectsModel.get(0)["parameters"]["ceiling_db"] == -2.0
        controller.addAudioEffect(master_bus["busId"], "compressor")
        compressor_id = controller.selectedAudioEffectId
        controller.moveAudioEffect(compressor_id, 0)
        assert controller.audioEffectsModel.get(0)["effectId"] == compressor_id
        presets = controller.audioEffectPresets("compressor")
        assert {item["presetId"] for item in presets} >= {"default", "dialogue", "strong"}
        controller.applyAudioEffectPreset(compressor_id, "dialogue")
        assert controller.audioEffectsModel.get(0)["parameters"]["threshold_db"] == -20.0
        controller.removeAudioEffect(compressor_id)
        assert controller.audioEffectsModel.rowCount() == 1
        controller.selectAudioEffect(limiter_id)

        controller.analyzeLoudness()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            QCoreApplication.processEvents()
            analyze_tasks = [
                controller.tasksModel.get(index)
                for index in range(controller.tasksModel.rowCount())
                if controller.tasksModel.get(index)["kind"] == "analyze"
            ]
            if analyze_tasks and analyze_tasks[0]["status"] in {"completed", "failed"}:
                break
            time.sleep(0.02)
        assert analyze_tasks[0]["status"] == "completed", analyze_tasks[0]["error"]
        assert -100.0 < controller.audioMetrics["integratedLufs"] < 0.0
        workspace.setProperty("activeMode", "audio")
        QCoreApplication.processEvents()
        integrated_texts = workspace.findChildren(QQuickItem, "audioMetricValue3")
        visible_values = [str(item.property("text")) for item in integrated_texts if item.isVisible()]
        assert any(value.endswith("LUFS") and not value.startswith("—") for value in visible_values)
        parameter_list = workspace.findChild(QQuickItem, "audioParameterList")
        audio_scroll = workspace.findChild(QQuickItem, "audioScroll")
        audio_content = workspace.findChild(QQuickItem, "audioContent")
        assert parameter_list is not None and parameter_list.isVisible(), (
            controller.selectedAudioBusId,
            controller.selectedAudioEffectId,
            parameter_list.width() if parameter_list else None,
            parameter_list.height() if parameter_list else None,
            parameter_list.parent().property("width") if parameter_list else None,
            parameter_list.parent().property("height") if parameter_list else None,
            parameter_list.parent().property("visible") if parameter_list else None,
            (audio_scroll.width(), audio_scroll.height()) if audio_scroll else None,
            (audio_content.width(), audio_content.height()) if audio_content else None,
        )

        video_track = next(
            controller.tracksModel.get(index)
            for index in range(controller.tracksModel.rowCount())
            if controller.tracksModel.get(index)["kind"] == "video"
        )
        controller.updateTrack(
            video_track["trackId"],
            True,
            True,
            False,
            False,
            video_track["audioBusId"],
        )
        controller.moveTrack(video_track["trackId"], 1)
        moved_track = controller.tracksModel.get(1)
        assert moved_track["trackId"] == video_track["trackId"]
        assert moved_track["locked"] is True

        project_path = Path(controller.projectPath)
        recent_artifact = project_path / "exports" / "recent-output.txt"
        recent_artifact.write_text("observable output", encoding="utf-8")
        TaskRepository(project_path).create(
            Task(
                project_id=controller._repository.get_project().id,
                sequence_id=controller.activeSequenceId,
                kind=TaskKind.EXPORT,
                status=TaskStatus.FAILED,
                name="失败导出",
                error="fixture failure",
                artifacts=[str(recent_artifact)],
            )
        )
        controller.closeProject()
        assert controller.homeSummary["failedTaskCount"] >= 1
        assert controller.homeSummary["recentArtifactCount"] >= 1
        recent_row = controller.recentProjectsModel.get(0)
        assert recent_row["recentArtifact"] == str(recent_artifact)
    finally:
        controller.shutdown()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        QCoreApplication.processEvents()
    assert {
        name: _windows_environment(name) for name in ("MLT_DATA", "MLT_REPOSITORY", "MLT_REPOSITORY_DENY")
    } == mlt_environment_before
    assert _windows_dll_directory() == dll_directory_before
