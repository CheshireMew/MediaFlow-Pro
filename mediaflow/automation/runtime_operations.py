from __future__ import annotations

from mediaflow.automation.operation_context import OperationContext
from mediaflow.domain.runtime_capabilities import RuntimeInspection
from mediaflow.infrastructure.runtime_capabilities import (
    RuntimeCapabilityInspector,
)


def inspect_runtime(context: OperationContext) -> RuntimeInspection:
    settings = context.application.settings if context.application is not None else None
    return RuntimeCapabilityInspector(settings=settings).inspect()
