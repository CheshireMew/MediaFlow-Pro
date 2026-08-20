from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aiohttp import ClientSession, WSMsgType
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.types import ToolAnnotations

from mediaflow.service.client import EditorServiceClient

EditorServiceConnector = Callable[[ClientSession], Awaitable[EditorServiceClient]]


@dataclass(slots=True)
class McpState:
    session: ClientSession
    connect: EditorServiceConnector

    async def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        client = await self.connect(self.session)
        return await client.call(method, params, session=self.session)

    async def client(self) -> EditorServiceClient:
        return await self.connect(self.session)


def _annotations(
    *,
    read_only: bool,
    destructive: bool,
    idempotent: bool,
) -> ToolAnnotations:
    return ToolAnnotations(
        read_only_hint=read_only,
        destructive_hint=destructive,
        idempotent_hint=idempotent,
        open_world_hint=False,
    )


def register_describe_tool(server: MCPServer[McpState]) -> None:
    @server.tool(
        name="mediaflow_describe",
        description="Read the current MediaFlow Editor operation and schema contract.",
        annotations=_annotations(
            read_only=True,
            destructive=False,
            idempotent=True,
        ),
    )
    async def describe(ctx: Context[McpState, Any]) -> dict[str, Any]:
        result = await ctx.request_context.lifespan_context.call("system.describe")
        if not isinstance(result, dict):
            raise RuntimeError("Editor Service returned an invalid contract document")
        return result


def register_execute_tool(server: MCPServer[McpState]) -> None:
    @server.tool(
        name="mediaflow_execute",
        description=(
            "Execute one operation through the resident Editor Service. Read operations may "
            "omit request_id and base_revision. Project writes must provide both values."
        ),
        annotations=_annotations(
            read_only=False,
            destructive=True,
            idempotent=False,
        ),
    )
    async def execute(
        operation: str,
        ctx: Context[McpState, Any],
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
            "actor": {"kind": "agent", "id": actor_id, "name": actor_name},
            "client_id": client_id,
        }
        result = await ctx.request_context.lifespan_context.call(
            "operation.execute",
            {"request": request},
        )
        if not isinstance(result, dict):
            raise RuntimeError("Editor Service returned an invalid operation result")
        return result


def register_execute_batch_tool(server: MCPServer[McpState]) -> None:
    @server.tool(
        name="mediaflow_execute_batch",
        description=(
            "Execute atomic project-write requests as one collaboration batch and one undo "
            "group. Every request must use the current mediaflow-editor v4 envelope."
        ),
        annotations=_annotations(
            read_only=False,
            destructive=True,
            idempotent=True,
        ),
    )
    async def execute_batch(
        requests: list[dict[str, Any]],
        batch_id: str,
        ctx: Context[McpState, Any],
        label: str = "Agent batch",
    ) -> dict[str, Any]:
        result = await ctx.request_context.lifespan_context.call(
            "operation.execute_batch",
            {"requests": requests, "batch_id": batch_id, "label": label},
        )
        if not isinstance(result, dict):
            raise RuntimeError("Editor Service returned an invalid batch result")
        return result


@dataclass(slots=True)
class _FollowResult:
    project_cursor: int
    task_cursor: int
    events: list[dict[str, Any]] = field(default_factory=list)
    terminal_task: dict[str, Any] | None = None


async def _record_follow_event(
    value: object,
    result: _FollowResult,
    *,
    task_id: str | None,
    ctx: Context[McpState, Any],
) -> bool:
    if not isinstance(value, dict) or not isinstance(value.get("payload"), dict):
        return False
    event_type = str(value.get("type") or "")
    payload = value["payload"]
    if event_type == "project.subscribed":
        result.project_cursor = max(
            result.project_cursor,
            int(payload.get("project_cursor", result.project_cursor)),
        )
        result.task_cursor = max(
            result.task_cursor,
            int(payload.get("task_cursor", result.task_cursor)),
        )
        return False
    if event_type == "project.changed":
        cursor = int(payload.get("cursor", 0))
        if cursor <= result.project_cursor:
            return False
        result.project_cursor = cursor
        result.events.append(value)
        await ctx.report_progress(
            float(payload.get("project_revision", 0)),
            None,
            f"project.changed:{payload.get('operation')}",
        )
        return task_id is None
    if event_type != "task.changed":
        return False
    cursor = int(payload.get("cursor", 0))
    if cursor <= result.task_cursor:
        return False
    result.task_cursor = cursor
    task = payload.get("payload")
    if not isinstance(task, dict):
        return False
    if task_id is not None and str(task.get("id") or "") != task_id:
        return False
    result.events.append(value)
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
        result.terminal_task = task
        return True
    return False


async def _follow_events(
    *,
    project: str,
    project_cursor: int,
    task_cursor: int,
    task_id: str | None,
    timeout_seconds: float,
    max_events: int,
    ctx: Context[McpState, Any],
) -> dict[str, Any]:
    project_path = str(Path(project).expanduser().resolve())
    state = ctx.request_context.lifespan_context
    client = await state.client()
    result = _FollowResult(project_cursor=project_cursor, task_cursor=task_cursor)
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
        while len(result.events) < max_events:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            try:
                message = await asyncio.wait_for(websocket.receive(), timeout=remaining)
            except TimeoutError:
                break
            if message.type != WSMsgType.TEXT:
                if message.type in {WSMsgType.CLOSE, WSMsgType.ERROR}:
                    break
                continue
            if await _record_follow_event(
                message.json(),
                result,
                task_id=task_id,
                ctx=ctx,
            ):
                break
    return {
        "project": project_path,
        "project_cursor": result.project_cursor,
        "task_cursor": result.task_cursor,
        "events": result.events,
        "terminal_task": result.terminal_task,
        "timed_out": not result.events
        or (task_id is not None and result.terminal_task is None),
    }


def register_follow_events_tool(server: MCPServer[McpState]) -> None:
    @server.tool(
        name="mediaflow_follow_events",
        description=(
            "Follow committed project changes and task progress through the Editor Service "
            "WebSocket. Task progress is also forwarded as MCP progress notifications."
        ),
        annotations=_annotations(
            read_only=True,
            destructive=False,
            idempotent=True,
        ),
    )
    async def follow_events(
        project: str,
        ctx: Context[McpState, Any],
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
        return await _follow_events(
            project=project,
            project_cursor=project_cursor,
            task_cursor=task_cursor,
            task_id=task_id,
            timeout_seconds=timeout_seconds,
            max_events=max_events,
            ctx=ctx,
        )


def register_workspace_tool(server: MCPServer[McpState]) -> None:
    @server.tool(
        name="mediaflow_workspace_command",
        description=(
            "Control the playhead or playback of one explicitly connected desktop "
            "workspace session. The desktop supplies the workspace_session_id."
        ),
        annotations=_annotations(
            read_only=False,
            destructive=False,
            idempotent=False,
        ),
    )
    async def workspace_command(
        workspace_session_id: str,
        command: str,
        ctx: Context[McpState, Any],
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


def register_mediaflow_tools(server: MCPServer[McpState]) -> None:
    register_describe_tool(server)
    register_execute_tool(server)
    register_execute_batch_tool(server)
    register_follow_events_tool(server)
    register_workspace_tool(server)
