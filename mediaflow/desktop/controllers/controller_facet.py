from __future__ import annotations

from collections.abc import Callable
from functools import wraps

from PySide6.QtCore import QObject

from mediaflow.service.client import EditorServiceRpcError

from .controller_scopes import ControllerScope


def report_ui_errors(
    method: Callable | None = None,
    *,
    message: str = "{error}",
):
    def decorate(action: Callable):
        @wraps(action)
        def guarded(controller: ControllerFacet[ControllerScope], *args, **kwargs):
            try:
                return action(controller, *args, **kwargs)
            except Exception as error:
                if isinstance(error, EditorServiceRpcError) and error.code == -32009:
                    controller._session._present_collaboration_conflict(error)
                    return None
                controller._session.updates.report_error(message.format(error=error))
                return None

        return guarded

    return decorate(method) if method is not None else decorate


class ControllerFacet[ScopeT: ControllerScope](QObject):
    def __init__(self, session: ScopeT):
        super().__init__(session.parent)
        self._session = session
