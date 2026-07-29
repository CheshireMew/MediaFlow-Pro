from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject

if TYPE_CHECKING:
    from mediaflow.desktop.controllers.project_controller import ProjectSession


def report_ui_errors(
    method: Callable | None = None,
    *,
    message: str = "{error}",
):
    def decorate(action: Callable):
        @wraps(action)
        def guarded(controller: ControllerFacet, *args, **kwargs):
            try:
                return action(controller, *args, **kwargs)
            except Exception as error:
                controller._session.events.errorOccurred.emit(message.format(error=error))
                return None

        return guarded

    return decorate(method) if method is not None else decorate


class ControllerFacet(QObject):
    def __init__(self, session: ProjectSession):
        super().__init__(session)
        self._session = session
