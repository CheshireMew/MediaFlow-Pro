from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .asset_projector import AssetProjector
from .audio_projector import AudioProjector
from .highlight_projector import HighlightProjector
from .subtitle_projector import SubtitleProjector
from .task_projector import TaskProjector
from .timeline_projector import TimelineProjector
from .workspace_projector import WorkspaceProjector

if TYPE_CHECKING:
    from mediaflow.desktop.controllers.project_controller import ProjectSession


@dataclass(frozen=True, slots=True)
class PresentationProjectors:
    session: ProjectSession
    workspace: WorkspaceProjector
    assets: AssetProjector
    timeline: TimelineProjector
    tasks: TaskProjector
    subtitles: SubtitleProjector
    highlights: HighlightProjector
    audio: AudioProjector

    @classmethod
    def create(cls, session: ProjectSession) -> PresentationProjectors:
        return cls(
            session=session,
            workspace=WorkspaceProjector(session),
            assets=AssetProjector(session),
            timeline=TimelineProjector(session),
            tasks=TaskProjector(session),
            subtitles=SubtitleProjector(session),
            highlights=HighlightProjector(session),
            audio=AudioProjector(session),
        )

    def refresh_project(self) -> None:
        self.assets.refresh_assets()
        self.timeline.refresh_sequences()
        self.tasks.refresh_tasks()
        self.refresh_active_sequence(refresh_sequences=False)
        self.workspace.refresh_recent_projects()
        self.session.updates.commit(workflow=True)

    def refresh_active_sequence(self, *, refresh_sequences: bool = False) -> None:
        if refresh_sequences:
            self.timeline.refresh_sequences()
        self.timeline.refresh_timeline()
        self.subtitles.refresh_documents()
        self.highlights.refresh_highlights()
        self.audio.refresh_audio_buses()
        self.audio.refresh_audio_metrics()
        self.timeline.refresh_preview_subtitles()
        self.session.updates.commit(project=True)
        self.session.updates.commit(history=True)
