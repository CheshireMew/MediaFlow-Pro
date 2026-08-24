from __future__ import annotations

import hashlib
import json
import math
import shutil
import struct
import subprocess
import sys
import time
import wave
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.automation.contracts import describe_contract
from mediaflow.cli import main
from mediaflow.composition import EditorApplication
from mediaflow.domain.enums import AssetKind, TrackKind
from mediaflow.domain.product_identity import PRODUCT_NAME
from mediaflow.domain.project import ProjectProfile
from mediaflow.domain.runtime_capabilities import RUNTIME_CAPABILITY_IDS
from mediaflow.domain.storage_names import utf16_units
from mediaflow.domain.subtitles import SubtitleDocument, SubtitleSegment, SubtitleWord
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.storage_paths import default_project_root
from mediaflow.service.client import EditorServiceRpcError, call_sync
from tests.v2.editor_service_api import EditorServiceApi

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
_SERVICE_API: EditorServiceApi | None = None


@pytest.fixture(autouse=True)
def _editor_api_service(
    editor_service_api: EditorServiceApi,
):
    global _SERVICE_API
    _SERVICE_API = editor_service_api
    try:
        yield
    finally:
        _SERVICE_API = None


def execute_request(
    request: dict,
    *,
    application: EditorApplication | None = None,
) -> dict:
    del application
    api = _SERVICE_API
    if api is None:
        raise RuntimeError("Editor Service test client is not bound")
    if request.get("protocol") != "mediaflow-editor" or request.get("version") != 4:
        raise AssertionError("Tests must exercise the current public protocol")
    payload = api.request(
        str(request["operation"]),
        project=request.get("project"),
        arguments=request.get("arguments"),
        request_id=request.get("request_id"),
        base_revision=request.get("base_revision"),
    )
    return api.execute_request(payload)["result"]


def wait_for_task(project: Path, receipt: dict, *, timeout: float = 30) -> dict:
    task_id = str(receipt["task"]["id"])
    deadline = time.monotonic() + timeout
    while True:
        task = execute_request(
            {
                "protocol": "mediaflow-editor",
                "version": 4,
                "operation": "task.get",
                "project": str(project),
                "arguments": {"task_id": task_id},
            }
        )["task"]
        if task["status"] in {"completed", "failed", "cancelled"}:
            return task
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Task did not finish: {task_id}")
        time.sleep(0.05)


def wait_for_imported_asset(project: Path, receipt: dict) -> dict:
    task = wait_for_task(project, receipt)
    assert task["status"] == "completed", task.get("error")
    asset_id = str(task["outcome"]["asset_id"])
    deadline = time.monotonic() + 5
    while True:
        inspected = execute_request(
            {
                "protocol": "mediaflow-editor",
                "version": 4,
                "operation": "project.inspect",
                "project": str(project),
            }
        )
        assets = [item for item in inspected["assets"] if item["id"] == asset_id]
        if assets:
            return assets[0]
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Task result was not committed: {task['id']}")
        time.sleep(0.05)


def _write_wave(path: Path) -> None:
    sample_rate = 48_000
    frames = bytearray()
    for index in range(sample_rate):
        value = int(math.sin(2 * math.pi * 440 * index / sample_rate) * 8_000)
        frames.extend(struct.pack("<h", value))
    with wave.open(str(path), "wb") as destination:
        destination.setnchannels(1)
        destination.setsampwidth(2)
        destination.setframerate(sample_rate)
        destination.writeframes(frames)


def test_recent_projects_disable_corrupt_databases_but_keep_upgradable_projects(
    tmp_path: Path,
) -> None:
    corrupt = tmp_path / "Corrupt project"
    corrupt.mkdir()
    (corrupt / "project.mfp").write_bytes(b"not a sqlite database")
    missing = tmp_path / "Missing project"
    upgradable = tmp_path / "Upgradable project"
    shutil.copytree(FIXTURES / "editable-media-v4-project", upgradable)

    snapshot = EditorApplication().recent_projects(
        [str(corrupt), str(missing), str(upgradable)]
    )
    rows = {Path(item["path"]).name: item for item in snapshot.items}

    assert rows[corrupt.name]["available"] is False
    assert rows[corrupt.name]["unavailableReason"] == "项目文件损坏或格式不受支持"
    assert rows[missing.name]["available"] is False
    assert rows[missing.name]["unavailableReason"] == "项目文件不存在"
    assert rows[upgradable.name]["available"] is True
    assert rows[upgradable.name]["unavailableReason"] == ""


def _run_cli_request(
    tmp_path: Path,
    project_path: Path,
    operation: str,
    arguments: dict | None = None,
) -> tuple[int, dict]:
    api = _SERVICE_API
    if api is None:
        raise RuntimeError("Editor Service test client is not bound")
    request_path = tmp_path / f"{operation.replace('.', '-')}.json"
    request_path.write_text(
        json.dumps(
            api.request(
                operation,
                project=project_path,
                arguments=arguments,
            )
        ),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "mediaflow.cli",
            "execute",
            "--request",
            str(request_path),
        ],
        cwd=Path(__file__).resolve().parents[3],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    if not completed.stdout.strip():
        raise AssertionError(
            {
                "operation": operation,
                "returncode": completed.returncode,
                "stderr": completed.stderr,
            }
        )
    try:
        output = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise AssertionError(
            {
                "operation": operation,
                "returncode": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            }
        ) from error
    if output.get("ok") is True and isinstance(output.get("result"), dict):
        operation_response = output["result"]
        if isinstance(operation_response.get("result"), dict):
            output = {
                **output,
                "collaboration": operation_response,
                "result": operation_response["result"],
            }
    return completed.returncode, output


def test_cli_describes_ai_transcript_plan_shape_without_hidden_references() -> None:
    contract = describe_contract()
    assert contract["product"] == PRODUCT_NAME
    assert contract["version"] == 4
    assert contract["default_project_root"] == default_project_root()
    assert "$ref" not in json.dumps(contract)
    assert "#/$defs/" not in json.dumps(contract)
    operations = {
        item["name"]: item
        for item in contract["operations"]
    }
    assert contract["editor_field_catalogs"]["visual_effects"]
    assert contract["editor_field_catalogs"]["audio_effects"]
    assert operations["diagnostics.bundle.create"]["execution_mode"] == "task"
    assert set(
        operations["diagnostics.bundle.create"]["arguments_schema"]["required"]
    ) == {"output_path"}
    assert operations["transcript.sequence.transcribe"]["execution_mode"] == "task"
    assert set(operations["project.create"]["arguments_schema"]["required"]) == {
        "name",
        "directory_name",
        "profile",
    }
    edit_schema = operations["transcript.edit.preview"]["arguments_schema"][
        "properties"
    ]["edit"]
    assert edit_schema["properties"]["selections"]["items"]["properties"]["kind"][
        "enum"
    ] == ["words", "segments"]
    assert "$ref" not in json.dumps(edit_schema)
    plan_schema = operations["transcript.edit.apply"]["arguments_schema"][
        "properties"
    ]["plan"]
    assert "plan_digest" in plan_schema["required"]
    assert operations["transcript.edit.apply"]["result_schema"]["required"] == [
        "edit"
    ]
    assert operations["runtime.inspect"]["project_access"] == "none"
    assert operations["resource.catalog.search"]["project_access"] == "none"
    assert operations["resource.catalog.search"]["arguments_schema"]["properties"][
        "category"
    ]["anyOf"][0]["enum"] == [
        "motion-graphic",
        "sound-effect",
        "audio-effect",
        "transition",
        "visual-effect",
        "zoom",
        "lut",
    ]
    assert operations["preview.frames.render"]["project_access"] == "read"
    assert operations["preview.frames.render"]["arguments_schema"]["properties"][
        "frames"
    ]["maxItems"] == 24
    assert operations["web.clip.render.inspect"]["project_access"] == "read"
    assert operations["web.clip.render.inspect"]["execution_mode"] == "atomic"
    assert set(
        operations["web.clip.render.inspect"]["arguments_schema"]["required"]
    ) == {"sequence_id", "clip_id"}
    assert operations["web.clip.render.inspect"]["result_schema"]["required"] == [
        "render_plan"
    ]
    assert operations["script.inspect"]["project_access"] == "read"
    assert operations["script.segment.update"]["history_mode"] == "reversible"
    assert operations["script.segment.split"]["history_mode"] == "reversible"
    assert operations["script.segment.merge"]["history_mode"] == "reversible"
    assert operations["script.segment.move"]["history_mode"] == "reversible"
    assert operations["script.gap.close"]["history_mode"] == "reversible"
    assert operations["speech.transcribe"]["project_access"] == "none"
    assert operations["speech.transcribe"]["required_capabilities"] == [
        "faster-whisper-xxl"
    ]
    assert set(operations["speech.transcribe"]["arguments_schema"]["required"]) == {
        "input_path",
        "output_path",
    }
    assert operations["speech.synthesize"]["project_access"] == "none"
    assert operations["speech.synthesize"]["required_capabilities"] == [
        "gpt-sovits-v2pro"
    ]
    assert operations["dubbing.prepare"]["execution_mode"] == "task"
    assert operations["dubbing.prepare"]["required_capabilities"] == [
        "project-editing",
        "speaker-diarization",
        "mlt",
        "ffmpeg",
        "ffprobe",
    ]
    assert operations["dubbing.synthesize"]["required_capabilities"] == [
        "project-editing",
        "gpt-sovits-v2pro",
        "ffmpeg",
    ]
    assert set(operations["dubbing.prepare"]["arguments_schema"]["required"]) == {
        "source_document_id"
    }
    reference_settings = operations["dubbing.prepare"]["arguments_schema"][
        "properties"
    ]["settings"]["properties"]
    assert reference_settings["reference_min_seconds"]["minimum"] == 3.0
    assert reference_settings["reference_max_seconds"]["maximum"] == 9.8
    batch_clip_schema = operations["timeline.clip.batch.add"][
        "arguments_schema"
    ]
    assert batch_clip_schema["properties"]["clips"]["minItems"] == 1
    assert operations["timeline.clip.batch.add"]["idempotency"] == "optional"
    assert set(operations["speech.synthesize"]["arguments_schema"]["required"]) == {
        "text",
        "text_language",
        "reference_audio",
        "reference_text",
        "reference_language",
        "output_path",
    }
    assert operations["export.fcpxml"]["required_capabilities"] == [
        "project-editing",
        "fcpxml-export",
        "chromium",
        "ffmpeg",
        "ffprobe",
    ]
    assert all(
        set(operation) == {
            "name",
            "project_access",
            "execution_mode",
            "history_mode",
            "idempotency",
            "required_capabilities",
            "arguments_schema",
            "result_schema",
        }
        for operation in operations.values()
    )


def test_progressive_describe_views_are_lossless_slices_of_the_full_contract() -> None:
    full = describe_contract()
    full_operations = {item["name"]: item for item in full["operations"]}

    summary = describe_contract({"view": "summary"})
    assert summary["view"] == "summary"
    assert summary["product"] == full["product"]
    assert summary["protocol"] == full["protocol"]
    assert summary["version"] == full["version"]
    assert summary["default_project_root"] == full["default_project_root"]
    assert summary["transport"] == full["transport"]
    assert summary["request_schema"] == full["request_schema"]
    assert summary["success_response_schema"] == full["success_response_schema"]
    assert summary["error_response_schema"] == full["error_response_schema"]
    assert summary["capabilities"] == full["capabilities"]
    assert summary["editor_field_catalogs"] == ["visual_effects", "audio_effects"]
    assert len(summary["operations"]) == len(full_operations)
    assert len(json.dumps(summary)) < len(json.dumps(full)) / 10
    for operation in summary["operations"]:
        assert set(operation) == {
            "name",
            "project_access",
            "execution_mode",
            "history_mode",
            "idempotency",
            "required_capabilities",
        }
        expected = full_operations[operation["name"]]
        assert operation == {key: expected[key] for key in operation}

    exact = describe_contract({"view": "operation", "name": "task.wait"})
    assert exact == {
        "view": "operation",
        "product": full["product"],
        "protocol": full["protocol"],
        "version": full["version"],
        "operation": full_operations["task.wait"],
    }

    catalog = describe_contract({"view": "catalog", "name": "audio_effects"})
    assert catalog == {
        "view": "catalog",
        "product": full["product"],
        "protocol": full["protocol"],
        "version": full["version"],
        "name": "audio_effects",
        "catalog": full["editor_field_catalogs"]["audio_effects"],
    }

    with pytest.raises(ValueError, match="Unknown automation operation"):
        describe_contract({"view": "operation", "name": "removed.operation"})
    with pytest.raises(ValueError, match="Unknown editor field catalog"):
        describe_contract({"view": "catalog", "name": "removed_catalog"})


def test_project_create_persists_the_explicit_public_profile(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MEDIAFLOW_PROJECT_ROOT", str(tmp_path))
    requested = ProjectProfile(
        width=1280,
        height=720,
        fps_numerator=12,
        fps_denominator=1,
        audio_sample_rate=48_000,
        audio_channels=1,
    )

    created = execute_request(
        {
            "protocol": "mediaflow-editor",
            "version": 4,
            "operation": "project.create",
            "arguments": {
                "name": "Explicit Profile",
                "directory_name": "explicit-profile",
                "profile": requested.model_dump(
                    mode="json", exclude_computed_fields=True
                ),
            },
        },
        application=EditorApplication(),
    )

    main_sequence = next(
        item
        for item in created["sequences"]
        if item["id"] == created["project"]["main_sequence_id"]
    )
    assert main_sequence["profile"] == requested.model_dump(
        mode="json", exclude_computed_fields=True
    )


def test_runtime_inspection_is_projectless_and_reports_every_runtime_capability() -> None:
    inspected = execute_request(
        {
            "protocol": "mediaflow-editor",
            "version": 4,
            "operation": "runtime.inspect",
        }
    )

    assert {item["id"] for item in inspected["capabilities"]} == set(
        RUNTIME_CAPABILITY_IDS
    )
    assert all(
        item["status"] in {"ready", "unavailable", "unverified"}
        for item in inspected["capabilities"]
    )


def test_resource_catalog_search_exposes_current_builtin_editor_resources() -> None:
    result = execute_request(
        {
            "protocol": "mediaflow-editor",
            "version": 4,
            "operation": "resource.catalog.search",
            "arguments": {"category": "transition"},
        }
    )

    assert result["result_count"] == 7
    assert result["categories"] == [
        "audio-effect",
        "transition",
        "visual-effect",
        "zoom",
    ]
    assert result["featured_count"] >= 2
    assert "audio" in result["tags"]
    assert {item["id"] for item in result["items"]} == {
        "dissolve",
        "fade",
        "fade_black",
        "wipe_left",
        "wipe_right",
        "slide_left",
        "slide_right",
    }
    assert all(item["catalog_id"] == "mediaflow-builtins" for item in result["items"])


def test_project_context_inspection_returns_one_revision_bound_editing_snapshot(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MEDIAFLOW_PROJECT_ROOT", str(tmp_path))
    created = execute_request(
        {
            "protocol": "mediaflow-editor",
            "version": 4,
            "operation": "project.create",
            "arguments": {
                "name": "Agent context",
                "directory_name": "agent-context",
                "profile": ProjectProfile().model_dump(
                    mode="json", exclude_computed_fields=True
                ),
            },
        }
    )
    project = Path(created["path"])
    sequence_id = created["project"]["main_sequence_id"]

    context = execute_request(
        {
            "protocol": "mediaflow-editor",
            "version": 4,
            "operation": "project.context.inspect",
            "project": str(project),
            "arguments": {"sequence_id": sequence_id},
        }
    )

    assert context["content_revision"] == context["handoff"]["current_revision"] == 0
    assert context["project"]["id"] == created["project"]["id"]
    assert context["sequence"]["id"] == sequence_id
    assert context["timeline"]["sequence"]["id"] == sequence_id
    assert context["transcript"] is None
    assert context["transcript_error"]
    assert context["handoff"]["project_path"] == str(project)


def test_preview_frame_render_returns_real_revision_bound_png_evidence(
    tmp_path: Path,
) -> None:
    project_path = tmp_path / "Proof Frames"
    application = EditorApplication()
    project = application.create_project(project_path, "Proof Frames")
    try:
        project.populate_sample_project()
        sequence_id = project.get_project().main_sequence_id
    finally:
        project.close()

    result = execute_request(
        {
            "protocol": "mediaflow-editor",
            "version": 4,
            "operation": "preview.frames.render",
            "project": str(project_path),
            "arguments": {
                "sequence_id": sequence_id,
                "frames": [15, 150, 270],
                "use_proxies": False,
            },
        }
    )

    assert Path(result["preview_graph"]).is_file()
    assert [item["frame"] for item in result["frames"]] == [15, 150, 270]
    assert len({item["sha256"] for item in result["frames"]}) == 3
    for item in result["frames"]:
        frame_path = Path(item["path"])
        assert frame_path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        assert (item["width"], item["height"]) == (1920, 1080)
        assert item["byte_count"] == frame_path.stat().st_size


def test_fcpxml_export_runs_through_the_public_cli_contract(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MEDIAFLOW_PROJECT_ROOT", str(tmp_path))
    application = EditorApplication()
    created = execute_request(
        {
            "protocol": "mediaflow-editor",
            "version": 4,
            "operation": "project.create",
            "arguments": {
                "name": "FCPXML CLI Project",
                "directory_name": "fcpxml-cli-project",
                "profile": ProjectProfile().model_dump(
                    mode="json", exclude_computed_fields=True
                ),
            },
        },
        application=application,
    )
    project_path = Path(created["path"])
    assert project_path == (tmp_path / "fcpxml-cli-project").resolve()
    source = tmp_path / "handoff-tone.wav"
    _write_wave(source)
    import_receipt = execute_request(
        {
            "protocol": "mediaflow-editor",
            "version": 4,
            "operation": "asset.import",
            "project": str(project_path),
            "arguments": {"source": str(source)},
        },
        application=application,
    )
    imported = wait_for_imported_asset(project_path, import_receipt)
    sequence_id = created["project"]["main_sequence_id"]
    track = execute_request(
        {
            "protocol": "mediaflow-editor",
            "version": 4,
            "operation": "timeline.track.add",
            "project": str(project_path),
            "arguments": {"sequence_id": sequence_id, "kind": "audio"},
        },
        application=application,
    )["track"]
    execute_request(
        {
            "protocol": "mediaflow-editor",
            "version": 4,
            "operation": "timeline.clip.add",
            "project": str(project_path),
            "arguments": {
                "sequence_id": sequence_id,
                "track_id": track["id"],
                "asset_id": imported["id"],
                "timeline_start": 0,
                "source_in": 0,
                "duration": min(
                    25,
                    imported["metadata"]["duration_frames"],
                ),
            },
        },
        application=application,
    )
    output = tmp_path / "handoff.fcpxml"

    exported = execute_request(
        {
            "protocol": "mediaflow-editor",
            "version": 4,
            "operation": "export.fcpxml",
            "project": str(project_path),
            "arguments": {
                "sequence_id": sequence_id,
                "output_path": str(output),
            },
        },
        application=application,
    )

    assert exported["format"] == "fcpxml"
    assert Path(exported["output_path"]) == output
    assert exported["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    root = ET.parse(output).getroot()
    assert root.tag == "fcpxml"
    assert root.find(".//asset") is not None
    assert root.find(".//audio") is not None


def test_clip_source_and_visual_effects_run_through_public_cli_contract(
    tmp_path: Path,
) -> None:
    application = EditorApplication()
    project_path = tmp_path / "Public Clip Editing"
    project = application.create_project(project_path, "Public Clip Editing")
    try:
        project.populate_sample_project()
        sequence_id = project.get_project().main_sequence_id
        state = project.timeline(sequence_id).state
        clip_id = state.clips[0].id
        replacement_id = project.list_assets()[1].id
    finally:
        project.close()

    replaced = execute_request(
        {
            "protocol": "mediaflow-editor",
            "version": 4,
            "operation": "timeline.clip.source.replace",
            "project": str(project_path),
            "arguments": {
                "sequence_id": sequence_id,
                "clip_id": clip_id,
                "asset_id": replacement_id,
            },
        },
        application=application,
    )["clip"]
    assert replaced["asset_id"] == replacement_id

    added = execute_request(
        {
            "protocol": "mediaflow-editor",
            "version": 4,
            "operation": "timeline.clip.effect.add",
            "project": str(project_path),
            "arguments": {
                "sequence_id": sequence_id,
                "clip_id": clip_id,
                "kind": "gaussian_blur",
            },
        },
        application=application,
    )["clip"]
    effect_id = added["visual_effects"][0]["id"]
    updated = execute_request(
        {
            "protocol": "mediaflow-editor",
            "version": 4,
            "operation": "timeline.clip.effect.update",
            "project": str(project_path),
            "arguments": {
                "sequence_id": sequence_id,
                "clip_id": clip_id,
                "effect_id": effect_id,
                "enabled": True,
                "parameters": {"sigma": 7.5},
            },
        },
        application=application,
    )["clip"]
    assert updated["visual_effects"][0]["parameters"] == {"sigma": 7.5}

    removed = execute_request(
        {
            "protocol": "mediaflow-editor",
            "version": 4,
            "operation": "timeline.clip.effect.remove",
            "project": str(project_path),
            "arguments": {
                "sequence_id": sequence_id,
                "clip_id": clip_id,
                "effect_id": effect_id,
            },
        },
        application=application,
    )["clip"]
    assert removed["visual_effects"] == []

    described = execute_request(
        {"protocol": "mediaflow-editor", "version": 4, "operation": "describe"},
        application=application,
    )
    operation_names = {item["name"] for item in described["operations"]}
    assert {
        "timeline.clip.source.replace",
        "timeline.clip.effect.add",
        "timeline.clip.effect.update",
        "timeline.clip.effect.move",
        "timeline.clip.effect.remove",
    } <= operation_names


def test_cli_and_desktop_composition_api_share_real_persisted_task_chain(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("MEDIAFLOW_PROJECT_ROOT", str(tmp_path))
    application = EditorApplication()

    created = execute_request(
        {
            "protocol": "mediaflow-editor",
            "version": 4,
            "operation": "project.create",
            "arguments": {
                "name": "Headless Project",
                "directory_name": "headless-project",
                "profile": ProjectProfile().model_dump(
                    mode="json", exclude_computed_fields=True
                ),
            },
        },
        application=application,
    )
    project_path = Path(created["path"])
    assert project_path == (tmp_path / "headless-project").resolve()
    assert created["project"]["name"] == "Headless Project"
    assert created["sequences"][0]["id"] == created["project"]["main_sequence_id"]

    source = tmp_path / "tone.wav"
    _write_wave(source)
    import_receipt = execute_request(
        {
            "protocol": "mediaflow-editor",
            "version": 4,
            "operation": "asset.import",
            "project": str(project_path),
            "arguments": {"source": str(source)},
        },
        application=application,
    )
    asset_id = wait_for_imported_asset(project_path, import_receipt)["id"]

    waveform_receipt = execute_request(
        {
            "protocol": "mediaflow-editor",
            "version": 4,
            "operation": "task.start",
            "project": str(project_path),
            "arguments": {
                "task_command": {
                    "command_type": "generate_waveform",
                    "asset_id": asset_id,
                },
                "input_asset_ids": [asset_id],
                "timeout": 60,
            },
        },
        application=application,
    )
    completed = wait_for_task(project_path, waveform_receipt, timeout=60)
    assert completed["status"] == "completed"
    assert completed["artifacts"]

    inspected = execute_request(
        {
            "protocol": "mediaflow-editor",
            "version": 4,
            "operation": "project.inspect",
            "project": str(project_path),
        },
        application=application,
    )
    asset = next(item for item in inspected["assets"] if item["id"] == asset_id)
    assert asset["waveform_path"]
    assert (project_path / asset["waveform_path"]).is_file()
    assert inspected["tasks"][-1]["id"] == completed["id"]

    request_path = tmp_path / "inspect-request.json"
    assert _SERVICE_API is not None
    request_path.write_text(
        json.dumps(
            _SERVICE_API.request(
                "project.inspect",
                project=project_path,
            )
        ),
        encoding="utf-8",
    )
    assert main(["execute", "--request", str(request_path)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is True
    assert output["result"]["result"]["assets"][0]["id"] == asset_id


def test_public_diagnostics_operation_persists_and_returns_a_readable_bundle(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MEDIAFLOW_PROJECT_ROOT", str(tmp_path))
    created = execute_request(
        {
            "protocol": "mediaflow-editor",
            "version": 4,
            "operation": "project.create",
            "arguments": {
                "name": "Diagnostics Project",
                "directory_name": "diagnostics-project",
                "profile": ProjectProfile().model_dump(
                    mode="json", exclude_computed_fields=True
                ),
            },
        }
    )
    project_path = Path(created["path"])
    output = (tmp_path / "diagnostics.zip").resolve()

    receipt = execute_request(
        {
            "protocol": "mediaflow-editor",
            "version": 4,
            "operation": "diagnostics.bundle.create",
            "project": str(project_path),
            "arguments": {
                "output_path": str(output),
                "task_ids": [],
                "overwrite": False,
            },
        }
    )
    completed = wait_for_task(project_path, receipt, timeout=60)

    assert completed["status"] == "completed", completed.get("error")
    assert completed["outcome"]["output"]["path"] == str(output)
    assert output.is_file()
    with zipfile.ZipFile(output) as bundle:
        names = set(bundle.namelist())
        manifest = json.loads(bundle.read("bundle-manifest.json"))
    assert "project/project.mfp" in names
    assert "environment/mediaflow-cli-describe.json" in names
    assert manifest["schema"] == "mediaflow-diagnostics-bundle/v1"


def test_cli_request_id_replays_persisted_result_without_repeating_edit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MEDIAFLOW_PROJECT_ROOT", str(tmp_path))
    application = EditorApplication()
    create_request = {
        "protocol": "mediaflow-editor",
        "version": 4,
        "operation": "project.create",
        "request_id": "create-project-once",
        "arguments": {
            "name": "Idempotent CLI Project",
            "directory_name": "idempotent-cli-project",
            "profile": ProjectProfile().model_dump(
                mode="json", exclude_computed_fields=True
            ),
        },
    }
    first_project = execute_request(create_request, application=application)
    repeated_project = execute_request(create_request, application=application)
    assert repeated_project == first_project
    mismatched_create = {
        **create_request,
        "arguments": {
            **create_request["arguments"],
            "profile": ProjectProfile(fps_numerator=24).model_dump(
                mode="json", exclude_computed_fields=True
            ),
        },
    }
    with pytest.raises(EditorServiceRpcError, match="request_id was reused"):
        execute_request(mismatched_create, application=application)
    project_path = Path(first_project["path"])
    assert project_path == (tmp_path / "idempotent-cli-project").resolve()

    sequence_id = first_project["project"]["main_sequence_id"]
    assert _SERVICE_API is not None
    add_track_request = {
        "protocol": "mediaflow-editor",
        "version": 4,
        "operation": "timeline.track.add",
        "project": str(project_path),
        "request_id": "add-track-once",
        "base_revision": _SERVICE_API.revision(project_path),
        "arguments": {
            "sequence_id": sequence_id,
            "kind": "video",
            "name": "Only once",
        },
    }
    first = execute_request(add_track_request, application=application)
    repeated = execute_request(add_track_request, application=application)
    assert repeated == first

    visible = execute_request(
        {
            "protocol": "mediaflow-editor",
            "version": 4,
            "operation": "timeline.get",
            "project": str(project_path),
            "arguments": {"sequence_id": sequence_id},
        },
        application=application,
    )
    assert [track["id"] for track in visible["timeline"]["tracks"]] == [
        first["track"]["id"]
    ]

    conflicting = dict(add_track_request)
    conflicting["arguments"] = {
        **add_track_request["arguments"],
        "name": "Different input",
    }
    with pytest.raises(EditorServiceRpcError, match="request_id was reused"):
        execute_request(conflicting, application=application)


def test_script_operations_expose_and_edit_real_transcript_paragraphs(
    tmp_path: Path,
) -> None:
    project_path = tmp_path / "Script Editing Project"
    source = tmp_path / "script-source.mp4"
    source.write_bytes(b"timeline-source")
    with ProjectRepository.create(project_path, "Script Editing Project") as repository:
        project = repository.projects.get_project()
        asset = repository.assets.import_external_asset(source, AssetKind.VIDEO)
        editor = TimelineEditor(repository, project.main_sequence_id)
        track = editor.add_track(TrackKind.VIDEO)
        editor.add_clip(
            track_id=track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=120,
        )
        document = SubtitleDocument(
            project_id=project.id,
            asset_id=asset.id,
            sequence_id=project.main_sequence_id,
            language="en",
            purpose="sequence_transcript",
        )
        segments = [
            SubtitleSegment(
                document_id=document.id,
                start_frame=0,
                end_frame=45,
                text="opening line",
            ),
            SubtitleSegment(
                document_id=document.id,
                start_frame=60,
                end_frame=120,
                text="second paragraph",
            ),
        ]
        words = [
            SubtitleWord(
                segment_id=segment.id,
                position=position,
                start_frame=segment.start_frame + position * (segment.end_frame - segment.start_frame) // 2,
                end_frame=segment.start_frame
                + (position + 1) * (segment.end_frame - segment.start_frame) // 2,
                text=text,
            )
            for segment in segments
            for position, text in enumerate(segment.text.split())
        ]
        repository.subtitles.create_subtitle_document(document, segments, words)
        sequence_id = project.main_sequence_id

    inspected = execute_request(
        {
            "protocol": "mediaflow-editor",
            "version": 4,
            "operation": "script.inspect",
            "project": str(project_path),
            "arguments": {"sequence_id": sequence_id},
        }
    )
    assert [item["gap_before_frames"] for item in inspected["paragraphs"]] == [0, 15]
    assert inspected["recognized_word_count"] == 4
    assert inspected["paragraphs"][0]["timing_precision"] == "recognized_words"
    first_id, second_id = [item["segment"]["id"] for item in inspected["paragraphs"]]

    assert _SERVICE_API is not None
    speaker_update = execute_request(
        {
            "protocol": "mediaflow-editor",
            "version": 4,
            "operation": "script.segment.update",
            "project": str(project_path),
            "request_id": "script-speaker-update",
            "base_revision": _SERVICE_API.revision(project_path),
            "arguments": {
                "document_id": document.id,
                "segment_id": first_id,
                "speaker": "Host",
            },
        }
    )
    assert speaker_update["segment"]["speaker"] == "Host"
    after_speaker = execute_request(
        {
            "protocol": "mediaflow-editor",
            "version": 4,
            "operation": "script.inspect",
            "project": str(project_path),
            "arguments": {"sequence_id": sequence_id},
        }
    )
    assert after_speaker["recognized_word_count"] == 4

    text_update = execute_request(
        {
            "protocol": "mediaflow-editor",
            "version": 4,
            "operation": "script.segment.update",
            "project": str(project_path),
            "request_id": "script-text-update",
            "base_revision": _SERVICE_API.revision(project_path),
            "arguments": {
                "document_id": document.id,
                "segment_id": first_id,
                "text": "rewritten opening",
            },
        }
    )
    assert text_update["segment"]["text"] == "rewritten opening"
    after_text = execute_request(
        {
            "protocol": "mediaflow-editor",
            "version": 4,
            "operation": "script.inspect",
            "project": str(project_path),
            "arguments": {"sequence_id": sequence_id},
        }
    )
    assert after_text["recognized_word_count"] == 2
    assert after_text["estimated_word_count"] == 2
    assert after_text["paragraphs"][0]["timing_precision"] == "estimated_words"

    split = execute_request(
        {
            "protocol": "mediaflow-editor",
            "version": 4,
            "operation": "script.segment.split",
            "project": str(project_path),
            "request_id": "script-split",
            "base_revision": _SERVICE_API.revision(project_path),
            "arguments": {
                "document_id": document.id,
                "segment_id": second_id,
                "split_index": 6,
            },
        }
    )
    split_ids = [item["id"] for item in split["segments"]]
    assert [item["text"] for item in split["segments"]] == ["second", "paragraph"]

    merged = execute_request(
        {
            "protocol": "mediaflow-editor",
            "version": 4,
            "operation": "script.segment.merge",
            "project": str(project_path),
            "request_id": "script-merge",
            "base_revision": _SERVICE_API.revision(project_path),
            "arguments": {
                "document_id": document.id,
                "segment_ids": split_ids,
            },
        }
    )
    assert merged["segment"]["text"] == "second paragraph"

    before_gap_close = execute_request(
        {
            "protocol": "mediaflow-editor",
            "version": 4,
            "operation": "script.inspect",
            "project": str(project_path),
            "arguments": {"sequence_id": sequence_id},
        }
    )
    closed = execute_request(
        {
            "protocol": "mediaflow-editor",
            "version": 4,
            "operation": "script.gap.close",
            "project": str(project_path),
            "request_id": "script-gap-close",
            "base_revision": _SERVICE_API.revision(project_path),
            "arguments": {
                "sequence_id": sequence_id,
                "document_id": document.id,
                "segment_id": second_id,
                "expected_content_revision": before_gap_close["content_revision"],
            },
        }
    )
    assert closed["changed_timeline_frames"] == 15
    assert (closed["before_duration_frames"], closed["after_duration_frames"]) == (120, 105)
    after_gap_close = execute_request(
        {
            "protocol": "mediaflow-editor",
            "version": 4,
            "operation": "script.inspect",
            "project": str(project_path),
            "arguments": {"sequence_id": sequence_id},
        }
    )
    assert [item["gap_before_frames"] for item in after_gap_close["paragraphs"]] == [0, 0]

    _SERVICE_API.history("undo", project_path)
    after_close_undo = execute_request(
        {
            "protocol": "mediaflow-editor",
            "version": 4,
            "operation": "script.inspect",
            "project": str(project_path),
            "arguments": {"sequence_id": sequence_id},
        }
    )
    assert after_close_undo["timeline_duration_frames"] == 120
    assert [item["gap_before_frames"] for item in after_close_undo["paragraphs"]] == [0, 15]
    _SERVICE_API.history("redo", project_path)
    after_gap_redo = execute_request(
        {
            "protocol": "mediaflow-editor",
            "version": 4,
            "operation": "script.inspect",
            "project": str(project_path),
            "arguments": {"sequence_id": sequence_id},
        }
    )
    assert after_gap_redo["timeline_duration_frames"] == 105
    assert [item["gap_before_frames"] for item in after_gap_redo["paragraphs"]] == [0, 0]

    moved = execute_request(
        {
            "protocol": "mediaflow-editor",
            "version": 4,
            "operation": "script.segment.move",
            "project": str(project_path),
            "request_id": "script-move",
            "base_revision": _SERVICE_API.revision(project_path),
            "arguments": {
                "sequence_id": sequence_id,
                "document_id": document.id,
                "segment_id": second_id,
                "position": 0,
                "expected_content_revision": after_gap_redo["content_revision"],
            },
        }
    )
    assert moved["before_duration_frames"] == moved["after_duration_frames"] == 105
    after_move = execute_request(
        {
            "protocol": "mediaflow-editor",
            "version": 4,
            "operation": "script.inspect",
            "project": str(project_path),
            "arguments": {"sequence_id": sequence_id},
        }
    )
    assert [item["segment"]["text"] for item in after_move["paragraphs"]] == [
        "second paragraph",
        "rewritten opening",
    ]
    timeline_after_move = execute_request(
        {
            "protocol": "mediaflow-editor",
            "version": 4,
            "operation": "timeline.get",
            "project": str(project_path),
            "arguments": {"sequence_id": sequence_id},
        }
    )["timeline"]
    assert max(
        clip["timeline_start"] + clip["duration"]
        for clip in timeline_after_move["clips"]
    ) == 105
    assert sorted(clip["source_in"] for clip in timeline_after_move["clips"]) == [0, 60]

    _SERVICE_API.history("undo", project_path)
    after_move_undo = execute_request(
        {
            "protocol": "mediaflow-editor",
            "version": 4,
            "operation": "script.inspect",
            "project": str(project_path),
            "arguments": {"sequence_id": sequence_id},
        }
    )
    assert [item["segment"]["text"] for item in after_move_undo["paragraphs"]] == [
        "rewritten opening",
        "second paragraph",
    ]


def test_ai_transcript_edit_plan_runs_through_real_cli_and_can_restore(
    tmp_path: Path,
) -> None:
    project_path = tmp_path / "AI Transcript Project"
    source = tmp_path / "interview.mp4"
    source.write_bytes(b"timeline-source")
    with ProjectRepository.create(project_path, "AI Transcript Project") as repository:
        project = repository.projects.get_project()
        asset = repository.assets.import_external_asset(source, AssetKind.VIDEO)
        editor = TimelineEditor(repository, project.main_sequence_id)
        track = editor.add_track(TrackKind.VIDEO)
        editor.add_clip(
            track_id=track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=90,
        )
        locked_track = editor.add_track(TrackKind.VIDEO)
        editor.add_clip(
            track_id=locked_track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=90,
        )
        editor.set_track_state(
            locked_track.id,
            enabled=True,
            locked=True,
            muted=False,
            solo=False,
        )
        document = SubtitleDocument(
            project_id=project.id,
            asset_id=asset.id,
            sequence_id=project.main_sequence_id,
            language="en",
            purpose="sequence_transcript",
        )
        segment = SubtitleSegment(
            document_id=document.id,
            start_frame=0,
            end_frame=90,
            text="one two three",
        )
        words = [
            SubtitleWord(
                segment_id=segment.id,
                position=position,
                start_frame=position * 30,
                end_frame=(position + 1) * 30,
                text=text,
            )
            for position, text in enumerate(("one", "two", "three"))
        ]
        repository.subtitles.create_subtitle_document(document, [segment], words)

    code, inspected = _run_cli_request(
        tmp_path,
        project_path,
        "transcript.get",
    )
    assert code == 0 and inspected["ok"] is True
    transcript = inspected["result"]["transcript"]
    assert transcript["recognized_word_count"] == 3
    assert transcript["estimated_word_count"] == 0
    word_id = transcript["segments"][0]["words"][1]["id"]
    edit = {
        "sequence_id": transcript["document"]["sequence_id"],
        "document_id": transcript["document"]["id"],
        "expected_content_revision": transcript["content_revision"],
        "selections": [
            {
                "kind": "words",
                "ids": [word_id],
                "reason": "Remove repeated filler",
            }
        ],
    }

    code, previewed = _run_cli_request(
        tmp_path,
        project_path,
        "transcript.edit.preview",
        {"edit": edit},
    )
    assert code == 0 and previewed["ok"] is True
    plan = previewed["result"]["plan"]
    assert plan["impact"]["removed_duration_frames"] == 30
    assert plan["impact"]["before_duration_frames"] == 90
    assert plan["impact"]["after_duration_frames"] == 90
    assert plan["impact"]["locked_track_ids"] == [locked_track.id]
    assert plan["warnings"]
    assert plan["resolved_selections"][0]["text"] == "two"

    code, unacknowledged = _run_cli_request(
        tmp_path,
        project_path,
        "transcript.edit.apply",
        {"plan": plan},
    )
    assert code == 1
    assert unacknowledged["error"]["code"] == "invalid_request"

    code, applied = _run_cli_request(
        tmp_path,
        project_path,
        "transcript.edit.apply",
        {"plan": plan, "accept_warnings": True},
    )
    assert code == 0 and applied["ok"] is True
    result = applied["result"]["edit"]
    assert result["removed_word_count"] == 1
    assert result["after_duration_frames"] == 90
    recovery_version = result["recovery_version"]
    recovery_path = project_path / recovery_version["snapshot_path"]
    assert recovery_path.is_file()

    with ProjectRepository.open(project_path) as repository:
        timeline = repository.timeline.load_timeline(transcript["document"]["sequence_id"])
        clips_by_track = {
            track_id: sorted(
                (
                    clip.timeline_start,
                    clip.source_in,
                    clip.duration,
                )
                for clip in timeline.clips
                if clip.track_id == track_id
            )
            for track_id in (track.id, locked_track.id)
        }
        assert clips_by_track == {
            track.id: [(0, 0, 30), (30, 60, 30)],
            locked_track.id: [(0, 0, 90)],
        }
        assert repository.subtitles.list_subtitle_segments(transcript["document"]["id"])[0].text == (
            "one three"
        )
        edited_srt = next(
            (project_path / "generated" / "subtitles").rglob("*.srt")
        )
        assert "one three" in edited_srt.read_text(encoding="utf-8-sig")

    code, stale = _run_cli_request(
        tmp_path,
        project_path,
        "transcript.edit.apply",
        {"plan": plan, "accept_warnings": True},
    )
    assert code == 1
    assert stale["error"]["code"] == "conflict"

    code, restored = _run_cli_request(
        tmp_path,
        project_path,
        "project.version.restore",
        {"version_id": recovery_version["id"]},
    )
    assert code == 0 and restored["ok"] is True
    with ProjectRepository.open(project_path) as repository:
        timeline = repository.timeline.load_timeline(transcript["document"]["sequence_id"])
        assert {
            clip.track_id: (clip.timeline_start, clip.source_in, clip.duration)
            for clip in timeline.clips
        } == {
            track.id: (0, 0, 90),
            locked_track.id: (0, 0, 90),
        }
        assert repository.subtitles.list_subtitle_segments(transcript["document"]["id"])[0].text == (
            "one two three"
        )
        restored_srt = next(
            (project_path / "generated" / "subtitles").rglob("*.srt")
        )
        assert "one two three" in restored_srt.read_text(encoding="utf-8-sig")


def test_timeline_navigation_preserves_one_project_history(tmp_path: Path) -> None:
    assert _SERVICE_API is not None
    created = _SERVICE_API.execute(
        "project.create",
        arguments={
            "name": "History Project",
            "directory_name": tmp_path.name,
            "profile": ProjectProfile().model_dump(
                mode="json",
                exclude_computed_fields=True,
            ),
        },
    )
    project_path = Path(created["path"])
    main = created["project"]["main_sequence_id"]
    track = _SERVICE_API.execute(
        "timeline.track.add",
        project=project_path,
        arguments={"sequence_id": main, "kind": "video", "name": "Undo me"},
    )["track"]

    inspected = _SERVICE_API.execute(
        "timeline.get",
        project=project_path,
        arguments={"sequence_id": main},
    )
    assert track["id"] in {item["id"] for item in inspected["timeline"]["tracks"]}

    _SERVICE_API.history("undo", project_path)
    undone = _SERVICE_API.execute(
        "timeline.get",
        project=project_path,
        arguments={"sequence_id": main},
    )
    assert track["id"] not in {item["id"] for item in undone["timeline"]["tracks"]}


def test_public_batch_clip_add_is_atomic_and_idempotent(
    tmp_path: Path,
) -> None:
    application = EditorApplication()
    source = tmp_path / "batch-tone.wav"
    _write_wave(source)
    project_path = tmp_path / "Batch Clip Project"
    with application.create_project(project_path, "Batch Clip Project") as project:
        asset = project.import_external_asset(source)
        sequence_id = project.get_project().main_sequence_id
        track = project.timeline(sequence_id).add_track(TrackKind.AUDIO)

    assert _SERVICE_API is not None
    request = {
        "protocol": "mediaflow-editor",
        "version": 4,
        "operation": "timeline.clip.batch.add",
        "project": str(project_path),
        "request_id": "batch-clips-once",
        "base_revision": _SERVICE_API.revision(project_path),
        "arguments": {
            "sequence_id": sequence_id,
            "clips": [
                {
                    "track_id": track.id,
                    "asset_id": asset.id,
                    "timeline_start": 0,
                    "source_in": 0,
                    "duration": 12,
                },
                {
                    "track_id": track.id,
                    "asset_id": asset.id,
                    "timeline_start": 12,
                    "source_in": 12,
                    "duration": 12,
                },
            ],
        },
    }
    first = execute_request(request, application=application)
    repeated = execute_request(request, application=application)

    assert [clip["timeline_start"] for clip in first["clips"]] == [0, 12]
    assert repeated == first
    with application.open_project(project_path, writable=False) as project:
        assert len(project.load_timeline(sequence_id).clips) == 2


def test_preview_snapshots_are_content_addressed_and_never_overwrite_active_graph(
    tmp_path: Path,
    max_project_path: Path,
) -> None:
    application = EditorApplication()
    source = tmp_path / "preview-tone.wav"
    _write_wave(source)

    with application.create_project(max_project_path, "Preview Project") as project:
        asset = project.import_external_asset(source)
        editor = project.timeline(project.get_project().main_sequence_id)
        audio_track = editor.add_track(TrackKind.AUDIO)
        clip = editor.add_clip(
            track_id=audio_track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=min(25, asset.metadata.duration_frames),
        )

        first = application.write_preview_snapshot(
            project.project_dir,
            editor.state,
            use_proxies=False,
            prefer_sdr_preview_proxy=False,
        )
        first_xml = first.read_text(encoding="utf-8")
        editor.move_clip(clip.id, timeline_start=8)
        second = application.write_preview_snapshot(
            project.project_dir,
            editor.state,
            use_proxies=False,
            prefer_sdr_preview_proxy=False,
        )
        repeated = application.write_preview_snapshot(
            project.project_dir,
            editor.state,
            use_proxies=False,
            prefer_sdr_preview_proxy=False,
        )

        assert first != second
        assert repeated == second
        assert first.is_file() and second.is_file()
        assert first.is_relative_to(application.runtime_paths.runtime_dir)
        assert second.is_relative_to(application.runtime_paths.runtime_dir)
        assert utf16_units(str(first)) <= 240
        assert utf16_units(str(second)) <= 240
        assert first.read_text(encoding="utf-8") == first_xml
        assert first.name.startswith("pv-") and second.name.startswith("pv-")
        assert not (
            project.project_dir / "cache" / "mlt" / f"{editor.state.sequence.id}-preview.mlt"
        ).exists()
        assert not list(
            (project.project_dir / "cache" / "mlt").glob("*-preview-*.mlt")
        )
        assert not list((project.project_dir / "cache" / "mlt").glob("*.partial"))


def test_short_sequence_archive_is_recoverable_through_project_history(
    tmp_path: Path,
) -> None:
    assert _SERVICE_API is not None
    project_path = tmp_path / "Recoverable Project"
    application = EditorApplication()
    with application.create_project(project_path, "Recoverable Project") as project:
        short = project.create_short_sequence("Recoverable")

    client_id = _SERVICE_API.client_id
    _SERVICE_API.execute("project.inspect", project=project_path)
    base_revision = _SERVICE_API.revision(project_path)

    call_sync(
        "project.open",
        {"project": str(project_path), "client_id": client_id},
    )
    call_sync(
        "desktop.project.call",
        {
            "project": str(project_path),
            "client_id": client_id,
            "target": "project",
            "command": "archive_short_sequence",
            "arguments": {"sequence_id": short.id},
            "base_revision": base_revision,
            "request_id": "archive-short-sequence",
            "actor": _SERVICE_API.actor,
        },
    )
    archived = _SERVICE_API.execute("project.inspect", project=project_path)
    assert short.id not in {item["id"] for item in archived["sequences"]}

    _SERVICE_API.history("undo", project_path)
    restored = _SERVICE_API.execute("project.inspect", project=project_path)
    assert short.id in {item["id"] for item in restored["sequences"]}

    _SERVICE_API.history("redo", project_path)
    rearchived = _SERVICE_API.execute("project.inspect", project=project_path)
    assert short.id not in {item["id"] for item in rearchived["sequences"]}
    call_sync(
        "project.close",
        {"project": str(project_path), "client_id": client_id},
    )
