from __future__ import annotations

from typing import cast

from PySide6.QtCore import Signal, Slot

from mediaflow.domain.settings import AssetViewMode, WorkspaceLayoutPreset

from .controller_facet import ControllerFacet, report_ui_errors
from .controller_scopes import WorkspaceSettingsControllerScope


class WorkspaceSettingsController(ControllerFacet[WorkspaceSettingsControllerScope]):
    settingsChanged = Signal()
    errorOccurred = Signal(str)

    @Slot(int, int, bool)
    @report_ui_errors
    def saveWindowState(self, width: int, height: int, maximized: bool) -> None:
        candidate = self._session.state.desktop_settings.model_copy(deep=True)
        candidate.ui.window_width = max(1, int(width))
        candidate.ui.window_height = max(1, int(height))
        candidate.ui.window_maximized = bool(maximized)
        self._session.settings_persistence.commit(candidate)

    @Slot(str)
    @report_ui_errors
    def setWorkspaceLayoutPreset(self, preset: str) -> None:
        if preset not in {"standard", "media", "vertical"}:
            raise ValueError(f"未知工作区布局：{preset}")
        selected_preset = cast(WorkspaceLayoutPreset, preset)
        candidate = self._session.state.desktop_settings.model_copy(deep=True)
        candidate.ui.workspace_layout_preset = selected_preset
        self._session.settings_persistence.commit(candidate)

    @Slot(str, int, int, int, bool, bool, bool)
    @report_ui_errors
    def saveWorkspaceLayout(
        self,
        preset: str,
        left: int,
        inspector: int,
        timeline: int,
        tool_visible: bool,
        inspector_visible: bool,
        timeline_visible: bool,
    ) -> None:
        if preset not in {"standard", "media", "vertical"}:
            raise ValueError(f"未知工作区布局：{preset}")
        selected_preset = cast(WorkspaceLayoutPreset, preset)
        candidate = self._session.state.desktop_settings.model_copy(deep=True)
        candidate.ui.workspace_layout_preset = selected_preset
        layout = getattr(candidate.ui.workspace_layouts, selected_preset)
        layout.left_panel_width = max(340, min(680, int(left)))
        layout.inspector_panel_width = max(300, min(560, int(inspector)))
        layout.timeline_height = max(210, min(640, int(timeline)))
        layout.tool_panel_visible = bool(tool_visible)
        layout.inspector_panel_visible = bool(inspector_visible)
        layout.timeline_visible = bool(timeline_visible)
        self._session.settings_persistence.commit(candidate)

    @Slot(bool)
    @report_ui_errors
    def setWorkspaceTourCompleted(self, completed: bool) -> None:
        candidate = self._session.state.desktop_settings.model_copy(deep=True)
        candidate.ui.workspace_tour_completed = bool(completed)
        self._session.settings_persistence.commit(candidate)

    @Slot(str)
    @report_ui_errors
    def setAssetViewMode(self, mode: str) -> None:
        if mode not in {"list", "thumbnails", "large_thumbnails"}:
            self._session.updates.report_error(f"未知素材视图模式：{mode}")
            return
        selected_mode = cast(AssetViewMode, mode)
        candidate = self._session.state.desktop_settings.model_copy(deep=True)
        candidate.ui.asset_view_mode = selected_mode
        self._session.settings_persistence.commit(candidate)
