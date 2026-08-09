from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "mediaflow.cli", *arguments],
        cwd=Path(__file__).resolve().parents[3],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_cli_argument_errors_use_the_versioned_json_error_contract() -> None:
    result = _run_cli("execute")

    assert result.returncode == 1
    assert result.stderr == ""
    output = json.loads(result.stdout)
    assert output["ok"] is False
    assert output["error"]["code"] == "invalid_request"
    assert "--request" in output["error"]["message"]


def test_cli_unknown_commands_use_the_versioned_json_error_contract() -> None:
    result = _run_cli("removed-command")

    assert result.returncode == 1
    assert result.stderr == ""
    output = json.loads(result.stdout)
    assert output["ok"] is False
    assert output["error"]["code"] == "invalid_request"


def test_cli_project_create_rejects_legacy_caller_owned_root_without_writing(
    tmp_path: Path,
) -> None:
    project_path = tmp_path / "caller-owned-project"
    request_path = tmp_path / "legacy-project-create.json"
    request_path.write_text(
        json.dumps(
            {
                "protocol": "mediaflow-editor",
                "version": 4,
                "operation": "project.create",
                "project": str(project_path),
                "request_id": "legacy-caller-root",
                "actor": {"kind": "agent", "id": "cli-test"},
                "client_id": "cli-test",
                "arguments": {
                    "name": "Must Not Exist",
                    "directory_name": "must-not-exist",
                    "profile": {
                        "width": 1920,
                        "height": 1080,
                        "fps_numerator": 30,
                        "fps_denominator": 1,
                        "color_mode": "sdr_bt709",
                        "bit_depth": 8,
                        "audio_sample_rate": 48000,
                        "audio_channels": 2,
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    try:
        result = _run_cli("execute", "--request", str(request_path))
    finally:
        _run_cli("service", "shutdown")

    assert result.returncode == 1
    assert result.stderr == ""
    output = json.loads(result.stdout)
    assert output["ok"] is False
    assert output["error"]["code"] == "invalid_request"
    assert output["error"]["type"] == "ValueError"
    assert "does not accept project" in output["error"]["message"]
    assert not project_path.exists()


def test_cli_rejects_the_removed_v2_protocol_without_creating_a_project(
    tmp_path: Path,
) -> None:
    project_path = tmp_path / "Must Not Exist"
    request_path = tmp_path / "removed-v1-request.json"
    request_path.write_text(
        json.dumps(
            {
                "protocol": "mediaflow-cli",
                "version": 2,
                "operation": "project.create",
                "project": str(project_path),
                "arguments": {
                    "name": "Must Not Exist",
                    "directory_name": "must-not-exist",
                    "profile": {
                        "width": 1920,
                        "height": 1080,
                        "fps_numerator": 30,
                        "fps_denominator": 1,
                        "color_mode": "sdr_bt709",
                        "bit_depth": 8,
                        "audio_sample_rate": 48000,
                        "audio_channels": 2,
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    result = _run_cli("execute", "--request", str(request_path))

    assert result.returncode == 1
    output = json.loads(result.stdout)
    assert output["version"] == 4
    assert output["error"]["code"] == "validation_error"
    assert not project_path.exists()


def test_cli_describe_uses_the_resident_service_contract() -> None:
    try:
        result = _run_cli("describe")
    finally:
        stopped = _run_cli("service", "shutdown")

    assert result.returncode == 0
    assert result.stderr == ""
    output = json.loads(result.stdout)
    assert output["protocol"] == "mediaflow-editor"
    assert output["version"] == 4
    assert output["result"]["transport"]["lifecycle"] == "resident-editor-service"
    assert stopped.returncode == 0
