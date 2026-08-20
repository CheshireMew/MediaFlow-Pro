from __future__ import annotations

import re
from typing import Any

from PySide6.QtCore import (
    QAbstractListModel,
    QByteArray,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QSortFilterProxyModel,
    Qt,
    QTimer,
    Slot,
)

_INVALID_MODEL_INDEX = QModelIndex()


class DictListModel(QAbstractListModel):
    def __init__(self, roles: list[str], parent: QObject | None = None):
        super().__init__(parent)
        if not roles or len(set(roles)) != len(roles):
            raise ValueError("Model roles must be non-empty and unique")
        self._roles = roles
        self._role_numbers = {
            int(Qt.ItemDataRole.UserRole) + index + 1: role for index, role in enumerate(roles)
        }
        self._items: list[dict[str, Any]] = []
        self._key_rows: dict[str, int] = {}
        self._deferred_role_changes: dict[int, set[int]] = {}
        self._deferred_change_scheduled = False

    def roleNames(self) -> dict[int, QByteArray]:
        return {number: QByteArray(name.encode("utf-8")) for number, name in self._role_numbers.items()}

    def rowCount(
        self,
        parent: QModelIndex | QPersistentModelIndex = _INVALID_MODEL_INDEX,
    ) -> int:
        return 0 if parent.isValid() else len(self._items)

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = int(Qt.ItemDataRole.DisplayRole),
    ) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self._items):
            return None
        role_name = self._role_numbers.get(role)
        return self._items[index.row()].get(role_name) if role_name else None

    def set_items(self, items: list[dict[str, Any]]) -> None:
        self._validate_items(items)
        self._deferred_role_changes.clear()
        self._set_items(items)

    def set_items_deferred(self, items: list[dict[str, Any]]) -> None:
        """Publish stable-row value changes on the next Qt event turn."""

        self._validate_items(items)
        key_role = self._roles[0]
        before_keys = [item.get(key_role) for item in self._items]
        after_keys = [item.get(key_role) for item in items]
        if before_keys != after_keys or any(key is None for key in after_keys):
            self.set_items(items)
            return

        role_by_name = {name: number for number, name in self._role_numbers.items()}
        for row, after in enumerate(items):
            before = self._items[row]
            changed_roles = {
                role_by_name[name] for name in self._roles if before.get(name) != after.get(name)
            }
            if changed_roles:
                self._items[row] = after
                self._deferred_role_changes.setdefault(row, set()).update(changed_roles)
        if self._deferred_role_changes and not self._deferred_change_scheduled:
            self._deferred_change_scheduled = True
            QTimer.singleShot(0, self._publish_deferred_changes)

    def _validate_items(self, items: list[dict[str, Any]]) -> None:
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

    def _publish_deferred_changes(self) -> None:
        changes = self._deferred_role_changes
        self._deferred_role_changes = {}
        self._deferred_change_scheduled = False
        for row, roles in sorted(changes.items()):
            if row >= len(self._items):
                continue
            index = self.index(row, 0)
            self.dataChanged.emit(index, index, sorted(roles))

    def _set_items(self, items: list[dict[str, Any]]) -> None:
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
                self._rebuild_key_rows()
                self.endInsertRows()
            return
        if not set(before_keys).intersection(after_keys):
            self._reset_items(items)
            return

        self._key_rows = {}
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

        self._rebuild_key_rows()
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
        self._rebuild_key_rows()
        self.endResetModel()

    def _rebuild_key_rows(self) -> None:
        key_role = self._roles[0]
        self._key_rows = {str(item[key_role]): row for row, item in enumerate(self._items)}

    @Slot(int, result="QVariantMap")
    def get(self, row: int) -> dict[str, Any]:
        return dict(self._items[row]) if 0 <= row < len(self._items) else {}

    @Slot(result="QVariantList")
    def snapshot(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._items]

    @Slot(str, str, result=int)
    def findRow(self, role: str, value: str) -> int:
        if role == self._roles[0] and self._key_rows:
            return self._key_rows.get(str(value), -1)
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
                "binId",
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
        self._bin_id = ""
        self._bin_ids: set[str] = set()
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

    def set_bin_scope(self, bin_id: str, bin_ids: set[str]) -> None:
        normalized = bin_id.strip()
        if normalized == self._bin_id and bin_ids == self._bin_ids:
            return
        self.beginFilterChange()
        self._bin_id = normalized
        self._bin_ids = set(bin_ids)
        self.endFilterChange(QSortFilterProxyModel.Direction.Rows)

    def filterAcceptsRow(
        self,
        source_row: int,
        source_parent: QModelIndex | QPersistentModelIndex,
    ) -> bool:
        source = self.sourceModel()
        if not isinstance(source, AssetListModel):
            return False
        row = source.get(source_row)
        if self._bin_id == "__unfiled__" and row.get("binId"):
            return False
        if self._bin_id not in {"", "__unfiled__"} and row.get("binId") not in self._bin_ids:
            return False
        if not self._search_text:
            return True
        corpus = str(row.get("searchText") or row.get("name") or "")
        return self.matches_text(self._search_text, corpus)

    @classmethod
    def matches_text(cls, query: str, corpus: str) -> bool:
        corpus = corpus.casefold()
        if query in corpus:
            return True
        query_terms = set(re.findall(r"[a-z0-9]+|[\u3400-\u9fff]+", query))
        if not query_terms:
            return False
        corpus_terms = set(re.findall(r"[a-z0-9]+|[\u3400-\u9fff]+", corpus))
        expanded_corpus = set(corpus_terms)
        for concept in cls._CONCEPTS:
            if any(term in corpus for term in concept):
                expanded_corpus.update(concept)
        return all(
            any(
                query == candidate or query in candidate or candidate in query
                for candidate in expanded_corpus
            )
            or any(query in concept and bool(concept & expanded_corpus) for concept in cls._CONCEPTS)
            for query in query_terms
        )


class AssetBinListModel(DictListModel):
    def __init__(self, parent: QObject | None = None):
        super().__init__(
            [
                "binId",
                "name",
                "parentId",
                "position",
                "depth",
                "displayName",
                "assetCount",
            ],
            parent,
        )


class AssetMomentListModel(DictListModel):
    def __init__(self, parent: QObject | None = None):
        super().__init__(
            [
                "momentId",
                "assetId",
                "assetName",
                "momentType",
                "label",
                "detail",
                "startFrame",
                "endFrame",
                "previewUrl",
                "searchText",
            ],
            parent,
        )


class AssetMomentFilterModel(QSortFilterProxyModel):
    def __init__(self, source_model: AssetMomentListModel, parent: QObject | None = None):
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

    def filterAcceptsRow(
        self,
        source_row: int,
        source_parent: QModelIndex | QPersistentModelIndex,
    ) -> bool:
        if not self._search_text:
            return False
        source = self.sourceModel()
        if not isinstance(source, AssetMomentListModel):
            return False
        row = source.get(source_row)
        return AssetFilterModel.matches_text(
            self._search_text,
            str(row.get("searchText") or row.get("label") or ""),
        )


class SequenceListModel(DictListModel):
    def __init__(self, parent: QObject | None = None):
        super().__init__(
            ["sequenceId", "name", "displayName", "kind", "profile", "colorMode"],
            parent,
        )


class RecentProjectListModel(DictListModel):
    def __init__(self, parent: QObject | None = None):
        super().__init__(
            [
                "name",
                "path",
                "available",
                "unavailableReason",
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


class TaskListModel(DictListModel):
    def __init__(self, parent: QObject | None = None):
        super().__init__(
            [
                "taskId",
                "displayName",
                "configurationLabel",
                "encoderFallbackUsed",
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
            ["key", "descriptor", "value", "options"],
            parent,
        )
