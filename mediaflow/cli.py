from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from mediaflow.automation.contracts import AUTOMATION_PROTOCOL, AUTOMATION_VERSION
from mediaflow.automation.dispatcher import execute_request


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mediaflow-cli",
        description="MediaFlow Pro headless editor interface. All output is JSON.",
    )
    commands = parser.add_subparsers(dest="subcommand", required=True)
    commands.add_parser("describe", help="Print the versioned capability contract")
    execute = commands.add_parser("execute", help="Execute one versioned JSON request")
    execute.add_argument(
        "--request",
        required=True,
        help="Read a structured JSON request from a file, or '-' for stdin.",
    )
    return parser


def _request_from_args(args: argparse.Namespace) -> dict:
    if args.subcommand == "describe":
        return {
            "protocol": AUTOMATION_PROTOCOL,
            "version": AUTOMATION_VERSION,
            "operation": "describe",
        }
    text = sys.stdin.read() if args.request == "-" else Path(args.request).read_text(encoding="utf-8")
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("The JSON request must be an object")
    return value


def _error_code(error: Exception) -> str:
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
    return "runtime_error"


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    request_id = None
    try:
        request = _request_from_args(parser.parse_args(argv))
        request_id = request.get("request_id")
        result = execute_request(request)
        output = {
            "protocol": AUTOMATION_PROTOCOL,
            "version": AUTOMATION_VERSION,
            "request_id": request_id,
            "ok": True,
            "result": result,
        }
        code = 0
    except Exception as error:
        output = {
            "protocol": AUTOMATION_PROTOCOL,
            "version": AUTOMATION_VERSION,
            "request_id": request_id,
            "ok": False,
            "error": {
                "code": _error_code(error),
                "type": type(error).__name__,
                "message": str(error),
            },
        }
        code = 1
    sys.stdout.write(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
