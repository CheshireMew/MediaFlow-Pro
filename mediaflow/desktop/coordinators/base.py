from __future__ import annotations

from typing import Protocol

from PySide6.QtCore import QObject


class QObjectContext(Protocol):
    @property
    def parent(self) -> QObject: ...


class SessionCoordinator[ContextT: QObjectContext](QObject):
    def __init__(self, context: ContextT):
        super().__init__(context.parent)
        self._context = context
