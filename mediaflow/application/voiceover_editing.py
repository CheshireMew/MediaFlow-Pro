from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Literal, Protocol

from mediaflow.domain.collaboration import (
    ProjectChange,
    ProjectChangeSet,
    ProjectEditAction,
    ProjectEditCommand,
)
from mediaflow.domain.enums import AssetOrigin, ClipMediaKind, TrackKind
from mediaflow.domain.model_base import new_id
from mediaflow.domain.timeline import Clip, TimelineState, Track
from mediaflow.domain.voiceover import (
    VoiceoverCue,
    VoiceoverCueStatus,
    VoiceoverLatencyCalibration,
    VoiceoverTake,
)

from .asset_service import AssetService
from .edit_history import ProjectEditHistory
from .project_service_ports import VoiceoverEditingDocuments
from .timeline_clock import asset_in_timeline_clock
from .timeline_editor import TimelineEditor
from .timeline_rules import TimelineRules
from .voiceover_latency import analyze_latency_recording


class VoiceoverTakeStoragePort(Protocol):
    def store(self, cue_id: str, take_id: str, source_value: str | Path) -> Path: ...
    def archive_uncommitted(self, path: Path, take_id: str) -> Path | None: ...


class VoiceoverEditingService:
    def __init__(
        self,
        repository: VoiceoverEditingDocuments,
        assets: AssetService,
        timeline_provider: Callable[[str], TimelineEditor],
        history: ProjectEditHistory,
        storage: VoiceoverTakeStoragePort,
    ) -> None:
        self.repository = repository
        self.assets = assets
        self.timeline_provider = timeline_provider
        self.history = history
        self.storage = storage

    def list_cues(
        self,
        sequence_id: str,
        *,
        include_archived: bool = False,
    ) -> list[VoiceoverCue]:
        return self.repository.voiceover.list_cues(
            sequence_id,
            include_archived=include_archived,
        )

    def get_latency_calibration(self, device_id: str) -> VoiceoverLatencyCalibration | None:
        try:
            return self.repository.voiceover.get_latency_calibration(device_id)
        except KeyError:
            return None

    def list_latency_calibrations(self) -> list[VoiceoverLatencyCalibration]:
        return self.repository.voiceover.list_latency_calibrations()

    def set_latency_calibration(
        self,
        *,
        device_id: str,
        device_name: str,
        latency_samples: int,
        sample_rate: int = 48_000,
    ) -> VoiceoverLatencyCalibration:
        return self.repository.voiceover.save_latency_calibration(
            VoiceoverLatencyCalibration(
                device_id=device_id,
                device_name=device_name,
                latency_samples=latency_samples,
                sample_rate=sample_rate,
                confidence=1.0,
                method="manual",
            )
        )

    def analyze_latency_calibration(
        self,
        recording: str | Path,
        *,
        device_id: str,
        device_name: str,
        marker_offset_samples: int,
    ) -> VoiceoverLatencyCalibration:
        calibration = analyze_latency_recording(
            recording,
            device_id=device_id,
            device_name=device_name,
            marker_offset_samples=marker_offset_samples,
        )
        return self.repository.voiceover.save_latency_calibration(calibration)

    def create_cue(
        self,
        sequence_id: str,
        *,
        start_frame: int,
        end_frame: int,
        text: str,
        speaker: str = "旁白",
        notes: str = "",
    ) -> VoiceoverCue:
        state = self.timeline_provider(sequence_id).state
        if state.duration_frames <= 0:
            raise ValueError("空时间线不能创建旁白提示")
        if not 0 <= start_frame < end_frame <= state.duration_frames:
            raise ValueError("旁白提示必须位于当前时间线内容范围内")
        cue = self.repository.voiceover.create_cue(
            VoiceoverCue(
                sequence_id=sequence_id,
                start_frame=start_frame,
                end_frame=end_frame,
                text=text.strip(),
                speaker=speaker.strip() or "旁白",
                notes=notes.strip(),
            )
        )
        archived = cue.model_copy(update={"archived": True})
        self._push_cue_state("创建旁白提示", archived, cue, action="create")
        return cue

    def update_cue(
        self,
        cue_id: str,
        *,
        expected_revision: int,
        start_frame: int,
        end_frame: int,
        text: str,
        speaker: str,
        notes: str,
        status: VoiceoverCueStatus,
    ) -> VoiceoverCue:
        before = self.repository.voiceover.get_cue(cue_id)
        state = self.timeline_provider(before.sequence_id).state
        if not 0 <= start_frame < end_frame <= state.duration_frames:
            raise ValueError("旁白提示必须位于当前时间线内容范围内")
        candidate = before.model_copy(
            update={
                "start_frame": start_frame,
                "end_frame": end_frame,
                "text": text.strip(),
                "speaker": speaker.strip() or "旁白",
                "notes": notes.strip(),
                "status": status,
            }
        )
        after = self.repository.voiceover.save_cue(
            candidate,
            expected_revision=expected_revision,
        )
        self._push_cue_state("更新旁白提示", before, after)
        return after

    def archive_cue(self, cue_id: str, *, expected_revision: int) -> VoiceoverCue:
        before = self.repository.voiceover.get_cue(cue_id)
        after = self.repository.voiceover.save_cue(
            before.model_copy(update={"archived": True, "status": "planned"}),
            expected_revision=expected_revision,
        )
        self._push_cue_state("归档旁白提示", before, after, action="delete")
        return after

    def add_take(
        self,
        cue_id: str,
        source: str | Path,
        *,
        name: str | None = None,
        notes: str = "",
        calibration_device_id: str | None = None,
    ) -> tuple[VoiceoverCue, VoiceoverTake]:
        before_cue = self.repository.voiceover.get_cue(cue_id)
        if before_cue.archived:
            raise ValueError("已归档的旁白提示不能添加 take")
        take_id = new_id()
        stored = self.storage.store(cue_id, take_id, source)
        try:
            prepared = self.assets.prepare_output(stored, AssetOrigin.GENERATED)

            def archive_on_rollback(_error: BaseException) -> None:
                self.storage.archive_uncommitted(stored, take_id)

            with self.repository.transaction():
                self.repository.enlist_transaction_publication(
                    on_commit=lambda: None,
                    on_rollback=archive_on_rollback,
                )
                asset = self.assets.commit_prepared(prepared)
                sequence = self.repository.sequences.get_sequence(before_cue.sequence_id)
                timed_asset = asset_in_timeline_clock(
                    self.repository.projects,
                    self.repository.sequences,
                    asset,
                    sequence,
                )
                if timed_asset.metadata.duration_frames <= 0:
                    raise ValueError("旁白 take 没有可用的音频时长")
                calibration = (
                    self.get_latency_calibration(calibration_device_id)
                    if calibration_device_id
                    else None
                )
                if calibration_device_id and calibration is None:
                    raise ValueError("当前录音设备还没有可用的延迟校准")
                take = self.repository.voiceover.create_take(
                    VoiceoverTake(
                        id=take_id,
                        cue_id=cue_id,
                        asset_id=asset.id,
                        name=(name or f"Take {len(before_cue.takes) + 1}").strip(),
                        duration_frames=timed_asset.metadata.duration_frames,
                        notes=notes.strip(),
                        latency_compensation_samples=(
                            calibration.latency_samples if calibration is not None else 0
                        ),
                        latency_sample_rate=(
                            calibration.sample_rate if calibration is not None else 48_000
                        ),
                        calibration_device_id=(
                            calibration.device_id if calibration is not None else None
                        ),
                        calibration_measured_at=(
                            calibration.measured_at if calibration is not None else None
                        ),
                    )
                )
                after_cue = self.repository.voiceover.save_cue(
                    before_cue.model_copy(
                        update={
                            "status": "review",
                            "selected_take_id": take.id,
                            "takes": [*before_cue.takes, take],
                        }
                    ),
                    expected_revision=before_cue.revision,
                )
        except BaseException:
            self.storage.archive_uncommitted(stored, take_id)
            raise
        archived_take = take.model_copy(update={"archived": True})
        self._push_take_and_cue_state(
            "添加旁白 take",
            before_cue,
            after_cue,
            archived_take,
            take,
            action="create",
        )
        return after_cue, take

    def update_take(
        self,
        take_id: str,
        *,
        name: str,
        notes: str,
        rating: int,
    ) -> VoiceoverTake:
        before = self.repository.voiceover.get_take(take_id)
        after = self.repository.voiceover.save_take(
            before.model_copy(
                update={"name": name.strip(), "notes": notes.strip(), "rating": rating}
            )
        )
        self._push_take_state("更新旁白 take", before, after)
        return after

    def select_take(self, cue_id: str, take_id: str, *, expected_revision: int) -> VoiceoverCue:
        before = self.repository.voiceover.get_cue(cue_id)
        take = self.repository.voiceover.get_take(take_id)
        if take.cue_id != cue_id or take.archived:
            raise ValueError("所选 take 不属于当前旁白提示")
        after = self.repository.voiceover.save_cue(
            before.model_copy(update={"selected_take_id": take_id, "status": "review"}),
            expected_revision=expected_revision,
        )
        self._push_cue_state("选择旁白 take", before, after)
        return after

    def archive_take(self, take_id: str) -> tuple[VoiceoverCue, VoiceoverTake]:
        before_take = self.repository.voiceover.get_take(take_id)
        before_cue = self.repository.voiceover.get_cue(before_take.cue_id)
        after_take = self.repository.voiceover.save_take(
            before_take.model_copy(update={"archived": True})
        )
        cue_candidate = before_cue.model_copy(
            update={
                "selected_take_id": (
                    None if before_cue.selected_take_id == take_id else before_cue.selected_take_id
                ),
                "takes": [after_take if item.id == take_id else item for item in before_cue.takes],
            }
        )
        after_cue = self.repository.voiceover.save_cue(
            cue_candidate,
            expected_revision=before_cue.revision,
        )
        self._push_take_and_cue_state(
            "归档旁白 take",
            before_cue,
            after_cue,
            before_take,
            after_take,
            action="delete",
        )
        return after_cue, after_take

    def place_selected_take(self, cue_id: str, *, expected_revision: int) -> VoiceoverCue:
        before_cue = self.repository.voiceover.get_cue(cue_id)
        if before_cue.revision != expected_revision:
            raise RuntimeError("旁白提示已被其它编辑更新，请刷新后重试")
        if before_cue.selected_take_id is None:
            raise ValueError("请先选择一个旁白 take")
        take = self.repository.voiceover.get_take(before_cue.selected_take_id)
        editor = self.timeline_provider(before_cue.sequence_id)
        before_state = editor.state
        state = before_state.model_copy(deep=True)
        state.clips = [item for item in state.clips if item.id != before_cue.placed_clip_id]
        profile = state.sequence.profile
        compensation_frames = round(
            take.latency_compensation_samples
            * profile.fps_numerator
            / (take.latency_sample_rate * profile.fps_denominator)
        )
        if compensation_frames >= take.duration_frames:
            raise ValueError("旁白 take 的延迟补偿已超过可用录音时长")
        duration = min(
            take.duration_frames - compensation_frames,
            before_cue.end_frame - before_cue.start_frame,
        )
        track = self._placement_track(state, before_cue.start_frame, duration)
        clip = Clip(
            track_id=track.id,
            asset_id=take.asset_id,
            timeline_start=before_cue.start_frame,
            source_in=compensation_frames,
            duration=duration,
            media_kind=ClipMediaKind.AUDIO_ONLY,
        )
        state.clips.append(clip)
        checkpoint = self.history.checkpoint()
        try:
            with self.repository.transaction():
                editor.replace_contents(state, label="放置旁白 take")
                after_cue = self.repository.voiceover.save_cue(
                    before_cue.model_copy(update={"placed_clip_id": clip.id}),
                    expected_revision=before_cue.revision,
                )
        except BaseException:
            self.history.restore(checkpoint)
            editor.reload()
            raise
        combined = self.history.combined_since(checkpoint, label="放置旁白 take")
        changes = self.history.change_set_since(checkpoint)
        self.history.restore(checkpoint)
        if combined is None:
            raise RuntimeError("旁白 take 没有产生时间线编辑")
        self.history.push(
            ProjectEditCommand(
                label="放置旁白 take",
                undo_actions=[
                    *combined.undo_actions,
                    ProjectEditAction(
                        kind="voiceover.cue-state",
                        payload={"cue": before_cue.model_dump(mode="json")},
                    ),
                ],
                redo_actions=[
                    *combined.redo_actions,
                    ProjectEditAction(
                        kind="voiceover.cue-state",
                        payload={"cue": after_cue.model_dump(mode="json")},
                    ),
                ],
            ),
            ProjectChangeSet.combine(
                [
                    changes,
                    ProjectChangeSet(
                        changes=[
                            ProjectChange(
                                path=f"/voiceover/{cue_id}",
                                action="update",
                                value=after_cue.model_dump(mode="json"),
                            )
                        ]
                    ),
                ]
            ),
        )
        return after_cue

    def apply_history_action(self, action: ProjectEditAction) -> None:
        if action.kind == "voiceover.cue-state":
            self.repository.voiceover.restore_cue(
                VoiceoverCue.model_validate(action.payload["cue"])
            )
            return
        if action.kind == "voiceover.take-state":
            self.repository.voiceover.save_take(
                VoiceoverTake.model_validate(action.payload["take"])
            )
            return
        raise ValueError(f"未知的旁白历史动作：{action.kind}")

    def _placement_track(self, state: TimelineState, start: int, duration: int) -> Track:
        candidates = [
            item
            for item in state.tracks
            if item.kind == TrackKind.AUDIO
            and not item.locked
            and item.name.startswith("旁白 / ADR")
        ]
        track = next(
            (
                item
                for item in candidates
                if TimelineRules.interval_available(state, item.id, start, duration)
            ),
            None,
        )
        if track is not None:
            return track
        dialogue = next(
            (
                item
                for item in self.repository.audio.list_audio_buses(state.sequence.id)
                if item.name == "对白"
            ),
            None,
        )
        track = Track(
            sequence_id=state.sequence.id,
            name=f"旁白 / ADR {len(candidates) + 1}",
            kind=TrackKind.AUDIO,
            position=len(state.tracks),
            audio_bus_id=dialogue.id if dialogue is not None else None,
        )
        state.tracks.append(track)
        return track

    def _push_cue_state(
        self,
        label: str,
        before: VoiceoverCue,
        after: VoiceoverCue,
        *,
        action: Literal["create", "update", "delete"] = "update",
    ) -> None:
        self.history.push(
            ProjectEditCommand(
                label=label,
                undo_actions=[
                    ProjectEditAction(
                        kind="voiceover.cue-state",
                        payload={"cue": before.model_dump(mode="json")},
                    )
                ],
                redo_actions=[
                    ProjectEditAction(
                        kind="voiceover.cue-state",
                        payload={"cue": after.model_dump(mode="json")},
                    )
                ],
            ),
            ProjectChangeSet(
                changes=[
                    ProjectChange(
                        path=f"/voiceover/{after.id}",
                        action=action,
                        value=after.model_dump(mode="json"),
                    )
                ]
            ),
        )

    def _push_take_state(
        self,
        label: str,
        before: VoiceoverTake,
        after: VoiceoverTake,
        *,
        action: Literal["create", "update", "delete"] = "update",
    ) -> None:
        self.history.push(
            ProjectEditCommand(
                label=label,
                undo_actions=[
                    ProjectEditAction(
                        kind="voiceover.take-state",
                        payload={"take": before.model_dump(mode="json")},
                    )
                ],
                redo_actions=[
                    ProjectEditAction(
                        kind="voiceover.take-state",
                        payload={"take": after.model_dump(mode="json")},
                    )
                ],
            ),
            ProjectChangeSet(
                changes=[
                    ProjectChange(
                        path=f"/voiceover/{after.cue_id}/takes/{after.id}",
                        action=action,
                        value=after.model_dump(mode="json"),
                    )
                ]
            ),
        )

    def _push_take_and_cue_state(
        self,
        label: str,
        before_cue: VoiceoverCue,
        after_cue: VoiceoverCue,
        before_take: VoiceoverTake,
        after_take: VoiceoverTake,
        *,
        action: Literal["create", "update", "delete"],
    ) -> None:
        self.history.push(
            ProjectEditCommand(
                label=label,
                undo_actions=[
                    ProjectEditAction(
                        kind="voiceover.take-state",
                        payload={"take": before_take.model_dump(mode="json")},
                    ),
                    ProjectEditAction(
                        kind="voiceover.cue-state",
                        payload={"cue": before_cue.model_dump(mode="json")},
                    ),
                ],
                redo_actions=[
                    ProjectEditAction(
                        kind="voiceover.take-state",
                        payload={"take": after_take.model_dump(mode="json")},
                    ),
                    ProjectEditAction(
                        kind="voiceover.cue-state",
                        payload={"cue": after_cue.model_dump(mode="json")},
                    ),
                ],
            ),
            ProjectChangeSet(
                changes=[
                    ProjectChange(
                        path=f"/voiceover/{after_cue.id}",
                        action="update",
                        value=after_cue.model_dump(mode="json"),
                    ),
                    ProjectChange(
                        path=f"/voiceover/{after_cue.id}/takes/{after_take.id}",
                        action=action,
                        value=after_take.model_dump(mode="json"),
                    ),
                ]
            ),
        )
