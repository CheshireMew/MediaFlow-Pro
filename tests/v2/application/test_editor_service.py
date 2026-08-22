from __future__ import annotations

import asyncio
import math
import sqlite3
import struct
import threading
import time
import wave
from contextlib import closing
from pathlib import Path

import psutil
import pytest
from aiohttp import ClientSession

import mediaflow.service.client as service_client_module
import mediaflow.service.desktop_application_proxy as desktop_application_module
import mediaflow.service.discovery as service_discovery_module
import mediaflow.service.remote_project as desktop_proxy_module
from mediaflow.application.asset_task_handlers import AssetTaskHandlers
from mediaflow.composition import EditorApplication
from mediaflow.desktop.controllers.controller_hub import EditorControllers
from mediaflow.domain.collaboration import ProjectChangeEvent
from mediaflow.domain.enums import TrackKind, TransitionKind
from mediaflow.domain.project import ProjectProfile
from mediaflow.domain.settings import DesktopSettings, ServiceSettings
from mediaflow.domain.subtitles import SubtitleSegment
from mediaflow.infrastructure.project_lock import ProcessFileLock
from mediaflow.infrastructure.project_operation_repository import (
    ProjectOperationRepository,
)
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.runtime_context import RuntimeContext
from mediaflow.service.client import (
    EditorServiceClient,
    EditorServiceRpcError,
    call_sync,
    execute_sync,
    shutdown_sync_service,
)
from mediaflow.service.codec import decode_transport, encode_transport
from mediaflow.service.desktop_application_proxy import DesktopEditorApplication
from mediaflow.service.discovery import ServiceDiscovery, ServicePaths
from mediaflow.service.server import PRIVATE_PORT_END, PRIVATE_PORT_START, EditorServiceServer


def _paths(root: Path) -> ServicePaths:
    return ServicePaths(
        root=root,
        lock=root / "service.lock",
        discovery=root / "discovery.json",
        log=root / "service.log",
    )


def _request(
    operation: str,
    *,
    project: str | None = None,
    arguments: dict | None = None,
    request_id: str,
    base_revision: int | None = None,
) -> dict:
    return {
        "protocol": "mediaflow-editor",
        "version": 4,
        "operation": operation,
        "project": project,
        "arguments": arguments or {},
        "request_id": request_id,
        "base_revision": base_revision,
        "actor": {"kind": "agent", "id": "service-test", "name": "Service Test"},
        "client_id": "pytest-service-client",
    }


def test_desktop_startup_bootstraps_runtime_settings_and_workspace_in_one_rpc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict]] = []
    runtime_descriptor = RuntimeContext.discover().desktop_descriptor()
    service_settings = ServiceSettings()
    runtime_tool_status = {"operation": None, "revision": 7}

    def bootstrap_call(method: str, params: dict, **_kwargs):
        calls.append((method, params))
        return {
            "runtime_descriptor": runtime_descriptor.model_dump(mode="json"),
            "settings": encode_transport(service_settings),
            "runtime_tool_status": encode_transport(runtime_tool_status),
            "workspace": {"workspace_session_id": "workspace-one-rpc"},
        }

    monkeypatch.setattr(desktop_application_module, "call_sync", bootstrap_call)
    monkeypatch.setattr(
        desktop_application_module.DesktopSettingsRepository,
        "load",
        lambda _repository: DesktopSettings(),
    )

    application = DesktopEditorApplication()

    assert len(calls) == 1
    assert calls[0][0] == "desktop.bootstrap"
    assert calls[0][1]["client_id"].startswith("desktop-")
    assert application.workspace_session_id == "workspace-one-rpc"
    assert application.service_settings == service_settings
    assert application.initial_runtime_tool_status == runtime_tool_status


@pytest.mark.asyncio
async def test_direct_task_rpc_uses_the_canonical_automation_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MEDIAFLOW_PROJECT_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("MEDIAFLOW_MEDIA_ROOT", str(tmp_path / "media"))
    server = EditorServiceServer(paths=_paths(tmp_path / "service-state"))
    discovery = await server.start()
    client = EditorServiceClient(discovery)
    try:
        created = await client.execute(
            _request(
                "project.create",
                request_id="create-direct-task-rpc-project",
                arguments={
                    "name": "Direct Task RPC",
                    "directory_name": "direct-task-rpc",
                    "profile": ProjectProfile().model_dump(
                        mode="json",
                        exclude_computed_fields=True,
                    ),
                },
            )
        )

        response = await client.call(
            "task.list",
            {
                "project": created["result"]["path"],
                "client_id": "direct-task-rpc-test",
            },
        )

        assert response["result"] == {"tasks": []}
        assert response["project_revision"] == 0
    finally:
        await server.stop()


def test_sync_shutdown_waits_until_the_resident_service_process_exits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service_root = tmp_path / "resident-service"
    monkeypatch.setenv("MEDIAFLOW_SERVICE_STATE_DIR", str(service_root))

    status = call_sync("service.status")
    discovery = ServiceDiscovery.read(service_root / "discovery.json")
    assert status["pid"] == discovery.pid
    assert discovery.belongs_to_live_process() is True

    shutdown_sync_service()

    assert discovery.belongs_to_live_process() is False
    assert not (service_root / "discovery.json").exists()


def test_service_discovery_does_not_treat_a_zombie_as_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started_at = time.time() - 1
    discovery = ServiceDiscovery(
        pid=1234,
        process_started_at=started_at,
        started_at=started_at,
        port=49_152,
        token="x" * 32,
    )

    class ZombieProcess:
        def is_running(self) -> bool:
            return True

        def status(self) -> str:
            return "zombie"

        def create_time(self) -> float:
            return started_at

    monkeypatch.setattr(
        service_discovery_module.psutil,
        "Process",
        lambda _pid: ZombieProcess(),
    )

    assert discovery.belongs_to_live_process() is False


def test_started_service_process_is_reaped_after_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CompletedProcess:
        def poll(self) -> int:
            return 0

    process = CompletedProcess()
    monkeypatch.setitem(service_client_module._started_processes, 1234, process)

    assert service_client_module._started_process_exit(1234) == 0
    assert 1234 not in service_client_module._started_processes


def test_force_shutdown_kills_only_the_exact_stalled_service_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    discovery = ServiceDiscovery(
        pid=1234,
        process_started_at=42.0,
        started_at=43.0,
        port=64000,
        token="x" * 32,
    )

    class StalledProcess:
        pid = 1234

        def __init__(self) -> None:
            self.terminated = False
            self.killed = False

        def is_running(self) -> bool:
            return True

        def status(self) -> str:
            return psutil.STATUS_RUNNING

        def create_time(self) -> float:
            return 42.0

        def terminate(self) -> None:
            self.terminated = True

        def kill(self) -> None:
            self.killed = True

        def wait(self, timeout: float) -> int:
            assert timeout == service_client_module.SERVICE_PROCESS_TERMINATE_TIMEOUT_SECONDS
            if not self.killed:
                raise psutil.TimeoutExpired(timeout, pid=self.pid)
            return 1

    process = StalledProcess()
    monkeypatch.setattr(service_client_module.psutil, "Process", lambda _pid: process)

    service_client_module._terminate_stalled_service(discovery)

    assert process.terminated is True
    assert process.killed is True


def test_force_shutdown_never_targets_a_reused_process_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    discovery = ServiceDiscovery(
        pid=1234,
        process_started_at=42.0,
        started_at=43.0,
        port=64000,
        token="x" * 32,
    )

    class ReusedProcess:
        pid = 1234
        terminated = False

        def is_running(self) -> bool:
            return True

        def status(self) -> str:
            return psutil.STATUS_RUNNING

        def create_time(self) -> float:
            return 84.0

        def terminate(self) -> None:
            self.terminated = True

    process = ReusedProcess()
    monkeypatch.setattr(service_client_module.psutil, "Process", lambda _pid: process)

    service_client_module._terminate_stalled_service(discovery)

    assert process.terminated is False


@pytest.mark.asyncio
async def test_websocket_subscription_never_crosses_project_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MEDIAFLOW_PROJECT_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("MEDIAFLOW_MEDIA_ROOT", str(tmp_path / "media"))
    server = EditorServiceServer(paths=_paths(tmp_path / "service-state"))
    discovery = await server.start()
    client = EditorServiceClient(discovery)
    profile = ProjectProfile().model_dump(
        mode="json",
        exclude_computed_fields=True,
    )
    try:
        first = await client.execute(
            _request(
                "project.create",
                request_id="create-filter-first",
                arguments={
                    "name": "Filter First",
                    "directory_name": "filter-first",
                    "profile": profile,
                },
            )
        )
        second = await client.execute(
            _request(
                "project.create",
                request_id="create-filter-second",
                arguments={
                    "name": "Filter Second",
                    "directory_name": "filter-second",
                    "profile": profile,
                },
            )
        )
        first_path = Path(first["result"]["path"])
        second_path = Path(second["result"]["path"])
        headers = {"Authorization": f"Bearer {discovery.token}"}
        async with ClientSession(headers=headers) as session:
            async with session.ws_connect(discovery.websocket_url) as websocket:
                assert (await websocket.receive_json())["type"] == "service.ready"
                await websocket.send_json(
                    {
                        "type": "project.subscribe",
                        "project": str(first_path),
                        "project_cursor": 1,
                        "task_cursor": 0,
                    }
                )
                subscribed = await websocket.receive_json()
                assert subscribed["payload"]["project_id"] == first["result"]["project"]["id"]

                await client.execute(
                    _request(
                        "timeline.track.add",
                        project=str(second_path),
                        request_id="change-filter-second",
                        base_revision=0,
                        arguments={"kind": "video", "name": "Must stay private"},
                    )
                )
                await client.execute(
                    _request(
                        "timeline.track.add",
                        project=str(first_path),
                        request_id="change-filter-first",
                        base_revision=0,
                        arguments={"kind": "video", "name": "Expected event"},
                    )
                )
                event = await websocket.receive_json(timeout=5)
                assert event["type"] == "project.changed"
                assert event["payload"]["project_id"] == first["result"]["project"]["id"]
                assert event["payload"]["request_id"] == "change-filter-first"
                with pytest.raises(TimeoutError):
                    await websocket.receive_json(timeout=0.1)
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_websocket_publishes_real_project_conflict_to_matching_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MEDIAFLOW_PROJECT_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("MEDIAFLOW_MEDIA_ROOT", str(tmp_path / "media"))
    server = EditorServiceServer(paths=_paths(tmp_path / "service-state"))
    discovery = await server.start()
    client = EditorServiceClient(discovery)
    try:
        created = await client.execute(
            _request(
                "project.create",
                request_id="create-conflict-event-project",
                arguments={
                    "name": "Conflict Event Project",
                    "directory_name": "conflict-event-project",
                    "profile": ProjectProfile().model_dump(
                        mode="json",
                        exclude_computed_fields=True,
                    ),
                },
            )
        )
        project = Path(created["result"]["path"])
        project_id = created["result"]["project"]["id"]
        headers = {"Authorization": f"Bearer {discovery.token}"}
        async with ClientSession(headers=headers) as session:
            async with session.ws_connect(discovery.websocket_url) as websocket:
                assert (await websocket.receive_json())["type"] == "service.ready"
                await websocket.send_json(
                    {
                        "type": "project.subscribe",
                        "project": str(project),
                        "project_cursor": 1,
                        "task_cursor": 0,
                    }
                )
                assert (await websocket.receive_json())["type"] == "project.subscribed"
                audio = await client.execute(
                    _request(
                        "audio.inspect",
                        project=str(project),
                        request_id="conflict-event-audio",
                    )
                )
                dialogue_bus = next(item for item in audio["result"]["buses"] if item["name"] == "对白")
                await client.execute(
                    _request(
                        "audio.bus.update",
                        project=str(project),
                        request_id="conflict-event-first-write",
                        base_revision=0,
                        arguments={
                            "bus_id": dialogue_bus["id"],
                            "changes": {"gain_db": -1.0},
                        },
                    )
                )
                assert (await websocket.receive_json(timeout=5))["type"] == "project.changed"

                with pytest.raises(EditorServiceRpcError) as conflict:
                    await client.execute(
                        _request(
                            "audio.bus.update",
                            project=str(project),
                            request_id="conflict-event-stale-write",
                            base_revision=0,
                            arguments={
                                "bus_id": dialogue_bus["id"],
                                "changes": {"gain_db": -2.0},
                            },
                        )
                    )
                assert conflict.value.code == -32009
                event = await websocket.receive_json(timeout=5)
                assert event["type"] == "project.conflict"
                assert event["payload"]["project_id"] == project_id
                assert event["payload"]["project_path"] == str(project)
                assert event["payload"]["request_id"] == "conflict-event-stale-write"
                assert event["payload"]["operation"] == "audio.bus.update"
                assert event["payload"]["expected_revision"] == 0
                assert event["payload"]["current_revision"] == 1
                assert event["payload"]["conflicting_events"]
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_service_subscription_receives_real_runtime_tool_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MEDIAFLOW_PROJECT_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("MEDIAFLOW_MEDIA_ROOT", str(tmp_path / "media"))
    server = EditorServiceServer(paths=_paths(tmp_path / "service-state"))
    discovery = await server.start()
    client = EditorServiceClient(discovery)
    try:
        headers = {"Authorization": f"Bearer {discovery.token}"}
        async with ClientSession(headers=headers) as session:
            async with session.ws_connect(discovery.websocket_url) as websocket:
                assert (await websocket.receive_json())["type"] == "service.ready"
                await websocket.send_json({"type": "service.subscribe"})
                subscribed = await websocket.receive_json()
                assert subscribed["type"] == "service.subscribed"

                result = await client.call(
                    "desktop.application.call",
                    {
                        "command": "run_runtime_tool",
                        "args": encode_transport(["inspect"]),
                        "kwargs": encode_transport({"arguments": {}}),
                    },
                )
                assert isinstance(decode_transport(result), dict)
                observed: list[dict] = []
                while not observed or observed[-1]["payload"]["state"] not in {
                    "completed",
                    "failed",
                    "cancelled",
                }:
                    observed.append(await websocket.receive_json(timeout=10))
                runtime_events = [item for item in observed if item["type"] == "runtime.changed"]
                assert [item["payload"]["state"] for item in runtime_events] == [
                    "running",
                    "completed",
                ]
                assert all(item["payload"]["operation"] == "inspect" for item in runtime_events)
                assert [item["payload"]["runtime_revision"] for item in runtime_events] == [1, 2]
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_service_subscription_receives_stopping_before_socket_closes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MEDIAFLOW_PROJECT_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("MEDIAFLOW_MEDIA_ROOT", str(tmp_path / "media"))
    server = EditorServiceServer(paths=_paths(tmp_path / "service-state"))
    discovery = await server.start()
    headers = {"Authorization": f"Bearer {discovery.token}"}
    stopped = False
    try:
        async with ClientSession(headers=headers) as session:
            async with session.ws_connect(discovery.websocket_url) as websocket:
                assert (await websocket.receive_json())["type"] == "service.ready"
                await websocket.send_json({"type": "service.subscribe"})
                assert (await websocket.receive_json())["type"] == "service.subscribed"
                stopping = asyncio.create_task(server.stop())
                stopping_started = time.perf_counter()
                event = await websocket.receive_json(timeout=5)
                assert event["type"] == "service.stopping"
                await websocket.close()
                await stopping
                assert time.perf_counter() - stopping_started < 2
                stopped = True
    finally:
        if not stopped:
            await server.stop()


@pytest.mark.asyncio
async def test_service_shutdown_bounds_uncooperative_websocket(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MEDIAFLOW_PROJECT_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("MEDIAFLOW_MEDIA_ROOT", str(tmp_path / "media"))
    server = EditorServiceServer(paths=_paths(tmp_path / "service-state"))
    discovery = await server.start()
    headers = {"Authorization": f"Bearer {discovery.token}"}
    stopped = False
    try:
        async with ClientSession(headers=headers) as session:
            async with session.ws_connect(discovery.websocket_url) as websocket:
                assert (await websocket.receive_json())["type"] == "service.ready"
                stopping_started = time.perf_counter()
                await server.stop()
                assert time.perf_counter() - stopping_started < 3.5
                stopped = True
    finally:
        if not stopped:
            await server.stop()


@pytest.mark.asyncio
async def test_websocket_stream_delivers_real_task_and_committed_project_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MEDIAFLOW_PROJECT_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("MEDIAFLOW_MEDIA_ROOT", str(tmp_path / "media"))
    server = EditorServiceServer(paths=_paths(tmp_path / "service-state"))
    discovery = await server.start()
    client = EditorServiceClient(discovery)
    source = tmp_path / "websocket-import.wav"
    _write_wave(source)
    try:
        created = await client.execute(
            _request(
                "project.create",
                request_id="create-websocket-task-project",
                arguments={
                    "name": "WebSocket Task Project",
                    "directory_name": "websocket-task-project",
                    "profile": ProjectProfile().model_dump(
                        mode="json",
                        exclude_computed_fields=True,
                    ),
                },
            )
        )
        project = Path(created["result"]["path"])
        desktop_client_id = "websocket-task-desktop"
        workspace = await client.call(
            "workspace.attach",
            {"client_id": desktop_client_id},
        )
        headers = {"Authorization": f"Bearer {discovery.token}"}
        async with ClientSession(headers=headers) as session:
            async with session.ws_connect(discovery.websocket_url) as websocket:
                assert (await websocket.receive_json())["type"] == "service.ready"
                await websocket.send_json(
                    {
                        "type": "project.subscribe",
                        "project": str(project),
                        "project_cursor": created["event"]["cursor"],
                        "task_cursor": 0,
                        "workspace_session_id": workspace["workspace_session_id"],
                        "client_id": desktop_client_id,
                    }
                )
                subscribed = await websocket.receive_json(timeout=5)
                assert subscribed["type"] == "project.subscribed"

                receipt = await client.execute(
                    _request(
                        "asset.import",
                        project=str(project),
                        request_id="websocket-real-import",
                        base_revision=0,
                        arguments={"source": str(source)},
                    )
                )
                task_id = receipt["result"]["task"]["id"]
                awaited = await client.execute(
                    _request(
                        "task.wait",
                        project=str(project),
                        request_id="wait-websocket-real-import",
                        arguments={"task_id": task_id, "timeout": 30},
                    )
                )
                assert awaited["result"]["task"]["status"] == "completed"

                observed: list[dict] = []
                deadline = asyncio.get_running_loop().time() + 5
                while not any(
                    item["type"] == "task.changed"
                    and item["payload"]["task_id"] == task_id
                    and item["payload"]["payload"]["status"] == "completed"
                    for item in observed
                ):
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        raise AssertionError(f"WebSocket missed terminal task event: {observed}")
                    observed.append(await websocket.receive_json(timeout=remaining))

        assert any(item["type"] == "project.changed" for item in observed)
        with closing(sqlite3.connect(project / "project.mfp")) as connection:
            task_events = connection.execute(
                """SELECT base_revision, project_revision, operation
                   FROM project_event
                   WHERE request_id LIKE ?
                   ORDER BY cursor""",
                (f"task-{task_id}:%",),
            ).fetchall()
        assert task_events == [(0, 1, "task.import_asset")]
        assert awaited["project_revision"] == 1
    finally:
        await server.stop()


def _write_wave(path: Path) -> None:
    sample_rate = 48_000
    frames = bytearray()
    for index in range(sample_rate // 5):
        value = int(math.sin(2 * math.pi * 440 * index / sample_rate) * 8_000)
        frames.extend(struct.pack("<h", value))
    with wave.open(str(path), "wb") as destination:
        destination.setnchannels(1)
        destination.setsampwidth(2)
        destination.setframerate(sample_rate)
        destination.writeframes(frames)


def test_service_transport_preserves_string_backed_enum_types() -> None:
    encoded = encode_transport(TransitionKind.FADE)

    assert encoded["$mediaflow_type"] == "enum"
    assert decode_transport(encoded) is TransitionKind.FADE


def test_service_transport_batches_homogeneous_domain_models() -> None:
    segments = [
        SubtitleSegment(
            document_id="document",
            start_frame=index,
            end_frame=index + 1,
            text=f"Segment {index}",
        )
        for index in range(3)
    ]

    encoded = encode_transport(segments)

    assert encoded["$mediaflow_type"] == "model_list"
    assert decode_transport(encoded) == segments


def test_service_transport_rejects_unregistered_schema_ids() -> None:
    with pytest.raises(ValueError, match="Unknown Editor Service transport schema"):
        decode_transport(
            {
                "$mediaflow_type": "model",
                "schema": "python.import.is.not.a.contract",
                "value": {},
            }
        )


def test_project_event_cursor_reports_latest_durable_event(tmp_path: Path) -> None:
    application = EditorApplication()
    project = application.create_project(tmp_path, "Cursor")
    try:
        assert project.project_event_cursor() == 0
        project.timeline(project.get_project().main_sequence_id).add_track(
            TrackKind.VIDEO,
            name="Video",
        )
        assert project.project_event_cursor() == 1
    finally:
        project.close()


@pytest.mark.asyncio
async def test_desktop_release_cannot_close_session_between_lookup_and_project_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = EditorServiceServer(paths=_paths(tmp_path / "service-state"))
    await server.start()
    try:
        operations = server._operations
        assert operations is not None
        registry = operations.registry
        project_root = tmp_path / "lifetime-boundary"
        await asyncio.to_thread(
            registry.create_desktop_project,
            project_root,
            "Lifetime Boundary",
            ProjectProfile(),
            True,
            "desktop-lifetime-test",
        )
        lookup_finished = threading.Event()
        allow_lookup_to_return = threading.Event()
        original_open_session = registry.open_session

        def paused_open_session(*args, **kwargs):
            session = original_open_session(*args, **kwargs)
            lookup_finished.set()
            if not allow_lookup_to_return.wait(5):
                raise TimeoutError("Test did not release the session lookup")
            return session

        monkeypatch.setattr(registry, "open_session", paused_open_session)
        read = asyncio.create_task(
            asyncio.to_thread(
                operations.desktop.execute_desktop_command,
                path=project_root,
                target="project",
                sequence_id="",
                command="get_project",
                args_value=[],
                kwargs_value={},
                base_revision=0,
                request_id="desktop-lifetime-read",
                actor_value={"kind": "human", "id": "desktop-lifetime-test"},
            )
        )
        assert await asyncio.to_thread(lookup_finished.wait, 3)
        release = asyncio.create_task(
            asyncio.to_thread(
                registry.release_desktop_project,
                project_root,
                "desktop-lifetime-test",
                5.0,
            )
        )
        await asyncio.sleep(0.1)
        assert not release.done()

        allow_lookup_to_return.set()
        response = await asyncio.wait_for(read, timeout=5)
        await asyncio.wait_for(release, timeout=5)

        assert decode_transport(response["value"]).name == "Lifetime Boundary"
        assert not registry.has_session(project_root)
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_service_owns_user_lock_before_application_construction(tmp_path: Path) -> None:
    paths = _paths(tmp_path / "service-state")
    calls: list[str] = []

    class FakeApplication:
        pass

    def factory():
        probe = ProcessFileLock(paths.lock)
        assert probe.acquire() is False
        calls.append("constructed-after-lock")
        return FakeApplication()

    first = EditorServiceServer(paths=paths, application_factory=factory)
    discovery = await first.start()
    try:
        assert PRIVATE_PORT_START <= discovery.port <= PRIVATE_PORT_END
        assert discovery.schema_version == 2
        assert discovery.started_at >= discovery.process_started_at
        assert calls == ["constructed-after-lock"]
        assert ServiceDiscovery.read(paths.discovery) == discovery
        second_calls: list[str] = []
        second = EditorServiceServer(
            paths=paths,
            application_factory=lambda: second_calls.append("constructed"),
        )
        with pytest.raises(RuntimeError, match="already owns"):
            await second.start()
        assert second_calls == []
    finally:
        await first.stop()


@pytest.mark.asyncio
async def test_real_service_commits_event_then_pushes_and_replays_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "projects"
    media_root = tmp_path / "media"
    monkeypatch.setenv("MEDIAFLOW_PROJECT_ROOT", str(project_root))
    monkeypatch.setenv("MEDIAFLOW_MEDIA_ROOT", str(media_root))
    server = EditorServiceServer(paths=_paths(tmp_path / "service-state"))
    discovery = await server.start()
    client = EditorServiceClient(discovery)
    try:
        created = await client.execute(
            _request(
                "project.create",
                request_id="create-service-project",
                arguments={
                    "name": "Service Project",
                    "directory_name": "service-project",
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
            )
        )
        project = Path(created["result"]["path"])
        assert created["project_revision"] == 0
        assert created["event"]["operation"] == "project.create"
        with pytest.raises(EditorServiceRpcError) as unnecessary_upgrade:
            await client.execute(
                _request(
                    "project.upgrade",
                    project=str(project),
                    request_id="upgrade-current-service-project",
                    base_revision=0,
                )
            )
        assert unnecessary_upgrade.value.code == -32602
        assert "current schema" in str(unnecessary_upgrade.value)
        sequence_id = created["result"]["project"]["main_sequence_id"]

        headers = {"Authorization": f"Bearer {discovery.token}"}
        async with ClientSession(headers=headers) as session:
            async with session.ws_connect(discovery.websocket_url) as websocket:
                ready = await websocket.receive_json()
                assert ready["type"] == "service.ready"
                await websocket.send_json(
                    {
                        "type": "project.subscribe",
                        "project": str(project),
                        "project_cursor": 1,
                        "task_cursor": 0,
                    }
                )
                subscribed = await websocket.receive_json()
                assert subscribed["type"] == "project.subscribed"

                changed = await client.execute(
                    _request(
                        "timeline.track.add",
                        project=str(project),
                        request_id="add-service-track",
                        base_revision=0,
                        arguments={"kind": "video", "name": "Agent video"},
                    )
                )
                pushed = await websocket.receive_json(timeout=5)

        assert changed["project_revision"] == 1
        assert changed["event"]["cursor"] == 2
        assert changed["event"]["operation"] == "timeline.track.add"
        track_id = changed["result"]["track"]["id"]
        assert changed["event"]["write_set"] == [
            f"/sequences/{sequence_id}/tracks/{track_id}",
            f"/sequences/{sequence_id}/tracks/order",
        ]
        assert changed["event"]["changes"][0]["action"] == "create"
        assert changed["event"]["changes"][0]["value"]["id"] == track_id
        assert pushed["type"] == "project.changed"
        assert pushed["payload"]["project_revision"] == 1
        assert pushed["payload"]["operation_result"] == changed["result"]

        replay = await client.call(
            "project.events",
            {"project": str(project), "after_cursor": 1},
        )
        assert [item["operation"] for item in replay] == ["timeline.track.add"]
        with closing(sqlite3.connect(project / "project.mfp")) as connection:
            stored = connection.execute(
                "SELECT project_revision, operation FROM project_event ORDER BY cursor"
            ).fetchall()
        assert stored == [(0, "project.create"), (1, "timeline.track.add")]

        audio = await client.execute(
            _request(
                "audio.inspect",
                project=str(project),
                request_id="inspect-audio",
                arguments={"sequence_id": sequence_id},
            )
        )
        dialogue_bus = next(item for item in audio["result"]["buses"] if item["name"] == "对白")
        rebased = await client.execute(
            _request(
                "audio.bus.update",
                project=str(project),
                request_id="rebase-disjoint-audio",
                base_revision=0,
                arguments={
                    "bus_id": dialogue_bus["id"],
                    "changes": {"gain_db": -2.0},
                },
            )
        )
        assert rebased["rebased_from"] == 0
        assert rebased["project_revision"] == 2
        assert rebased["result"]["bus"]["gain_db"] == -2.0

        stale_track = await client.execute(
            _request(
                "timeline.track.add",
                project=str(project),
                request_id="stale-service-track",
                base_revision=0,
                arguments={"kind": "audio", "name": "Stale audio"},
            )
        )
        assert stale_track["rebased_from"] == 0
        assert stale_track["project_revision"] == 3
        assert stale_track["result"]["track"]["name"] == "Stale audio"

        batch_requests = [
            _request(
                "timeline.track.add",
                project=str(project),
                request_id=f"batch-track-{kind}",
                base_revision=2,
                arguments={
                    "sequence_id": sequence_id,
                    "kind": kind,
                    "name": name,
                },
            )
            for kind, name in (("video", "Batch video"), ("audio", "Batch audio"))
        ]
        batch = await client.call(
            "operation.execute_batch",
            {
                "batch_id": "agent-batch-1",
                "label": "AI adds paired tracks",
                "requests": batch_requests,
            },
        )
        assert batch["project_revision"] == 4
        assert batch["event"]["operation"] == "operation.execute_batch"
        assert batch["event"]["undo_group_id"] == "agent-batch-1"
        assert [item["request_id"] for item in batch["results"]] == [
            "batch-track-video",
            "batch-track-audio",
        ]
        history = await client.call("history.list", {"project": str(project)})
        assert history["can_undo"] is True
        assert history["can_redo"] is False
        assert history["items"][-1]["id"] == "agent-batch-1"
        summary = await client.call(
            "history.list",
            {"project": str(project), "include_items": False},
        )
        assert summary == {
            "project_revision": history["project_revision"],
            "items": [],
            "can_undo": True,
            "can_redo": False,
        }

        undone = await client.call(
            "history.undo",
            {
                "project": str(project),
                "request_id": "undo-agent-batch",
                "base_revision": 4,
                "actor": batch_requests[0]["actor"],
                "undo_group_id": "agent-batch-1",
            },
        )
        assert undone["project_revision"] == 5
        timeline = await client.execute(
            _request(
                "timeline.get",
                project=str(project),
                request_id="get-timeline-after-undo",
                arguments={"sequence_id": sequence_id},
            )
        )
        track_names = {item["name"] for item in timeline["result"]["timeline"]["tracks"]}
        assert "Batch video" not in track_names
        assert "Batch audio" not in track_names

        batch_retry = await client.call(
            "operation.execute_batch",
            {
                "batch_id": "agent-batch-1",
                "label": "AI adds paired tracks",
                "requests": batch_requests,
            },
        )
        assert batch_retry["project_revision"] == 5
        assert batch_retry["event"]["cursor"] == batch["event"]["cursor"]
        assert batch_retry["event"]["operation"] == "operation.execute_batch"

        exact_retry = await client.execute(
            _request(
                "audio.bus.update",
                project=str(project),
                request_id="rebase-disjoint-audio",
                base_revision=0,
                arguments={
                    "bus_id": dialogue_bus["id"],
                    "changes": {"gain_db": -2.0},
                },
            )
        )
        assert exact_retry["project_revision"] == 5
        assert exact_retry["result"] == rebased["result"]
        with closing(sqlite3.connect(project / "project.mfp")) as connection:
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM project_event WHERE request_id=?",
                    ("rebase-disjoint-audio",),
                ).fetchone()[0]
                == 1
            )
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM project_event WHERE undo_group_id=?",
                    ("agent-batch-1",),
                ).fetchone()[0]
                == 2
            )
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM undo_group WHERE id=?",
                    ("agent-batch-1",),
                ).fetchone()[0]
                == 1
            )
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_history_survives_service_restart_and_redo_restores_the_edit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MEDIAFLOW_PROJECT_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("MEDIAFLOW_MEDIA_ROOT", str(tmp_path / "media"))
    service_paths = _paths(tmp_path / "service-state")
    server = EditorServiceServer(paths=service_paths)
    discovery = await server.start()
    client = EditorServiceClient(discovery)
    created = await client.execute(
        _request(
            "project.create",
            request_id="create-restart-history-project",
            arguments={
                "name": "Restart History",
                "directory_name": "restart-history",
                "profile": ProjectProfile().model_dump(
                    mode="json",
                    exclude_computed_fields=True,
                ),
            },
        )
    )
    project = Path(created["result"]["path"])
    sequence_id = created["result"]["project"]["main_sequence_id"]
    changed = await client.execute(
        _request(
            "timeline.track.add",
            project=str(project),
            request_id="restart-history-track",
            base_revision=0,
            arguments={
                "sequence_id": sequence_id,
                "kind": "video",
                "name": "Restarted track",
            },
        )
    )
    assert changed["project_revision"] == 1
    await server.stop()

    restarted = EditorServiceServer(paths=service_paths)
    restarted_discovery = await restarted.start()
    restarted_client = EditorServiceClient(restarted_discovery)
    try:
        history = await restarted_client.call(
            "history.list",
            {"project": str(project)},
        )
        assert [item["id"] for item in history["items"]] == ["restart-history-track"]
        undone = await restarted_client.call(
            "history.undo",
            {
                "project": str(project),
                "request_id": "restart-history-undo",
                "base_revision": 1,
                "actor": _request(
                    "timeline.get",
                    request_id="actor-template",
                )["actor"],
            },
        )
        assert undone["project_revision"] == 2
        assert undone["result"]["can_redo"] is True
        after_undo = await restarted_client.execute(
            _request(
                "timeline.get",
                project=str(project),
                request_id="restart-history-after-undo",
                arguments={"sequence_id": sequence_id},
            )
        )
        assert all(track["name"] != "Restarted track" for track in after_undo["result"]["timeline"]["tracks"])

        redone = await restarted_client.call(
            "history.redo",
            {
                "project": str(project),
                "request_id": "restart-history-redo",
                "base_revision": 2,
                "actor": _request(
                    "timeline.get",
                    request_id="actor-template",
                )["actor"],
            },
        )
        assert redone["project_revision"] == 3
        after_redo = await restarted_client.execute(
            _request(
                "timeline.get",
                project=str(project),
                request_id="restart-history-after-redo",
                arguments={"sequence_id": sequence_id},
            )
        )
        assert any(track["name"] == "Restarted track" for track in after_redo["result"]["timeline"]["tracks"])
    finally:
        await restarted.stop()


@pytest.mark.asyncio
async def test_history_undo_rejects_a_later_overlapping_edit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MEDIAFLOW_PROJECT_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("MEDIAFLOW_MEDIA_ROOT", str(tmp_path / "media"))
    server = EditorServiceServer(paths=_paths(tmp_path / "service-state"))
    discovery = await server.start()
    client = EditorServiceClient(discovery)
    try:
        created = await client.execute(
            _request(
                "project.create",
                request_id="create-history-conflict-project",
                arguments={
                    "name": "History Conflict",
                    "directory_name": "history-conflict",
                    "profile": ProjectProfile().model_dump(
                        mode="json",
                        exclude_computed_fields=True,
                    ),
                },
            )
        )
        project = Path(created["result"]["path"])
        sequence_id = created["result"]["project"]["main_sequence_id"]
        for revision, request_id, name in (
            (0, "history-conflict-first", "First track"),
            (1, "history-conflict-second", "Second track"),
        ):
            await client.execute(
                _request(
                    "timeline.track.add",
                    project=str(project),
                    request_id=request_id,
                    base_revision=revision,
                    arguments={
                        "sequence_id": sequence_id,
                        "kind": "video",
                        "name": name,
                    },
                )
            )
        with pytest.raises(EditorServiceRpcError) as conflict:
            await client.call(
                "history.undo",
                {
                    "project": str(project),
                    "request_id": "history-conflict-undo",
                    "base_revision": 2,
                    "actor": _request(
                        "timeline.get",
                        request_id="actor-template",
                    )["actor"],
                    "undo_group_id": "history-conflict-first",
                },
            )
        assert conflict.value.code == -32009
        assert conflict.value.data["conflicting_events"][0]["request_id"] == ("history-conflict-second")
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_task_request_releases_foreground_gate_but_keeps_real_writes_serialized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MEDIAFLOW_PROJECT_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("MEDIAFLOW_MEDIA_ROOT", str(tmp_path / "media"))
    entered = threading.Event()
    release = threading.Event()
    original_import = AssetTaskHandlers.import_asset

    def paused_import(self, context):
        entered.set()
        if not release.wait(10):
            raise TimeoutError("Test did not release the real import handler")
        return original_import(self, context)

    monkeypatch.setattr(AssetTaskHandlers, "import_asset", paused_import)
    server = EditorServiceServer(paths=_paths(tmp_path / "service-state"))
    discovery = await server.start()
    client = EditorServiceClient(discovery)
    source = tmp_path / "concurrent-tone.wav"
    _write_wave(source)
    pending_import: asyncio.Task | None = None
    pending_wait: asyncio.Task | None = None
    try:
        created = await client.execute(
            _request(
                "project.create",
                request_id="create-concurrent-project",
                arguments={
                    "name": "Concurrent Project",
                    "directory_name": "concurrent-project",
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
            )
        )
        project = Path(created["result"]["path"])
        sequence_id = created["result"]["project"]["main_sequence_id"]
        pending_import = asyncio.create_task(
            client.execute(
                _request(
                    "asset.import",
                    project=str(project),
                    request_id="concurrent-real-import",
                    base_revision=0,
                    arguments={"source": str(source)},
                )
            )
        )
        assert await asyncio.to_thread(entered.wait, 5)
        imported = await asyncio.wait_for(pending_import, timeout=3)
        pending_import = None
        imported_task_id = imported["result"]["task"]["id"]
        pending_wait = asyncio.create_task(
            client.execute(
                _request(
                    "task.wait",
                    project=str(project),
                    request_id="wait-concurrent-real-import",
                    arguments={"task_id": imported_task_id, "timeout": 30},
                )
            )
        )
        await asyncio.sleep(0.1)
        assert not pending_wait.done()

        foreground = await asyncio.wait_for(
            client.execute(
                _request(
                    "timeline.track.add",
                    project=str(project),
                    request_id="foreground-during-import",
                    base_revision=0,
                    arguments={
                        "sequence_id": sequence_id,
                        "kind": "video",
                        "name": "Visible during import",
                    },
                )
            ),
            timeout=3,
        )
        assert foreground["project_revision"] == 1
        release.set()
        waited = await asyncio.wait_for(pending_wait, timeout=30)
        pending_wait = None
        assert waited["result"]["task"]["status"] == "completed"
        inspected = await client.execute(
            _request(
                "project.inspect",
                project=str(project),
                request_id="inspect-concurrent-project",
            )
        )
        assert len(inspected["result"]["assets"]) == 1
        timeline = await client.execute(
            _request(
                "timeline.get",
                project=str(project),
                request_id="inspect-concurrent-timeline",
                arguments={"sequence_id": sequence_id},
            )
        )
        assert "Visible during import" in {
            track["name"] for track in timeline["result"]["timeline"]["tracks"]
        }
    finally:
        release.set()
        if pending_import is not None:
            await asyncio.gather(pending_import, return_exceptions=True)
        if pending_wait is not None:
            await asyncio.gather(pending_wait, return_exceptions=True)
        await server.stop()


@pytest.mark.asyncio
async def test_async_task_receipt_recovers_after_scheduling_persistence_fault(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MEDIAFLOW_PROJECT_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("MEDIAFLOW_MEDIA_ROOT", str(tmp_path / "media"))
    server = EditorServiceServer(paths=_paths(tmp_path / "service-state"))
    discovery = await server.start()
    client = EditorServiceClient(discovery)
    source = tmp_path / "recoverable-tone.wav"
    _write_wave(source)
    try:
        created = await client.execute(
            _request(
                "project.create",
                request_id="create-recovery-project",
                arguments={
                    "name": "Recovery Project",
                    "directory_name": "recovery-project",
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
            )
        )
        project = Path(created["result"]["path"])
        original_save = ProjectOperationRepository.save_result
        injected = False

        def fail_once(self, request_id, operation, input_hash, result):
            nonlocal injected
            if request_id == "recover-running-import" and not injected:
                injected = True
                raise RuntimeError("injected fault after real task result")
            return original_save(self, request_id, operation, input_hash, result)

        monkeypatch.setattr(ProjectOperationRepository, "save_result", fail_once)
        request = _request(
            "asset.import",
            project=str(project),
            request_id="recover-running-import",
            base_revision=0,
            arguments={"source": str(source)},
        )
        with pytest.raises(EditorServiceRpcError, match="injected fault"):
            await client.execute(request)

        deadline = time.monotonic() + 10
        while True:
            before_retry = await client.execute(
                _request(
                    "project.inspect",
                    project=str(project),
                    request_id="inspect-before-retry",
                )
            )
            if before_retry["result"]["assets"] and any(
                task["status"] == "completed" for task in before_retry["result"]["tasks"]
            ):
                break
            if time.monotonic() >= deadline:
                raise AssertionError("Service did not commit the asynchronous task result")
            await asyncio.sleep(0.05)
        imported_tasks = [
            task
            for task in before_retry["result"]["tasks"]
            if task.get("idempotency_key") == "automation:recover-running-import:asset.import"
        ]
        assert len(imported_tasks) == 1
        assert imported_tasks[0]["status"] == "completed"
        assert len(before_retry["result"]["assets"]) == 1
        with closing(sqlite3.connect(project / "project.mfp")) as connection:
            assert (
                connection.execute(
                    "SELECT state FROM automation_request WHERE request_id=?",
                    ("recover-running-import",),
                ).fetchone()[0]
                == "running"
            )
            assert connection.execute(
                "SELECT task_id FROM task_consumption WHERE task_id=?",
                (imported_tasks[0]["id"],),
            ).fetchone() == (imported_tasks[0]["id"],)

        monkeypatch.setattr(ProjectOperationRepository, "save_result", original_save)
        recovered = await client.execute(request)
        assert recovered["rebased_from"] == 0
        assert recovered["result"]["task"]["id"] == imported_tasks[0]["id"]
        assert recovered["result"]["task"]["status"] == "completed"
        with closing(sqlite3.connect(project / "project.mfp")) as connection:
            receipt = connection.execute(
                "SELECT state FROM automation_request WHERE request_id=?",
                ("recover-running-import",),
            ).fetchone()
        assert receipt == ("completed",)
    finally:
        await server.stop()


def test_desktop_and_agent_share_service_writer_and_live_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service_state = tmp_path / "service-state"
    monkeypatch.setenv("MEDIAFLOW_SERVICE_STATE_DIR", str(service_state))
    monkeypatch.setenv("MEDIAFLOW_PROJECT_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("MEDIAFLOW_MEDIA_ROOT", str(tmp_path / "media"))
    application = DesktopEditorApplication()
    project = application.create_project(tmp_path / "desktop-project", "Desktop Project")
    observed: list[ProjectChangeEvent] = []
    token = project.subscribe_project_events(observed.append, include_snapshot=False)
    try:
        document = project.get_project()
        timeline = project.timeline(document.main_sequence_id)
        human_track = timeline.add_track(TrackKind.VIDEO, "Human video")
        assert human_track.name == "Human video"
        human_revision = project.content_revision()
        time.sleep(0.1)
        assert observed == []

        changed = execute_sync(
            _request(
                "timeline.track.add",
                project=str(project.project_dir),
                request_id="agent-live-track",
                base_revision=human_revision,
                arguments={
                    "sequence_id": document.main_sequence_id,
                    "kind": "audio",
                    "name": "Agent audio",
                },
            )
        )
        deadline = time.monotonic() + 5
        while not any(event.request_id == "agent-live-track" for event in observed):
            if time.monotonic() >= deadline:
                raise AssertionError("Desktop did not receive the committed agent event")
            time.sleep(0.02)

        assert changed["project_revision"] == human_revision + 1
        assert {track.name for track in timeline.state.tracks} >= {
            "Human video",
            "Agent audio",
        }
        project.begin_draft(f"/sequences/{document.main_sequence_id}/tracks")
        second_agent = execute_sync(
            _request(
                "timeline.track.add",
                project=str(project.project_dir),
                request_id="agent-conflicting-track",
                base_revision=changed["project_revision"],
                arguments={
                    "sequence_id": document.main_sequence_id,
                    "kind": "video",
                    "name": "Agent conflict",
                },
            )
        )
        deadline = time.monotonic() + 5
        while project.known_content_revision < second_agent["project_revision"]:
            if time.monotonic() >= deadline:
                raise AssertionError("Desktop revision did not follow the agent event")
            time.sleep(0.02)
        rebased_local = timeline.add_track(TrackKind.AUDIO, "Human stale draft")
        assert rebased_local.name == "Human stale draft"
        assert {track.name for track in timeline.state.tracks} >= {
            "Agent conflict",
            "Human stale draft",
        }
        assert timeline.can_undo is True
        timeline.undo()
        assert "Human stale draft" not in {track.name for track in timeline.state.tracks}
        assert "Agent conflict" in {track.name for track in timeline.state.tracks}
        assert timeline.can_redo is True
        timeline.redo()
        assert "Human stale draft" in {track.name for track in timeline.state.tracks}
        with closing(sqlite3.connect(project.project_dir / "project.mfp")) as connection:
            actors = [
                row[0]
                for row in connection.execute(
                    "SELECT json_extract(actor_json, '$.kind') FROM project_event ORDER BY cursor"
                )
            ]
            operations = [
                row[0] for row in connection.execute("SELECT operation FROM project_event ORDER BY cursor")
            ]
        assert actors == ["human", "agent", "agent", "human", "human", "human"]
        assert operations[-2:] == ["history.undo", "history.redo"]
    finally:
        project.unsubscribe_project_events(token)
        project.close()
        call_sync("service.shutdown", start_if_needed=False)


def test_agent_event_updates_the_qml_bound_track_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    qapp,
) -> None:
    service_state = tmp_path / "service-state"
    monkeypatch.setenv("MEDIAFLOW_SERVICE_STATE_DIR", str(service_state))
    monkeypatch.setenv("MEDIAFLOW_PROJECT_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("MEDIAFLOW_MEDIA_ROOT", str(tmp_path / "media"))
    controllers = EditorControllers(application=DesktopEditorApplication())
    try:
        controllers.session.lifecycle.create_and_open(
            tmp_path,
            "QML Service Projection",
        )
        project = controllers.session.state.binding.current
        assert project is not None
        document = project.get_project()
        changed = execute_sync(
            _request(
                "timeline.track.add",
                project=str(project.project_dir),
                request_id="agent-qml-track",
                base_revision=project.content_revision(),
                arguments={
                    "sequence_id": document.main_sequence_id,
                    "kind": "video",
                    "name": "Agent visible in QML",
                },
            )
        )
        model = controllers.timeline_view.tracksModel
        roles = {bytes(name).decode("utf-8"): role for role, name in model.roleNames().items()}
        deadline = time.monotonic() + 5
        while True:
            qapp.processEvents()
            names = {model.data(model.index(row), roles["name"]) for row in range(model.rowCount())}
            if "Agent visible in QML" in names:
                break
            if time.monotonic() >= deadline:
                raise AssertionError("The QML-bound track model did not apply the agent event")
            time.sleep(0.02)
        assert project.known_content_revision == changed["project_revision"]
        assert controllers.timeline_view.tracksModel is controllers.session.models.tracks
    finally:
        controllers.shutdown()
        call_sync("service.shutdown", {"force": True}, start_if_needed=False)


def test_desktop_runtime_tool_call_crosses_the_real_service_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "MEDIAFLOW_SERVICE_STATE_DIR",
        str(tmp_path / "service-state"),
    )
    monkeypatch.setenv("MEDIAFLOW_PROJECT_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("MEDIAFLOW_MEDIA_ROOT", str(tmp_path / "media"))
    application = DesktopEditorApplication()
    progress = []
    try:
        result = application.run_runtime_tool("inspect", progress=progress.append)
        assert isinstance(result, dict)
        assert [item.message_code for item in progress] == ["runtime_service_operation"]
        status = call_sync("service.status", start_if_needed=False)
        assert status["active_runtime_operation"] is None
    finally:
        application.close_client_transport()
        call_sync("service.shutdown", {"force": True}, start_if_needed=False)


def test_desktop_session_refs_release_only_the_registered_last_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service_state = tmp_path / "service-state"
    monkeypatch.setenv("MEDIAFLOW_SERVICE_STATE_DIR", str(service_state))
    monkeypatch.setenv("MEDIAFLOW_PROJECT_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("MEDIAFLOW_MEDIA_ROOT", str(tmp_path / "media"))
    first_application = DesktopEditorApplication()
    second_application = DesktopEditorApplication()
    first = first_application.create_project(
        tmp_path / "shared-desktop-project",
        "Shared Desktop Project",
    )
    second = second_application.open_project(first.project_dir)
    try:
        call_sync(
            "project.close",
            {
                "project": str(first.project_dir),
                "client_id": "desktop-client-that-never-opened-this-project",
            },
        )
        document = first.get_project()
        first.timeline(document.main_sequence_id).add_track(
            TrackKind.VIDEO,
            "First client remains connected",
        )
        first_timeline = first.timeline(document.main_sequence_id)
        with monkeypatch.context() as isolated:
            isolated.setattr(
                desktop_proxy_module,
                "call_sync",
                lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    AssertionError("history cache performed an extra RPC")
                ),
            )
            assert first_timeline.can_undo is True
            assert first_timeline.can_redo is False

        first.close()
        second.timeline(document.main_sequence_id).add_track(
            TrackKind.AUDIO,
            "Second client remains connected",
        )
        names = {track.name for track in second.timeline(document.main_sequence_id).state.tracks}
        assert names >= {
            "First client remains connected",
            "Second client remains connected",
        }
    finally:
        first.close()
        second.close()
        with ProjectRepository.open(
            tmp_path / "shared-desktop-project",
            writable=True,
        ):
            pass
        call_sync("service.shutdown", start_if_needed=False)
