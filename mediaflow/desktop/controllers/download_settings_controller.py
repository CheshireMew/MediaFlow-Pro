from __future__ import annotations

import json

from PySide6.QtCore import Property, Signal, Slot

from .controller_facet import ControllerFacet, report_ui_errors
from .controller_scopes import DownloadSettingsControllerScope


class DownloadSettingsController(ControllerFacet[DownloadSettingsControllerScope]):
    settingsChanged = Signal()
    downloadPlanChanged = Signal()
    errorOccurred = Signal(str)

    @Property(str, constant=True)
    def builtInMediaDirectory(self) -> str:
        return self._session._api.default_media_directory

    @Property(dict, notify=settingsChanged)
    def managedCookieStatus(self) -> dict:
        return self._session.state.download.cookie_status

    @Slot(str)
    @report_ui_errors
    def setDefaultDownloadDirectory(self, value: str) -> None:
        if not value.strip():
            raise ValueError("媒体默认保存目录不能为空")
        selected = str(self._session._local_path(value))
        if self._session.state.service_settings.download.output_directory == selected:
            return
        candidate = self._session.state.service_settings.model_copy(deep=True)
        candidate.download.output_directory = selected
        self._session.settings_persistence.commit(candidate, "默认下载目录已更新")

    @Slot()
    def resetDefaultDownloadDirectory(self) -> None:
        self.setDefaultDownloadDirectory(self._session._api.default_media_directory)

    @Slot(str)
    @report_ui_errors
    def setDefaultProjectDirectory(self, value: str) -> None:
        if not value.strip():
            return
        self._session.settings_persistence.remember_default_project_directory(
            self._session._local_path(value),
            "默认项目保存目录已更新",
        )
        self._session.updates.commit(download_plan=True)

    @Slot(str)
    @report_ui_errors
    def setLastDownloadUrl(self, value: str) -> None:
        normalized = value.strip()
        if self._session.state.service_settings.download.last_url == normalized:
            return
        candidate = self._session.state.service_settings.model_copy(deep=True)
        candidate.download.last_url = normalized
        self._session.settings_persistence.commit(candidate)

    @Slot(str)
    @report_ui_errors
    def inspectManagedCookies(self, domain: str) -> None:
        self._session.state.download.cookie_status = self._session._api.cookies.status(domain)
        self._session.updates.commit(settings=True)

    @Slot(str, str, result=bool)
    @report_ui_errors
    def saveManagedCookies(self, domain: str, json_text: str) -> bool:
        payload = json.loads(json_text)
        cookies = payload.get("cookies") if isinstance(payload, dict) else payload
        if not isinstance(cookies, list) or not all(isinstance(item, dict) for item in cookies):
            raise ValueError("Cookie JSON 必须是对象数组或包含 cookies 数组的对象")
        path = self._session._api.cookies.save(domain, cookies)
        self._session.state.download.cookie_status = self._session._api.cookies.status(domain)
        self._session.updates.commit(settings=True)
        self._session._set_status("Cookie 已保存到 %1", path)
        return True

    @Slot(str)
    @report_ui_errors
    def clearManagedCookies(self, domain: str) -> None:
        removed = self._session._api.cookies.clear(domain)
        self._session.state.download.cookie_status = self._session._api.cookies.status(domain)
        self._session.updates.commit(settings=True)
        if removed:
            self._session._set_status("Cookie 已清除")
        else:
            self._session._set_status("该域名没有已保存的 Cookie")
