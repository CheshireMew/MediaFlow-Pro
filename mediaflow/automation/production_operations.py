from __future__ import annotations

from mediaflow.automation.operation_context import OperationContext
from mediaflow.domain.production_bundle import ProductionBundle


def inspect_bundle(context: OperationContext) -> dict:
    bundle = ProductionBundle.model_validate(context.required("bundle"))
    return context.application.production_bundles.inspect(bundle).model_dump(
        mode="json",
        exclude_computed_fields=True,
    )
