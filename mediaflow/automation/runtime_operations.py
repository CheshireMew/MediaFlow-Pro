from __future__ import annotations

from mediaflow.automation.operation_context import OperationContext
from mediaflow.domain.runtime_capabilities import RuntimeInspection
from mediaflow.infrastructure.runtime_capabilities import (
    RuntimeCapabilityInspector,
)


def inspect_runtime(context: OperationContext) -> RuntimeInspection:
    application = context.application
    return RuntimeCapabilityInspector(
        settings=application.service_settings,
        runtime=application.runtime,
    ).inspect()
