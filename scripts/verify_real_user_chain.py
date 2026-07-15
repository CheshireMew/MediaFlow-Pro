from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from datetime import datetime
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QUrl
from PySide6.QtGui import QGuiApplication

from mediaflow.desktop.controllers.project_controller import ProjectController
from mediaflow.domain.enums import TaskStatus, TrackKind, WorkflowStage, WorkflowStatus
from mediaflow.infrastructure.task_repository import TaskRepository

TEST_ROOT = Path("D:/Tools/MediaFlow/test-runs")


class QuietFileHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_):
        return


def process_events() -> None:
    QCoreApplication.processEvents()
    time.sleep(0.02)


def failed_task(controller: ProjectController):
    if not controller._tasks:
        return None
    return next(
        (task for task in controller._tasks.repository.list() if task.status == TaskStatus.FAILED),
        None,
    )


def wait_workflow(
    controller: ProjectController,
    stage: WorkflowStage,
    status: WorkflowStatus,
    *,
    timeout: float,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        process_events()
        failure = failed_task(controller)
        if failure:
            raise RuntimeError(f"{failure.name}: {failure.error}")
        if controller.workflowStage == stage.value and controller.workflowStatus == status.value:
            return
    raise TimeoutError(
        f"Workflow did not reach {stage.value}/{status.value}: "
        f"{controller.workflowStage}/{controller.workflowStatus}"
    )


def wait_export(controller: ProjectController, output: Path, *, timeout: float = 300) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        process_events()
        tasks = [
            task
            for task in controller._tasks.repository.list()
            if task.kind.value == "export"
            and str(output.resolve()) in {str(Path(value).resolve()) for value in task.artifacts}
        ]
        if tasks and tasks[-1].status == TaskStatus.COMPLETED:
            return
        failure = failed_task(controller)
        if failure:
            raise RuntimeError(f"{failure.name}: {failure.error}")
    raise TimeoutError(f"Export did not finish: {output}")


def create_image(path: Path, controller: ProjectController) -> None:
    result = subprocess.run(
        [
            str(controller.paths.ffmpeg),
            "-y",
            "-hide_banner",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x2389f4:s=640x360",
            "-frames:v",
            "1",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr)


def create_music(path: Path, controller: ProjectController) -> None:
    result = subprocess.run(
        [
            str(controller.paths.ffmpeg),
            "-y",
            "-hide_banner",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=330:sample_rate=48000:duration=8",
            "-af",
            "volume=0.12",
            "-c:a",
            "pcm_s16le",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr)


def create_spoken_video(path: Path, controller: ProjectController) -> None:
    speech = path.with_suffix(".wav")
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$s.SetOutputToWaveFile('{str(speech).replace("'", "''")}'); "
        "$s.Speak('Welcome to Media Flow Pro. This real workflow checks downloading, "
        "transcription, translation, highlight analysis, short video editing, and export. "
        "Every generated result must remain available after reopening the project.'); "
        "$s.Dispose()"
    )
    spoken = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if spoken.returncode != 0:
        raise RuntimeError(spoken.stderr)
    encoded = subprocess.run(
        [
            str(controller.paths.ffmpeg),
            "-y",
            "-hide_banner",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x18263d:s=640x360:r=25",
            "-i",
            str(speech),
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if encoded.returncode != 0 or not path.is_file():
        raise RuntimeError(encoded.stderr)


def import_without_workflow(controller: ProjectController, path: Path) -> str:
    controller.importMedia(QUrl.fromLocalFile(str(path)).toString())
    asset_id = controller.selectedAssetId
    run_id = controller.workflowRunId
    if run_id:
        controller.cancelWorkflow(run_id)
    return asset_id


def wait_preview_graph(
    controller: ProjectController,
    *,
    previous: str = "",
    timeout: float = 30,
) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        process_events()
        if controller.previewGraphPath and controller.previewGraphPath != previous:
            return controller.previewGraphPath
    raise TimeoutError("Preview graph was not generated")


def main() -> None:
    app = QGuiApplication.instance() or QGuiApplication([])
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    project_parent = TEST_ROOT / f"real-user-chain-{timestamp}"
    project_parent.mkdir(parents=True, exist_ok=False)
    errors: list[str] = []
    controller = ProjectController()
    fixture_server = None
    fixture_thread = None
    controller.errorOccurred.connect(errors.append)
    try:
        controller.settings.asr.model = "tiny.en"
        controller.settings.asr.device = "cpu"
        controller.settings.asr.compute_type = "int8"
        controller.settings.asr.language = "en"
        if not any(provider.enabled and provider.api_key for provider in controller.settings.llm_providers):
            raise RuntimeError("No configured LLM provider is available for the real workflow")

        source_url = os.environ.get("MEDIAFLOW_E2E_URL")
        if not source_url:
            fixture = project_parent / "spoken-workflow-source.mp4"
            create_spoken_video(fixture, controller)
            fixture_server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                partial(QuietFileHandler, directory=str(project_parent)),
            )
            fixture_thread = threading.Thread(target=fixture_server.serve_forever, daemon=True)
            fixture_thread.start()
            source_url = (
                f"http://127.0.0.1:{fixture_server.server_address[1]}/{fixture.name}"
            )

        controller.createProject(
            QUrl.fromLocalFile(str(project_parent)).toString(),
            "MediaFlow Pro 真实链路",
        )
        controller.setProjectWorkflowMode("confirm")
        controller.downloadUrl(source_url)
        wait_workflow(
            controller,
            WorkflowStage.PREPARE_MEDIA,
            WorkflowStatus.AWAITING_CONFIRMATION,
            timeout=180,
        )
        video_asset_id = controller.selectedAssetId
        controller.addAssetToTimeline(video_asset_id)
        main_sequence_id = controller.activeSequenceId

        workflow_id = controller.workflowRunId
        controller.continueWorkflow(workflow_id, "")
        wait_workflow(
            controller,
            WorkflowStage.TRANSCRIBE,
            WorkflowStatus.AWAITING_CONFIRMATION,
            timeout=180,
        )
        controller.continueWorkflow(workflow_id, "")
        wait_workflow(
            controller,
            WorkflowStage.TRANSLATE,
            WorkflowStatus.AWAITING_CONFIRMATION,
            timeout=600,
        )
        controller.continueWorkflow(workflow_id, "zh_CN")
        wait_workflow(
            controller,
            WorkflowStage.HIGHLIGHT,
            WorkflowStatus.AWAITING_CONFIRMATION,
            timeout=300,
        )
        controller.continueWorkflow(workflow_id, "")
        wait_workflow(
            controller,
            WorkflowStage.CREATE_SHORTS,
            WorkflowStatus.AWAITING_CONFIRMATION,
            timeout=300,
        )
        controller.continueWorkflow(workflow_id, "")
        wait_workflow(
            controller,
            WorkflowStage.EXPORT,
            WorkflowStatus.AWAITING_CONFIRMATION,
            timeout=120,
        )

        repository = controller._repository
        workflow = repository.get_workflow_run(workflow_id)
        translated_id = workflow.payload["translated_document_ids"][0]
        short_ids = workflow.payload["short_sequence_ids"]
        if not short_ids:
            raise RuntimeError("Highlight workflow created no short sequences")

        controller.selectSequence(main_sequence_id)
        controller.placeSubtitleDocument(translated_id)
        image = project_parent / "overlay.png"
        music = project_parent / "music.wav"
        create_image(image, controller)
        create_music(music, controller)
        image_asset_id = import_without_workflow(controller, image)
        music_asset_id = import_without_workflow(controller, music)

        controller.selectSequence(main_sequence_id)
        controller.addAssetToTimeline(image_asset_id)
        video_clip = next(clip for clip in controller._editor.state.clips if clip.asset_id == video_asset_id)
        controller.addTransitionAfter(video_clip.id, "dissolve", 15)
        controller.addAssetToTimeline(image_asset_id)
        overlay_clip = controller.selectedClipId
        controller.addTrack("video")
        overlay_track = [track for track in controller._editor.state.tracks if track.kind == TrackKind.VIDEO][
            -1
        ]
        controller.moveClip(overlay_clip, 0, overlay_track.id)
        controller.setClipTransform(overlay_clip, 0.68, 0.08, 0.28, 0.28, 0, 0, 0, 0, 0, 0.9)
        controller.addAssetToTimeline(music_asset_id)
        controller.setClipAudio(controller.selectedClipId, -8.0, 0.0, 12, 24)
        master = next(
            bus for bus in repository.list_audio_buses(main_sequence_id) if bus.parent_bus_id is None
        )
        controller.updateAudioBus(master.id, -1.0, False, False)
        controller.addAudioEffect(master.id, "limiter")
        main_preview = wait_preview_graph(controller)

        main_subtitle_track = next(
            track for track in controller._editor.state.tracks if track.kind == TrackKind.SUBTITLE
        )
        main_output = repository.project_dir / "exports" / "main-real-chain.mp4"
        controller.exportSequenceWithOptions(
            "h264",
            QUrl.fromLocalFile(str(main_output)).toString(),
            {"burnSubtitleTrackId": main_subtitle_track.id, "preset": "veryfast"},
        )
        wait_export(controller, main_output)
        deadline = time.monotonic() + 30
        while controller.workflowPending and time.monotonic() < deadline:
            process_events()
        if controller.workflowPending:
            raise RuntimeError("Workflow did not finish after the verified main export")

        short_id = short_ids[0]
        controller.selectSequence(short_id)
        controller.placeSubtitleDocument(translated_id)
        controller.addAssetToTimeline(music_asset_id)
        short_video = next(clip for clip in controller._editor.state.clips if clip.asset_id == video_asset_id)
        short_audio = next(clip for clip in controller._editor.state.clips if clip.asset_id == music_asset_id)
        controller.trimClip(short_audio.id, 0, min(short_audio.duration, short_video.duration))
        short_preview = wait_preview_graph(controller, previous=main_preview)
        short_subtitle_track = next(
            track for track in controller._editor.state.tracks if track.kind == TrackKind.SUBTITLE
        )
        short_output = repository.project_dir / "exports" / "short-real-chain.mp4"
        controller.exportSequenceWithOptions(
            "h264",
            QUrl.fromLocalFile(str(short_output)).toString(),
            {"burnSubtitleTrackId": short_subtitle_track.id, "preset": "veryfast"},
        )
        wait_export(controller, short_output)

        project_root = repository.project_dir
        task_count = len(controller._tasks.repository.list())
        controller.closeProject()
        controller.openProject(QUrl.fromLocalFile(str(project_root)).toString())
        reopened = controller._repository
        if len(reopened.list_sequences()) < 2:
            raise RuntimeError("Short sequences were not restored after reopening")
        if len(reopened.list_subtitle_documents()) < 2:
            raise RuntimeError("Subtitle documents were not restored after reopening")
        if len(TaskRepository(project_root).list()) != task_count:
            raise RuntimeError("Task history changed after reopening")

        report = {
            "project": str(project_root),
            "source_url": source_url,
            "asset_count": len(reopened.list_assets()),
            "sequence_count": len(reopened.list_sequences()),
            "subtitle_document_count": len(reopened.list_subtitle_documents()),
            "highlight_count": len(reopened.list_highlights()),
            "task_count": task_count,
            "main_preview_graph": main_preview,
            "short_preview_graph": short_preview,
            "main_export": str(main_output),
            "short_export": str(short_output),
            "main_export_size": main_output.stat().st_size,
            "short_export_size": short_output.stat().st_size,
            "errors": errors,
        }
        report_path = project_root / "generated" / "real-user-chain-report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        controller.shutdown()
        if fixture_server is not None:
            fixture_server.shutdown()
            fixture_server.server_close()
        if fixture_thread is not None:
            fixture_thread.join(timeout=5)
        app.processEvents()


if __name__ == "__main__":
    main()
