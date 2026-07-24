from __future__ import annotations

import re
from typing import Any

from PySide6.QtCore import (
    QAbstractListModel,
    QByteArray,
    QModelIndex,
    QObject,
    QSortFilterProxyModel,
    Qt,
    Slot,
)


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
                "previewUrl",
                "proxyReady",
                "waveformReady",
                "searchText",
            ],
            parent,
        )


class AssetFilterModel(QSortFilterProxyModel):
    _CONCEPTS = (
        {"night", "nighttime", "夜", "夜晚", "夜景", "黑夜"},
        {"city", "urban", "城市", "都市", "街道", "街景"},
        {"interview", "talking", "dialogue", "访谈", "采访", "对话", "口播"},
        {"people", "person", "human", "人物", "人像", "人", "主体"},
        {"nature", "outdoor", "landscape", "自然", "户外", "风景", "景观"},
        {"food", "cooking", "meal", "食物", "美食", "烹饪", "餐饮"},
        {"technology", "tech", "digital", "科技", "技术", "数码"},
        {"business", "office", "work", "商业", "办公", "工作"},
        {"music", "concert", "音乐", "演出", "演唱会"},
        {"sport", "sports", "fitness", "运动", "体育", "健身"},
        {"vertical", "portrait", "竖屏", "纵向"},
        {"horizontal", "landscape", "横屏", "横向"},
        {"video", "footage", "clip", "视频", "镜头", "片段"},
        {"audio", "sound", "voice", "音频", "声音", "语音"},
        {"image", "photo", "picture", "图片", "照片", "图像"},
    )

    def __init__(self, source_model: AssetListModel, parent: QObject | None = None):
        super().__init__(parent)
        self._search_text = ""
        self.setDynamicSortFilter(True)
        self.setSourceModel(source_model)

    @Slot(str)
    def setSearchText(self, value: str) -> None:
        normalized = value.strip().casefold()
        if normalized == self._search_text:
            return
        self.beginFilterChange()
        self._search_text = normalized
        self.endFilterChange(QSortFilterProxyModel.Direction.Rows)

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        if not self._search_text:
            return True
        source = self.sourceModel()
        if not isinstance(source, AssetListModel):
            return False
        row = source.get(source_row)
        corpus = str(row.get("searchText") or row.get("name") or "").casefold()
        if self._search_text in corpus:
            return True
        query_terms = set(re.findall(r"[a-z0-9]+|[\u3400-\u9fff]+", self._search_text))
        if not query_terms:
            return False
        corpus_terms = set(re.findall(r"[a-z0-9]+|[\u3400-\u9fff]+", corpus))
        expanded_corpus = set(corpus_terms)
        for concept in self._CONCEPTS:
            if any(term in corpus for term in concept):
                expanded_corpus.update(concept)
        return all(
            any(
                query == candidate
                or query in candidate
                or candidate in query
                for candidate in expanded_corpus
            )
            or any(query in concept and bool(concept & expanded_corpus) for concept in self._CONCEPTS)
            for query in query_terms
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
                "compoundId",
                "canDetachAudio",
                "transformKeyframeCount",
                "transformKeyframeSource",
            ],
            parent,
        )


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
                "editable",
                "content",
                "color",
                "fontFamily",
                "fontSize",
                "image",
                "x",
                "y",
                "width",
                "height",
                "rotation",
                "opacity",
                "zIndex",
                "layerVisible",
                "lockedFields",
                "allFieldsLocked",
                "keyframeCount",
                "enterMs",
                "exitMs",
                "delayMs",
                "durationMs",
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


class TaskListModel(DictListModel):
    def __init__(self, parent: QObject | None = None):
        super().__init__(
            [
                "taskId",
                "displayName",
                "configurationLabel",
                "commandType",
                "kind",
                "status",
                "statusLabel",
                "progressMode",
                "progressValue",
                "progressCompleted",
                "progressTotal",
                "progressUnit",
                "hasOverallProgress",
                "overallProgressValue",
                "overallProgressCompleted",
                "overallProgressTotal",
                "overallProgressUnit",
                "progressItemIndex",
                "progressItemTotal",
                "progressItemLabel",
                "messageCode",
                "messageLabel",
                "queuePosition",
                "inputAssetIds",
                "contextId",
                "error",
                "artifacts",
                "executionTrace",
                "createdAt",
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
                "sequenceId",
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
                "documentId",
                "segmentId",
                "clipId",
                "audioTrackPosition",
                "startFrame",
                "endFrame",
                "text",
                "sourceText",
                "hasOverride",
                "timingOverridden",
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
                "sourceSequenceId",
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
