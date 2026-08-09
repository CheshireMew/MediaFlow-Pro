from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QUrl
from PySide6.QtGui import QGuiApplication

from mediaflow.atomic_file import atomic_write_text
from mediaflow.composition import EditorApplication
from mediaflow.desktop.controllers.controller_hub import EditorControllers
from mediaflow.domain.enums import AssetKind, TaskStatus, TrackKind
from mediaflow.infrastructure.runtime_context import RuntimeContext
from mediaflow.service.client import shutdown_sync_service
from mediaflow.service.desktop_proxy import RemoteEditorProject
from scripts.run_artifacts import verification_run

ROOT = Path(__file__).resolve().parents[1]
REACT_PACKAGE = ROOT / "tests" / "fixtures" / "editable-media-v6-react-reference"


def _process_until(predicate: Callable[[], bool], *, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QCoreApplication.processEvents()
        if predicate():
            return
        time.sleep(0.02)
    raise TimeoutError("Desktop state did not reach the expected value")


def _create_spoken_video(output: Path) -> None:
    paths = RuntimeContext.discover().paths
    speech = output.with_suffix(".wav")
    escaped = str(speech).replace("'", "''")
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$s.SetOutputToWaveFile('{escaped}'); "
        "$s.Speak('Media Flow Pro turns one editable React package into a deterministic video.'); "
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
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if encoded.returncode != 0 or not output.is_file():
        raise RuntimeError(encoded.stderr)


def _run_cli(request_path: Path) -> dict[str, Any]:
    result = subprocess.run(
        [sys.executable, "-m", "mediaflow.cli", "execute", "--request", str(request_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=7200,
        check=False,
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            f"mediaflow-cli returned non-JSON output ({result.returncode}): {result.stdout}\n{result.stderr}"
        ) from error
    if result.returncode != 0 or payload.get("ok") is not True:
        raise RuntimeError(json.dumps(payload, ensure_ascii=False))
    value = payload.get("result")
    if not isinstance(value, dict):
        raise RuntimeError("mediaflow-cli returned no result object")
    operation_result = value.get("result")
    if not isinstance(operation_result, dict):
        raise RuntimeError("mediaflow-cli returned no operation result object")
    return operation_result


def _wait_cli_task(request_path: Path, copied: dict[str, Any], task_id: str) -> dict[str, Any]:
    wait_request = {
        "protocol": "mediaflow-editor",
        "version": 4,
        "operation": "task.wait",
        "project": copied["project"],
        "arguments": {"task_id": task_id, "timeout": 7200},
        "request_id": f"desktop-verification-wait-{task_id}",
        "base_revision": copied["base_revision"],
        "actor": copied["actor"],
        "client_id": copied["client_id"],
    }
    wait_path = request_path.with_name(f"{request_path.stem}-wait.json")
    atomic_write_text(wait_path, json.dumps(wait_request, ensure_ascii=False, indent=2))
    result = _run_cli(wait_path)
    task = result.get("task")
    if not isinstance(task, dict) or task.get("status") != TaskStatus.COMPLETED.value:
        raise RuntimeError(f"CLI task did not complete: {json.dumps(task, ensure_ascii=False)}")
    return task


def _close_project(controller: EditorControllers) -> None:
    controller.workspace.closeProject()
    _process_until(lambda: not controller.workspace.projectReleasePending, timeout=60)
    if controller.workspace.projectCloseFailed:
        raise RuntimeError(controller.workspace.projectCloseError)


def _open_project(controller: EditorControllers, project_path: Path) -> None:
    controller.workspace.openProject(QUrl.fromLocalFile(str(project_path)).toString())
    _process_until(lambda: controller.session.binding.current is not None, timeout=60)


def _wait_for_project_task_quiescence(
    project: RemoteEditorProject,
    *,
    timeout: float = 120,
) -> None:
    deadline = time.monotonic() + timeout
    stable_since: float | None = None
    previous: tuple[tuple[str, str, int], ...] | None = None
    while time.monotonic() < deadline:
        QCoreApplication.processEvents()
        tasks = project.list_tasks()
        signature = tuple(
            sorted((task.id, task.status.value, task.revision) for task in tasks)
        )
        if any(not task.status.is_settled for task in tasks) or signature != previous:
            stable_since = None
            previous = signature
        elif stable_since is None:
            stable_since = time.monotonic()
        elif time.monotonic() - stable_since >= 1:
            return
        time.sleep(0.02)
    raise TimeoutError("Desktop project background tasks did not become quiescent")


def _copy_request(
    controller: EditorControllers,
    output: Path,
    action: Callable[[], None],
) -> dict[str, Any]:
    project = controller.session.binding.current
    if project is None:
        raise RuntimeError("Project must be open before copying a request")
    _wait_for_project_task_quiescence(project)
    before_revision = project.content_revision()
    before_task_ids = {task.id for task in project.list_tasks()}
    previous = controller.automation.requestPreviewJson
    action()
    _process_until(lambda: controller.automation.requestPreviewJson != previous)
    rendered = controller.automation.requestPreviewJson
    if QGuiApplication.clipboard().text() != rendered:
        raise RuntimeError("The desktop preview and clipboard request differ")
    copied = json.loads(rendered)
    if copied.get("protocol") != "mediaflow-editor" or copied.get("version") != 4:
        raise RuntimeError("Desktop copied a non-v4 automation request")
    after_revision = project.content_revision()
    after_task_ids = {task.id for task in project.list_tasks()}
    if after_revision != before_revision or after_task_ids != before_task_ids:
        raise RuntimeError(
            "Copying a request changed project state or started a task: "
            f"revision={before_revision}->{after_revision}, "
            f"new_tasks={sorted(after_task_ids - before_task_ids)}"
        )
    atomic_write_text(output, rendered)
    return copied


def _execute_task_request(request_path: Path, copied: dict[str, Any]) -> dict[str, Any]:
    receipt = _run_cli(request_path)
    task = receipt.get("task")
    if not isinstance(task, dict) or not isinstance(task.get("id"), str):
        raise RuntimeError("Task-backed CLI operation returned no task receipt")
    return _wait_cli_task(request_path, copied, task["id"])


def verify(run_root: Path) -> None:
    os.environ["MEDIAFLOW_SERVICE_STATE_DIR"] = str(run_root / "editor-service")
    source = run_root / "spoken-source.mp4"
    project_path = run_root / "Desktop CLI Requests"
    _create_spoken_video(source)

    application = EditorApplication()
    with application.create_project(project_path, "Desktop CLI Requests") as project:
        native = project.import_external_asset(source, expected_kind=AssetKind.VIDEO)
        web = project.import_web_package(REACT_PACKAGE)
        sequence_id = project.get_project().main_sequence_id
        editor = project.timeline(sequence_id)
        native_track = editor.add_track(TrackKind.VIDEO)
        editor.add_clip(
            track_id=native_track.id,
            asset_id=native.id,
            timeline_start=0,
            source_in=0,
            duration=75,
        )
        web_track = editor.add_track(TrackKind.VIDEO)
        web_clip = editor.add_clip(
            track_id=web_track.id,
            asset_id=web.id,
            timeline_start=0,
            source_in=0,
            duration=75,
        )
        web_clip_id = web_clip.id

    app = QGuiApplication.instance() or QGuiApplication([])
    controller = EditorControllers()
    errors: list[str] = []
    controller.session.events.errorOccurred.connect(errors.append)
    artifacts = run_root / "requests"
    artifacts.mkdir(parents=True, exist_ok=True)
    try:
        _open_project(controller, project_path)
        controller.timeline.selectClip(web_clip_id)
        _process_until(lambda: bool(controller.web.parameterDescriptors))
        parameter = dict(controller.web.parameterDescriptors[0])
        definition = dict(parameter["descriptor"])
        parameter_id = str(parameter["source_id"])
        original_value = parameter["value"]
        if definition["kind"] in {"number", "integer"}:
            constraints = dict(definition.get("constraints") or {})
            step = float(constraints.get("step") or 1)
            maximum = constraints.get("maximum")
            candidate = float(original_value) + step
            if maximum is not None and candidate > float(maximum):
                candidate = float(original_value) - step
            changed_value = int(candidate) if definition["kind"] == "integer" else candidate
        elif definition["kind"] == "boolean":
            changed_value = not bool(original_value)
        else:
            raise RuntimeError("React reference package needs an editable scalar parameter")
        web_request_path = artifacts / "web-field-update.json"
        _copy_request(
            controller,
            web_request_path,
            lambda: controller.automation.copyWebFieldUpdateRequest(
                "parameter", parameter_id, changed_value
            ),
        )
        _close_project(controller)
        _run_cli(web_request_path)

        _open_project(controller, project_path)
        persisted = controller.session.binding.current.get_web_clip(web_clip_id)
        if persisted.parameters[parameter_id] != changed_value:
            raise RuntimeError("CLI web edit did not reach the reopened desktop project")
        export_request_path = artifacts / "export-sequence.json"
        export_request = _copy_request(
            controller,
            export_request_path,
            lambda: controller.automation.copyCurrentExportRequest("h264", "mp4", {}),
        )
        export_output = Path(export_request["arguments"]["output_path"])
        _close_project(controller)
        export_task = _execute_task_request(export_request_path, export_request)
        if not export_output.is_file() or export_output.stat().st_size <= 0:
            raise RuntimeError("Copied export request did not produce a usable file")

        _open_project(controller, project_path)
        transcript_request_path = artifacts / "transcribe-sequence.json"
        transcript_request = _copy_request(
            controller,
            transcript_request_path,
            lambda: controller.automation.copyCurrentTranscriptionRequest(
                "tiny.en", "cpu", "en", 1
            ),
        )
        _close_project(controller)
        transcript_task = _execute_task_request(
            transcript_request_path,
            transcript_request,
        )

        _open_project(controller, project_path)
        reopened = controller.session.binding.current
        documents = reopened.list_subtitle_documents(sequence_id=sequence_id)
        if not documents:
            raise RuntimeError("Copied transcription request produced no transcript document")
        if errors:
            raise RuntimeError(f"Desktop reported an error: {errors[-1]}")
        report = {
            "project": str(project_path),
            "protocol": "mediaflow-editor",
            "version": 4,
            "copied_requests": [
                str(web_request_path),
                str(export_request_path),
                str(transcript_request_path),
            ],
            "web_parameter": {"id": parameter_id, "value": changed_value},
            "export": {
                "path": str(export_output),
                "bytes": export_output.stat().st_size,
                "task_id": export_task["id"],
            },
            "transcript": {
                "document_ids": [document.id for document in documents],
                "task_id": transcript_task["id"],
            },
        }
        report_path = run_root / "desktop-cli-request-report.json"
        atomic_write_text(report_path, json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        try:
            controller.shutdown()
        finally:
            shutdown_sync_service()
        app.processEvents()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path)
    arguments = parser.parse_args(argv)
    with verification_run(
        "desktop-cli-requests",
        explicit_root=arguments.root,
    ) as run_dir:
        verify(run_dir)


if __name__ == "__main__":
    main()
