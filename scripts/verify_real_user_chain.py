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

from mediaflow.desktop.controllers.controller_hub import EditorControllers
from mediaflow.domain.enums import TaskStatus, TrackKind, WorkflowStage, WorkflowStatus
from mediaflow.infrastructure.runtime_paths import RuntimePaths

TEST_ROOT = Path("D:/Tools/MediaFlow/test-runs")


class QuietFileHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_):
        return


def process_events() -> None:
    QCoreApplication.processEvents()
    time.sleep(0.02)


def failed_task(controller: EditorControllers):
    if not controller.session._tasks:
        return None
    return next(
        (task for task in controller.session._tasks.list() if task.status == TaskStatus.FAILED),
        None,
    )


def wait_workflow(
    controller: EditorControllers,
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
            raise RuntimeError(f"{failure.kind.value}: {failure.error}")
        if (
            controller.workspace.workflowStage == stage.value
            and controller.workspace.workflowStatus == status.value
        ):
            return
    raise TimeoutError(
        f"Workflow did not reach {stage.value}/{status.value}: "
        f"{controller.workspace.workflowStage}/{controller.workspace.workflowStatus}"
    )


def wait_download_plan(controller: EditorControllers, *, timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        process_events()
        failure = failed_task(controller)
        if failure:
            raise RuntimeError(f"{failure.kind.value}: {failure.error}")
        if controller.tasks.downloadPlanReady:
            return
    raise TimeoutError("Download analysis did not produce a plan")


def wait_export(controller: EditorControllers, output: Path, *, timeout: float = 300) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        process_events()
        tasks = [
            task
            for task in controller.session._tasks.list()
            if task.kind.value == "export"
            and str(output.resolve()) in {str(Path(value).resolve()) for value in task.artifacts}
        ]
        if tasks and tasks[-1].status == TaskStatus.COMPLETED:
            return
        failure = failed_task(controller)
        if failure:
            raise RuntimeError(f"{failure.kind.value}: {failure.error}")
    raise TimeoutError(f"Export did not finish: {output}")


def create_image(path: Path, paths: RuntimePaths) -> None:
    result = subprocess.run(
        [
            str(paths.ffmpeg),
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


def create_music(path: Path, paths: RuntimePaths) -> None:
    result = subprocess.run(
        [
            str(paths.ffmpeg),
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


def create_spoken_video(path: Path, paths: RuntimePaths) -> None:
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
            str(paths.ffmpeg),
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


def import_without_workflow(controller: EditorControllers, path: Path) -> str:
    before = {asset.id for asset in controller.session._documents.list_assets()}
    controller.media.importMedia(QUrl.fromLocalFile(str(path)).toString())
    deadline = time.monotonic() + 30
    asset_id = ""
    run_id = ""
    while time.monotonic() < deadline:
        process_events()
        added = [asset.id for asset in controller.session._documents.list_assets() if asset.id not in before]
        if added:
            asset_id = added[0]
            run = next(
                (
                    run
                    for run in controller.session._documents.list_workflow_runs(active_only=True)
                    if asset_id in run.asset_ids
                ),
                None,
            )
            if run is not None:
                run_id = run.id
                break
    if not asset_id or not run_id:
        raise TimeoutError(f"Imported asset workflow was not visible: {path}")
    controller.workspace.cancelWorkflow(run_id)
    return asset_id


def wait_preview_graph(
    controller: EditorControllers,
    *,
    previous: str = "",
    timeout: float = 30,
) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        process_events()
        if controller.workspace.previewGraphPath and controller.workspace.previewGraphPath != previous:
            return controller.workspace.previewGraphPath
    raise TimeoutError("Preview graph was not generated")


def main() -> None:
    app = QGuiApplication.instance() or QGuiApplication([])
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    project_parent = TEST_ROOT / f"real-user-chain-{timestamp}"
    project_parent.mkdir(parents=True, exist_ok=False)
    errors: list[str] = []
    controller = EditorControllers()
    paths = RuntimePaths.discover()
    fixture_server = None
    fixture_thread = None
    controller.session.errorOccurred.connect(errors.append)
    try:
        controller.session.settings.asr.model = "tiny.en"
        controller.session.settings.asr.device = "cpu"
        controller.session.settings.asr.compute_type = "int8"
        controller.session.settings.asr.language = "en"
        if not any(
            provider.enabled and provider.api_key for provider in controller.session.settings.llm_providers
        ):
            raise RuntimeError("No configured LLM provider is available for the real workflow")

        source_url = os.environ.get("MEDIAFLOW_E2E_URL")
        if not source_url:
            fixture = project_parent / "spoken-workflow-source.mp4"
            create_spoken_video(fixture, paths)
            fixture_server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                partial(QuietFileHandler, directory=str(project_parent)),
            )
            fixture_thread = threading.Thread(target=fixture_server.serve_forever, daemon=True)
            fixture_thread.start()
            source_url = f"http://127.0.0.1:{fixture_server.server_address[1]}/{fixture.name}"

        controller.workspace.createProject(
            QUrl.fromLocalFile(str(project_parent)).toString(),
            "MediaFlow Pro 真实链路",
        )
        controller.workspace.setProjectWorkflowMode("confirm")
        controller.tasks.analyzeDownloadUrl(source_url)
        wait_download_plan(controller)
        controller.tasks.submitDownloadPlan(
            controller.session.settings.download.resolution,
            "",
            controller.session.settings.download.download_subtitles,
            controller.session.settings.download.codec,
            "",
        )
        wait_workflow(
            controller,
            WorkflowStage.PREPARE_MEDIA,
            WorkflowStatus.AWAITING_CONFIRMATION,
            timeout=180,
        )
        video_asset_id = controller.media.selectedAssetId
        controller.media.addAssetToTimeline(video_asset_id)
        main_sequence_id = controller.workspace.activeSequenceId

        workflow_id = controller.workspace.workflowRunId
        controller.workspace.continueWorkflow(workflow_id, "")
        wait_workflow(
            controller,
            WorkflowStage.TRANSCRIBE,
            WorkflowStatus.AWAITING_CONFIRMATION,
            timeout=180,
        )
        controller.workspace.continueWorkflow(workflow_id, "")
        wait_workflow(
            controller,
            WorkflowStage.TRANSLATE,
            WorkflowStatus.AWAITING_CONFIRMATION,
            timeout=600,
        )
        controller.workspace.continueWorkflow(workflow_id, "zh_CN")
        wait_workflow(
            controller,
            WorkflowStage.HIGHLIGHT,
            WorkflowStatus.AWAITING_CONFIRMATION,
            timeout=300,
        )
        controller.workspace.continueWorkflow(workflow_id, "")
        wait_workflow(
            controller,
            WorkflowStage.CREATE_SHORTS,
            WorkflowStatus.AWAITING_CONFIRMATION,
            timeout=300,
        )
        controller.workspace.continueWorkflow(workflow_id, "")
        wait_workflow(
            controller,
            WorkflowStage.EXPORT,
            WorkflowStatus.AWAITING_CONFIRMATION,
            timeout=120,
        )

        repository = controller.session._documents
        workflow = repository.get_workflow_run(workflow_id)
        translated_id = workflow.payload.translated_document_ids[0]
        short_ids = workflow.payload.short_sequence_ids
        if not short_ids:
            raise RuntimeError("Highlight workflow created no short sequences")

        controller.workspace.selectSequence(main_sequence_id)
        controller.subtitles.placeSubtitleDocument(translated_id)
        image = project_parent / "overlay.png"
        music = project_parent / "music.wav"
        create_image(image, paths)
        create_music(music, paths)
        image_asset_id = import_without_workflow(controller, image)
        music_asset_id = import_without_workflow(controller, music)

        controller.workspace.selectSequence(main_sequence_id)
        controller.media.addAssetToTimeline(image_asset_id)
        video_clip = next(
            clip for clip in controller.session._editor.state.clips if clip.asset_id == video_asset_id
        )
        controller.timeline.addTransitionAfter(video_clip.id, "dissolve", 15)
        controller.media.addAssetToTimeline(image_asset_id)
        overlay_clip = controller.timeline.selectedClipId
        controller.timeline.addTrack("video")
        overlay_track = [
            track for track in controller.session._editor.state.tracks if track.kind == TrackKind.VIDEO
        ][-1]
        controller.timeline.moveClip(overlay_clip, 0, overlay_track.id)
        controller.timeline.setClipTransform(overlay_clip, 0.68, 0.08, 0.28, 0.28, 0, 0, 0, 0, 0, 0.9)
        controller.media.addAssetToTimeline(music_asset_id)
        controller.timeline.setClipAudio(controller.timeline.selectedClipId, -8.0, 0.0, 12, 24)
        master = next(
            bus for bus in repository.list_audio_buses(main_sequence_id) if bus.parent_bus_id is None
        )
        controller.audio.updateAudioBus(master.id, -1.0, False, False)
        controller.audio.addAudioEffect(master.id, "limiter")
        main_preview = wait_preview_graph(controller)

        main_subtitle_track = next(
            track for track in controller.session._editor.state.tracks if track.kind == TrackKind.SUBTITLE
        )
        main_output = repository.project_dir / "exports" / "main-real-chain.mp4"
        controller.export.exportSequenceWithOptions(
            "h264",
            QUrl.fromLocalFile(str(main_output)).toString(),
            {"burnSubtitleTrackId": main_subtitle_track.id, "preset": "veryfast"},
        )
        wait_export(controller, main_output)
        deadline = time.monotonic() + 30
        while controller.workspace.workflowPending and time.monotonic() < deadline:
            process_events()
        if controller.workspace.workflowPending:
            raise RuntimeError("Workflow did not finish after the verified main export")

        short_id = short_ids[0]
        controller.workspace.selectSequence(short_id)
        controller.subtitles.placeSubtitleDocument(translated_id)
        controller.media.addAssetToTimeline(music_asset_id)
        short_video = next(
            clip for clip in controller.session._editor.state.clips if clip.asset_id == video_asset_id
        )
        short_audio = next(
            clip for clip in controller.session._editor.state.clips if clip.asset_id == music_asset_id
        )
        controller.timeline.trimClip(short_audio.id, 0, min(short_audio.duration, short_video.duration))
        short_preview = wait_preview_graph(controller, previous=main_preview)
        short_subtitle_track = next(
            track for track in controller.session._editor.state.tracks if track.kind == TrackKind.SUBTITLE
        )
        short_output = repository.project_dir / "exports" / "short-real-chain.mp4"
        controller.export.exportSequenceWithOptions(
            "h264",
            QUrl.fromLocalFile(str(short_output)).toString(),
            {"burnSubtitleTrackId": short_subtitle_track.id, "preset": "veryfast"},
        )
        wait_export(controller, short_output)

        project_root = repository.project_dir
        task_count = len(controller.session._tasks.list())
        controller.workspace.closeProject()
        controller.workspace.openProject(QUrl.fromLocalFile(str(project_root)).toString())
        reopened = controller.session._documents
        if len(reopened.list_sequences()) < 2:
            raise RuntimeError("Short sequences were not restored after reopening")
        if len(reopened.list_subtitle_documents()) < 2:
            raise RuntimeError("Subtitle documents were not restored after reopening")
        if len(controller.session._tasks.list()) != task_count:
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
