from concurrent.futures import CancelledError


class OperationCancelled(CancelledError):
    """Keep cancellation catchable across thread, asyncio and RPC boundaries."""
