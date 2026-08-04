from __future__ import annotations

import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import pytest
from aiohttp import ClientSession
from mcp import Client, StdioServerParameters, stdio_client

from mediaflow.mcp_server import build_mcp_server
from mediaflow.service.client import EditorServiceClient
from mediaflow.service.discovery import ServicePaths
from mediaflow.service.server import EditorServiceServer


def _paths(root: Path) -> ServicePaths:
    return ServicePaths(
        root=root,
        lock=root / "service.lock",
        discovery=root / "discovery.json",
        log=root / "service.log",
    )


@pytest.mark.asyncio
async def test_installed_mcp_stdio_entrypoint_starts_and_uses_the_editor_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service_root = tmp_path / "stdio-service"
    monkeypatch.setenv("MEDIAFLOW_SERVICE_STATE_DIR", str(service_root))
    monkeypatch.setenv("MEDIAFLOW_PROJECT_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("MEDIAFLOW_MEDIA_ROOT", str(tmp_path / "media"))
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mediaflow.mcp_server"],
        env=os.environ.copy(),
        cwd=Path(__file__).resolve().parents[3],
    )
    try:
        async with Client(stdio_client(parameters), raise_exceptions=True) as client:
            tools = await client.list_tools()
            assert {tool.name for tool in tools.tools} == {
                "mediaflow_describe",
                "mediaflow_execute",
                "mediaflow_execute_batch",
                "mediaflow_follow_events",
                "mediaflow_workspace_command",
            }
            execute_schema = next(
                tool.input_schema
                for tool in tools.tools
                if tool.name == "mediaflow_execute"
            )
            generated_operations = {
                branch["properties"]["operation"]["const"]
                for branch in execute_schema["oneOf"]
            }
            assert "timeline.track.add" in generated_operations
            assert "runtime.inspect" in generated_operations
            described = await client.call_tool("mediaflow_describe")
            assert described.structured_content["transport"]["lifecycle"] == (
                "resident-editor-service"
            )
    finally:
        if _paths(service_root).discovery.is_file():
            service_client = await EditorServiceClient.connect(
                paths=_paths(service_root),
                start_if_needed=False,
            )
            await service_client.call("service.shutdown")


@pytest.mark.asyncio
async def test_mcp_protocol_calls_the_real_resident_editor_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MEDIAFLOW_PROJECT_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("MEDIAFLOW_MEDIA_ROOT", str(tmp_path / "media"))
    editor_server = EditorServiceServer(paths=_paths(tmp_path / "service-state"))
    discovery = await editor_server.start()
    editor_client = EditorServiceClient(discovery)

    async def connect(_session) -> EditorServiceClient:
        return editor_client

    mcp_server = build_mcp_server(connect=connect)
    try:
        async with Client(mcp_server, raise_exceptions=True) as client:
            tools = await client.list_tools()
            assert {tool.name for tool in tools.tools} == {
                "mediaflow_describe",
                "mediaflow_execute",
                "mediaflow_execute_batch",
                "mediaflow_follow_events",
                "mediaflow_workspace_command",
            }
            described = await client.call_tool("mediaflow_describe")
            assert described.structured_content["version"] == 3
            execute_schema = next(
                tool.input_schema
                for tool in tools.tools
                if tool.name == "mediaflow_execute"
            )
            assert len(execute_schema["oneOf"]) == len(
                described.structured_content["operations"]
            )
            track_branch = next(
                branch
                for branch in execute_schema["oneOf"]
                if branch["properties"]["operation"]["const"]
                == "timeline.track.add"
            )
            assert set(track_branch["required"]) >= {
                "operation",
                "arguments",
                "project",
                "request_id",
                "base_revision",
            }
            assert (
                track_branch["properties"]["arguments"]["properties"]
                ["kind"]["enum"]
                == ["video", "audio", "subtitle"]
            )
            created_call = await client.call_tool(
                "mediaflow_execute",
                {
                    "operation": "project.create",
                    "request_id": "mcp-create-project",
                    "arguments": {
                        "name": "MCP Project",
                        "directory_name": "mcp-project",
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
                    "actor_id": "mcp-integration-agent",
                    "client_id": "mcp-integration-client",
                },
            )
            created = created_call.structured_content
            project = Path(created["result"]["path"])
            sequence_id = created["result"]["project"]["main_sequence_id"]
            changed_call = await client.call_tool(
                "mediaflow_execute",
                {
                    "operation": "timeline.track.add",
                    "project": str(project),
                    "request_id": "mcp-add-track",
                    "base_revision": 0,
                    "arguments": {
                        "sequence_id": sequence_id,
                        "kind": "video",
                        "name": "MCP visible track",
                    },
                    "actor_id": "mcp-integration-agent",
                    "client_id": "mcp-integration-client",
                },
            )
            changed = changed_call.structured_content
            assert changed["project_revision"] == 1
            assert changed["event"]["actor"]["id"] == "mcp-integration-agent"

            inspected_call = await client.call_tool(
                "mediaflow_execute",
                {
                    "operation": "timeline.get",
                    "project": str(project),
                    "arguments": {"sequence_id": sequence_id},
                },
            )
            inspected = inspected_call.structured_content
            assert "MCP visible track" in {
                track["name"]
                for track in inspected["result"]["timeline"]["tracks"]
            }

            followed_call = await client.call_tool(
                "mediaflow_follow_events",
                {
                    "project": str(project),
                    "project_cursor": 1,
                    "max_events": 1,
                    "timeout_seconds": 5,
                },
            )
            followed = followed_call.structured_content
            assert followed["events"][0]["type"] == "project.changed"
            assert followed["events"][0]["payload"]["request_id"] == "mcp-add-track"

            workspace = await editor_client.call(
                "workspace.attach",
                {
                    "client_id": "mcp-workspace-desktop",
                    "project": str(project),
                },
            )
            workspace_id = workspace["workspace_session_id"]
            headers = {"Authorization": f"Bearer {discovery.token}"}
            async with ClientSession(headers=headers) as session:
                async with session.ws_connect(discovery.websocket_url) as websocket:
                    assert (await websocket.receive_json())["type"] == "service.ready"
                    await websocket.send_json(
                        {
                            "type": "project.subscribe",
                            "project": str(project),
                            "project_cursor": 2,
                            "task_cursor": 0,
                            "workspace_session_id": workspace_id,
                            "client_id": "mcp-workspace-desktop",
                        }
                    )
                    assert (await websocket.receive_json())["type"] == "project.subscribed"
                    workspace_call = await client.call_tool(
                        "mediaflow_workspace_command",
                        {
                            "workspace_session_id": workspace_id,
                            "command": "playhead.seek",
                            "arguments": {"frame": 37},
                        },
                    )
                    assert workspace_call.structured_content["workspace_revision"] == 1
                    workspace_event = await websocket.receive_json(timeout=5)
                    assert workspace_event["type"] == "workspace.changed"
                    assert workspace_event["payload"]["arguments"] == {"frame": 37}
            await editor_client.call(
                "workspace.detach",
                {
                    "workspace_session_id": workspace_id,
                    "client_id": "mcp-workspace-desktop",
                },
            )

        with closing(sqlite3.connect(project / "project.mfp")) as connection:
            event = connection.execute(
                "SELECT request_id, json_extract(actor_json, '$.kind') "
                "FROM project_event WHERE request_id=?",
                ("mcp-add-track",),
            ).fetchone()
        assert event == ("mcp-add-track", "agent")
    finally:
        await editor_server.stop()
