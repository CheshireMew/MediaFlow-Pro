from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject

if TYPE_CHECKING:
    from mediaflow.desktop.controllers.project_controller import ProjectSession


class SessionCoordinator(QObject):
    def __init__(self, session: ProjectSession):
        super().__init__(session)
        self._session = session
