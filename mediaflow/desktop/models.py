from __future__ import annotations

from typing import Any

from PySide6.QtCore import QAbstractListModel, QByteArray, QModelIndex, QObject, Qt, Slot


class DictListModel(QAbstractListModel):
    def __init__(self, roles: list[str], parent: QObject | None = None):
        super().__init__(parent)
        if not roles or len(set(roles)) != len(roles):
            raise ValueError("Model roles must be non-empty and unique")
        self._roles = roles
        self._role_numbers = {Qt.UserRole + index + 1: role for index, role in enumerate(roles)}
        self._items: list[dict[str, Any]] = []

    def roleNames(self) -> dict[int, QByteArray]:
        return {number: QByteArray(name.encode("utf-8")) for number, name in self._role_numbers.items()}

    def rowCount(self, parent: QModelIndex | None = None) -> int:
        return 0 if parent is not None and parent.isValid() else len(self._items)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self._items):
            return None
        role_name = self._role_numbers.get(role)
        return self._items[index.row()].get(role_name) if role_name else None

    def set_items(self, items: list[dict[str, Any]]) -> None:
        expected = set(self._roles)
        for row, item in enumerate(items):
            actual = set(item)
            if actual != expected:
                missing = sorted(expected - actual)
                unexpected = sorted(actual - expected)
                raise ValueError(
                    f"Model row {row} does not match its declared roles; "
                    f"missing={missing}, unexpected={unexpected}"
                )
        key_role = self._roles[0]
        before_keys = [item.get(key_role) for item in self._items]
        after_keys = [item.get(key_role) for item in items]
        if (
            any(key is None for key in after_keys)
            or len(set(after_keys)) != len(after_keys)
            or len(set(before_keys)) != len(before_keys)
        ):
            self._reset_items(items)
            return
        if not self._items:
            if items:
                self.beginInsertRows(QModelIndex(), 0, len(items) - 1)
                self._items = list(items)
                self.endInsertRows()
            return
        if not set(before_keys).intersection(after_keys):
            self._reset_items(items)
            return

        wanted = set(after_keys)
        for row in range(len(self._items) - 1, -1, -1):
            if self._items[row].get(key_role) not in wanted:
                self.beginRemoveRows(QModelIndex(), row, row)
                self._items.pop(row)
                self.endRemoveRows()

        for destination, key in enumerate(after_keys):
            if destination < len(self._items) and self._items[destination].get(key_role) == key:
                continue
            source = next(
                (
                    row
                    for row in range(destination + 1, len(self._items))
                    if self._items[row].get(key_role) == key
                ),
                None,
            )
            if source is None:
                self.beginInsertRows(QModelIndex(), destination, destination)
                self._items.insert(destination, items[destination])
                self.endInsertRows()
            else:
                self.beginMoveRows(
                    QModelIndex(),
                    source,
                    source,
                    QModelIndex(),
                    destination,
                )
                self._items.insert(destination, self._items.pop(source))
                self.endMoveRows()

        role_by_name = {name: number for number, name in self._role_numbers.items()}
        for row, after in enumerate(items):
            before = self._items[row]
            changed_roles = [
                role_by_name[name] for name in self._roles if before.get(name) != after.get(name)
            ]
            if changed_roles:
                self._items[row] = after
                index = self.index(row, 0)
                self.dataChanged.emit(index, index, changed_roles)

    def _reset_items(self, items: list[dict[str, Any]]) -> None:
        self.beginResetModel()
        self._items = list(items)
        self.endResetModel()

    @Slot(int, result="QVariantMap")
    def get(self, row: int) -> dict[str, Any]:
        return dict(self._items[row]) if 0 <= row < len(self._items) else {}

    @Slot(str, str, result=int)
    def findRow(self, role: str, value: str) -> int:
        for index, item in enumerate(self._items):
            if str(item.get(role)) == value:
                return index
        return -1


class AssetListModel(DictListModel):
    def __init__(self, parent: QObject | None = None):
        super().__init__(
            [
                "assetId",
                "name",
                "kind",
                "path",
                "status",
                "managed",
                "durationFrames",
                "width",
                "height",
                "proxyReady",
                "waveformReady",
            ],
            parent,
        )


class SequenceListModel(DictListModel):
    def __init__(self, parent: QObject | None = None):
        super().__init__(["sequenceId", "name", "kind", "profile", "colorMode"], parent)


class RecentProjectListModel(DictListModel):
    def __init__(self, parent: QObject | None = None):
        super().__init__(
            [
                "name",
                "path",
                "available",
                "runningTaskCount",
                "failedTaskCount",
                "offlineAssetCount",
                "pendingWorkflowCount",
                "recentArtifact",
                "coverUrl",
            ],
            parent,
        )


class DownloadEntryListModel(DictListModel):
    def __init__(self, parent: QObject | None = None):
        super().__init__(
            [
                "entryIndex",
                "mediaId",
                "title",
                "pageUrl",
                "duration",
                "uploader",
                "available",
                "unavailableReason",
                "selected",
            ],
            parent,
        )


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
                "assetKind",
                "trackKind",
                "allowedTrackKinds",
                "hasAudio",
                "audioTrackPosition",
                "waveformReady",
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
            ],
            parent,
        )


class TimelineMarkerListModel(DictListModel):
    def __init__(self, parent: QObject | None = None):
        super().__init__(["markerId", "frame", "name", "markerColor"], parent)


class TimelineRangeListModel(DictListModel):
    def __init__(self, parent: QObject | None = None):
        super().__init__(["rangeId", "startFrame", "endFrame", "name", "rangeColor"], parent)


class TaskListModel(DictListModel):
    def __init__(self, parent: QObject | None = None):
        super().__init__(
            [
                "taskId",
                "displayName",
                "kind",
                "status",
                "statusLabel",
                "progress",
                "messageCode",
                "messageLabel",
                "queuePosition",
                "error",
                "artifacts",
                "executionTrace",
            ],
            parent,
        )


class SubtitleDocumentListModel(DictListModel):
    def __init__(self, parent: QObject | None = None):
        super().__init__(
            [
                "documentId",
                "assetId",
                "mediaAssetId",
                "language",
                "isSource",
                "sourceDocumentId",
                "segmentCount",
            ],
            parent,
        )


class SubtitleSegmentListModel(DictListModel):
    def __init__(self, parent: QObject | None = None):
        super().__init__(
            [
                "segmentId",
                "startFrame",
                "endFrame",
                "text",
                "speaker",
                "confidence",
                "hasOverlap",
            ],
            parent,
        )


class SubtitlePlacementListModel(DictListModel):
    def __init__(self, parent: QObject | None = None):
        super().__init__(
            [
                "placementId",
                "trackId",
                "segmentId",
                "clipId",
                "audioTrackPosition",
                "startFrame",
                "endFrame",
                "text",
                "sourceText",
                "hasOverride",
            ],
            parent,
        )


class GlossaryTermListModel(DictListModel):
    def __init__(self, parent: QObject | None = None):
        super().__init__(["termId", "source", "target", "note", "category"], parent)


class LlmProviderListModel(DictListModel):
    def __init__(self, parent: QObject | None = None):
        super().__init__(
            ["providerId", "name", "baseUrl", "apiKey", "model", "enabled", "active"],
            parent,
        )


class HighlightListModel(DictListModel):
    def __init__(self, parent: QObject | None = None):
        super().__init__(
            [
                "highlightId",
                "assetId",
                "documentId",
                "sequenceId",
                "startFrame",
                "endFrame",
                "title",
                "reason",
                "score",
                "selected",
            ],
            parent,
        )


class AudioBusListModel(DictListModel):
    def __init__(self, parent: QObject | None = None):
        super().__init__(
            [
                "busId",
                "name",
                "displayName",
                "parentBusId",
                "gainDb",
                "muted",
                "solo",
                "channelLayout",
            ],
            parent,
        )


class AudioEffectListModel(DictListModel):
    def __init__(self, parent: QObject | None = None):
        super().__init__(
            [
                "effectId",
                "busId",
                "kind",
                "displayName",
                "position",
                "enabled",
                "parameters",
            ],
            parent,
        )


class AudioEffectParameterListModel(DictListModel):
    def __init__(self, parent: QObject | None = None):
        super().__init__(
            ["key", "label", "value", "minimum", "maximum", "step", "unit", "valueType"],
            parent,
        )
