from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from typing import Any, Literal

ServiceWorkload = Literal[
    "control",
    "project",
    "wait",
    "runtime",
    "preview",
    "tool",
    "lifecycle",
]


class ServiceBusyError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class _PoolConfiguration:
    workers: int
    max_waiters: int
    admission_timeout: float


_POOL_CONFIGURATIONS: dict[ServiceWorkload, _PoolConfiguration] = {
    "control": _PoolConfiguration(workers=2, max_waiters=16, admission_timeout=0.5),
    "project": _PoolConfiguration(workers=8, max_waiters=64, admission_timeout=2.0),
    "wait": _PoolConfiguration(workers=8, max_waiters=64, admission_timeout=0.5),
    "runtime": _PoolConfiguration(workers=4, max_waiters=32, admission_timeout=3.0),
    "preview": _PoolConfiguration(workers=4, max_waiters=32, admission_timeout=10.0),
    "tool": _PoolConfiguration(workers=2, max_waiters=16, admission_timeout=10.0),
    "lifecycle": _PoolConfiguration(workers=2, max_waiters=8, admission_timeout=2.0),
}


class _BoundedExecutionPool:
    def __init__(self, name: str, configuration: _PoolConfiguration) -> None:
        self._name = name
        self._configuration = configuration
        self._executor = ThreadPoolExecutor(
            max_workers=configuration.workers,
            thread_name_prefix=f"mediaflow-service-{name}",
        )
        self._slots = asyncio.Semaphore(configuration.workers)
        self._state_lock = threading.Lock()
        self._waiters = 0
        self._closed = False

    async def run(self, operation: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        with self._state_lock:
            if self._closed:
                raise RuntimeError(f"Editor Service {self._name} executor is closed")
            if self._waiters >= self._configuration.max_waiters:
                raise ServiceBusyError(
                    f"Editor Service {self._name} workload has reached its admission limit"
                )
            self._waiters += 1
        try:
            try:
                await asyncio.wait_for(
                    self._slots.acquire(),
                    timeout=self._configuration.admission_timeout,
                )
            except TimeoutError as error:
                raise ServiceBusyError(
                    f"Editor Service {self._name} workload is busy; retry the request"
                ) from error
        finally:
            with self._state_lock:
                self._waiters -= 1

        loop = asyncio.get_running_loop()
        try:
            future = loop.run_in_executor(self._executor, partial(operation, *args, **kwargs))
        except BaseException:
            self._slots.release()
            raise
        future.add_done_callback(lambda _future: loop.call_soon_threadsafe(self._slots.release))
        return await future

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
        self._executor.shutdown(wait=True, cancel_futures=True)


class ServiceExecutionPools:
    """Bounded, workload-specific ownership for every blocking service call."""

    def __init__(self) -> None:
        self._pools = {
            name: _BoundedExecutionPool(name, configuration)
            for name, configuration in _POOL_CONFIGURATIONS.items()
        }

    async def run(
        self,
        workload: ServiceWorkload,
        operation: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        return await self._pools[workload].run(operation, *args, **kwargs)

    def close(self) -> None:
        for pool in self._pools.values():
            pool.close()
