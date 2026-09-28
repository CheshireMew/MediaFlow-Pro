from __future__ import annotations

from types import SimpleNamespace
from typing import Any, get_args

import pytest
from pydantic import ValidationError

from mediaflow.application.presentation_models import RecentProjectSnapshot
from mediaflow.domain.workspace_commands import (
    WORKSPACE_COMMANDS,
    WorkspaceCommandEvent,
    WorkspaceCommandName,
)
from mediaflow.service.application_commands import APPLICATION_COMMANDS, application_command
from mediaflow.service.client import EditorServiceClient, EditorServiceUnavailable
from mediaflow.service.codec import decode_transport, encode_transport
from mediaflow.service.commands import command_models, command_signature
from mediaflow.service.desktop_application_proxy import DesktopEditorApplication
from mediaflow.service.discovery import SERVICE_PROTOCOL_VERSION, ServiceDiscovery, ServicePaths
from mediaflow.service.execution import ServiceExecutionPools
from mediaflow.service.request_dispatcher import ServiceRequestDispatcher
from mediaflow.service.runtime_sessions import ApplicationRuntimeOperations
from mediaflow.service.workspaces import WorkspaceRegistry


def test_command_contract_cannot_silently_accept_an_untyped_result() -> None:
    def untyped() -> Any:
        return None

    signature, annotations, result = command_signature(untyped)
    with pytest.raises(RuntimeError, match="untyped result"):
        command_models("application", "untyped", signature, annotations, result)


@pytest.mark.parametrize("name", tuple(APPLICATION_COMMANDS))
def test_application_commands_have_individual_request_and_result_contracts(name: str) -> None:
    definition = application_command(name)
    assert definition.request_model.model_json_schema()["additionalProperties"] is False
    assert definition.result_model.model_fields["value"].annotation not in {object, Any}
    with pytest.raises(ValidationError):
        definition.invoke(object(), {"unexpected_argument": True})
    with pytest.raises(ValidationError):
        definition.validate_result(object())
    assert sum(item.request_model is definition.request_model for item in APPLICATION_COMMANDS.values()) == 1
    assert sum(item.result_model is definition.result_model for item in APPLICATION_COMMANDS.values()) == 1


def test_application_proxy_and_runtime_share_named_arguments_and_domain_results(monkeypatch) -> None:
    snapshot = RecentProjectSnapshot(items=[], totals={"projects": 0})
    requested = []

    def recent_projects(paths: list[str]) -> RecentProjectSnapshot:
        requested.append(paths)
        return snapshot

    runtime = ApplicationRuntimeOperations(
        SimpleNamespace(recent_projects=recent_projects), None, update_project_settings=lambda: None,
    )

    def rpc(method: str, params: dict[str, Any]) -> object:
        assert method == "desktop.application.call"
        assert set(params) == {"command", "arguments"}
        assert decode_transport(params["arguments"]) == {"paths": ["example"]}
        return runtime.execute_application_command(params["command"], params["arguments"])

    monkeypatch.setattr("mediaflow.service.desktop_application_proxy.call_sync", rpc)
    assert DesktopEditorApplication._application_call("recent_projects", ["example"]) == snapshot
    assert requested == [["example"]]


def test_invalid_application_request_never_reaches_the_service_handler() -> None:
    runtime = ApplicationRuntimeOperations(object(), None, update_project_settings=lambda: None)
    with pytest.raises(ValidationError):
        runtime.execute_application_command("recent_projects", encode_transport({"paths": 12}))
    with pytest.raises(ValueError, match="Unknown desktop application command"):
        runtime.execute_application_command("unregistered_method", {})


def test_application_runtime_rejects_a_wrong_handler_result() -> None:
    runtime = ApplicationRuntimeOperations(
        SimpleNamespace(installed_asr_models=lambda: 123), None, update_project_settings=lambda: None,
    )
    with pytest.raises(ValidationError):
        runtime.execute_application_command("installed_asr_models", {})


def test_invalid_runtime_tool_result_cannot_publish_a_completed_event() -> None:
    events = []
    runtime = ApplicationRuntimeOperations(
        SimpleNamespace(run_runtime_tool=lambda *_args, **_kwargs: object()),
        SimpleNamespace(publish_from_worker=events.append),
        update_project_settings=lambda: None,
    )
    with pytest.raises(ValidationError):
        runtime.execute_application_command(
            "run_runtime_tool", encode_transport({"operation": "inspect"}),
        )
    assert [event.payload["state"] for event in events] == ["running", "failed"]


def test_application_proxy_rejects_a_wrong_remote_result(monkeypatch) -> None:
    monkeypatch.setattr("mediaflow.service.desktop_application_proxy.call_sync", lambda *_: 123)
    with pytest.raises(ValidationError):
        DesktopEditorApplication._application_call("installed_asr_models")


@pytest.mark.asyncio
async def test_new_application_contract_cannot_attach_to_an_older_service(monkeypatch, tmp_path) -> None:
    discovery = ServiceDiscovery(
        protocol_version=SERVICE_PROTOCOL_VERSION - 1,
        pid=1, process_started_at=1, started_at=1, port=1, token="0" * 32,
    )
    calls = []

    async def hello(self, method, **kwargs):
        calls.append(method)
        return {
            "protocol": discovery.protocol, "protocol_version": discovery.protocol_version,
            "pid": discovery.pid,
        }

    monkeypatch.setattr(EditorServiceClient, "_live_discovery", lambda _paths: discovery)
    monkeypatch.setattr(EditorServiceClient, "call", hello)
    paths = ServicePaths(tmp_path, tmp_path / "lock", tmp_path / "discovery.json", tmp_path / "log")
    with pytest.raises(EditorServiceUnavailable, match="does not match"):
        await EditorServiceClient.connect(paths=paths, start_if_needed=False)
    assert calls == ["system.hello"]


@pytest.mark.asyncio
async def test_application_dispatch_rejects_the_retired_positional_wire_format() -> None:
    execution = ServiceExecutionPools()
    dispatcher = ServiceRequestDispatcher(
        SimpleNamespace(runtime=object()), None, None, lambda: None, execution,
    )
    try:
        with pytest.raises(ValueError, match="named arguments"):
            await dispatcher.dispatch("desktop.application.call", {
                "command": "installed_asr_models", "args": [], "kwargs": {},
            })
    finally:
        execution.close()


_WORKSPACE_EXAMPLES = {
    "playhead.seek": {"frame": 12},
    "playback.play": {"frame": 0},
    "playback.pause": {},
    "playback.stop": {},
    "workspace.mode.activate": {"mode": "transcript"},
    "timeline.selection.set": {"clip_ids": [" clip-one ", "clip-one"], "transition_id": None},
}


@pytest.mark.parametrize("command", tuple(WORKSPACE_COMMANDS))
def test_workspace_producer_and_desktop_event_use_the_same_contract(command: str) -> None:
    assert set(WORKSPACE_COMMANDS) == set(get_args(WorkspaceCommandName)) == set(_WORKSPACE_EXAMPLES)
    registry = WorkspaceRegistry()
    workspace = registry.attach(client_id="contract-test")
    identity = workspace["workspace_session_id"]
    registry.connect(identity, "contract-test")
    event = registry.command(identity, command, _WORKSPACE_EXAMPLES[command])
    accepted = WorkspaceCommandEvent.model_validate(event)
    assert accepted.command == command
    assert accepted.workspace_revision == 1
    if command == "timeline.selection.set":
        assert accepted.arguments == {"clip_ids": ["clip-one"], "transition_id": None}
    with pytest.raises(ValidationError):
        registry.command(identity, command, {**_WORKSPACE_EXAMPLES[command], "unexpected": True})
    assert registry.status(identity)["workspace_revision"] == 1


@pytest.mark.parametrize(("command", "arguments"), [
    ("playhead.seek", {"frame": True}),
    ("playhead.seek", {"frame": -1}),
    ("playback.play", {"frame": "12"}),
    ("workspace.mode.activate", {"mode": "not-a-mode"}),
    ("timeline.selection.set", {"clip_ids": [" "]}),
    ("timeline.selection.set", {"clip_ids": ["clip"], "transition_id": "transition"}),
])
def test_workspace_event_rejects_invalid_arguments(command: str, arguments: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        WorkspaceCommandEvent.model_validate({
            "workspace_session_id": "workspace-contract-test", "workspace_revision": 1,
            "project": None, "command": command, "arguments": arguments,
        })
