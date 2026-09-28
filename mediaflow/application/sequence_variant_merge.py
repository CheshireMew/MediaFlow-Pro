from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, TypeVar, cast

from mediaflow.domain.audio import AudioBus, AudioEffect
from mediaflow.domain.model_base import DomainModel
from mediaflow.domain.sequence_variants import (
    SequenceVariantChange,
    SequenceVariantConflict,
)
from mediaflow.domain.subtitles import SubtitlePlacement
from mediaflow.domain.timeline import TimelineState

_MISSING = object()
_ModelT = TypeVar("_ModelT", bound=DomainModel)


@dataclass(frozen=True, slots=True)
class VariantMergeResult:
    timeline: TimelineState
    audio_buses: list[AudioBus]
    audio_effects: list[AudioEffect]
    subtitle_placements: list[SubtitlePlacement]
    changes: list[SequenceVariantChange]
    conflicts: list[SequenceVariantConflict]


class SequenceVariantMerger:
    """Three-way merge source refreshes into one stable delivery sequence."""

    def __init__(
        self,
        resolutions: Mapping[str, str] | None = None,
        *,
        take_source_on_conflict: bool = False,
    ) -> None:
        self.resolutions = dict(resolutions or {})
        self.take_source_on_conflict = take_source_on_conflict
        self.changes: list[SequenceVariantChange] = []
        self.conflicts: list[SequenceVariantConflict] = []

    def merge(
        self,
        *,
        baseline_timeline: TimelineState,
        current_timeline: TimelineState,
        source_timeline: TimelineState,
        baseline_audio_buses: list[AudioBus],
        current_audio_buses: list[AudioBus],
        source_audio_buses: list[AudioBus],
        baseline_audio_effects: list[AudioEffect],
        current_audio_effects: list[AudioEffect],
        source_audio_effects: list[AudioEffect],
        baseline_subtitle_placements: list[SubtitlePlacement],
        current_subtitle_placements: list[SubtitlePlacement],
        source_subtitle_placements: list[SubtitlePlacement],
    ) -> VariantMergeResult:
        baseline = self._normalized_timeline(baseline_timeline, current_timeline)
        current = self._normalized_timeline(current_timeline, current_timeline)
        incoming = self._normalized_timeline(source_timeline, current_timeline)
        timeline_payload = self._value(baseline, current, incoming, "/timeline")
        timeline = TimelineState.model_validate(timeline_payload)
        return VariantMergeResult(
            timeline=timeline,
            audio_buses=self._model_list(
                baseline_audio_buses,
                current_audio_buses,
                source_audio_buses,
                "/audio/buses",
            ),
            audio_effects=self._model_list(
                baseline_audio_effects,
                current_audio_effects,
                source_audio_effects,
                "/audio/effects",
            ),
            subtitle_placements=self._model_list(
                baseline_subtitle_placements,
                current_subtitle_placements,
                source_subtitle_placements,
                "/subtitles/placements",
            ),
            changes=self.changes,
            conflicts=self.conflicts,
        )

    @staticmethod
    def _normalized_timeline(source: TimelineState, current: TimelineState) -> dict[str, Any]:
        payload = source.model_dump(mode="python", exclude_computed_fields=True)
        current_sequence = current.sequence.model_dump(
            mode="python", exclude_computed_fields=True
        )
        for field in ("id", "project_id", "kind", "position", "timeline_revision", "created_at"):
            payload["sequence"][field] = current_sequence[field]
        return payload

    def _model_list(
        self,
        baseline: list[_ModelT],
        current: list[_ModelT],
        incoming: list[_ModelT],
        path: str,
    ) -> list[_ModelT]:
        examples = [item for values in (current, incoming, baseline) for item in values]
        if not examples:
            return []
        merged = self._value(
            [item.model_dump(mode="python", exclude_computed_fields=True) for item in baseline],
            [item.model_dump(mode="python", exclude_computed_fields=True) for item in current],
            [item.model_dump(mode="python", exclude_computed_fields=True) for item in incoming],
            path,
        )
        model_type = type(examples[0])
        return [cast(_ModelT, model_type.model_validate(item)) for item in merged]

    def _value(self, baseline: Any, local: Any, incoming: Any, path: str) -> Any:
        if local == incoming:
            return local
        if local == baseline:
            self._change(path, local, incoming)
            return incoming
        if incoming == baseline:
            return local
        if all(isinstance(item, dict) for item in (baseline, local, incoming)):
            keys = set(baseline) | set(local) | set(incoming)
            merged: dict[str, Any] = {}
            for key in sorted(keys):
                value = self._value(
                    baseline.get(key, _MISSING),
                    local.get(key, _MISSING),
                    incoming.get(key, _MISSING),
                    f"{path}/{key}",
                )
                if value is not _MISSING:
                    merged[key] = value
            return merged
        if all(isinstance(item, list) for item in (baseline, local, incoming)) and self._entity_lists(
            baseline, local, incoming
        ):
            return self._entity_list(baseline, local, incoming, path)
        return self._conflict(path, baseline, local, incoming)

    def _entity_list(
        self,
        baseline: list[dict[str, Any]],
        local: list[dict[str, Any]],
        incoming: list[dict[str, Any]],
        path: str,
    ) -> list[dict[str, Any]]:
        baseline_by_id = {str(item["id"]): item for item in baseline}
        local_by_id = {str(item["id"]): item for item in local}
        incoming_by_id = {str(item["id"]): item for item in incoming}
        ids = set(baseline_by_id) | set(local_by_id) | set(incoming_by_id)
        merged_by_id: dict[str, dict[str, Any]] = {}
        for item_id in sorted(ids):
            value = self._value(
                baseline_by_id.get(item_id, _MISSING),
                local_by_id.get(item_id, _MISSING),
                incoming_by_id.get(item_id, _MISSING),
                f"{path}/{item_id}",
            )
            if value is not _MISSING:
                merged_by_id[item_id] = value

        baseline_order = [str(item["id"]) for item in baseline]
        local_order = [str(item["id"]) for item in local if str(item["id"]) in merged_by_id]
        incoming_order = [str(item["id"]) for item in incoming if str(item["id"]) in merged_by_id]
        if (
            local_order != baseline_order
            and incoming_order != baseline_order
            and local_order != incoming_order
        ):
            order = self._conflict(f"{path}/@order", baseline_order, local_order, incoming_order)
        elif local_order == baseline_order:
            order = incoming_order
            if order != local_order:
                self.changes.append(SequenceVariantChange(path=f"{path}/@order", action="reorder"))
        else:
            order = local_order
        for item_id in [*incoming_order, *local_order, *sorted(merged_by_id)]:
            if item_id in merged_by_id and item_id not in order:
                order.append(item_id)
        return [merged_by_id[item_id] for item_id in order]

    def _conflict(self, path: str, baseline: Any, local: Any, incoming: Any) -> Any:
        resolution = self.resolutions.get(path)
        if resolution not in {None, "keep_local", "take_source"}:
            raise ValueError(f"未知的交付版本冲突解决方式：{resolution}")
        if resolution == "take_source" or self.take_source_on_conflict:
            self._change(path, local, incoming)
            return incoming
        if resolution == "keep_local":
            return local
        self.conflicts.append(
            SequenceVariantConflict(
                path=path,
                baseline=self._evidence(baseline),
                local=self._evidence(local),
                source=self._evidence(incoming),
            )
        )
        return local

    def _change(self, path: str, local: Any, incoming: Any) -> None:
        if local == incoming:
            return
        if local is _MISSING:
            action: Literal["add", "remove", "update"] = "add"
        elif incoming is _MISSING:
            action = "remove"
        else:
            action = "update"
        self.changes.append(SequenceVariantChange(path=path, action=action))

    @staticmethod
    def _entity_lists(*values: list[Any]) -> bool:
        combined = [item for value in values for item in value]
        return bool(combined) and all(isinstance(item, dict) and "id" in item for item in combined)

    @staticmethod
    def _evidence(value: Any) -> Any:
        return {"missing": True} if value is _MISSING else value
