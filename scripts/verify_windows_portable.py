from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from scripts.run_artifacts import verification_run

ROOT = Path(__file__).resolve().parents[1]
EXE = Path()
RUNTIME = Path()
RUN_ROOT = Path()
FIXTURE = Path()
ENV: dict[str, str] = {}
ACTOR = {"kind": "agent", "id": "portable-exe-test", "name": "Portable EXE test"}
CLIENT_ID = f"portable-exe-test-{uuid.uuid4().hex}"

def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def portable_environment() -> dict[str, str]:
    environment = dict(os.environ)
    values = {
        "MEDIAFLOW_RUNTIME_DIR": RUNTIME,
        "MEDIAFLOW_PROJECT_ROOT": RUN_ROOT / "Projects",
        "MEDIAFLOW_MEDIA_ROOT": RUN_ROOT / "Media",
        "MEDIAFLOW_SERVICE_STATE_DIR": RUN_ROOT / "Service",
        "MEDIAFLOW_SERVICE_SETTINGS_PATH": RUN_ROOT / "Settings" / "service-settings.json",
        "MEDIAFLOW_DESKTOP_SETTINGS_PATH": RUN_ROOT / "Settings" / "desktop-settings.json",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD": "1",
        "PIP_NO_INDEX": "1",
        "HTTP_PROXY": "http://127.0.0.1:9",
        "HTTPS_PROXY": "http://127.0.0.1:9",
        "NO_PROXY": "127.0.0.1,localhost",
    }
    environment.update({name: str(value) for name, value in values.items()})
    return environment


def run_process(arguments: list[str], *, timeout: float = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        cwd=ROOT,
        env=ENV,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def cli_raw(request: dict[str, Any], *, timeout: float = 180) -> dict[str, Any]:
    completed = subprocess.run(
        [str(EXE), "-m", "mediaflow.cli", "execute", "--request", "-"],
        input=json.dumps(request, ensure_ascii=False),
        cwd=ROOT,
        env=ENV,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            f"CLI returned non-JSON output ({completed.returncode}): "
            f"stdout={completed.stdout!r}, stderr={completed.stderr!r}"
        ) from error
    if completed.returncode != 0 or payload.get("ok") is not True:
        raise RuntimeError(json.dumps(payload, ensure_ascii=False, indent=2))
    response = payload.get("result")
    if not isinstance(response, dict):
        raise RuntimeError("CLI response contained no service result")
    return response


def request(
    operation: str,
    *,
    project: Path | None = None,
    arguments: dict[str, Any] | None = None,
    access: str = "read",
    timeout: float = 180,
) -> tuple[dict[str, Any], dict[str, Any]]:
    payload: dict[str, Any] = {
        "protocol": "mediaflow-editor",
        "version": 4,
        "operation": operation,
        "arguments": arguments or {},
        "actor": ACTOR,
        "client_id": CLIENT_ID,
    }
    if project is not None:
        payload["project"] = str(project.resolve())
    if access in {"create", "write"}:
        payload["request_id"] = f"{operation}-{uuid.uuid4().hex}"
    if access == "write":
        if project is None:
            raise ValueError(f"{operation} needs a project")
        inspected = request("project.inspect", project=project)[1]
        payload["base_revision"] = int(inspected["project_revision"])
    response = cli_raw(payload, timeout=timeout)
    result = response.get("result")
    if not isinstance(result, dict):
        raise RuntimeError(f"{operation} returned no operation result")
    return result, response


def wait_task(project: Path, receipt: dict[str, Any], *, timeout: float) -> dict[str, Any]:
    task = receipt.get("task")
    if not isinstance(task, dict) or not isinstance(task.get("id"), str):
        raise RuntimeError("Task-backed operation returned no task receipt")
    result, _ = request(
        "task.wait",
        project=project,
        arguments={"task_id": task["id"], "timeout": timeout},
        timeout=timeout + 30,
    )
    completed = result.get("task")
    if not isinstance(completed, dict) or completed.get("status") != "completed":
        raise RuntimeError(json.dumps(completed, ensure_ascii=False, indent=2))
    return completed


def artifact_path(project: Path, reference: dict[str, Any]) -> Path:
    path = Path(str(reference["path"]))
    if reference.get("scope") == "project" and not path.is_absolute():
        return project / path
    return path


def verify() -> int:
    report: dict[str, Any] = {
        "exe": str(EXE),
        "exe_sha256": sha256(EXE),
        "runtime": str(RUNTIME),
        "run_root": str(RUN_ROOT),
        "offline_environment": {
            name: ENV[name]
            for name in (
                "HF_HUB_OFFLINE",
                "TRANSFORMERS_OFFLINE",
                "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD",
                "PIP_NO_INDEX",
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "NO_PROXY",
            )
        },
        "checks": {},
    }
    checks = report["checks"]

    described = run_process(
        [str(EXE), "-m", "mediaflow.cli", "describe"], timeout=120
    )
    describe_payload = json.loads(described.stdout)
    operations = describe_payload["result"]["operations"]
    checks["cli_contract"] = {
        "passed": described.returncode == 0 and describe_payload.get("ok") is True,
        "protocol": describe_payload.get("protocol"),
        "version": describe_payload.get("version"),
        "operation_count": len(operations),
        "stderr": described.stderr,
    }

    source = RUN_ROOT / "generated-source.mp4"
    media_tools = RUNTIME / "deps" / "shotcut-26.6.25" / "Shotcut"
    ffmpeg = media_tools / "ffmpeg.exe"
    ffprobe = media_tools / "ffprobe.exe"
    generated = run_process(
        [
            str(ffmpeg),
            "-y",
            "-hide_banner",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=640x360:rate=30:duration=4",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=4",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(source),
        ],
        timeout=120,
    )
    if generated.returncode != 0 or not source.is_file():
        raise RuntimeError(f"Failed to generate local source: {generated.stderr}")
    checks["local_media_generation"] = {
        "passed": True,
        "path": str(source),
        "bytes": source.stat().st_size,
        "sha256": sha256(source),
    }

    created, create_response = request(
        "project.create",
        access="create",
        arguments={
            "name": "Portable EXE Complete Test",
            "directory_name": "portable-exe-complete-test",
            "profile": {
                "width": 640,
                "height": 360,
                "fps_numerator": 30,
                "fps_denominator": 1,
                "color_mode": "sdr_bt709",
                "bit_depth": 8,
                "audio_sample_rate": 48000,
                "audio_channels": 2,
            },
        },
    )
    project = Path(created["path"])
    sequence_id = str(created["project"]["main_sequence_id"])
    checks["project_create"] = {
        "passed": (project / "project.mfp").is_file(),
        "path": str(project),
        "sequence_id": sequence_id,
        "project_revision": create_response.get("project_revision"),
    }

    imported_receipt, _ = request(
        "asset.import",
        project=project,
        access="write",
        arguments={"source": str(source)},
    )
    imported_task = wait_task(project, imported_receipt, timeout=180)
    native_asset_id = str(imported_task["outcome"]["asset_id"])
    inspected, _ = request("project.inspect", project=project)
    native_asset = next(item for item in inspected["assets"] if item["id"] == native_asset_id)
    checks["native_asset_import"] = {
        "passed": native_asset["metadata"]["duration_frames"] >= 100,
        "asset_id": native_asset_id,
        "kind": native_asset["kind"],
        "duration_frames": native_asset["metadata"]["duration_frames"],
        "task_id": imported_task["id"],
    }

    native_track_result, _ = request(
        "timeline.track.add",
        project=project,
        access="write",
        arguments={"sequence_id": sequence_id, "kind": "video", "name": "Native Video"},
    )
    native_track_id = str(native_track_result["track"]["id"])
    native_clip_result, _ = request(
        "timeline.clip.add",
        project=project,
        access="write",
        arguments={
            "sequence_id": sequence_id,
            "track_id": native_track_id,
            "asset_id": native_asset_id,
            "timeline_start": 0,
            "source_in": 0,
            "duration": 90,
        },
    )

    web_imported, _ = request(
        "web.import",
        project=project,
        access="write",
        arguments={"source": str(FIXTURE)},
    )
    web_asset_id = str(web_imported["asset"]["id"])
    web_track_result, _ = request(
        "timeline.track.add",
        project=project,
        access="write",
        arguments={"sequence_id": sequence_id, "kind": "video", "name": "Editable Web"},
    )
    web_track_id = str(web_track_result["track"]["id"])
    web_clip_result, _ = request(
        "timeline.clip.add",
        project=project,
        access="write",
        arguments={
            "sequence_id": sequence_id,
            "track_id": web_track_id,
            "asset_id": web_asset_id,
            "timeline_start": 0,
            "source_in": 0,
            "duration": 60,
        },
    )
    web_clip_id = str(web_clip_result["clip"]["id"])
    checks["timeline_editing"] = {
        "passed": True,
        "native_track_id": native_track_id,
        "native_clip_id": native_clip_result["clip"]["id"],
        "web_track_id": web_track_id,
        "web_clip_id": web_clip_id,
    }

    web_updated, _ = request(
        "web.clip.update",
        project=project,
        access="write",
        arguments={
            "sequence_id": sequence_id,
            "clip_id": web_clip_id,
            "scene_id": "opening",
            "updates": {"title": {"content": "Portable EXE edit"}},
            "expected_revision": 0,
            "actor": "automation",
        },
    )
    web_revision = int(web_updated["web_clip_state"]["revision"])
    parameter_updated, _ = request(
        "web.clip.parameter.update",
        project=project,
        access="write",
        arguments={
            "sequence_id": sequence_id,
            "clip_id": web_clip_id,
            "scene_id": "opening",
            "parameter_id": "spring_strength",
            "value": 0.88,
            "expected_revision": web_revision,
            "actor": "automation",
        },
    )
    web_revision = int(parameter_updated["web_clip_state"]["revision"])
    web_state, _ = request(
        "web.clip.get",
        project=project,
        arguments={"clip_id": web_clip_id},
    )
    state = web_state["web_clip_state"]
    checks["editable_web_state"] = {
        "passed": state["scenes"]["opening"]["layers"]["title"]["content"]
        == "Portable EXE edit"
        and state["parameters"]["spring_strength"] == 0.88,
        "revision": web_revision,
        "title": state["scenes"]["opening"]["layers"]["title"]["content"],
        "spring_strength": state["parameters"]["spring_strength"],
    }

    version_created, _ = request(
        "project.version.create",
        project=project,
        access="write",
        arguments={"name": "Before portable render"},
    )
    checks["project_version"] = {
        "passed": version_created["version"]["name"] == "Before portable render",
        "version": version_created["version"],
    }

    render_receipt, _ = request(
        "web.clip.render",
        project=project,
        access="write",
        arguments={"sequence_id": sequence_id, "clip_id": web_clip_id, "timeout": 180},
        timeout=240,
    )
    render_task = wait_task(project, render_receipt, timeout=300)
    render_artifacts = [artifact_path(project, item) for item in render_task["artifacts"]]
    checks["editable_web_render"] = {
        "passed": bool(render_artifacts) and all(path.is_file() for path in render_artifacts),
        "task_id": render_task["id"],
        "artifacts": [str(path) for path in render_artifacts],
        "artifact_bytes": [path.stat().st_size for path in render_artifacts],
    }

    previews, _ = request(
        "preview.frames.render",
        project=project,
        arguments={"sequence_id": sequence_id, "frames": [0, 30, 59], "use_proxies": False},
        timeout=300,
    )
    preview_files = [Path(item["path"]) for item in previews["frames"]]
    preview_hashes = [item["sha256"] for item in previews["frames"]]
    checks["preview_frames"] = {
        "passed": all(path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n") for path in preview_files)
        and all((item["width"], item["height"]) == (640, 360) for item in previews["frames"])
        and len(set(preview_hashes)) >= 2,
        "preview_graph": previews["preview_graph"],
        "frames": previews["frames"],
    }

    fcpxml_path = RUN_ROOT / "portable-test.fcpxml"
    fcpxml, _ = request(
        "export.fcpxml",
        project=project,
        access="write",
        arguments={
            "sequence_id": sequence_id,
            "output_path": str(fcpxml_path),
            "overwrite": True,
        },
    )
    checks["fcpxml_export"] = {
        "passed": fcpxml_path.is_file() and fcpxml_path.stat().st_size > 0,
        "path": str(fcpxml_path),
        "bytes": fcpxml_path.stat().st_size,
        "sha256": fcpxml["sha256"],
    }

    export_path = RUN_ROOT / "portable-test.mp4"
    export_receipt, _ = request(
        "export.sequence",
        project=project,
        access="write",
        arguments={
            "sequence_id": sequence_id,
            "output_path": str(export_path),
            "format": "h264",
            "overwrite": True,
            "timeout": 600,
        },
        timeout=180,
    )
    export_task = wait_task(project, export_receipt, timeout=900)
    probed = run_process(
        [
            str(ffprobe),
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,codec_name,width,height",
            "-of",
            "json",
            str(export_path),
        ],
        timeout=120,
    )
    probe = json.loads(probed.stdout)
    stream_types = {item["codec_type"] for item in probe["streams"]}
    checks["video_export"] = {
        "passed": export_path.is_file()
        and export_path.stat().st_size > 0
        and probed.returncode == 0
        and "video" in stream_types
        and "audio" in stream_types,
        "path": str(export_path),
        "bytes": export_path.stat().st_size,
        "sha256": sha256(export_path),
        "task_id": export_task["id"],
        "probe": probe,
    }

    shutdown = run_process(
        [str(EXE), "-m", "mediaflow.cli", "service", "shutdown"], timeout=60
    )
    if shutdown.returncode != 0:
        raise RuntimeError(f"Service shutdown failed: {shutdown.stdout}\n{shutdown.stderr}")
    time.sleep(1)
    reopened, _ = request("project.inspect", project=project, timeout=180)
    reopened_state, _ = request(
        "web.clip.get",
        project=project,
        arguments={"clip_id": web_clip_id},
    )
    checks["service_restart_and_project_reopen"] = {
        "passed": reopened["project"]["id"] == created["project"]["id"]
        and reopened_state["web_clip_state"]["scenes"]["opening"]["layers"]["title"]["content"]
        == "Portable EXE edit",
        "project_id": reopened["project"]["id"],
        "asset_count": len(reopened["assets"]),
    }

    forbidden_runtime_directories = [RUNTIME / name for name in ("downloads", "models", "tools")]
    unexpected_download_files = [
        path
        for path in RUN_ROOT.rglob("*")
        if path.is_file()
        and (
            path.suffix.lower() in {".part", ".download", ".crdownload"}
            or "download" in path.name.lower()
        )
    ]
    checks["no_downloads"] = {
        "passed": not any(path.exists() for path in forbidden_runtime_directories)
        and not unexpected_download_files,
        "offline_flags": report["offline_environment"],
        "forbidden_runtime_directories_present": [
            str(path) for path in forbidden_runtime_directories if path.exists()
        ],
        "unexpected_download_files": [str(path) for path in unexpected_download_files],
    }

    report["passed"] = all(item.get("passed") is True for item in checks.values())
    report_path = RUN_ROOT / "portable-exe-test-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))

    final_shutdown = run_process(
        [str(EXE), "-m", "mediaflow.cli", "service", "shutdown"], timeout=60
    )
    if final_shutdown.returncode != 0:
        print(final_shutdown.stdout, final_shutdown.stderr, file=sys.stderr)
        return 2
    return 0 if report["passed"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Exercise the final portable executable through the complete offline chain"
    )
    parser.add_argument("--portable-root", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path)
    parser.add_argument(
        "--fixture",
        type=Path,
        default=ROOT / "tests" / "fixtures" / "editable-media-v6",
    )
    arguments = parser.parse_args(argv)
    portable_root = arguments.portable_root.expanduser().resolve()
    executable = portable_root / "MediaFlow Pro.exe"
    runtime = portable_root / "runtime"
    if not executable.is_file() or not runtime.is_dir():
        raise RuntimeError("Portable root does not contain the executable and runtime")
    fixture = arguments.fixture.expanduser().resolve()
    if not (fixture / "editable-media.json").is_file():
        raise FileNotFoundError(fixture)

    global EXE, RUNTIME, RUN_ROOT, FIXTURE, ENV
    EXE = executable
    RUNTIME = runtime
    FIXTURE = fixture
    with verification_run(
        "portable-executable",
        explicit_parent=arguments.evidence_root,
    ) as run_dir:
        RUN_ROOT = run_dir
        ENV = portable_environment()
        result = verify()
        if result != 0:
            raise RuntimeError(f"Portable executable verification failed with code {result}")
        return result


if __name__ == "__main__":
    raise SystemExit(main())
