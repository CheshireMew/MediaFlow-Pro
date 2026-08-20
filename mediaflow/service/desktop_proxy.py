"""Stable desktop service proxy imports backed by focused transport modules."""

from mediaflow.service.desktop_application_proxy import (
    DesktopEditorApplication as DesktopEditorApplication,
)
from mediaflow.service.desktop_application_proxy import (
    create_desktop_editor_application as create_desktop_editor_application,
)
from mediaflow.service.remote_project import RemoteEditorProject as RemoteEditorProject
from mediaflow.service.remote_timeline import RemoteTimelineEditor as RemoteTimelineEditor

__all__ = (
    "DesktopEditorApplication",
    "RemoteEditorProject",
    "RemoteTimelineEditor",
    "create_desktop_editor_application",
)
