from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from aiohttp import ClientSession, ClientTimeout, TCPConnector
from mcp.server import MCPServer
from mcp.types import Tool as MCPTool

from mediaflow.mcp_tools import (
    EditorServiceConnector,
    McpState,
    register_mediaflow_tools,
)
from mediaflow.service.client import EditorServiceClient


async def _connect_editor_service(session: ClientSession) -> EditorServiceClient:
    return await EditorServiceClient.connect(session=session)


def _operation_schema(
    schema: dict[str, Any],
    *,
    namespace: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    definitions = schema.get("$defs")
    names = {
        str(name): f"{namespace}__{name}"
        for name in definitions
    } if isinstance(definitions, dict) else {}

    def rewrite(value: Any) -> Any:
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        if not isinstance(value, dict):
            return value
        rewritten: dict[str, Any] = {}
        for key, item in value.items():
            if key == "$defs":
                continue
            if (
                key == "$ref"
                and isinstance(item, str)
                and item.startswith("#/$defs/")
            ):
                original = item.removeprefix("#/$defs/")
                rewritten[key] = f"#/$defs/{names[original]}"
            else:
                rewritten[key] = rewrite(item)
        return rewritten

    shared = {
        names[str(name)]: rewrite(value)
        for name, value in (definitions or {}).items()
    } if isinstance(definitions, dict) else {}
    return rewrite(schema), shared


def _execute_input_schema(
    base_schema: dict[str, Any],
    contract: dict[str, Any],
) -> dict[str, Any]:
    operations = contract.get("operations")
    if not isinstance(operations, list) or not operations:
        raise RuntimeError("Editor Service describe document has no operations")
    branches: list[dict[str, Any]] = []
    definitions: dict[str, Any] = {}
    for index, operation in enumerate(operations):
        if not isinstance(operation, dict):
            raise RuntimeError("Editor Service operation contract is invalid")
        name = str(operation.get("name") or "").strip()
        arguments = operation.get("arguments_schema")
        if not name or not isinstance(arguments, dict):
            raise RuntimeError("Editor Service operation schema is incomplete")
        namespace = f"operation_{index}_{name.replace('.', '_')}"
        argument_schema, shared = _operation_schema(
            arguments,
            namespace=namespace,
        )
        overlap = definitions.keys() & shared.keys()
        if overlap:
            raise RuntimeError(
                "Editor Service operation definitions are not uniquely namespaced"
            )
        definitions.update(shared)
        required = ["operation"]
        if argument_schema.get("required"):
            required.append("arguments")
        access = str(operation.get("project_access") or "none")
        if access in {"read", "write"}:
            required.append("project")
        if access in {"create", "write"}:
            required.append("request_id")
        if access == "write":
            required.append("base_revision")
        branches.append(
            {
                "properties": {
                    "operation": {"const": name},
                    "arguments": argument_schema,
                },
                "required": required,
            }
        )
    generated = dict(base_schema)
    generated["oneOf"] = branches
    if definitions:
        generated["$defs"] = definitions
    return generated


class _ContractMCPServer(MCPServer[McpState]):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._described_execute_schema: dict[str, Any] | None = None

    def install_contract(self, contract: dict[str, Any]) -> None:
        execute_tool = self._tool_manager.get_tool("mediaflow_execute")
        if execute_tool is None:
            raise RuntimeError("MCP execute tool is not registered")
        self._described_execute_schema = _execute_input_schema(
            execute_tool.parameters,
            contract,
        )

    async def list_tools(self) -> list[MCPTool]:
        tools = await super().list_tools()
        if self._described_execute_schema is None:
            return tools
        return [
            tool.model_copy(
                update={"input_schema": self._described_execute_schema}
            )
            if tool.name == "mediaflow_execute"
            else tool
            for tool in tools
        ]


def build_mcp_server(
    *,
    connect: EditorServiceConnector = _connect_editor_service,
) -> MCPServer[McpState]:
    @asynccontextmanager
    async def lifespan(_server: MCPServer) -> AsyncIterator[McpState]:
        timeout = ClientTimeout(total=None, connect=5, sock_connect=5, sock_read=None)
        connector = TCPConnector(limit=32, limit_per_host=32, keepalive_timeout=30)
        async with ClientSession(timeout=timeout, connector=connector) as session:
            state = McpState(session=session, connect=connect)
            contract = await state.call("system.describe")
            if not isinstance(contract, dict):
                raise RuntimeError("Editor Service returned an invalid contract document")
            if not isinstance(_server, _ContractMCPServer):
                raise RuntimeError("MCP server lost its contract-aware boundary")
            _server.install_contract(contract)
            yield state

    server = _ContractMCPServer(
        "MediaFlow Pro",
        description="Thin MCP adapter for the resident MediaFlow Pro Editor Service.",
        instructions=(
            "Call mediaflow_describe before choosing an operation. Project writes require "
            "a stable request_id and the latest base_revision. Reuse the same request_id and "
            "unchanged input when retrying an uncertain result."
        ),
        version="4",
        lifespan=lifespan,
    )
    register_mediaflow_tools(server)
    return server


def main() -> None:
    build_mcp_server().run("stdio")


if __name__ == "__main__":
    main()
