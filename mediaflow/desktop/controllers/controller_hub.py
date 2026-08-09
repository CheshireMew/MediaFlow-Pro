from __future__ import annotations

from PySide6.QtCore import QObject

from mediaflow.service.desktop_proxy import DesktopEditorApplication

from .audio_controller import AudioController
from .automation_controller import AutomationController
from .export_controller import ExportController
from .highlight_controller import HighlightController
from .media_controller import MediaController
from .project_controller import ProjectSession
from .settings_controller import SettingsController
from .subtitle_controller import SubtitleController
from .task_controller import TaskController
from .timeline_controller import TimelineController
from .web_controller import WebController
from .web_delivery_controller import WebDeliveryController
from .web_timeline_controller import WebTimelineController
from .workspace_controller import WorkspaceController


class EditorControllers:
    """Composition root for the focused desktop presentation boundaries."""

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        application: DesktopEditorApplication | None = None,
    ):
        self.session = ProjectSession(parent, application=application)
        self.workspace = WorkspaceController(self.session)
        self.settings = SettingsController(self.session)
        self.media = MediaController(self.session)
        self.timeline = TimelineController(self.session)
        self.subtitles = SubtitleController(self.session)
        self.highlights = HighlightController(self.session)
        self.audio = AudioController(self.session)
        self.tasks = TaskController(self.session)
        self.export = ExportController(self.session)
        self.web = WebController(self.session)
        self.web_timeline = WebTimelineController(self.session, self.web)
        self.web_delivery = WebDeliveryController(self.session, self.web)
        self.automation = AutomationController(
            self.session,
            export=self.export,
            subtitles=self.subtitles,
            web=self.web,
            web_timeline=self.web_timeline,
            web_delivery=self.web_delivery,
        )
        self.session._attach_controllers(self.context_properties())

    def context_properties(self) -> dict[str, QObject]:
        return {
            "workspaceController": self.workspace,
            "settingsController": self.settings,
            "mediaController": self.media,
            "timelineController": self.timeline,
            "subtitleController": self.subtitles,
            "highlightController": self.highlights,
            "audioController": self.audio,
            "taskController": self.tasks,
            "exportController": self.export,
            "webController": self.web,
            "webTimelineController": self.web_timeline,
            "webDeliveryController": self.web_delivery,
            "automationController": self.automation,
        }

    def shutdown(self) -> None:
        self.workspace.shutdown()
