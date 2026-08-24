from __future__ import annotations

import re

from PySide6.QtCore import (
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QSortFilterProxyModel,
    Slot,
)

from .list_model_base import DictListModel


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


class MediaResourceListModel(DictListModel):
    def __init__(self, parent: QObject | None = None):
        super().__init__(
            [
                "resourceKey",
                "category",
                "name",
                "description",
                "provider",
                "tags",
                "capabilities",
                "previewType",
                "previewUrl",
                "license",
                "adoptionType",
                "adoptionTarget",
                "presetId",
                "parameters",
                "defaultDurationFrames",
                "adoptionPath",
                "featuredRank",
                "isFavorite",
                "canAdopt",
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
        self._extra_search_text: dict[str, str] = {}
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

    def set_extra_search_text(self, values: dict[str, str]) -> None:
        normalized = {str(key): value.casefold() for key, value in values.items()}
        if normalized == self._extra_search_text:
            return
        self.beginFilterChange()
        self._extra_search_text = normalized
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
        corpus = " ".join(
            (
                str(row.get("searchText") or row.get("name") or ""),
                self._extra_search_text.get(str(row.get("assetId") or ""), ""),
            )
        )
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
