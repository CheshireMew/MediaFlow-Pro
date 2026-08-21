from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QT_TRANSLATE_NOOP, QCoreApplication


@dataclass(frozen=True, slots=True)
class WorkspaceModeDefinition:
    key: str
    label_source: str
    panel_object_name: str
    icon: str
    navigation_visible: bool = True


WORKSPACE_MODES = (
    WorkspaceModeDefinition(
        "media",
        str(QT_TRANSLATE_NOOP("WorkspaceNavigation", "素材")),
        "mediaPanel",
        "media",
    ),
    WorkspaceModeDefinition(
        "resources",
        str(QT_TRANSLATE_NOOP("WorkspaceNavigation", "资源")),
        "resourceLibraryPanel",
        "edit",
    ),
    WorkspaceModeDefinition(
        "transcript",
        str(QT_TRANSLATE_NOOP("WorkspaceNavigation", "字幕")),
        "transcriptWorkspace",
        "transcript",
    ),
    WorkspaceModeDefinition(
        "highlight",
        str(QT_TRANSLATE_NOOP("WorkspaceNavigation", "高光")),
        "highlightPanel",
        "highlight",
    ),
    WorkspaceModeDefinition(
        "audio",
        str(QT_TRANSLATE_NOOP("WorkspaceNavigation", "音频")),
        "audioScroll",
        "audio",
    ),
    WorkspaceModeDefinition(
        "export",
        str(QT_TRANSLATE_NOOP("WorkspaceNavigation", "导出")),
        "exportPanel",
        "export",
        False,
    ),
    WorkspaceModeDefinition(
        "tasks",
        str(QT_TRANSLATE_NOOP("WorkspaceNavigation", "任务")),
        "taskCenterPanel",
        "tasks",
    ),
)
WORKSPACE_MODE_KEYS = tuple(mode.key for mode in WORKSPACE_MODES)
WORKSPACE_NAVIGATION_MODE_KEYS = tuple(mode.key for mode in WORKSPACE_MODES if mode.navigation_visible)


def workspace_mode_catalog() -> list[dict[str, object]]:
    return [
        {
            "key": mode.key,
            "label": QCoreApplication.translate("WorkspaceNavigation", mode.label_source),
            "panelObjectName": mode.panel_object_name,
            "icon": mode.icon,
            "navigationVisible": mode.navigation_visible,
        }
        for mode in WORKSPACE_MODES
    ]
