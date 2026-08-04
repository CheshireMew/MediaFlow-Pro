from __future__ import annotations

import asyncio
import signal

from mediaflow.infrastructure.application_logging import (
    configure_application_logging,
    shutdown_application_logging,
)

from .server import EditorServiceServer


async def _run() -> None:
    server = EditorServiceServer()
    configure_application_logging(server.paths.root)
    loop = asyncio.get_running_loop()
    for name in ("SIGINT", "SIGTERM"):
        signal_value = getattr(signal, name, None)
        if signal_value is not None:
            try:
                loop.add_signal_handler(signal_value, server.request_stop)
            except (NotImplementedError, RuntimeError):
                pass
    await server.start()
    try:
        await server.serve()
    finally:
        await server.stop()


def main() -> int:
    try:
        asyncio.run(_run())
        return 0
    finally:
        shutdown_application_logging()


if __name__ == "__main__":
    raise SystemExit(main())
