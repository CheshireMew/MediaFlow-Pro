from __future__ import annotations

import asyncio
import atexit
import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import psutil
from aiohttp import ClientConnectorError, ClientSession, ClientTimeout, TCPConnector

from .discovery import SERVICE_PROTOCOL, SERVICE_PROTOCOL_VERSION, ServiceDiscovery, ServicePaths
from .process_launcher import launch_editor_service


class EditorServiceUnavailable(RuntimeError):
    pass


class EditorServiceRpcError(RuntimeError):
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(message)
        self.code = code
        self.data = data


_started_processes: dict[int, subprocess.Popen[bytes]] = {}
_started_processes_lock = threading.Lock()
SERVICE_PROCESS_EXIT_TIMEOUT_SECONDS = 15.0
SERVICE_PROCESS_TERMINATE_TIMEOUT_SECONDS = 5.0


def _started_process_exit(pid: int) -> int | None:
    with _started_processes_lock:
        process = _started_processes.get(pid)
    if process is None:
        return None
    exit_code = process.poll()
    if exit_code is not None:
        with _started_processes_lock:
            if _started_processes.get(pid) is process:
                _started_processes.pop(pid, None)
    return exit_code


def _matches_service_process(discovery: ServiceDiscovery, process: psutil.Process) -> bool:
    try:
        return (
            process.pid == discovery.pid
            and process.is_running()
            and process.status() not in {psutil.STATUS_DEAD, psutil.STATUS_ZOMBIE}
            and abs(process.create_time() - discovery.process_started_at) < 0.01
        )
    except (psutil.Error, OSError):
        return False


def _terminate_stalled_service(discovery: ServiceDiscovery) -> None:
    """Finish a force shutdown without ever targeting a reused process id."""

    try:
        process = psutil.Process(discovery.pid)
    except (psutil.Error, OSError):
        return
    if not _matches_service_process(discovery, process):
        return
    try:
        process.terminate()
        process.wait(timeout=SERVICE_PROCESS_TERMINATE_TIMEOUT_SECONDS)
        return
    except psutil.NoSuchProcess:
        return
    except psutil.TimeoutExpired:
        pass
    except (psutil.AccessDenied, OSError) as error:
        raise EditorServiceUnavailable(
            f"Editor Service process {discovery.pid} completed graceful shutdown "
            "but could not be terminated"
        ) from error
    if not _matches_service_process(discovery, process):
        return
    try:
        process.kill()
        process.wait(timeout=SERVICE_PROCESS_TERMINATE_TIMEOUT_SECONDS)
    except psutil.NoSuchProcess:
        return
    except (psutil.AccessDenied, psutil.TimeoutExpired, OSError) as error:
        raise EditorServiceUnavailable(
            f"Editor Service process {discovery.pid} completed graceful shutdown "
            "but remained alive after termination"
        ) from error


class EditorServiceClient:
    def __init__(self, discovery: ServiceDiscovery):
        self.discovery = discovery

    @classmethod
    async def connect(
        cls,
        *,
        paths: ServicePaths | None = None,
        start_if_needed: bool = True,
        startup_timeout: float = 15.0,
        session: ClientSession | None = None,
    ) -> EditorServiceClient:
        selected = paths or ServicePaths.discover()
        deadline = time.monotonic() + startup_timeout
        started = False
        replacement_requested_pid: int | None = None
        last_error: EditorServiceUnavailable | None = None
        while True:
            discovery = cls._live_discovery(selected)
            if discovery is not None:
                client = cls(discovery)
                try:
                    hello = await client.call("system.hello", session=session)
                except EditorServiceUnavailable as error:
                    # A service that just acknowledged shutdown can remain a
                    # live process for a few scheduler turns. Wait for its lock
                    # and discovery ownership to expire before starting the
                    # replacement process.
                    last_error = error
                else:
                    if (
                        hello.get("protocol") == SERVICE_PROTOCOL
                        and hello.get("protocol_version") == SERVICE_PROTOCOL_VERSION
                        and int(hello.get("pid", 0)) == discovery.pid
                    ):
                        return client
                    last_error = EditorServiceUnavailable(
                        "Editor Service discovery does not match the live process"
                    )
                    if not start_if_needed:
                        raise last_error
                    if replacement_requested_pid != discovery.pid:
                        try:
                            await client.call("service.shutdown", session=session)
                        except EditorServiceUnavailable:
                            pass
                        replacement_requested_pid = discovery.pid
            elif start_if_needed and not started:
                cls._start_service_process(selected)
                started = True
            elif not start_if_needed:
                raise last_error or EditorServiceUnavailable("MediaFlow Editor Service is not running")
            if time.monotonic() >= deadline:
                raise last_error or EditorServiceUnavailable(
                    "MediaFlow Editor Service did not become ready before the startup deadline"
                )
            await asyncio.sleep(0.05)

    async def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        session: ClientSession | None = None,
    ) -> Any:
        request = {
            "jsonrpc": "2.0",
            "id": f"client-{time.time_ns()}",
            "method": method,
            "params": params or {},
        }
        timeout = ClientTimeout(total=None, connect=5, sock_connect=5, sock_read=None)
        try:
            if session is None:
                async with ClientSession(timeout=timeout) as owned_session:
                    payload = await self._post(owned_session, request)
            else:
                payload = await self._post(session, request)
        except (ClientConnectorError, OSError) as connection_error:
            exit_code = _started_process_exit(self.discovery.pid)
            detail = str(connection_error)
            if exit_code is not None:
                detail = f"{detail}; Editor Service process {self.discovery.pid} exited with code {exit_code}"
            raise EditorServiceUnavailable(detail) from connection_error
        if not isinstance(payload, dict):
            raise EditorServiceUnavailable("Editor Service returned a non-object response")
        payload_error = payload.get("error")
        if isinstance(payload_error, dict):
            raise EditorServiceRpcError(
                int(payload_error.get("code", -32603)),
                str(payload_error.get("message", "Editor Service request failed")),
                payload_error.get("data"),
            )
        result = payload.get("result")
        return result

    async def _post(
        self,
        session: ClientSession,
        request: dict[str, Any],
    ) -> Any:
        async with session.post(
            f"{self.discovery.base_url}/rpc",
            headers={"Authorization": f"Bearer {self.discovery.token}"},
            json=request,
        ) as response:
            return await response.json(loads=json.loads)

    async def execute(self, request: dict[str, Any]) -> dict[str, Any]:
        result = await self.call("operation.execute", {"request": request})
        if not isinstance(result, dict):
            raise EditorServiceUnavailable("Editor Service operation result must be an object")
        return result

    @staticmethod
    def _live_discovery(paths: ServicePaths) -> ServiceDiscovery | None:
        try:
            discovery = ServiceDiscovery.read(paths.discovery)
        except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
            return None
        return discovery if discovery.belongs_to_live_process() else None

    @staticmethod
    def _start_service_process(paths: ServicePaths) -> None:
        paths.prepare()
        process = launch_editor_service(
            working_directory=Path.cwd(),
            log_path=paths.log,
        )
        if process is not None:
            with _started_processes_lock:
                _started_processes[process.pid] = process


def execute_sync(request: dict[str, Any]) -> dict[str, Any]:
    result = _sync_transport.call("operation.execute", {"request": request})
    if not isinstance(result, dict):
        raise EditorServiceUnavailable("Editor Service operation result must be an object")
    return result


def call_sync(
    method: str,
    params: dict[str, Any] | None = None,
    *,
    start_if_needed: bool = True,
) -> Any:
    return _sync_transport.call(
        method,
        params,
        start_if_needed=start_if_needed,
    )


class _SyncEditorServiceTransport:
    """Thread-safe synchronous facade over one persistent aiohttp connection pool."""

    def __init__(self) -> None:
        self._ready = threading.Event()
        self._lifecycle_lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._session: ClientSession | None = None
        self._client: EditorServiceClient | None = None
        self._paths: ServicePaths | None = None
        self._lock: asyncio.Lock | None = None
        self._thread: threading.Thread | None = None
        self._start()

    def _start(self) -> None:
        with self._lifecycle_lock:
            if self._loop is not None and self._loop.is_running():
                return
            self._ready.clear()
            self._loop = None
            self._lock = None
            self._thread = threading.Thread(
                target=self._run,
                name="mediaflow-service-client",
                daemon=True,
            )
            self._thread.start()
        if not self._ready.wait(timeout=5):
            raise EditorServiceUnavailable("Editor Service client transport did not start")

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._lock = asyncio.Lock()
        self._ready.set()
        loop.run_forever()
        loop.run_until_complete(self._close_async())
        loop.close()

    def call(
        self,
        method: str,
        params: dict[str, Any] | None,
        *,
        start_if_needed: bool = True,
    ) -> Any:
        self._start()
        loop = self._loop
        if loop is None or not loop.is_running():
            raise EditorServiceUnavailable("Editor Service client transport is not running")
        future = asyncio.run_coroutine_threadsafe(
            self._call_async(method, params, start_if_needed=start_if_needed),
            loop,
        )
        return future.result()

    async def _call_async(
        self,
        method: str,
        params: dict[str, Any] | None,
        *,
        start_if_needed: bool,
    ) -> Any:
        selected = ServicePaths.discover()
        lock = self._lock
        if lock is None:
            raise EditorServiceUnavailable("Editor Service client transport is not initialized")
        async with lock:
            if self._paths != selected:
                await self._reset_async()
                self._paths = selected
            if self._session is None or self._session.closed:
                self._session = ClientSession(
                    timeout=ClientTimeout(
                        total=None,
                        connect=5,
                        sock_connect=5,
                        sock_read=None,
                    ),
                    connector=TCPConnector(limit=32, limit_per_host=32, keepalive_timeout=30),
                )
            live = EditorServiceClient._live_discovery(selected)
            if self._client is None or self._client.discovery != live:
                self._client = await EditorServiceClient.connect(
                    paths=selected,
                    start_if_needed=start_if_needed,
                    session=self._session,
                )
            client = self._client
            session = self._session
        try:
            return await client.call(method, params, session=session)
        finally:
            if method == "service.shutdown":
                async with lock:
                    self._client = None

    async def _reset_async(self) -> None:
        self._client = None
        session = self._session
        self._session = None
        if session is not None and not session.closed:
            await session.close()

    async def _close_async(self) -> None:
        await self._reset_async()

    def reset(self) -> None:
        with self._lifecycle_lock:
            loop = self._loop
            if loop is None or not loop.is_running():
                return
            if self._thread is threading.current_thread():
                loop.create_task(self._reset_async())
                return
            future = asyncio.run_coroutine_threadsafe(self._reset_async(), loop)
            future.result(timeout=5)

    def close(self) -> None:
        with self._lifecycle_lock:
            loop = self._loop
            thread = self._thread
            if loop is None or not loop.is_running():
                return
            future = asyncio.run_coroutine_threadsafe(self._close_async(), loop)
            try:
                future.result(timeout=5)
            finally:
                loop.call_soon_threadsafe(loop.stop)
                if thread is not None and thread is not threading.current_thread():
                    thread.join(timeout=5)
                self._loop = None
                self._lock = None
                self._thread = None
                self._paths = None


_sync_transport = _SyncEditorServiceTransport()
atexit.register(_sync_transport.close)


def close_sync_transport() -> None:
    _sync_transport.reset()


def shutdown_sync_service() -> None:
    paths = ServicePaths.discover()
    discovery = EditorServiceClient._live_discovery(paths)
    try:
        call_sync(
            "service.shutdown",
            {"force": True},
            start_if_needed=False,
        )
    except EditorServiceUnavailable:
        pass
    finally:
        close_sync_transport()
    if discovery is None:
        return
    deadline = time.monotonic() + SERVICE_PROCESS_EXIT_TIMEOUT_SECONDS
    while True:
        if _started_process_exit(discovery.pid) is not None:
            return
        if not discovery.belongs_to_live_process():
            return
        if time.monotonic() >= deadline:
            # The server has already accepted a forced shutdown. A disconnected
            # request may still own an uninterruptible default-executor thread
            # (for example, an FFmpeg capability probe), which otherwise keeps
            # the Python interpreter alive after projects and discovery state
            # have been closed. Escalate only after checking the exact process
            # creation time so a reused PID can never be targeted.
            _terminate_stalled_service(discovery)
            return
        time.sleep(0.05)
