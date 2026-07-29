from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from mediaflow.domain.storage_names import (
    PROJECT_DIRECTORY_COMPONENT_UTF16_LIMIT,
    PROJECT_ROOT_PATH_UTF16_LIMIT,
    safe_child_path,
    utf16_units,
)


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


def test_cli_project_create_rejects_over_budget_root_without_writing(
    tmp_path: Path,
) -> None:
    project_path = safe_child_path(
        tmp_path,
        "CLI-Over-Budget-Project-Root-" * 20,
        max_path_utf16_units=PROJECT_ROOT_PATH_UTF16_LIMIT + 1,
        max_component_utf16_units=PROJECT_DIRECTORY_COMPONENT_UTF16_LIMIT,
    )
    assert utf16_units(str(project_path)) == PROJECT_ROOT_PATH_UTF16_LIMIT + 1
    request_path = tmp_path / "over-budget-project-create.json"
    request_path.write_text(
        json.dumps(
            {
                "protocol": "mediaflow-cli",
                "version": 1,
                "operation": "project.create",
                "project": str(project_path),
                "arguments": {"name": "Must Not Exist"},
            }
        ),
        encoding="utf-8",
    )

    result = _run_cli("execute", "--request", str(request_path))

    assert result.returncode == 1
    assert result.stderr == ""
    output = json.loads(result.stdout)
    assert output["ok"] is False
    assert output["error"]["code"] == "invalid_request"
    assert output["error"]["type"] == "ValueError"
    assert "路径过深" in output["error"]["message"]
    assert not project_path.exists()
