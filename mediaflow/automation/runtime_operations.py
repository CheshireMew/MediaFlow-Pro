from __future__ import annotations

from mediaflow.automation.operation_context import OperationContext
from mediaflow.domain.runtime_capabilities import RuntimeInspection


def inspect_runtime(context: OperationContext) -> RuntimeInspection:
    return context.application.runtime_inspection.inspect()
