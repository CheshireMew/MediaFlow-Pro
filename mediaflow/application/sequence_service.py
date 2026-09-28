from __future__ import annotations

from mediaflow.application.ports import SequenceServiceDocuments
from mediaflow.application.sequence_copy_planner import (
    PreparedShortSequence,
    SequenceCopyPlanner,
)
from mediaflow.application.sequence_variant_merge import SequenceVariantMerger
from mediaflow.domain.enums import AssetKind
from mediaflow.domain.model_base import now_ms
from mediaflow.domain.project import ProjectProfile, Sequence
from mediaflow.domain.sequence_variants import (
    SequenceVariantGeneration,
    SequenceVariantPlan,
    SequenceVariantPlanItem,
    SequenceVariantRecord,
    SequenceVariantSnapshot,
    SequenceVariantSpec,
)
from mediaflow.domain.timeline import (
    Clip,
    ClipTransform,
    TimelineRange,
    TimelineState,
)


class SequenceService:
    def __init__(self, repository: SequenceServiceDocuments):
        self.repository = repository
        self._copy_planner = SequenceCopyPlanner(repository)

    def create_short_from_range(
        self,
        source_sequence_id: str,
        range_id: str,
        *,
        name: str | None = None,
    ) -> Sequence:
        return self.commit_prepared_short(
            self.prepare_short_from_range(
                source_sequence_id,
                range_id,
                name=name,
            )
        )

    def create_short_from_bounds(
        self,
        source_sequence_id: str,
        start_frame: int,
        end_frame: int,
        *,
        name: str | None = None,
    ) -> Sequence:
        return self.commit_prepared_short(
            self.prepare_short_from_bounds(
                source_sequence_id,
                start_frame,
                end_frame,
                name=name,
            )
        )

    def sync_short_from_bounds(
        self,
        source_sequence_id: str,
        short_sequence_id: str,
        start_frame: int,
        end_frame: int,
        *,
        name: str | None = None,
    ) -> Sequence:
        return self.commit_prepared_short(
            self.prepare_short_from_bounds(
                source_sequence_id,
                start_frame,
                end_frame,
                name=name,
                destination_sequence=(self.repository.sequences.get_sequence(short_sequence_id)),
            )
        )

    def list_variants(
        self,
        source_sequence_id: str | None = None,
        *,
        include_archived: bool = False,
    ) -> list[SequenceVariantRecord]:
        return self.repository.sequences.list_sequence_variants(
            source_sequence_id,
            include_archived=include_archived,
        )

    def generate_variants(
        self,
        source_sequence_id: str,
        specs: list[SequenceVariantSpec],
        *,
        force: bool = False,
        conflict_resolutions: dict[str, str] | None = None,
    ) -> SequenceVariantGeneration:
        if not specs:
            raise ValueError("至少选择一个交付版本")
        preset_ids = [item.preset_id for item in specs]
        if len(preset_ids) != len(set(preset_ids)):
            raise ValueError("同一次生成不能包含重复的版本预设")
        try:
            self.repository.sequences.get_sequence_variant(source_sequence_id)
        except KeyError:
            pass
        else:
            raise ValueError("交付版本不能继续作为其它版本的母版")

        source = self.repository.timeline.load_timeline(source_sequence_id)
        if source.duration_frames <= 0:
            raise ValueError("母版序列没有可派生的时间线内容")
        active_records = self.repository.sequences.list_sequence_variants(
            source_sequence_id,
            include_archived=False,
        )
        by_preset = {item.preset_id: item for item in active_records}
        records: list[SequenceVariantRecord] = []
        sequences: list[Sequence] = []
        created_ids: list[str] = []
        reused_ids: list[str] = []
        refreshed_ids: list[str] = []
        plans: list[SequenceVariantPlanItem] = []

        with self.repository.transaction():
            for spec in specs:
                existing_record = by_preset.get(spec.preset_id)
                existing_sequence = (
                    self.repository.sequences.get_sequence(existing_record.sequence_id)
                    if existing_record is not None
                    else None
                )
                if existing_record is not None and existing_sequence is not None and self._variant_is_current(
                    existing_record, existing_sequence, source, spec
                ) and not force:
                    current_record = existing_record
                    if existing_record.baseline_timeline is None:
                        current_state = self.repository.timeline.load_timeline(existing_sequence.id)
                        buses, effects, placements = self._variant_artifacts(current_state)
                        current_record = self.repository.sequences.save_sequence_variant(
                            existing_record.model_copy(
                                update={
                                    "baseline_timeline": current_state,
                                    "baseline_audio_buses": buses,
                                    "baseline_audio_effects": effects,
                                    "baseline_subtitle_placements": placements,
                                    "updated_at": now_ms(),
                                }
                            )
                        )
                    records.append(current_record)
                    sequences.append(existing_sequence)
                    reused_ids.append(existing_sequence.id)
                    plans.append(
                        SequenceVariantPlanItem(
                            preset_id=spec.preset_id,
                            sequence_id=existing_sequence.id,
                            action="reuse",
                            source_timeline_revision=source.sequence.timeline_revision,
                        )
                    )
                    continue

                destination_profile = source.sequence.profile.model_copy(
                    update={"width": spec.width, "height": spec.height}
                )
                destination_sequence = existing_sequence or self.repository.sequences.prepare_short_sequence(
                    spec.name, destination_profile
                )
                prepared = self._prepare_variant(
                    source,
                    destination_sequence,
                    spec,
                    destination_is_new=existing_sequence is None,
                )
                baseline_prepared = prepared
                plan: SequenceVariantPlanItem
                if existing_sequence is not None:
                    if existing_record is None or existing_record.baseline_timeline is None:
                        raise ValueError(
                            f"交付版本 {existing_sequence.name} 缺少同步基线；请先在变更计划中确认采用母版"
                        )
                    current_state = self.repository.timeline.load_timeline(existing_sequence.id)
                    current_buses, current_effects, current_placements = self._variant_artifacts(
                        current_state
                    )
                    merger = SequenceVariantMerger(
                        conflict_resolutions,
                        take_source_on_conflict=force,
                    )
                    merged = merger.merge(
                        baseline_timeline=existing_record.baseline_timeline,
                        current_timeline=current_state,
                        source_timeline=prepared.state,
                        baseline_audio_buses=existing_record.baseline_audio_buses,
                        current_audio_buses=current_buses,
                        source_audio_buses=list(prepared.audio_buses),
                        baseline_audio_effects=existing_record.baseline_audio_effects,
                        current_audio_effects=current_effects,
                        source_audio_effects=list(prepared.audio_effects),
                        baseline_subtitle_placements=existing_record.baseline_subtitle_placements,
                        current_subtitle_placements=current_placements,
                        source_subtitle_placements=list(prepared.subtitle_placements),
                    )
                    plan = SequenceVariantPlanItem(
                        preset_id=spec.preset_id,
                        sequence_id=existing_sequence.id,
                        action="conflict" if merged.conflicts else "refresh",
                        source_timeline_revision=source.sequence.timeline_revision,
                        changes=merged.changes,
                        conflicts=merged.conflicts,
                    )
                    if merged.conflicts:
                        paths = "、".join(item.path for item in merged.conflicts[:5])
                        raise ValueError(f"交付版本同步存在未解决冲突：{paths}")
                    prepared = PreparedShortSequence(
                        state=merged.timeline,
                        audio_buses=tuple(merged.audio_buses),
                        audio_effects=tuple(merged.audio_effects),
                        subtitle_placements=tuple(merged.subtitle_placements),
                        new_sequence=False,
                    )
                else:
                    plan = SequenceVariantPlanItem(
                        preset_id=spec.preset_id,
                        sequence_id=destination_sequence.id,
                        action="create",
                        source_timeline_revision=source.sequence.timeline_revision,
                    )
                created = self.commit_prepared_short(prepared)
                record = self.repository.sequences.save_sequence_variant(
                    SequenceVariantRecord(
                        sequence_id=created.id,
                        source_sequence_id=source_sequence_id,
                        preset_id=spec.preset_id,
                        reframe_mode=spec.reframe_mode,
                        source_timeline_revision=source.sequence.timeline_revision,
                        baseline_timeline=baseline_prepared.state,
                        baseline_audio_buses=list(baseline_prepared.audio_buses),
                        baseline_audio_effects=list(baseline_prepared.audio_effects),
                        baseline_subtitle_placements=list(
                            baseline_prepared.subtitle_placements
                        ),
                        created_at=(
                            existing_record.created_at
                            if existing_record is not None
                            else now_ms()
                        ),
                        updated_at=now_ms(),
                    )
                )
                records.append(record)
                sequences.append(created)
                plans.append(plan)
                if existing_sequence is None:
                    created_ids.append(created.id)
                else:
                    refreshed_ids.append(created.id)

        return SequenceVariantGeneration(
            records=records,
            sequences=sequences,
            created_sequence_ids=created_ids,
            archived_sequence_ids=[],
            reused_sequence_ids=reused_ids,
            refreshed_sequence_ids=refreshed_ids,
            plans=plans,
        )

    def plan_variants(
        self,
        source_sequence_id: str,
        specs: list[SequenceVariantSpec],
    ) -> SequenceVariantPlan:
        if not specs:
            raise ValueError("至少选择一个交付版本")
        source = self.repository.timeline.load_timeline(source_sequence_id)
        active = {
            item.preset_id: item
            for item in self.repository.sequences.list_sequence_variants(
                source_sequence_id, include_archived=False
            )
        }
        items: list[SequenceVariantPlanItem] = []
        for spec in specs:
            record = active.get(spec.preset_id)
            if record is None:
                items.append(
                    SequenceVariantPlanItem(
                        preset_id=spec.preset_id,
                        action="create",
                        source_timeline_revision=source.sequence.timeline_revision,
                    )
                )
                continue
            sequence = self.repository.sequences.get_sequence(record.sequence_id)
            if self._variant_is_current(record, sequence, source, spec):
                items.append(
                    SequenceVariantPlanItem(
                        preset_id=spec.preset_id,
                        sequence_id=sequence.id,
                        action="reuse",
                        source_timeline_revision=source.sequence.timeline_revision,
                    )
                )
                continue
            if record.baseline_timeline is None:
                from mediaflow.domain.sequence_variants import SequenceVariantConflict

                items.append(
                    SequenceVariantPlanItem(
                        preset_id=spec.preset_id,
                        sequence_id=sequence.id,
                        action="conflict",
                        source_timeline_revision=source.sequence.timeline_revision,
                        conflicts=[
                            SequenceVariantConflict(
                                path="/baseline",
                                baseline={"missing": True},
                                local="existing variant",
                                source="refreshed master",
                            )
                        ],
                    )
                )
                continue
            prepared = self._prepare_variant(source, sequence, spec)
            current = self.repository.timeline.load_timeline(sequence.id)
            current_buses, current_effects, current_placements = self._variant_artifacts(current)
            merged = SequenceVariantMerger().merge(
                baseline_timeline=record.baseline_timeline,
                current_timeline=current,
                source_timeline=prepared.state,
                baseline_audio_buses=record.baseline_audio_buses,
                current_audio_buses=current_buses,
                source_audio_buses=list(prepared.audio_buses),
                baseline_audio_effects=record.baseline_audio_effects,
                current_audio_effects=current_effects,
                source_audio_effects=list(prepared.audio_effects),
                baseline_subtitle_placements=record.baseline_subtitle_placements,
                current_subtitle_placements=current_placements,
                source_subtitle_placements=list(prepared.subtitle_placements),
            )
            items.append(
                SequenceVariantPlanItem(
                    preset_id=spec.preset_id,
                    sequence_id=sequence.id,
                    action="conflict" if merged.conflicts else "refresh",
                    source_timeline_revision=source.sequence.timeline_revision,
                    changes=merged.changes,
                    conflicts=merged.conflicts,
                )
            )
        return SequenceVariantPlan(source_sequence_id=source_sequence_id, items=items)

    @staticmethod
    def _variant_is_current(
        record: SequenceVariantRecord,
        sequence: Sequence,
        source: TimelineState,
        spec: SequenceVariantSpec,
    ) -> bool:
        return (
            record.source_timeline_revision == source.sequence.timeline_revision
            and record.reframe_mode == spec.reframe_mode
            and sequence.name == spec.name
            and sequence.profile.width == spec.width
            and sequence.profile.height == spec.height
        )

    def _prepare_variant(
        self,
        source: TimelineState,
        destination_sequence: Sequence,
        spec: SequenceVariantSpec,
        *,
        destination_is_new: bool = False,
    ) -> PreparedShortSequence:
        selection = TimelineRange(
            sequence_id=source.sequence.id,
            start_frame=0,
            end_frame=source.duration_frames,
            name=spec.name,
        )
        destination_profile = source.sequence.profile.model_copy(
            update={"width": spec.width, "height": spec.height}
        )
        prepared_destination = destination_sequence.model_copy(
            update={"profile": destination_profile}
        )
        prepared = self._copy_planner.prepare(
            source,
            selection,
            name=spec.name,
            destination_sequence=prepared_destination,
            destination_is_new=destination_is_new,
            identity_namespace=destination_sequence.id,
        )
        prepared.state.sequence = prepared.state.sequence.model_copy(
            update={"profile": destination_profile}
        )
        prepared.state.clips = self._reframe_variant_clips(
            prepared.state.clips, destination_profile, spec
        )
        return prepared

    def _variant_artifacts(self, state: TimelineState) -> tuple[list, list, list]:
        buses = self.repository.audio.list_audio_buses(state.sequence.id)
        effects = [
            effect
            for bus in buses
            for effect in self.repository.audio.list_audio_effects(bus.id)
        ]
        placements = [
            placement
            for track in state.tracks
            for placement in self.repository.subtitles.list_subtitle_placements(track.id)
        ]
        return buses, effects, placements

    def snapshot_variant(self, sequence_id: str) -> SequenceVariantSnapshot:
        state = self.repository.timeline.load_timeline(sequence_id)
        buses, effects, placements = self._variant_artifacts(state)
        return SequenceVariantSnapshot(
            timeline=state,
            audio_buses=buses,
            audio_effects=effects,
            subtitle_placements=placements,
            record=self.repository.sequences.get_sequence_variant(sequence_id),
        )

    def restore_variant_snapshot(
        self, snapshot: SequenceVariantSnapshot
    ) -> SequenceVariantRecord:
        current_sequence = self.repository.sequences.get_sequence(
            snapshot.timeline.sequence.id
        )
        timeline = snapshot.timeline.model_copy(
            update={
                "sequence": snapshot.timeline.sequence.model_copy(
                    update={"timeline_revision": current_sequence.timeline_revision}
                )
            }
        )
        self.commit_prepared_short(
            PreparedShortSequence(
                state=timeline,
                audio_buses=tuple(snapshot.audio_buses),
                audio_effects=tuple(snapshot.audio_effects),
                subtitle_placements=tuple(snapshot.subtitle_placements),
                new_sequence=False,
            )
        )
        return self.repository.sequences.save_sequence_variant(snapshot.record)

    def apply_history_action(self, action) -> None:
        if action.kind != "sequence.variant-state":
            raise ValueError(f"Unsupported sequence variant history action: {action.kind}")
        self.restore_variant_snapshot(
            SequenceVariantSnapshot.model_validate(action.payload["snapshot"])
        )

    def _reframe_variant_clips(
        self,
        clips: list[Clip],
        profile: ProjectProfile,
        spec: SequenceVariantSpec,
    ) -> list[Clip]:
        if spec.reframe_mode == "fit":
            return clips
        assets = {item.id: item for item in self.repository.assets.list_assets()}
        reframed: list[Clip] = []
        for clip in clips:
            asset = assets[clip.asset_id]
            if asset.kind not in {AssetKind.VIDEO, AssetKind.IMAGE}:
                reframed.append(clip)
                continue
            width = asset.metadata.width or profile.width
            height = asset.metadata.height or profile.height
            source_ratio = width / height
            target_ratio = profile.width / profile.height
            zoom = max(source_ratio / target_ratio, target_ratio / source_ratio, 1.0)
            if zoom <= 1.000001:
                reframed.append(clip)
                continue
            reframed.append(
                clip.model_copy(
                    update={
                        "transform": self._compose_center_fill(clip.transform, zoom),
                        "transform_keyframes": [
                            item.model_copy(
                                update={
                                    "transform": self._compose_center_fill(
                                        item.transform,
                                        zoom,
                                    )
                                }
                            )
                            for item in clip.transform_keyframes
                        ],
                    }
                )
            )
        return reframed

    @staticmethod
    def _compose_center_fill(transform: ClipTransform, zoom: float) -> ClipTransform:
        offset = (100.0 - zoom * 100.0) / 2.0
        return transform.model_copy(
            update={
                "x": offset + transform.x * zoom,
                "y": offset + transform.y * zoom,
                "scale_x": transform.scale_x * zoom,
                "scale_y": transform.scale_y * zoom,
            }
        )

    def prepare_short_from_range(
        self,
        source_sequence_id: str,
        range_id: str,
        *,
        name: str | None = None,
    ) -> PreparedShortSequence:
        source = self.repository.timeline.load_timeline(source_sequence_id)
        try:
            selected = next(item for item in source.ranges if item.id == range_id)
        except StopIteration as error:
            raise KeyError(range_id) from error
        return self._prepare_copy_selection(source, selected, name=name)

    def prepare_short_from_bounds(
        self,
        source_sequence_id: str,
        start_frame: int,
        end_frame: int,
        *,
        name: str | None = None,
        destination_sequence: Sequence | None = None,
    ) -> PreparedShortSequence:
        source, selected = self._bounded_selection(
            source_sequence_id,
            start_frame,
            end_frame,
            name=name,
        )
        return self._prepare_copy_selection(
            source,
            selected,
            name=name,
            destination_sequence=destination_sequence,
        )

    def _bounded_selection(
        self,
        source_sequence_id: str,
        start_frame: int,
        end_frame: int,
        *,
        name: str | None,
    ) -> tuple[TimelineState, TimelineRange]:
        source = self.repository.timeline.load_timeline(source_sequence_id)
        start = max(0, int(start_frame))
        end = min(source.duration_frames, int(end_frame))
        if end <= start:
            raise ValueError("短视频区间必须落在源时间轴内")
        return source, TimelineRange(
            sequence_id=source_sequence_id,
            start_frame=start,
            end_frame=end,
            name=(name or "短视频").strip() or "短视频",
        )

    def _prepare_copy_selection(
        self,
        source: TimelineState,
        selected: TimelineRange,
        *,
        name: str | None,
        destination_sequence: Sequence | None = None,
    ) -> PreparedShortSequence:
        return self._copy_planner.prepare(
            source,
            selected,
            name=name,
            destination_sequence=destination_sequence,
        )

    def commit_prepared_short(
        self,
        prepared: PreparedShortSequence,
    ) -> Sequence:
        sequence = prepared.state.sequence
        with self.repository.transaction():
            if prepared.new_sequence:
                self.repository.sequences.commit_short_sequence(sequence)
            self.repository.audio.replace_audio_graph(
                sequence.id,
                list(prepared.audio_buses),
                list(prepared.audio_effects),
            )
            self.repository.timeline.save_timeline(prepared.state)
            self.repository.subtitles.add_subtitle_placements(list(prepared.subtitle_placements))
        return self.repository.sequences.get_sequence(sequence.id)
