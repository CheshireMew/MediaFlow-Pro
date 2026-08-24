from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor, wait

from PySide6.QtCore import QObject, Signal, Slot

from .background_result_router import BackgroundResult, BackgroundResultRouter
from .base import SessionCoordinator

PROJECT_REQUEST_SHUTDOWN_TIMEOUT_SECONDS = 15.0

_PROJECT_REQUEST_KINDS = frozenset(
    {
        "asset_thumbnails",
        "audio_metrics",
        "timeline_filmstrip",
        "project_close",
        "waveform",
    }
)


class _BackgroundBridge(QObject):
    resultReceived = Signal(object)


class BackgroundRequests(SessionCoordinator):
    def __init__(self, session):
        super().__init__(session)
        self._application_executor = ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="mediaflow-desktop-io",
        )
        self._project_executor = ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="mediaflow-project-io",
        )
        self.preview_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="mediaflow-preview-compile",
        )
        self._project_futures: set[Future[object]] = set()
        self._project_futures_lock = threading.Lock()
        self._callbacks: dict[
            tuple[str, object],
            tuple[Callable[[object | None], None], Callable[[BaseException], None]],
        ] = {}
        self._result_router = BackgroundResultRouter(session)
        self._bridge = _BackgroundBridge(session)
        self._bridge.resultReceived.connect(self._on_result)

    def submit(
        self,
        kind: str,
        request_id: object,
        operation: Callable[[], object],
        *,
        executor: ThreadPoolExecutor | None = None,
        publish_result: bool = True,
    ) -> Future[object] | None:
        if self._session.state.requests.shutting_down:
            return None
        worker = executor or (
            self._project_executor if kind in _PROJECT_REQUEST_KINDS else self._application_executor
        )
        future = worker.submit(operation)
        if worker is self._project_executor or worker is self.preview_executor:
            with self._project_futures_lock:
                self._project_futures.add(future)
            future.add_done_callback(self._forget_project_future)
        if publish_result:
            future.add_done_callback(
                lambda completed: self._publish_result(
                    kind,
                    request_id,
                    completed,
                )
            )
        return future

    def submit_project_callback(
        self,
        kind: str,
        request_id: object,
        operation: Callable[[], object],
        *,
        on_result: Callable[[object | None], None],
        on_error: Callable[[BaseException], None],
    ) -> Future[object] | None:
        key = (kind, request_id)
        if key in self._callbacks:
            raise RuntimeError(f"Duplicate desktop background request: {kind} {request_id!r}")
        self._callbacks[key] = (on_result, on_error)
        future = self.submit(
            kind,
            request_id,
            operation,
            executor=self._project_executor,
        )
        if future is None:
            self._callbacks.pop(key, None)
        return future

    def _forget_project_future(self, future: Future[object]) -> None:
        with self._project_futures_lock:
            self._project_futures.discard(future)

    def _publish_result(
        self,
        kind: str,
        request_id: object,
        completed: Future[object],
    ) -> None:
        try:
            result = completed.result()
        except Exception as error:
            payload = BackgroundResult(kind, request_id, None, error)
        else:
            payload = BackgroundResult(kind, request_id, result, None)
        if not self._session.state.requests.shutting_down:
            self._bridge.resultReceived.emit(payload)

    def shutdown_project_requests(
        self,
        *,
        timeout: float = PROJECT_REQUEST_SHUTDOWN_TIMEOUT_SECONDS,
    ) -> None:
        if timeout < 0:
            raise ValueError("Project request shutdown timeout cannot be negative")
        # Project readers must finish before the project session is released;
        # they may own SQLite readers or fingerprint service-owned cache files.
        self.preview_executor.shutdown(wait=False, cancel_futures=True)
        self._project_executor.shutdown(wait=False, cancel_futures=True)
        with self._project_futures_lock:
            pending = tuple(self._project_futures)
        if pending:
            _done, unfinished = wait(pending, timeout=timeout)
            if unfinished:
                raise TimeoutError(f"Timed out waiting for {len(unfinished)} project background request(s)")
        self.preview_executor.shutdown(wait=True, cancel_futures=True)
        self._project_executor.shutdown(wait=True, cancel_futures=True)

    def shutdown_application_requests(self) -> None:
        # The service transport is closed before this call, which interrupts
        # pending network operations such as download inspection.
        self._application_executor.shutdown(wait=True, cancel_futures=True)

    def raise_if_shutting_down(self) -> None:
        if self._session.state.requests.shutting_down:
            raise CancelledError("Desktop background requests are shutting down")

    @Slot(object)
    def _on_result(self, payload: object) -> None:
        if self._session.state.requests.shutting_down:
            return
        if not isinstance(payload, BackgroundResult):
            return
        callback = self._callbacks.pop((payload.kind, payload.request_id), None)
        if callback is not None:
            on_result, on_error = callback
            if payload.error is not None:
                on_error(payload.error)
            else:
                on_result(payload.result)
            return
        self._result_router.dispatch(payload)
