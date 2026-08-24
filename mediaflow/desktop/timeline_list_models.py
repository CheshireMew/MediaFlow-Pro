from __future__ import annotations

from typing import Any

from PySide6.QtCore import (
    QObject,
    Signal,
    Slot,
)

from .list_model_base import DictListModel


class TrackListModel(DictListModel):
    def __init__(self, parent: QObject | None = None):
        super().__init__(
            [
                "trackId",
                "name",
                "displayName",
                "kind",
                "position",
                "enabled",
                "locked",
                "muted",
                "solo",
                "audioBusId",
                "linkedAudioTrackId",
                "primaryDialogue",
            ],
            parent,
        )


class ClipListModel(DictListModel):
    def __init__(self, parent: QObject | None = None):
        super().__init__(
            [
                "clipId",
                "trackId",
                "trackPosition",
                "assetId",
                "assetName",
                "sourceIn",
                "startFrame",
                "durationFrames",
                "endFrame",
                "speed",
                "pitchCompensation",
                "mediaKind",
                "assetKind",
                "trackKind",
                "hasAudio",
                "audioTrackPosition",
                "waveformReady",
                "filmstripFrames",
                "x",
                "y",
                "scaleX",
                "scaleY",
                "rotation",
                "cropLeft",
                "cropTop",
                "cropRight",
                "cropBottom",
                "opacity",
                "gainDb",
                "pan",
                "fadeInFrames",
                "fadeOutFrames",
                "compoundId",
                "canDetachAudio",
                "transformKeyframeCount",
                "transformKeyframeSource",
            ],
            parent,
        )


class TimelineClipViewportModel(ClipListModel):
    """Bounded interactive projection of the clips around the visible viewport.

    The complete clip model remains the authoritative desktop projection. This
    model only controls how many heavyweight QML delegates are instantiated.
    At low zoom levels it keeps one representative per visual bucket while the
    QML overview canvas paints every clip.
    """

    _MIN_INTERACTIVE_WIDTH = 12.0
    _BUCKET_WIDTH = 18.0
    _MAX_INTERACTIVE_ROWS = 640

    sourceItemsReset = Signal()
    sourceItemsPatched = Signal(list, list)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._source_items: list[dict[str, Any]] = []
        self._source_indexes: dict[str, int] = {}
        self._selected_ids: set[str] = set()
        self._primary_selected_id = ""
        self._visible_start = 0.0
        self._visible_end = 1.0
        self._pixels_per_frame = 1.0

    def set_source_items(self, items: list[dict[str, Any]]) -> None:
        self._source_items = items
        self._source_indexes = {str(item["clipId"]): index for index, item in enumerate(items)}
        self._refresh()
        self.sourceItemsReset.emit()

    def update_source_items(self, items: list[dict[str, Any]]) -> bool:
        source_rows = {str(item["clipId"]): item for item in items}
        if any(key not in self._source_indexes for key in source_rows):
            return False
        membership_changed = False
        for key, item in source_rows.items():
            source_index = self._source_indexes[key]
            before = self._source_items[source_index]
            if key not in self._selected_ids and self._interactive_membership_key(
                before
            ) != self._interactive_membership_key(item):
                membership_changed = True
            self._source_items[source_index] = item
        if source_rows:
            self.sourceItemsPatched.emit(list(source_rows.values()), [])
        visible_items = [item for key, item in source_rows.items() if key in self._key_rows]
        if membership_changed:
            self._refresh()
        elif visible_items:
            self.update_items_by_key(visible_items, deferred=True)
        return True

    def patch_source_items(
        self,
        items: list[dict[str, Any]],
        *,
        removed_keys: set[str],
        ordered_keys: list[str],
        selected_ids: list[str] | None = None,
    ) -> bool:
        replacements = {str(item["clipId"]): item for item in items}
        if len(replacements) != len(items) or removed_keys & set(replacements):
            return False
        projected = {
            str(item["clipId"]): item
            for item in self._source_items
            if str(item["clipId"]) not in removed_keys
        }
        projected.update(replacements)
        if len(ordered_keys) != len(set(ordered_keys)) or set(projected) != set(ordered_keys):
            return False
        self._source_items = [projected[key] for key in ordered_keys]
        self._source_indexes = {
            str(item["clipId"]): index for index, item in enumerate(self._source_items)
        }
        if selected_ids is not None:
            self._selected_ids = set(selected_ids)
            self._primary_selected_id = selected_ids[-1] if selected_ids else ""
        self._refresh()
        self.sourceItemsPatched.emit(list(replacements.values()), sorted(removed_keys))
        return True

    def _interactive_membership_key(self, item: dict[str, Any]) -> tuple[object, ...] | None:
        overscan = max(1.0, (self._visible_end - self._visible_start) * 0.2)
        if (
            float(item["endFrame"]) < max(0.0, self._visible_start - overscan)
            or float(item["startFrame"]) > self._visible_end + overscan
        ):
            return None
        duration = int(item["durationFrames"])
        if duration * self._pixels_per_frame >= self._MIN_INTERACTIVE_WIDTH:
            return ("clip", str(item["clipId"]))
        bucket = int(
            (float(item["startFrame"]) - self._visible_start) * self._pixels_per_frame / self._BUCKET_WIDTH
        )
        return ("bucket", int(item["trackPosition"]), bucket, duration)

    def set_selected_ids(self, clip_ids: list[str]) -> None:
        selected = set(clip_ids)
        primary = clip_ids[-1] if clip_ids else ""
        if selected == self._selected_ids and primary == self._primary_selected_id:
            return
        self._selected_ids = selected
        self._primary_selected_id = primary
        self._refresh()

    def set_viewport(self, start_frame: float, end_frame: float, pixels_per_frame: float) -> None:
        start = max(0.0, float(start_frame))
        end = max(start + 1.0, float(end_frame))
        scale = max(0.000001, float(pixels_per_frame))
        if (
            abs(start - self._visible_start) < 0.5
            and abs(end - self._visible_end) < 0.5
            and abs(scale - self._pixels_per_frame) < max(0.000001, scale * 0.001)
        ):
            return
        self._visible_start = start
        self._visible_end = end
        self._pixels_per_frame = scale
        self._refresh()

    def _refresh(self) -> None:
        if not self._source_items:
            self.set_items([])
            return
        overscan = max(1.0, (self._visible_end - self._visible_start) * 0.2)
        visible_start = max(0.0, self._visible_start - overscan)
        visible_end = self._visible_end + overscan
        visible = [
            item
            for item in self._source_items
            if (float(item["endFrame"]) >= visible_start and float(item["startFrame"]) <= visible_end)
            or str(item["clipId"]) in self._selected_ids
        ]
        if len(visible) <= self._MAX_INTERACTIVE_ROWS:
            self.set_items(visible)
            return

        chosen: dict[str, dict[str, Any]] = {}
        buckets: dict[tuple[int, int], dict[str, Any]] = {}
        for item in visible:
            clip_id = str(item["clipId"])
            width = float(item["durationFrames"]) * self._pixels_per_frame
            if clip_id in self._selected_ids or width >= self._MIN_INTERACTIVE_WIDTH:
                chosen[clip_id] = item
                continue
            bucket = int(
                (float(item["startFrame"]) - self._visible_start)
                * self._pixels_per_frame
                / self._BUCKET_WIDTH
            )
            key = (int(item["trackPosition"]), bucket)
            existing = buckets.get(key)
            if existing is None or int(item["durationFrames"]) > int(existing["durationFrames"]):
                buckets[key] = item
        for item in buckets.values():
            chosen.setdefault(str(item["clipId"]), item)

        selected = [item for item in visible if str(item["clipId"]) in self._selected_ids]
        selected_ids = {str(item["clipId"]) for item in selected}
        remaining = [item for item in visible if str(item["clipId"]) in chosen]
        remaining.sort(
            key=lambda item: (
                str(item["clipId"]) != self._primary_selected_id,
                str(item["clipId"]) not in selected_ids,
                int(item["trackPosition"]),
                int(item["startFrame"]),
                str(item["clipId"]),
            )
        )
        kept_ids = {str(item["clipId"]) for item in remaining[: self._MAX_INTERACTIVE_ROWS]}
        self.set_items([item for item in visible if str(item["clipId"]) in kept_ids])

    @Slot(result="QVariantList")
    def overview(self) -> list[dict[str, Any]]:
        return [
            {
                "clipId": item["clipId"],
                "trackPosition": item["trackPosition"],
                "audioTrackPosition": item["audioTrackPosition"],
                "startFrame": item["startFrame"],
                "endFrame": item["endFrame"],
                "assetKind": item["assetKind"],
                "trackKind": item["trackKind"],
                "mediaKind": item["mediaKind"],
                "hasAudio": item["hasAudio"],
                "compoundId": item["compoundId"],
            }
            for item in self._source_items
        ]


class CompoundClipListModel(DictListModel):
    def __init__(self, parent: QObject | None = None):
        super().__init__(
            [
                "compoundId",
                "name",
                "primaryClipId",
                "memberClipIds",
                "memberCount",
                "trackId",
                "trackPosition",
                "trackKind",
                "startFrame",
                "endFrame",
                "durationFrames",
                "hasAudio",
            ],
            parent,
        )


class WebLayerListModel(DictListModel):
    def __init__(self, parent: QObject | None = None):
        super().__init__(
            [
                "layerId",
                "name",
                "kind",
                "parentId",
                "layerVisible",
                "allFieldsLocked",
                "keyframeCount",
            ],
            parent,
        )


class TransitionListModel(DictListModel):
    def __init__(self, parent: QObject | None = None):
        super().__init__(
            [
                "transitionId",
                "trackId",
                "trackPosition",
                "leftClipId",
                "rightClipId",
                "kind",
                "durationFrames",
                "boundaryFrame",
                "internalToCompound",
            ],
            parent,
        )


class TimelineMarkerListModel(DictListModel):
    def __init__(self, parent: QObject | None = None):
        super().__init__(["markerId", "frame", "name", "markerColor"], parent)


class TimelineRangeListModel(DictListModel):
    def __init__(self, parent: QObject | None = None):
        super().__init__(["rangeId", "startFrame", "endFrame", "name", "rangeColor"], parent)
