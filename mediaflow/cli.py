from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import NoReturn

from pydantic import ValidationError

from mediaflow.automation.contracts import (
    AUTOMATION_PROTOCOL,
    AUTOMATION_VERSION,
    AutomationError,
    AutomationFailureResponse,
    AutomationRequest,
    AutomationSuccessResponse,
)
from mediaflow.domain.product_identity import PRODUCT_NAME
from mediaflow.infrastructure.project_migration_runner import (
    ProjectUpgradeRequiredError,
)
from mediaflow.service.client import (
    EditorServiceRpcError,
    EditorServiceUnavailable,
    call_sync,
    execute_sync,
)


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise ValueError(message)


def _parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(
        prog="mediaflow-cli",
        description=f"{PRODUCT_NAME} headless editor interface. All output is JSON.",
    )
    commands = parser.add_subparsers(dest="subcommand", required=True)
    commands.add_parser("describe", help="Print the versioned capability contract")
    execute = commands.add_parser("execute", help="Execute one versioned JSON request")
    execute.add_argument(
        "--request",
        required=True,
        help="Read a structured JSON request from a file, or '-' for stdin.",
    )
    service = commands.add_parser("service", help="Inspect or explicitly stop the Editor Service")
    service.add_argument("action", choices=("status", "shutdown"))
    service.add_argument(
        "--force",
        action="store_true",
        help="Cancel active tasks before shutting down the service.",
    )
    return parser


def _request_from_args(args: argparse.Namespace) -> dict:
    if args.subcommand == "describe":
        return {
            "protocol": AUTOMATION_PROTOCOL,
            "version": AUTOMATION_VERSION,
            "operation": "describe",
            "actor": {"kind": "agent", "id": "mediaflow-cli"},
            "client_id": "mediaflow-cli",
        }
    text = sys.stdin.read() if args.request == "-" else Path(args.request).read_text(encoding="utf-8")
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("The JSON request must be an object")
    return value


def _execute_from_args(args: argparse.Namespace) -> tuple[str | None, dict]:
    if args.subcommand == "service":
        method = "service.status" if args.action == "status" else "service.shutdown"
        result = call_sync(
            method,
            {"force": bool(args.force)} if args.action == "shutdown" else None,
            start_if_needed=False,
        )
        if not isinstance(result, dict):
            raise RuntimeError("Editor Service command returned an invalid result")
        return None, result
    request = _request_from_args(args)
    envelope = AutomationRequest.model_validate(request)
    result = (
        call_sync("system.describe")
        if envelope.operation == "describe"
        else execute_sync(envelope.model_dump(mode="json"))
    )
    if not isinstance(result, dict):
        raise RuntimeError("Editor Service command returned an invalid result")
    return envelope.request_id, result


def _error_code(error: Exception) -> str:
    if isinstance(error, EditorServiceRpcError):
        if (
            isinstance(error.data, dict)
            and error.data.get("type") == "ProjectUpgradeRequiredError"
        ):
            return "upgrade_required"
        return {
            -32602: "invalid_request",
            -32009: "conflict",
            -32004: "not_found",
            -32003: "permission_denied",
        }.get(error.code, "runtime_error")
    if isinstance(error, ProjectUpgradeRequiredError):
        return "upgrade_required"
    if isinstance(error, ValidationError):
        return "validation_error"
    if isinstance(error, (FileNotFoundError, KeyError)):
        return "not_found"
    if isinstance(error, PermissionError):
        return "permission_denied"
    if isinstance(error, ValueError):
        return "invalid_request"
    if isinstance(error, RuntimeError) and "conflict" in str(error).lower():
        return "conflict"
    if isinstance(error, EditorServiceUnavailable):
        return "service_unavailable"
    return "runtime_error"


def _error_type(error: Exception) -> str:
    if isinstance(error, EditorServiceRpcError) and isinstance(error.data, dict):
        remote_type = error.data.get("type")
        if isinstance(remote_type, str) and remote_type:
            return remote_type
    return type(error).__name__


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    request_id = None
    try:
        request_id, result = _execute_from_args(parser.parse_args(argv))
        output = AutomationSuccessResponse(
            request_id=request_id,
            result=result,
        ).model_dump(mode="json")
        code = 0
    except Exception as error:
        output = AutomationFailureResponse(
            request_id=request_id,
            error=AutomationError(
                code=_error_code(error),
                type=_error_type(error),
                message=str(error),
            ),
        ).model_dump(mode="json")
        code = 1
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        try:
            reconfigure(encoding="utf-8")
        except ValueError:
            pass
    sys.stdout.write(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    sys.stdout.flush()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
