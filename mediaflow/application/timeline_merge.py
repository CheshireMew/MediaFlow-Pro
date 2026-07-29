from __future__ import annotations

from mediaflow.domain.timeline import TimelineMergeConflict, TimelineState
from mediaflow.domain.web_media import WebClipState


class TimelineMergePolicy:
    @classmethod
    def merge(
        cls,
        source: TimelineState,
        destination: TimelineState,
        current: TimelineState,
    ) -> TimelineState:
        source_sequence = source.sequence.model_copy(update={"timeline_revision": 0})
        destination_sequence = destination.sequence.model_copy(update={"timeline_revision": 0})
        current_sequence = current.sequence.model_copy(update={"timeline_revision": 0})
        if source_sequence == destination_sequence:
            merged_sequence = current.sequence
        elif current_sequence == source_sequence:
            merged_sequence = destination.sequence.model_copy(
                update={"timeline_revision": current.sequence.timeline_revision}
            )
        elif current_sequence == destination_sequence:
            merged_sequence = current.sequence
        else:
            raise TimelineMergeConflict("sequence", current.sequence.id)

        return current.model_copy(
            update={
                "sequence": merged_sequence,
                "tracks": cls._merge_entity_list(
                    "track",
                    source.tracks,
                    destination.tracks,
                    current.tracks,
                ),
                "clips": cls._merge_entity_list(
                    "clip",
                    source.clips,
                    destination.clips,
                    current.clips,
                ),
                "compounds": cls._merge_entity_list(
                    "compound",
                    source.compounds,
                    destination.compounds,
                    current.compounds,
                ),
                "transitions": cls._merge_entity_list(
                    "transition",
                    source.transitions,
                    destination.transitions,
                    current.transitions,
                ),
                "markers": cls._merge_entity_list(
                    "marker",
                    source.markers,
                    destination.markers,
                    current.markers,
                ),
                "ranges": cls._merge_entity_list(
                    "range",
                    source.ranges,
                    destination.ranges,
                    current.ranges,
                ),
                "web_states": cls._merge_entity_map(
                    "web state",
                    source.web_states,
                    destination.web_states,
                    current.web_states,
                ),
            }
        )

    @staticmethod
    def _merge_entity_list(
        entity_name: str,
        source: list,
        destination: list,
        current: list,
    ) -> list:
        source_by_id = {item.id: item for item in source}
        destination_by_id = {item.id: item for item in destination}
        current_by_id = {item.id: item for item in current}
        changed_ids = {
            item_id
            for item_id in set(source_by_id) | set(destination_by_id)
            if source_by_id.get(item_id) != destination_by_id.get(item_id)
        }
        merged_by_id = dict(current_by_id)
        for item_id in changed_ids:
            before = source_by_id.get(item_id)
            after = destination_by_id.get(item_id)
            present = current_by_id.get(item_id)
            if present == after:
                continue
            if present != before:
                raise TimelineMergeConflict(entity_name, item_id)
            if after is None:
                merged_by_id.pop(item_id, None)
            else:
                merged_by_id[item_id] = after

        merged: list = []
        emitted: set[str] = set()
        for item in destination:
            if item.id in merged_by_id and item.id not in emitted:
                merged.append(merged_by_id[item.id])
                emitted.add(item.id)
        for item in current:
            if item.id in merged_by_id and item.id not in emitted:
                merged.append(merged_by_id[item.id])
                emitted.add(item.id)
        return merged

    @staticmethod
    def _merge_entity_map(
        entity_name: str,
        source: dict[str, WebClipState],
        destination: dict[str, WebClipState],
        current: dict[str, WebClipState],
    ) -> dict[str, WebClipState]:
        merged = dict(current)
        for item_id in set(source) | set(destination):
            before = source.get(item_id)
            after = destination.get(item_id)
            if before == after or current.get(item_id) == after:
                continue
            if current.get(item_id) != before:
                raise TimelineMergeConflict(entity_name, item_id)
            if after is None:
                merged.pop(item_id, None)
            else:
                merged[item_id] = after
        return merged
