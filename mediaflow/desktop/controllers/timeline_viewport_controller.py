from __future__ import annotations

from PySide6.QtCore import Property, QObject, Signal, Slot

from .controller_facet import ControllerFacet
from .controller_scopes import TimelinePresentationScope


class TimelineViewportController(ControllerFacet[TimelinePresentationScope]):
    """Own the bounded interactive clip projection for the visible timeline."""

    selectionChanged = Signal()

    def __init__(self, session: TimelinePresentationScope):
        super().__init__(session)
        self.selectionChanged.connect(self._sync_selection)

    @Property(QObject, constant=True)
    def visibleClipsModel(self) -> QObject:
        return self._session.models.visible_clips

    @Slot(float, float, float)
    def setClipViewport(
        self,
        visible_start_frame: float,
        visible_end_frame: float,
        pixels_per_frame: float,
    ) -> None:
        self._sync_selection()
        self._session.models.visible_clips.set_viewport(
            visible_start_frame,
            visible_end_frame,
            pixels_per_frame,
        )

    def _sync_selection(self) -> None:
        self._session.models.visible_clips.set_selected_ids(
            self._session.state.selection.clip_ids
        )
