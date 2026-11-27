from __future__ import annotations

from dataclasses import dataclass

from mediaflow.desktop.component_contexts import PresentationRefreshContext

from .asset_projector import AssetProjector
from .audio_projector import AudioProjector
from .highlight_projector import HighlightProjector
from .subtitle_projector import SubtitleProjector
from .task_projector import TaskProjector
from .timeline_projector import TimelineProjector
from .timeline_subtitle_projector import TimelineSubtitleProjector
from .workspace_projector import WorkspaceProjector


@dataclass(frozen=True, slots=True)
class PresentationProjectors:
    context: PresentationRefreshContext
    workspace: WorkspaceProjector
    assets: AssetProjector
    timeline: TimelineProjector
    timeline_subtitles: TimelineSubtitleProjector
    tasks: TaskProjector
    subtitles: SubtitleProjector
    highlights: HighlightProjector
    audio: AudioProjector

    def refresh_project(self) -> None:
        self.context.invalidate_active_workflow()
        self.context.refresh_active_workflow()
        self.assets.refresh_assets(refresh_timeline=False)
        self.timeline.refresh_sequences()
        self.tasks.refresh_tasks()
        self.refresh_active_sequence(refresh_sequences=False)
        self.workspace.refresh_recent_projects()
        self.context.updates.commit(workflow=True)

    def refresh_active_sequence(self, *, refresh_sequences: bool = False) -> None:
        if refresh_sequences:
            self.timeline.refresh_sequences()
        # Project opening performs several synchronous projections after the
        # timeline rows are ready. Start the native-graph idle window only when
        # all of them are complete so a large graph cannot become overdue while
        # the UI thread is still constructing the workspace.
        self.timeline.refresh_timeline(schedule_preview=False)
        self.subtitles.refresh_documents()
        self.highlights.refresh_highlights()
        self.audio.refresh_audio_buses()
        self.audio.refresh_audio_metrics()
        self.timeline_subtitles.refresh_preview_subtitles()
        self.context.rebuild_snapping()
        self.context.updates.commit(project=True)
        self.context.updates.commit(history=True)
        self.context.schedule_preview()
