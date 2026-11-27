from __future__ import annotations

from PySide6.QtCore import Property, QObject, Signal, Slot

from .controller_facet import ControllerFacet
from .timeline_controller_scopes import TimelineViewportScope


class TimelineViewportController(ControllerFacet[TimelineViewportScope]):
    """Own the bounded interactive clip projection for the visible timeline."""

    selectionChanged = Signal()

    def __init__(self, session: TimelineViewportScope):
        super().__init__(session)
        self.selectionChanged.connect(self._sync_selection)

    @Property(QObject, constant=True)
    def visibleClipsModel(self) -> QObject:
        return self._session.visible_clips_model

    @Slot(float, float, float)
    def setClipViewport(
        self,
        visible_start_frame: float,
        visible_end_frame: float,
        pixels_per_frame: float,
    ) -> None:
        self._sync_selection()
        self._session.visible_clips_model.set_viewport(
            visible_start_frame,
            visible_end_frame,
            pixels_per_frame,
        )

    def _sync_selection(self) -> None:
        self._session.visible_clips_model.set_selected_ids(self._session.read_selection().clip_ids)
