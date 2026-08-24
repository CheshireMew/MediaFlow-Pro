from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Property, Signal, Slot

from mediaflow.desktop.presentation_runtime import localized_runtime_tool_status
from mediaflow.desktop.runtime_directory_management import (
    cancel_pending_runtime_directory_change,
    current_runtime_directory,
    runtime_directory_info,
    runtime_directory_is_managed_externally,
    schedule_runtime_directory_change,
    validate_existing_runtime_directory,
    validate_runtime_change_destination,
)

from .controller_facet import ControllerFacet, report_ui_errors
from .controller_scopes import RuntimeSettingsControllerScope


class RuntimeSettingsController(ControllerFacet[RuntimeSettingsControllerScope]):
    runtimeToolsChanged = Signal()
    runtimeDirectoryChanged = Signal()
    errorOccurred = Signal(str)

    @Property(dict, notify=runtimeToolsChanged)
    def runtimeToolStatus(self) -> dict:
        return localized_runtime_tool_status(self._session.state.runtime_state.status)

    @Property(dict, notify=runtimeDirectoryChanged)
    def runtimeDirectoryInfo(self) -> dict:
        return runtime_directory_info(current_runtime_directory())

    @Slot(str, bool, result=bool)
    @report_ui_errors
    def scheduleRuntimeDirectoryChange(self, destination: str, migrate_existing: bool) -> bool:
        if runtime_directory_is_managed_externally():
            raise RuntimeError("运行环境目录由 MEDIAFLOW_RUNTIME_DIR 或开发环境配置管理，请修改对应配置")
        target = validate_runtime_change_destination(
            current_runtime_directory(),
            Path(destination),
            migrate_existing=migrate_existing,
        )
        if not migrate_existing:
            validate_existing_runtime_directory(target)
        schedule_runtime_directory_change(target, migrate_existing=migrate_existing)
        self.runtimeDirectoryChanged.emit()
        if migrate_existing:
            self._session._set_status("已安排在下次启动时迁移并切换运行环境目录")
        else:
            self._session._set_status("已安排在下次启动时切换运行环境目录")
        return True

    @Slot()
    def cancelRuntimeDirectoryChange(self) -> None:
        cancel_pending_runtime_directory_change()
        self.runtimeDirectoryChanged.emit()
        self._session._set_status("已取消运行环境目录变更")

    @Slot()
    def inspectRuntimeTools(self) -> None:
        self._session.runtime_tools.start("inspect")

    @Slot()
    def updateYtDlp(self) -> None:
        self._session.runtime_tools.start("update_ytdlp")

    @Slot("QVariantList")
    def installRuntimeComponents(self, component_ids: list) -> None:
        self._session.runtime_tools.start(
            "install_components",
            {"component_ids": [str(item) for item in component_ids]},
        )

    @Slot()
    def installSpeakerClustering(self) -> None:
        self._session.runtime_tools.start("install_speaker_clustering")

    @Slot()
    def prewarmAsrCli(self) -> None:
        self._session.runtime_tools.start("prewarm_asr_cli")

    @Slot()
    def cancelRuntimeToolOperation(self) -> None:
        if self._session.state.runtime_state.thread and self._session.state.runtime_state.thread.is_alive():
            result = self._session._api.cancel_runtime_tool()
            if result.get("cancel_requested"):
                self._session.state.runtime_state.cancel.set()
                self._session._set_status("已请求取消运行时工具操作")
