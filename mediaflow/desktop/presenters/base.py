from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mediaflow.desktop.controllers.project_controller import ProjectSession


class Projector:
    def __init__(self, session: ProjectSession):
        self._session = session
