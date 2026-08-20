from __future__ import annotations

from collections.abc import Callable

from mediaflow.application.edit_history import (
    ProjectEditAction,
    ProjectEditCommand,
    ProjectEditHistory,
)
from mediaflow.application.ports import TimelineEditorDocuments
from mediaflow.application.project_changes import (
    entity_sequence_change_set,
    timeline_change_set,
)
from mediaflow.application.timeline_clock import (
    project_frame_profile,
    reframe_timeline_clock,
)
from mediaflow.application.timeline_merge import TimelineMergePolicy
from mediaflow.application.timeline_rules import TimelineRules
from mediaflow.application.timeline_validator import TimelineValidator
from mediaflow.domain.collaboration import ProjectChangeSet
from mediaflow.domain.frame_clock import MainFrameClockSnapshot
from mediaflow.domain.project import ProjectProfile
from mediaflow.domain.timeline import TimelineMergeConflict, TimelineState
from mediaflow.domain.timeline_history import (
    TIMELINE_HISTORY_MODE,
    compact_timeline_change,
)


class TimelineChangeSession:
    """Own one timeline's mutable session, durable commits, and local history."""

    def __init__(
        self,
        repository: TimelineEditorDocuments,
        sequence_id: str,
        history: ProjectEditHistory | None = None,
    ) -> None:
        self.repository = repository
        self.sequence_id = sequence_id
        self.history = history or ProjectEditHistory()
        self._validator = TimelineValidator(repository)
        self._state = repository.timeline.load_timeline(sequence_id)
        self._history_action_kind = f"timeline.restore:{sequence_id}"
        self.history.register_handler(self._history_action_kind, self._apply_history_action)

    @property
    def current(self) -> TimelineState:
        """Return the immutable domain objects in the current editing snapshot."""

        return self._state

    @property
    def snapshot(self) -> TimelineState:
        return self.copy_state(self._state)

    def reload(self) -> TimelineState:
        self._state = self.repository.timeline.load_timeline(self.sequence_id)
        return self.snapshot

    def restore_snapshot(
        self,
        source: TimelineState,
        destination: TimelineState,
    ) -> TimelineState:
        if source.sequence.id != self.sequence_id or destination.sequence.id != self.sequence_id:
            raise ValueError("Timeline snapshot belongs to another sequence")
        self._state = self._apply_change(source, destination)
        return self.snapshot

    def set_sequence_profile(self, profile: ProjectProfile) -> TimelineState:
        old_profile = self._state.sequence.profile
        if profile == old_profile and self._state.sequence.profile_confirmed:
            return self.snapshot
        project = self.repository.projects.get_project()
        is_main_sequence = self.sequence_id == project.main_sequence_id
        source_snapshot = (
            self.repository.timeline.capture_main_frame_clock(self.sequence_id) if is_main_sequence else None
        )
        source_state = source_snapshot.timeline if source_snapshot is not None else self._state
        if source_snapshot is not None:
            session_state = self.copy_state(self._state)
            session_state.sequence = session_state.sequence.model_copy(update={"timeline_revision": 0})
            if session_state != source_snapshot.timeline:
                raise TimelineMergeConflict("main frame clock snapshot", self.sequence_id)

        change = reframe_timeline_clock(
            source_state,
            self.repository.assets.list_assets(),
            profile,
            asset_source_profile=project_frame_profile(self.repository.projects, self.repository.sequences),
            invalidate_proxies=is_main_sequence,
        )
        if source_snapshot is None:

            def mutate(state: TimelineState) -> None:
                state.sequence = change.state.sequence
                state.clips = list(change.state.clips)
                state.compounds = list(change.state.compounds)
                state.transitions = list(change.state.transitions)
                state.markers = list(change.state.markers)
                state.ranges = list(change.state.ranges)

            self.commit_change("修改序列配置", mutate, allow_locked_changes=True)
            return self.snapshot

        self._validator.validate(
            change.state,
            baseline=source_state,
            allow_locked_changes=True,
            assets={asset.id: asset for asset in change.assets},
        )
        destination_snapshot = self.repository.timeline.change_main_frame_clock(
            source_snapshot,
            change.state,
            list(change.assets),
            old_profile=old_profile,
        )
        self._state = self.repository.timeline.load_timeline(self.sequence_id)
        changes = ProjectChangeSet.combine(
            [
                timeline_change_set(source_snapshot.timeline, destination_snapshot.timeline),
                entity_sequence_change_set("/assets", source_snapshot.assets, destination_snapshot.assets),
                entity_sequence_change_set(
                    "/subtitles/segments",
                    source_snapshot.subtitle_segments,
                    destination_snapshot.subtitle_segments,
                ),
                entity_sequence_change_set(
                    "/subtitles/words",
                    source_snapshot.subtitle_words,
                    destination_snapshot.subtitle_words,
                ),
                entity_sequence_change_set(
                    "/highlights",
                    source_snapshot.highlights,
                    destination_snapshot.highlights,
                ),
                entity_sequence_change_set(
                    "/subtitles/track-links",
                    source_snapshot.subtitle_links,
                    destination_snapshot.subtitle_links,
                ),
                entity_sequence_change_set(
                    "/subtitles/placements",
                    source_snapshot.subtitle_placements,
                    destination_snapshot.subtitle_placements,
                ),
            ]
        )
        self.history.push(
            ProjectEditCommand(
                label="修改序列配置",
                undo_actions=[self._frame_clock_action(destination_snapshot, source_snapshot)],
                redo_actions=[self._frame_clock_action(source_snapshot, destination_snapshot)],
            ),
            changes,
        )
        return self.snapshot

    def commit_change(
        self,
        label: str,
        mutate: Callable[[TimelineState], None],
        *,
        allow_locked_changes: bool = False,
    ) -> None:
        before = self.copy_state(self._state)
        after = self.copy_state(before)
        mutate(after)
        TimelineRules.assign_default_primary_dialogue_track(after)
        TimelineRules.normalize_sequence_in_out(after)
        TimelineRules.normalize_compounds(after)
        self._validator.validate(
            after,
            baseline=before,
            allow_locked_changes=allow_locked_changes,
        )
        if after == before:
            return
        self._state = self._apply_change(before, after)
        before_patch, after_patch = compact_timeline_change(before, after)
        self.history.push(
            ProjectEditCommand(
                label=label,
                undo_actions=[self._timeline_action(after_patch, before_patch)],
                redo_actions=[self._timeline_action(before_patch, after_patch)],
            ),
            timeline_change_set(before, after),
        )

    def validate_preview(self, candidate: TimelineState) -> None:
        self._validator.validate(candidate, baseline=self._state)

    def undo(self) -> TimelineState:
        self.history.undo()
        return self.snapshot

    def redo(self) -> TimelineState:
        self.history.redo()
        return self.snapshot

    def _timeline_action(
        self,
        source: TimelineState,
        destination: TimelineState,
    ) -> ProjectEditAction:
        return ProjectEditAction(
            kind=self._history_action_kind,
            payload={
                "mode": TIMELINE_HISTORY_MODE,
                "source": source.model_dump(mode="json", exclude_computed_fields=True),
                "destination": destination.model_dump(mode="json", exclude_computed_fields=True),
            },
        )

    def _frame_clock_action(
        self,
        source: MainFrameClockSnapshot,
        destination: MainFrameClockSnapshot,
    ) -> ProjectEditAction:
        return ProjectEditAction(
            kind=self._history_action_kind,
            payload={
                "mode": "frame_clock",
                "source": source.model_dump(mode="json", exclude_computed_fields=True),
                "destination": destination.model_dump(mode="json", exclude_computed_fields=True),
            },
        )

    def _apply_history_action(self, action: ProjectEditAction) -> None:
        payload = action.payload
        mode = str(payload.get("mode") or "")
        if mode == TIMELINE_HISTORY_MODE:
            self.restore_snapshot(
                TimelineState.model_validate(payload.get("source")),
                TimelineState.model_validate(payload.get("destination")),
            )
            return
        if mode == "frame_clock":
            self.repository.timeline.restore_main_frame_clock(
                MainFrameClockSnapshot.model_validate(payload.get("source")),
                MainFrameClockSnapshot.model_validate(payload.get("destination")),
            )
            self.reload()
            return
        raise ValueError(f"Unknown timeline history action mode: {mode}")

    def _apply_change(
        self,
        source: TimelineState,
        destination: TimelineState,
    ) -> TimelineState:
        stored_sequence = self.repository.sequences.get_sequence(self.sequence_id)
        current = (
            self._state
            if stored_sequence.timeline_revision == self._state.sequence.timeline_revision
            else self.repository.timeline.load_timeline(self.sequence_id)
        )
        merged = self.canonical_state(TimelineMergePolicy.merge(source, destination, current))
        if merged == current:
            return current
        self._validator.validate(merged, baseline=self._state)
        return self._persist_change(current, merged)

    @staticmethod
    def copy_state(state: TimelineState) -> TimelineState:
        return state.model_copy(
            update={
                "tracks": list(state.tracks),
                "clips": list(state.clips),
                "compounds": list(state.compounds),
                "transitions": list(state.transitions),
                "markers": list(state.markers),
                "ranges": list(state.ranges),
                "web_states": dict(state.web_states),
            }
        )

    @staticmethod
    def canonical_state(state: TimelineState) -> TimelineState:
        return state.model_copy(
            update={
                "tracks": sorted(state.tracks, key=lambda item: (item.position, item.id)),
                "clips": sorted(state.clips, key=lambda item: (item.timeline_start, item.id)),
                "compounds": sorted(state.compounds, key=lambda item: item.id),
                "transitions": sorted(state.transitions, key=lambda item: item.id),
                "markers": sorted(state.markers, key=lambda item: (item.frame, item.id)),
                "ranges": sorted(state.ranges, key=lambda item: (item.start_frame, item.id)),
                "web_states": dict(sorted(state.web_states.items())),
            }
        )

    def _persist_change(
        self,
        before: TimelineState,
        after: TimelineState,
    ) -> TimelineState:
        before_clips = {clip.id: clip for clip in before.clips}
        after_clips = {clip.id: clip for clip in after.clips}
        graph_is_unchanged = (
            before.sequence == after.sequence
            and before.tracks == after.tracks
            and before.compounds == after.compounds
            and before.transitions == after.transitions
            and before.markers == after.markers
            and before.ranges == after.ranges
            and set(before_clips) == set(after_clips)
        )
        if graph_is_unchanged:
            changed_clip_ids = {
                clip_id for clip_id, clip in after_clips.items() if clip != before_clips[clip_id]
            }
            changed_web_states = [
                web_state
                for clip_id, web_state in after.web_states.items()
                if web_state != before.web_states.get(clip_id)
            ]
            revision = (
                self.repository.timeline.save_timeline(after)
                if changed_web_states
                else self.repository.timeline.save_clip_changes(after, changed_clip_ids)
            )
        else:
            revision = self.repository.timeline.save_timeline(after)
        return self.canonical_state(after).model_copy(
            update={"sequence": after.sequence.model_copy(update={"timeline_revision": revision})}
        )
