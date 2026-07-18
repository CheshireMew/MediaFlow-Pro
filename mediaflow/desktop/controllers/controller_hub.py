from __future__ import annotations

from PySide6.QtCore import QObject

from mediaflow.composition import EditorApplication

from .audio_controller import AudioController
from .export_controller import ExportController
from .highlight_controller import HighlightController
from .media_controller import MediaController
from .project_controller import ProjectSession
from .settings_controller import SettingsController
from .subtitle_controller import SubtitleController
from .task_controller import TaskController
from .timeline_controller import TimelineController
from .workspace_controller import WorkspaceController


class EditorControllers:
    """Composition root for the focused desktop presentation boundaries."""

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        application: EditorApplication | None = None,
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
        }

    def shutdown(self) -> None:
        self.workspace.shutdown()
