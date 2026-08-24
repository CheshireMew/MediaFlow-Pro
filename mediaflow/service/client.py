from __future__ import annotations

import asyncio
import atexit
import json
import math
import subprocess
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeoutError
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


class EditorServiceTimeout(EditorServiceUnavailable, TimeoutError):
    pass


_started_processes: dict[int, subprocess.Popen[bytes]] = {}
_started_processes_lock = threading.Lock()
SERVICE_PROCESS_EXIT_TIMEOUT_SECONDS = 15.0
SERVICE_PROCESS_TERMINATE_TIMEOUT_SECONDS = 5.0
DEFAULT_RPC_TIMEOUT_SECONDS = 120.0
MAX_RPC_TIMEOUT_SECONDS = 7_200.0
DEFAULT_SERVICE_STARTUP_TIMEOUT_SECONDS = 60.0
MAX_SERVICE_STARTUP_TIMEOUT_SECONDS = 300.0
SYNC_TRANSPORT_MARGIN_SECONDS = 1.0
SyncFutureWaiter = Callable[[Future[Any], float], Any]
_sync_future_waiter: SyncFutureWaiter | None = None
_sync_future_waiter_lock = threading.Lock()


def install_sync_future_waiter(waiter: SyncFutureWaiter) -> None:
    """Install the host event-loop adapter used while a synchronous facade waits.

    The service package stays independent of Qt. Desktop startup installs a Qt
    adapter so legacy synchronous command surfaces keep painting and delivering
    queued results while the actual HTTP request runs on the transport thread.
    """

    global _sync_future_waiter
    with _sync_future_waiter_lock:
        _sync_future_waiter = waiter


def _wait_for_sync_future(future: Future[Any], timeout_seconds: float) -> Any:
    with _sync_future_waiter_lock:
        waiter = _sync_future_waiter
    if waiter is None:
        return future.result(timeout=timeout_seconds)
    return waiter(future, timeout_seconds)


def _validated_rpc_timeout(value: float | None) -> float:
    timeout = DEFAULT_RPC_TIMEOUT_SECONDS if value is None else float(value)
    if not math.isfinite(timeout) or timeout <= 0 or timeout > MAX_RPC_TIMEOUT_SECONDS:
        raise ValueError(
            f"RPC timeout must be greater than 0 and at most {MAX_RPC_TIMEOUT_SECONDS:g} seconds"
        )
    return timeout


def _validated_service_startup_timeout(value: float) -> float:
    timeout = float(value)
    if (
        not math.isfinite(timeout)
        or timeout <= 0
        or timeout > MAX_SERVICE_STARTUP_TIMEOUT_SECONDS
    ):
        raise ValueError(
            "Service startup timeout must be greater than 0 and at most "
            f"{MAX_SERVICE_STARTUP_TIMEOUT_SECONDS:g} seconds"
        )
    return timeout


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
        startup_timeout: float = DEFAULT_SERVICE_STARTUP_TIMEOUT_SECONDS,
        session: ClientSession | None = None,
    ) -> EditorServiceClient:
        selected = paths or ServicePaths.discover()
        deadline = time.monotonic() + _validated_service_startup_timeout(
            startup_timeout
        )
        started = False
        replacement_requested_pid: int | None = None
        last_error: EditorServiceUnavailable | None = None
        while True:
            discovery = cls._live_discovery(selected)
            if discovery is not None:
                client = cls(discovery)
                try:
                    remaining = max(0.05, deadline - time.monotonic())
                    hello = await client.call(
                        "system.hello",
                        session=session,
                        timeout_seconds=min(5.0, remaining),
                    )
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
                            remaining = max(0.05, deadline - time.monotonic())
                            await client.call(
                                "service.shutdown",
                                session=session,
                                timeout_seconds=min(5.0, remaining),
                            )
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
        timeout_seconds: float | None = None,
    ) -> Any:
        request = {
            "jsonrpc": "2.0",
            "id": f"client-{time.time_ns()}",
            "method": method,
            "params": params or {},
        }
        deadline = _validated_rpc_timeout(timeout_seconds)
        timeout = ClientTimeout(
            total=deadline,
            connect=min(5.0, deadline),
            sock_connect=min(5.0, deadline),
            sock_read=deadline,
        )
        try:
            if session is None:
                async with ClientSession(timeout=timeout) as owned_session:
                    payload = await self._post(owned_session, request, timeout=timeout)
            else:
                payload = await self._post(session, request, timeout=timeout)
        except TimeoutError as timeout_error:
            raise EditorServiceTimeout(
                f"Editor Service request {method!r} exceeded its {deadline:g}-second deadline"
            ) from timeout_error
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
        *,
        timeout: ClientTimeout,
    ) -> Any:
        async with session.post(
            f"{self.discovery.base_url}/rpc",
            headers={"Authorization": f"Bearer {self.discovery.token}"},
            json=request,
            timeout=timeout,
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
    timeout_seconds: float | None = None,
) -> Any:
    return _sync_transport.call(
        method,
        params,
        start_if_needed=start_if_needed,
        timeout_seconds=timeout_seconds,
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
        timeout_seconds: float | None = None,
    ) -> Any:
        deadline = _validated_rpc_timeout(timeout_seconds)
        self._start()
        loop = self._loop
        if loop is None or not loop.is_running():
            raise EditorServiceUnavailable("Editor Service client transport is not running")
        future = asyncio.run_coroutine_threadsafe(
            self._call_async(
                method,
                params,
                start_if_needed=start_if_needed,
                timeout_seconds=deadline,
            ),
            loop,
        )
        try:
            return _wait_for_sync_future(
                future,
                deadline + SYNC_TRANSPORT_MARGIN_SECONDS,
            )
        except FutureTimeoutError as timeout_error:
            future.cancel()
            raise EditorServiceTimeout(
                f"Editor Service request {method!r} exceeded its {deadline:g}-second deadline"
            ) from timeout_error

    async def _call_async(
        self,
        method: str,
        params: dict[str, Any] | None,
        *,
        start_if_needed: bool,
        timeout_seconds: float,
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
                        total=DEFAULT_RPC_TIMEOUT_SECONDS,
                        connect=5,
                        sock_connect=5,
                        sock_read=DEFAULT_RPC_TIMEOUT_SECONDS,
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
            return await client.call(
                method,
                params,
                session=session,
                timeout_seconds=timeout_seconds,
            )
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
