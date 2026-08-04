from __future__ import annotations

from typing import Any

from mediaflow.automation.contracts import AutomationRequest
from mediaflow.service.client import call_sync, execute_sync


def execute_request(
    request: dict[str, Any] | AutomationRequest,
) -> dict[str, Any]:
    """Execute through the resident Editor Service.

    This module is a Python transport adapter only. It deliberately has no
    application or repository injection point, so it cannot become a second
    project writer.
    """

    envelope = (
        request
        if isinstance(request, AutomationRequest)
        else AutomationRequest.model_validate(request)
    )
    if envelope.operation == "describe":
        result = call_sync("system.describe")
        if not isinstance(result, dict):
            raise RuntimeError("Editor Service describe returned an invalid result")
        return result
    response = execute_sync(envelope.model_dump(mode="json"))
    result = response.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("Editor Service operation returned an invalid result")
    return result
