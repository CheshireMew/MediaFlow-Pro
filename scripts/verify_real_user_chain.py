from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QUrl
from PySide6.QtGui import QGuiApplication

from mediaflow.atomic_file import atomic_write_text
from mediaflow.desktop.app import configure_application_identity
from mediaflow.desktop.controllers.controller_hub import EditorControllers
from mediaflow.domain.enums import (
    AssetKind,
    TaskStatus,
    TrackKind,
    WorkflowStage,
    WorkflowStatus,
)
from mediaflow.domain.settings import LlmProviderSettings
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from scripts.run_artifacts import verification_run

ROOT = Path(__file__).resolve().parents[1]
WEB_MEDIA_STARTER = Path(
    os.environ.get(
        "MEDIAFLOW_EDITABLE_MEDIA_PACKAGE",
        ROOT / "tests" / "fixtures" / "editable-media-v5",
    )
).resolve()


class QuietFileHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_):
        return


def raise_ui_errors(errors: list[str]) -> None:
    if errors:
        raise RuntimeError(f"Desktop UI error: {errors[-1]}")


def process_events(errors: list[str]) -> None:
    QCoreApplication.processEvents()
    time.sleep(0.02)
    raise_ui_errors(errors)


def wait_project_release(
    controller: EditorControllers,
    *,
    timeout: float = 30,
    errors: list[str],
) -> None:
    deadline = time.monotonic() + timeout
    while controller.workspace.projectReleasePending and time.monotonic() < deadline:
        process_events(errors)
        if controller.workspace.projectCloseFailed:
            raise RuntimeError(
                "Project resources could not be released: "
                f"{controller.workspace.projectCloseError}"
            )
    if controller.workspace.projectReleasePending:
        raise TimeoutError(
            "Project resources were not released before reopening: "
            f"{controller.workspace.closingProjectPath}"
        )


def failed_task(controller: EditorControllers):
    project = controller.session.binding.current
    if project is None:
        return None
    return next(
        (task for task in project.list_tasks() if task.status == TaskStatus.FAILED),
        None,
    )


def wait_workflow(
    controller: EditorControllers,
    stage: WorkflowStage,
    status: WorkflowStatus,
    *,
    timeout: float,
    errors: list[str],
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        process_events(errors)
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


def wait_download_plan(
    controller: EditorControllers,
    *,
    timeout: float = 60,
    errors: list[str],
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        process_events(errors)
        failure = failed_task(controller)
        if failure:
            raise RuntimeError(f"{failure.kind.value}: {failure.error}")
        if controller.tasks.downloadPlanReady:
            return
    raise TimeoutError("Download analysis did not produce a plan")


def wait_downloaded_video_selection(
    controller: EditorControllers,
    workflow_id: str,
    errors: list[str],
    *,
    timeout: float = 30,
) -> str:
    deadline = time.monotonic() + timeout
    last_state: dict[str, object] = {}
    while time.monotonic() < deadline:
        process_events(errors)
        project = controller.session.binding.current
        if project is None:
            raise RuntimeError("Project closed while waiting for downloaded media")
        run = next(
            (
                item
                for item in project.list_workflow_runs()
                if item.id == workflow_id
            ),
            None,
        )
        if run is None:
            raise RuntimeError(f"Download workflow disappeared: {workflow_id}")
        if run.status == WorkflowStatus.BLOCKED:
            raise RuntimeError(
                f"Download workflow was blocked before placement: {run.message_code}"
            )
        video_ids = [
            asset_id
            for asset_id in run.asset_ids
            if project.get_asset(asset_id).kind == AssetKind.VIDEO
        ]
        selected_id = controller.media.selectedAssetId
        last_state = {
            "workflow_asset_ids": list(run.asset_ids),
            "video_asset_ids": video_ids,
            "selected_asset_id": selected_id,
            "asset_rows": controller.media.assetsModel.rowCount(),
        }
        if selected_id in video_ids:
            asset = project.get_asset(selected_id)
            source = project.resolve_asset_path(asset)
            if source.is_file() and source.stat().st_size > 0:
                return selected_id
    raise TimeoutError(
        "Downloaded video never reached the media selection consumer: "
        f"{json.dumps(last_state, ensure_ascii=False)}"
    )


def place_downloaded_video_on_timeline(
    controller: EditorControllers,
    workflow_id: str,
    asset_id: str,
    errors: list[str],
    *,
    timeout: float = 30,
) -> str:
    controller.timeline.dropAssets(
        [asset_id],
        "",
        -1,
        0,
        3.0,
        0,
        True,
        False,
    )
    return wait_for_downloaded_video_placement(
        controller,
        workflow_id,
        asset_id,
        errors,
        timeout=timeout,
    )


def wait_for_downloaded_video_placement(
    controller: EditorControllers,
    workflow_id: str,
    asset_id: str,
    errors: list[str],
    *,
    timeout: float = 30,
) -> str:
    project = controller.session.binding.current
    if project is None:
        raise RuntimeError("Project must be open before placing downloaded media")
    sequence_id = controller.workspace.activeSequenceId
    deadline = time.monotonic() + timeout
    last_state: dict[str, object] = {}
    while time.monotonic() < deadline:
        process_events(errors)
        if controller.workspace.profileConfirmationPending:
            controller.workspace.resolveProfileAdoption(True)
            process_events(errors)

        state = controller.session.binding.timeline.state
        session_clip = next(
            (clip for clip in state.clips if clip.asset_id == asset_id),
            None,
        )
        session_track = (
            next(
                (
                    track
                    for track in state.tracks
                    if session_clip is not None
                    and track.id == session_clip.track_id
                    and track.kind == TrackKind.VIDEO
                ),
                None,
            )
            if session_clip is not None
            else None
        )
        projected_clip_ids = {
            str(controller.timeline.clipsModel.get(index)["clipId"])
            for index in range(controller.timeline.clipsModel.rowCount())
            if controller.timeline.clipsModel.get(index)["assetId"] == asset_id
        }
        last_state = {
            "profile_confirmation_pending": (
                controller.workspace.profileConfirmationPending
            ),
            "session_clip_id": session_clip.id if session_clip else "",
            "session_track_id": session_track.id if session_track else "",
            "projected_clip_ids": sorted(projected_clip_ids),
        }
        if (
            session_clip is None
            or session_track is None
            or session_clip.id not in projected_clip_ids
        ):
            continue

        with ProjectRepository.open(project.project_dir, writable=False) as persisted:
            persisted_state = persisted.timeline.load_timeline(sequence_id)
        persisted_clip = next(
            (
                clip
                for clip in persisted_state.clips
                if clip.id == session_clip.id and clip.asset_id == asset_id
            ),
            None,
        )
        persisted_track = (
            next(
                (
                    track
                    for track in persisted_state.tracks
                    if persisted_clip is not None
                    and track.id == persisted_clip.track_id
                    and track.kind == TrackKind.VIDEO
                ),
                None,
            )
            if persisted_clip is not None
            else None
        )
        if persisted_clip is None or persisted_track is None:
            continue

        workflow = next(
            (
                item
                for item in project.list_workflow_runs()
                if item.id == workflow_id
            ),
            None,
        )
        if workflow is None:
            raise RuntimeError(f"Workflow disappeared before continue: {workflow_id}")
        if (
            workflow.stage != WorkflowStage.PREPARE_MEDIA
            or workflow.status != WorkflowStatus.AWAITING_CONFIRMATION
        ):
            raise RuntimeError(
                "Workflow advanced before downloaded media became durable: "
                f"{workflow.stage.value}/{workflow.status.value}"
            )
        return persisted_clip.id
    raise TimeoutError(
        "Downloaded video did not reach the timeline consumer and database: "
        f"{json.dumps(last_state, ensure_ascii=False)}"
    )


def wait_export(
    controller: EditorControllers,
    output: Path,
    *,
    timeout: float = 300,
    errors: list[str],
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        process_events(errors)
        project = controller.session.binding.current
        if project is None:
            raise RuntimeError("Project closed while waiting for export")
        tasks = [
            task
            for task in project.list_tasks()
            if task.kind.value == "export"
            and output.resolve()
            in {
                value.resolve(project.project_dir)
                for value in task.artifacts
            }
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


def import_without_workflow(
    controller: EditorControllers,
    path: Path,
    errors: list[str],
) -> str:
    project = controller.session.binding.current
    if project is None:
        raise RuntimeError("Project must be open before importing")
    before = {asset.id for asset in project.list_assets()}
    controller.media.importFiles(
        [QUrl.fromLocalFile(str(path))]
    )
    deadline = time.monotonic() + 30
    asset_id = ""
    run_id = ""
    while time.monotonic() < deadline:
        process_events(errors)
        added = [asset.id for asset in project.list_assets() if asset.id not in before]
        if added:
            asset_id = added[0]
            run = next(
                (
                    run
                    for run in project.list_workflow_runs(active_only=True)
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
    errors: list[str],
) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        process_events(errors)
        if controller.workspace.previewGraphPath and controller.workspace.previewGraphPath != previous:
            return controller.workspace.previewGraphPath
    raise TimeoutError("Preview graph was not generated")


def verify(project_parent: Path) -> None:
    configure_application_identity()
    app = QGuiApplication.instance() or QGuiApplication([])
    errors: list[str] = []
    controller = EditorControllers()
    paths = RuntimePaths.discover()
    fixture_server = None
    fixture_thread = None
    controller.session.events.errorOccurred.connect(errors.append)
    try:
        run_root = project_parent.resolve()
        download_root = run_root / "downloads"
        controller.session.settings.download.output_directory = str(download_root)
        controller.session.settings.asr.model = "tiny.en"
        controller.session.settings.asr.device = "cpu"
        controller.session.settings.asr.compute_type = "int8"
        controller.session.settings.asr.language = "en"
        model_override = os.environ.get("MEDIAFLOW_E2E_MODEL", "").strip()
        api_key_override = (
            os.environ.get("MEDIAFLOW_E2E_API_KEY", "").strip()
            or os.environ.get("OPENAI_API_KEY", "").strip()
        )
        if (
            not controller.session.settings.llm_providers
            and model_override
            and api_key_override
        ):
            provider = LlmProviderSettings(
                name="Real workflow provider",
                base_url=(
                    os.environ.get("MEDIAFLOW_E2E_BASE_URL", "").strip()
                    or os.environ.get("OPENAI_BASE_URL", "").strip()
                    or "https://api.openai.com/v1"
                ),
                api_key=api_key_override,
                model=model_override,
            )
            controller.session.settings.llm_providers = [provider]
            controller.session.settings.active_llm_provider_id = provider.id
        if model_override and controller.session.settings.llm_providers:
            active_provider_id = controller.session.settings.active_llm_provider_id
            provider = next(
                (
                    item
                    for item in controller.session.settings.llm_providers
                    if item.id == active_provider_id
                ),
                None,
            )
            if provider is None:
                raise RuntimeError("No active LLM provider is available for model override")
            provider.model = model_override
        if not any(
            provider.enabled and provider.api_key for provider in controller.session.settings.llm_providers
        ):
            raise RuntimeError(
                "The isolated real workflow needs MEDIAFLOW_E2E_MODEL and "
                "MEDIAFLOW_E2E_API_KEY (or OPENAI_API_KEY)"
            )

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
        current_project = controller.session.binding.current
        if current_project is None:
            raise RuntimeError("Project creation did not bind a project")
        current_project.set_workflow_mode(False)
        controller.tasks.analyzeDownloadUrl(source_url)
        wait_download_plan(controller, errors=errors)
        controller.tasks.submitDownloadPlan(
            controller.session.settings.download.resolution,
            "",
            controller.session.settings.download.download_subtitles,
            controller.session.settings.download.codec,
            "",
        )
        workflow_id = controller.workspace.workflowRunId
        project = controller.session.binding.current
        if project is None or not workflow_id:
            raise RuntimeError("Download workflow did not start")
        download_workflow = next(
            run
            for run in project.list_workflow_runs()
            if run.id == workflow_id
        )
        request_directories = {
            Path(request.output_directory).expanduser().resolve()
            for request in download_workflow.payload.requests
        }
        if not request_directories or any(
            not directory.is_relative_to(run_root)
            for directory in request_directories
        ):
            raise RuntimeError(
                "Download workflow escaped the managed run: "
                f"{sorted(str(path) for path in request_directories)}"
            )
        wait_workflow(
            controller,
            WorkflowStage.PREPARE_MEDIA,
            WorkflowStatus.AWAITING_CONFIRMATION,
            timeout=180,
            errors=errors,
        )
        video_asset_id = wait_downloaded_video_selection(
            controller,
            workflow_id,
            errors,
        )
        downloaded_asset_path = project.resolve_asset_path(
            project.get_asset(video_asset_id)
        ).resolve()
        if not downloaded_asset_path.is_relative_to(run_root):
            raise RuntimeError(
                f"Downloaded asset escaped the managed run: {downloaded_asset_path}"
            )
        video_clip_id = place_downloaded_video_on_timeline(
            controller,
            workflow_id,
            video_asset_id,
            errors,
        )
        main_sequence_id = controller.workspace.activeSequenceId

        raise_ui_errors(errors)
        controller.workspace.continueWorkflow(workflow_id, "")
        wait_workflow(
            controller,
            WorkflowStage.TRANSCRIBE,
            WorkflowStatus.AWAITING_CONFIRMATION,
            timeout=180,
            errors=errors,
        )
        controller.workspace.continueWorkflow(workflow_id, "")
        wait_workflow(
            controller,
            WorkflowStage.TRANSLATE,
            WorkflowStatus.AWAITING_CONFIRMATION,
            timeout=600,
            errors=errors,
        )
        controller.workspace.continueWorkflow(workflow_id, "zh_CN")
        wait_workflow(
            controller,
            WorkflowStage.HIGHLIGHT,
            WorkflowStatus.AWAITING_CONFIRMATION,
            timeout=300,
            errors=errors,
        )
        controller.workspace.continueWorkflow(workflow_id, "")
        wait_workflow(
            controller,
            WorkflowStage.CREATE_SHORTS,
            WorkflowStatus.AWAITING_CONFIRMATION,
            timeout=300,
            errors=errors,
        )
        controller.workspace.continueWorkflow(workflow_id, "")
        wait_workflow(
            controller,
            WorkflowStage.EXPORT,
            WorkflowStatus.AWAITING_CONFIRMATION,
            timeout=120,
            errors=errors,
        )

        project = controller.session.binding.current
        if project is None:
            raise RuntimeError("Project closed during workflow")
        workflow = next(
            run for run in project.list_workflow_runs() if run.id == workflow_id
        )
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
        image_asset_id = import_without_workflow(controller, image, errors)
        music_asset_id = import_without_workflow(controller, music, errors)

        controller.workspace.selectSequence(main_sequence_id)
        video_clip = next(
            clip
            for clip in controller.session.binding.timeline.state.clips
            if clip.id == video_clip_id and clip.asset_id == video_asset_id
        )
        video_track_position = next(
            track.position
            for track in controller.session.binding.timeline.state.tracks
            if track.id == video_clip.track_id
        )
        controller.timeline.dropAssets(
            [image_asset_id],
            video_clip.track_id,
            video_track_position,
            video_clip.timeline_end,
            3.0,
            0,
            True,
            False,
        )
        controller.timeline.addTransitionAfter(video_clip.id, "dissolve", 15)
        first_image_clip = next(
            clip
            for clip in controller.session.binding.timeline.state.clips
            if clip.asset_id == image_asset_id
        )
        image_track_position = next(
            track.position
            for track in controller.session.binding.timeline.state.tracks
            if track.id == first_image_clip.track_id
        )
        controller.timeline.dropAssets(
            [image_asset_id],
            first_image_clip.track_id,
            image_track_position,
            first_image_clip.timeline_end,
            3.0,
            0,
            True,
            False,
        )
        overlay_clip = controller.timeline.selectedClipId
        controller.timeline.addTrack("video")
        overlay_track = [
            track
            for track in controller.session.binding.timeline.state.tracks
            if track.kind == TrackKind.VIDEO
        ][-1]
        controller.timeline.moveClip(overlay_clip, 0, overlay_track.id)
        controller.timeline.setClipTransform(overlay_clip, 0.68, 0.08, 0.28, 0.28, 0, 0, 0, 0, 0, 0.9)
        controller.media.importFiles(
            [QUrl.fromLocalFile(str(WEB_MEDIA_STARTER / "editable-media.json"))]
        )
        web_asset_id = controller.media.selectedAssetId
        controller.timeline.addTrack("video")
        web_track = [
            track
            for track in controller.session.binding.timeline.state.tracks
            if track.kind == TrackKind.VIDEO
        ][-1]
        controller.timeline.dropAssets(
            [web_asset_id], web_track.id, web_track.position, 0, 3.0, 0, True, False
        )
        web_clip_id = controller.timeline.selectedClipId
        controller.web.selectLayer("title")
        controller.web.updateLayer("title", {"content": "Real editable web chain"})
        controller.web_timeline.setDescriptorKeyframeAtFrame(
            "layer",
            "title.opacity",
            0.4,
            "ease_in_out",
            0,
        )
        controller.web_timeline.setDescriptorKeyframeAtFrame(
            "layer",
            "title.opacity",
            1.0,
            "ease_out",
            25,
        )
        controller.web.updateDescriptorValue("theme", "accent", "#e6007a")
        controller.web.updateDescriptorValue(
            "data",
            "left_value",
            '"Desktop and CLI share state"',
        )
        persisted_web = project.get_web_clip(web_clip_id)
        if (
            persisted_web.scenes["opening"].layers["title"].content
            != "Real editable web chain"
        ):
            raise RuntimeError("Desktop web edit did not reach project state")
        main_audio_track = next(
            track
            for track in controller.session.binding.timeline.state.tracks
            if track.kind == TrackKind.AUDIO
        )
        controller.timeline.dropAssets(
            [music_asset_id],
            main_audio_track.id,
            main_audio_track.position,
            0,
            3.0,
            0,
            True,
            False,
        )
        controller.timeline.setClipAudio(controller.timeline.selectedClipId, -8.0, 0.0, 12, 24)
        master = next(
            bus
            for bus in project.list_audio_buses(main_sequence_id)
            if bus.parent_bus_id is None
        )
        controller.audio.updateAudioBus(master.id, -1.0, False, False)
        controller.audio.addAudioEffect(master.id, "limiter")
        main_preview = wait_preview_graph(controller, errors=errors)

        main_subtitle_track = next(
            track
            for track in controller.session.binding.timeline.state.tracks
            if track.kind == TrackKind.SUBTITLE
        )
        main_output = project.project_dir / "exports" / "main-real-chain.mp4"
        controller.export.exportSequenceWithOptions(
            "h264",
            QUrl.fromLocalFile(str(main_output)).toString(),
            {"burnSubtitleTrackId": main_subtitle_track.id, "preset": "veryfast"},
        )
        wait_export(controller, main_output, errors=errors)
        deadline = time.monotonic() + 30
        while controller.workspace.workflowPending and time.monotonic() < deadline:
            process_events(errors)
        if controller.workspace.workflowPending:
            raise RuntimeError("Workflow did not finish after the verified main export")

        short_id = short_ids[0]
        controller.workspace.selectSequence(short_id)
        controller.subtitles.placeSubtitleDocument(translated_id)
        short_audio_track = next(
            track
            for track in controller.session.binding.timeline.state.tracks
            if track.kind == TrackKind.AUDIO
        )
        controller.timeline.dropAssets(
            [music_asset_id],
            short_audio_track.id,
            short_audio_track.position,
            0,
            3.0,
            0,
            True,
            False,
        )
        short_video = next(
            clip
            for clip in controller.session.binding.timeline.state.clips
            if clip.asset_id == video_asset_id
        )
        short_audio = next(
            clip
            for clip in controller.session.binding.timeline.state.clips
            if clip.asset_id == music_asset_id
        )
        controller.timeline.trimClip(short_audio.id, 0, min(short_audio.duration, short_video.duration))
        short_preview = wait_preview_graph(
            controller,
            previous=main_preview,
            errors=errors,
        )
        short_subtitle_track = next(
            track
            for track in controller.session.binding.timeline.state.tracks
            if track.kind == TrackKind.SUBTITLE
        )
        short_output = project.project_dir / "exports" / "short-real-chain.mp4"
        controller.export.exportSequenceWithOptions(
            "h264",
            QUrl.fromLocalFile(str(short_output)).toString(),
            {"burnSubtitleTrackId": short_subtitle_track.id, "preset": "veryfast"},
        )
        wait_export(controller, short_output, errors=errors)

        project_root = project.project_dir
        task_count = len(project.list_tasks())
        controller.workspace.closeProject()
        wait_project_release(controller, errors=errors)
        controller.workspace.openProject(QUrl.fromLocalFile(str(project_root)).toString())
        reopened = controller.session.binding.current
        if reopened is None:
            raise RuntimeError("Project did not reopen")
        if len(reopened.list_sequences()) < 2:
            raise RuntimeError("Short sequences were not restored after reopening")
        if len(reopened.list_subtitle_documents()) < 2:
            raise RuntimeError("Subtitle documents were not restored after reopening")
        if len(reopened.list_tasks()) != task_count:
            raise RuntimeError("Task history changed after reopening")
        reopened_web = reopened.get_web_clip(web_clip_id)
        if (
            reopened_web.scenes["opening"].layers["title"].content
            != "Real editable web chain"
        ):
            raise RuntimeError("Editable web state was not restored after reopening")
        raise_ui_errors(errors)
        if (
            not project_root.resolve().is_relative_to(run_root)
            or not downloaded_asset_path.is_relative_to(run_root)
        ):
            raise RuntimeError("Real workflow artifacts escaped the managed run")

        report = {
            "project": str(project_root),
            "source_url": source_url,
            "download_request_directories": sorted(
                str(path) for path in request_directories
            ),
            "downloaded_asset": str(downloaded_asset_path),
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
            "web_clip_id": web_clip_id,
            "web_state_revision": reopened_web.revision,
            "errors": errors,
        }
        report_path = project_root / "generated" / "real-user-chain-report.json"
        atomic_write_text(report_path, json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        controller.shutdown()
        if fixture_server is not None:
            fixture_server.shutdown()
            fixture_server.server_close()
        if fixture_thread is not None:
            fixture_thread.join(timeout=5)
        app.processEvents()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path)
    arguments = parser.parse_args(argv)
    with verification_run(
        "real-user-chain",
        explicit_root=arguments.root,
    ) as run_dir:
        verify(run_dir)


if __name__ == "__main__":
    main()
