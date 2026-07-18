from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from mediaflow.composition import EditorApplication
from mediaflow.domain.task_commands import TaskCommand

_TASK_COMMAND_ADAPTER = TypeAdapter(TaskCommand)


def _project_path(request: dict[str, Any]) -> Path:
    value = str(request.get("project") or "").strip()
    if not value:
        raise ValueError("project is required")
    path = Path(value).expanduser()
    return path.parent if path.name == "project.mfp" else path


def _project_snapshot(project) -> dict[str, Any]:
    documents = project.documents
    return {
        "project": documents.get_project().model_dump(mode="json"),
        "path": str(project.project_dir),
        "read_only": project.read_only,
        "sequences": [item.model_dump(mode="json") for item in documents.list_sequences()],
        "assets": [item.model_dump(mode="json") for item in documents.list_assets()],
        "active_workflows": [
            item.model_dump(mode="json") for item in documents.list_workflow_runs(active_only=True)
        ],
        "tasks": [item.model_dump(mode="json") for item in project.tasks.list()],
    }


def execute_request(
    request: dict[str, Any],
    *,
    application: EditorApplication | None = None,
) -> dict[str, Any]:
    api = application or EditorApplication()
    command = str(request.get("command") or "").strip()

    if command == "project.create":
        root = _project_path(request)
        name = str(request.get("name") or root.name).strip()
        if not name:
            raise ValueError("name is required")
        with api.create_project(root, name) as project:
            return _project_snapshot(project)

    if command == "project.inspect":
        with api.open_project(_project_path(request), writable=False) as project:
            return _project_snapshot(project)

    if command == "asset.import":
        source = str(request.get("source") or "").strip()
        if not source:
            raise ValueError("source is required")
        with api.open_project(_project_path(request), writable=True) as project:
            if project.read_only:
                raise PermissionError("项目以只读方式打开")
            task = project.import_asset(source)
            completed = project.tasks.wait(task.id, timeout=float(request.get("timeout", 3600)))
            result = project.consume_task_result(completed)
            if completed.status.value != "completed" or not completed.artifacts:
                raise RuntimeError(completed.error or "素材导入失败")
            asset = project.documents.get_asset(result.imported_asset_id)
            return {
                "asset": asset.model_dump(mode="json"),
                "project": str(project.project_dir),
            }

    if command == "task.list":
        with api.open_project(_project_path(request), writable=False) as project:
            return {"tasks": [item.model_dump(mode="json") for item in project.tasks.list()]}

    if command == "task.status":
        task_id = str(request.get("task_id") or "").strip()
        if not task_id:
            raise ValueError("task_id is required")
        with api.open_project(_project_path(request), writable=False) as project:
            return {"task": project.tasks.get(task_id).model_dump(mode="json")}

    if command in {"task.start", "task.resume"}:
        with api.open_project(_project_path(request), writable=True) as project:
            if project.read_only:
                raise PermissionError("项目以只读方式打开")
            timeout = float(request.get("timeout", 3600))
            if command == "task.resume":
                task_id = str(request.get("task_id") or "").strip()
                if not task_id:
                    raise ValueError("task_id is required")
                task = project.tasks.resume(task_id)
            else:
                command = _TASK_COMMAND_ADAPTER.validate_python(request.get("task_command"))
                project_document = project.documents.get_project()
                sequence_id = str(request.get("sequence_id") or project_document.main_sequence_id)
                task = project.start_task(
                    command,
                    [str(value) for value in request.get("input_asset_ids") or []],
                    sequence_id=sequence_id,
                )
            completed = project.tasks.wait(task.id, timeout=timeout)
            result = project.consume_task_result(completed)
            return {
                "task": completed.model_dump(mode="json"),
                "result": result.as_dict(),
            }

    raise ValueError(f"Unknown command: {command}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mediaflow-cli",
        description="MediaFlow Pro headless editor interface. All output is JSON.",
    )
    parser.add_argument(
        "--request",
        help="Read a structured JSON request from a file, or '-' for stdin.",
    )
    commands = parser.add_subparsers(dest="subcommand")

    create = commands.add_parser("project-create")
    create.add_argument("--project", required=True)
    create.add_argument("--name", required=True)

    inspect = commands.add_parser("project-inspect")
    inspect.add_argument("--project", required=True)

    asset_import = commands.add_parser("asset-import")
    asset_import.add_argument("--project", required=True)
    asset_import.add_argument("--source", required=True)

    task_list = commands.add_parser("task-list")
    task_list.add_argument("--project", required=True)

    task_status = commands.add_parser("task-status")
    task_status.add_argument("--project", required=True)
    task_status.add_argument("--task-id", required=True)

    task_start = commands.add_parser("task-start")
    task_start.add_argument("--project", required=True)
    task_start.add_argument("--task-command", required=True, help="Typed task command JSON object")
    task_start.add_argument("--sequence-id")
    task_start.add_argument("--input-asset-id", action="append", default=[])
    task_start.add_argument("--timeout", type=float, default=3600)

    task_resume = commands.add_parser("task-resume")
    task_resume.add_argument("--project", required=True)
    task_resume.add_argument("--task-id", required=True)
    task_resume.add_argument("--timeout", type=float, default=3600)
    return parser


def _request_from_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> dict[str, Any]:
    if args.request:
        text = sys.stdin.read() if args.request == "-" else Path(args.request).read_text(encoding="utf-8")
        value = json.loads(text)
        if not isinstance(value, dict):
            raise ValueError("The JSON request must be an object")
        return value
    if not args.subcommand:
        parser.error("a subcommand or --request is required")
    values = vars(args)
    command = args.subcommand.replace("-", ".")
    request: dict[str, Any] = {
        "command": command,
        **{
            key: value
            for key, value in values.items()
            if key not in {"request", "subcommand"} and value is not None
        },
    }
    if args.subcommand == "task-start":
        request["task_command"] = json.loads(args.task_command)
        request["input_asset_ids"] = args.input_asset_id
        request.pop("input_asset_id", None)
    return request


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    try:
        request = _request_from_args(parser.parse_args(argv), parser)
        result = execute_request(request)
        output = {"ok": True, "result": result}
        code = 0
    except Exception as error:
        output = {
            "ok": False,
            "error": {"type": type(error).__name__, "message": str(error)},
        }
        code = 1
    sys.stdout.write(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
