from __future__ import annotations

import asyncio

from mediaflow.service.client import EditorServiceClient


def main() -> int:
    asyncio.run(EditorServiceClient.connect())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
