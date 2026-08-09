from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiohttp import ClientSession, ClientTimeout, TCPConnector, WSMsgType
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.types import Tool as MCPTool
from mcp.types import ToolAnnotations

from mediaflow.service.client import EditorServiceClient

EditorServiceConnector = Callable[[ClientSession], Awaitable[EditorServiceClient]]


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


class _ContractMCPServer(MCPServer["_McpState"]):
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


@dataclass(slots=True)
class _McpState:
    session: ClientSession
    connect: EditorServiceConnector

    async def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        client = await self.connect(self.session)
        return await client.call(method, params, session=self.session)

    async def client(self) -> EditorServiceClient:
        return await self.connect(self.session)


def build_mcp_server(
    *,
    connect: EditorServiceConnector = _connect_editor_service,
) -> MCPServer[_McpState]:
    @asynccontextmanager
    async def lifespan(_server: MCPServer) -> AsyncIterator[_McpState]:
        timeout = ClientTimeout(total=None, connect=5, sock_connect=5, sock_read=None)
        connector = TCPConnector(limit=32, limit_per_host=32, keepalive_timeout=30)
        async with ClientSession(timeout=timeout, connector=connector) as session:
            state = _McpState(session=session, connect=connect)
            contract = await state.call("system.describe")
            if not isinstance(contract, dict):
                raise RuntimeError(
                    "Editor Service returned an invalid contract document"
                )
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

    @server.tool(
        name="mediaflow_describe",
        description="Read the current MediaFlow Editor operation and schema contract.",
        annotations=ToolAnnotations(
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    async def describe(ctx: Context[_McpState, Any]) -> dict[str, Any]:
        result = await ctx.request_context.lifespan_context.call("system.describe")
        if not isinstance(result, dict):
            raise RuntimeError("Editor Service returned an invalid contract document")
        return result

    @server.tool(
        name="mediaflow_execute",
        description=(
            "Execute one operation through the resident Editor Service. Read operations may "
            "omit request_id and base_revision. Project writes must provide both values."
        ),
        annotations=ToolAnnotations(
            destructive_hint=True,
            idempotent_hint=False,
            open_world_hint=False,
        ),
    )
    async def execute(
        operation: str,
        ctx: Context[_McpState, Any],
        project: str | None = None,
        arguments: dict[str, Any] | None = None,
        request_id: str | None = None,
        base_revision: int | None = None,
        actor_id: str = "mediaflow-mcp",
        actor_name: str = "MCP Agent",
        client_id: str = "mediaflow-mcp",
    ) -> dict[str, Any]:
        request = {
            "protocol": "mediaflow-editor",
            "version": 4,
            "operation": operation,
            "project": project,
            "arguments": arguments or {},
            "request_id": request_id,
            "base_revision": base_revision,
            "actor": {
                "kind": "agent",
                "id": actor_id,
                "name": actor_name,
            },
            "client_id": client_id,
        }
        result = await ctx.request_context.lifespan_context.call(
            "operation.execute",
            {"request": request},
        )
        if not isinstance(result, dict):
            raise RuntimeError("Editor Service returned an invalid operation result")
        return result

    @server.tool(
        name="mediaflow_execute_batch",
        description=(
            "Execute atomic project-write requests as one collaboration batch and one undo "
            "group. Every request must use the current mediaflow-editor v4 envelope."
        ),
        annotations=ToolAnnotations(
            destructive_hint=True,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    async def execute_batch(
        requests: list[dict[str, Any]],
        batch_id: str,
        ctx: Context[_McpState, Any],
        label: str = "Agent batch",
    ) -> dict[str, Any]:
        result = await ctx.request_context.lifespan_context.call(
            "operation.execute_batch",
            {
                "requests": requests,
                "batch_id": batch_id,
                "label": label,
            },
        )
        if not isinstance(result, dict):
            raise RuntimeError("Editor Service returned an invalid batch result")
        return result

    @server.tool(
        name="mediaflow_follow_events",
        description=(
            "Follow committed project changes and task progress through the Editor Service "
            "WebSocket. Task progress is also forwarded as MCP progress notifications."
        ),
        annotations=ToolAnnotations(
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    async def follow_events(
        project: str,
        ctx: Context[_McpState, Any],
        project_cursor: int = 0,
        task_cursor: int = 0,
        task_id: str | None = None,
        timeout_seconds: float = 30.0,
        max_events: int = 100,
    ) -> dict[str, Any]:
        if project_cursor < 0 or task_cursor < 0:
            raise ValueError("event cursors must be non-negative")
        if not 0 < timeout_seconds <= 3600:
            raise ValueError("timeout_seconds must be between 0 and 3600")
        if not 1 <= max_events <= 1000:
            raise ValueError("max_events must be between 1 and 1000")
        project_path = str(Path(project).expanduser().resolve())
        state = ctx.request_context.lifespan_context
        client = await state.client()
        events: list[dict[str, Any]] = []
        terminal_task: dict[str, Any] | None = None
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        async with state.session.ws_connect(
            client.discovery.websocket_url,
            headers={"Authorization": f"Bearer {client.discovery.token}"},
            heartbeat=20,
        ) as websocket:
            await websocket.send_json(
                {
                    "type": "project.subscribe",
                    "project": project_path,
                    "project_cursor": project_cursor,
                    "task_cursor": task_cursor,
                }
            )
            while len(events) < max_events:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                try:
                    message = await asyncio.wait_for(
                        websocket.receive(),
                        timeout=remaining,
                    )
                except TimeoutError:
                    break
                if message.type != WSMsgType.TEXT:
                    if message.type in {WSMsgType.CLOSE, WSMsgType.ERROR}:
                        break
                    continue
                value = message.json()
                if not isinstance(value, dict) or not isinstance(value.get("payload"), dict):
                    continue
                event_type = str(value.get("type") or "")
                payload = value["payload"]
                if event_type == "project.subscribed":
                    project_cursor = max(
                        project_cursor,
                        int(payload.get("project_cursor", project_cursor)),
                    )
                    task_cursor = max(
                        task_cursor,
                        int(payload.get("task_cursor", task_cursor)),
                    )
                    continue
                if event_type == "project.changed":
                    cursor = int(payload.get("cursor", 0))
                    if cursor <= project_cursor:
                        continue
                    project_cursor = cursor
                    events.append(value)
                    await ctx.report_progress(
                        float(payload.get("project_revision", 0)),
                        None,
                        f"project.changed:{payload.get('operation')}",
                    )
                    if task_id is None:
                        break
                    continue
                if event_type != "task.changed":
                    continue
                cursor = int(payload.get("cursor", 0))
                if cursor <= task_cursor:
                    continue
                task_cursor = cursor
                task = payload.get("payload")
                if not isinstance(task, dict):
                    continue
                if task_id is not None and str(task.get("id") or "") != task_id:
                    continue
                events.append(value)
                progress = task.get("progress")
                if isinstance(progress, dict):
                    completed = progress.get("overall_completed", progress.get("completed"))
                    total = progress.get("overall_total", progress.get("total"))
                    await ctx.report_progress(
                        float(completed) if completed is not None else 0.0,
                        float(total) if total is not None else None,
                        str(progress.get("message_code") or task.get("status") or "task.changed"),
                    )
                if str(task.get("status") or "") in {"completed", "failed", "cancelled"}:
                    terminal_task = task
                    break
        return {
            "project": project_path,
            "project_cursor": project_cursor,
            "task_cursor": task_cursor,
            "events": events,
            "terminal_task": terminal_task,
            "timed_out": not events or (task_id is not None and terminal_task is None),
        }

    @server.tool(
        name="mediaflow_workspace_command",
        description=(
            "Control the playhead or playback of one explicitly connected desktop "
            "workspace session. The desktop supplies the workspace_session_id."
        ),
        annotations=ToolAnnotations(
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=False,
        ),
    )
    async def workspace_command(
        workspace_session_id: str,
        command: str,
        ctx: Context[_McpState, Any],
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result = await ctx.request_context.lifespan_context.call(
            "workspace.command",
            {
                "workspace_session_id": workspace_session_id,
                "command": command,
                "arguments": arguments or {},
            },
        )
        if not isinstance(result, dict):
            raise RuntimeError("Editor Service returned an invalid workspace event")
        return result

    return server


def main() -> None:
    build_mcp_server().run("stdio")


if __name__ == "__main__":
    main()
