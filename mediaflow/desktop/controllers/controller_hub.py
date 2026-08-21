from __future__ import annotations

from PySide6.QtCore import Property, QObject

from mediaflow.service.desktop_application_proxy import DesktopEditorApplication

from .audio_controller import AudioController
from .automation_controller import AutomationController
from .controller_scopes import (
    automation_scope,
    creative_scope,
    export_scope,
    media_scope,
    settings_scope,
    subtitle_scope,
    task_scope,
    timeline_scope,
    web_scope,
    workspace_playback_scope,
    workspace_project_scope,
    workspace_sequence_scope,
    workspace_view_scope,
    workspace_workflow_scope,
)
from .dubbing_controller import DubbingController
from .export_controller import ExportController
from .highlight_controller import HighlightController
from .media_controller import MediaController
from .project_controller import ProjectSession
from .resource_library_controller import ResourceLibraryController
from .settings_controller import SettingsController
from .subtitle_editing_controller import SubtitleEditingController
from .subtitle_placement_controller import SubtitlePlacementController
from .subtitle_transcription_controller import SubtitleTranscriptionController
from .subtitle_translation_controller import SubtitleTranslationController
from .subtitle_view_controller import SubtitleViewController
from .task_controller import TaskController
from .timeline_analysis_controller import TimelineAnalysisController
from .timeline_clip_controller import TimelineClipController
from .timeline_effects_controller import TimelineEffectsController
from .timeline_structure_controller import TimelineStructureController
from .timeline_view_controller import TimelineViewController
from .web_controller import WebController
from .web_delivery_controller import WebDeliveryController
from .web_timeline_controller import WebTimelineController
from .workspace_controller import WorkspaceViewController
from .workspace_playback_controller import WorkspacePlaybackController
from .workspace_project_controller import WorkspaceProjectController
from .workspace_sequence_controller import WorkspaceSequenceController
from .workspace_workflow_controller import WorkspaceWorkflowController


class EditorControllers(QObject):
    """Composition root for the focused desktop presentation boundaries."""

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        application: DesktopEditorApplication | None = None,
    ):
        super().__init__(parent)
        self.session = ProjectSession(self, application=application)
        workspace_view = workspace_view_scope(self.session)
        settings = settings_scope(self.session)
        media = media_scope(self.session)
        timeline = timeline_scope(self.session)
        subtitles = subtitle_scope(self.session)
        creative = creative_scope(self.session)
        tasks = task_scope(self.session)
        exports = export_scope(self.session)
        web = web_scope(self.session)
        self.workspace = WorkspaceViewController(workspace_view)
        self.workspace_project = WorkspaceProjectController(workspace_project_scope(self.session))
        self.workspace_sequence = WorkspaceSequenceController(workspace_sequence_scope(self.session))
        self.workspace_workflow = WorkspaceWorkflowController(workspace_workflow_scope(self.session))
        self.workspace_playback = WorkspacePlaybackController(workspace_playback_scope(self.session))
        self.settings = SettingsController(settings)
        self.media = MediaController(media)
        self.resources = ResourceLibraryController(media)
        self.timeline_view = TimelineViewController(timeline)
        self.timeline_clips = TimelineClipController(timeline)
        self.timeline_structure = TimelineStructureController(timeline)
        self.timeline_effects = TimelineEffectsController(timeline)
        self.timeline_analysis = TimelineAnalysisController(timeline)
        self.subtitle_view = SubtitleViewController(subtitles)
        self.subtitle_placement = SubtitlePlacementController(subtitles)
        self.subtitle_transcription = SubtitleTranscriptionController(subtitles)
        self.subtitle_translation = SubtitleTranslationController(subtitles)
        self.subtitle_editing = SubtitleEditingController(subtitles)
        self.highlights = HighlightController(creative)
        self.audio = AudioController(creative)
        self.dubbing = DubbingController(creative)
        self.tasks = TaskController(tasks)
        self.export = ExportController(exports)
        self.web = WebController(web)
        self.web_timeline = WebTimelineController(web, self.web)
        self.web_delivery = WebDeliveryController(web, self.web)
        self.automation = AutomationController(
            automation_scope(self.session),
            web=self.web,
        )
        self.session._attach_controllers(self._controller_objects())

    def _controller_objects(self) -> dict[str, QObject]:
        return {
            "workspace": self.workspace,
            "workspaceProject": self.workspace_project,
            "workspaceSequence": self.workspace_sequence,
            "workspaceWorkflow": self.workspace_workflow,
            "workspacePlayback": self.workspace_playback,
            "settings": self.settings,
            "media": self.media,
            "resources": self.resources,
            "timelineView": self.timeline_view,
            "timelineClips": self.timeline_clips,
            "timelineStructure": self.timeline_structure,
            "timelineEffects": self.timeline_effects,
            "timelineAnalysis": self.timeline_analysis,
            "subtitleView": self.subtitle_view,
            "subtitlePlacement": self.subtitle_placement,
            "subtitleTranscription": self.subtitle_transcription,
            "subtitleTranslation": self.subtitle_translation,
            "subtitleEditing": self.subtitle_editing,
            "highlights": self.highlights,
            "audio": self.audio,
            "dubbing": self.dubbing,
            "tasks": self.tasks,
            "export": self.export,
            "web": self.web,
            "webTimeline": self.web_timeline,
            "webDelivery": self.web_delivery,
            "automation": self.automation,
        }

    workspaceViewController = Property(QObject, lambda self: self.workspace, constant=True)
    workspaceProjectController = Property(QObject, lambda self: self.workspace_project, constant=True)
    workspaceSequenceController = Property(QObject, lambda self: self.workspace_sequence, constant=True)
    workspaceWorkflowController = Property(QObject, lambda self: self.workspace_workflow, constant=True)
    workspacePlaybackController = Property(QObject, lambda self: self.workspace_playback, constant=True)
    settingsController = Property(QObject, lambda self: self.settings, constant=True)
    mediaController = Property(QObject, lambda self: self.media, constant=True)
    resourceLibraryController = Property(QObject, lambda self: self.resources, constant=True)
    timelineViewController = Property(QObject, lambda self: self.timeline_view, constant=True)
    timelineClipController = Property(QObject, lambda self: self.timeline_clips, constant=True)
    timelineStructureController = Property(QObject, lambda self: self.timeline_structure, constant=True)
    timelineEffectsController = Property(QObject, lambda self: self.timeline_effects, constant=True)
    timelineAnalysisController = Property(QObject, lambda self: self.timeline_analysis, constant=True)
    subtitleViewController = Property(QObject, lambda self: self.subtitle_view, constant=True)
    subtitlePlacementController = Property(QObject, lambda self: self.subtitle_placement, constant=True)
    subtitleTranscriptionController = Property(
        QObject,
        lambda self: self.subtitle_transcription,
        constant=True,
    )
    subtitleTranslationController = Property(
        QObject,
        lambda self: self.subtitle_translation,
        constant=True,
    )
    subtitleEditingController = Property(QObject, lambda self: self.subtitle_editing, constant=True)
    highlightController = Property(QObject, lambda self: self.highlights, constant=True)
    audioController = Property(QObject, lambda self: self.audio, constant=True)
    dubbingController = Property(QObject, lambda self: self.dubbing, constant=True)
    taskController = Property(QObject, lambda self: self.tasks, constant=True)
    exportController = Property(QObject, lambda self: self.export, constant=True)
    webController = Property(QObject, lambda self: self.web, constant=True)
    webTimelineController = Property(QObject, lambda self: self.web_timeline, constant=True)
    webDeliveryController = Property(QObject, lambda self: self.web_delivery, constant=True)
    automationController = Property(QObject, lambda self: self.automation, constant=True)

    def shutdown(self) -> None:
        self.workspace_project.shutdown()
