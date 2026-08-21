from __future__ import annotations

from mediaflow.automation.operation_context import OperationContext
from mediaflow.domain.enums import ColorMode


def search_catalog(context: OperationContext) -> dict[str, object]:
    return context.application.media_resources.search(
        color_mode=ColorMode(context.arguments.get("color_mode", ColorMode.SDR_BT709)),
        catalog_paths=context.arguments.get("catalog_paths"),
        category=context.arguments.get("category"),
        query=str(context.arguments.get("query") or ""),
        tags=[str(value) for value in context.arguments.get("tags") or ()],
        capabilities=[
            str(value) for value in context.arguments.get("capabilities") or ()
        ],
    )
