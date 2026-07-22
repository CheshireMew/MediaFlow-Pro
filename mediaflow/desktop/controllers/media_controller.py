from __future__ import annotations

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot

from mediaflow.domain.enums import (
    AssetKind,
)

from .controller_facet import ControllerFacet


class MediaController(ControllerFacet):
    projectStateChanged = Signal()
    selectionChanged = Signal()
    historyChanged = Signal()
    statusChanged = Signal()
    tasksChanged = Signal()
    previewGraphChanged = Signal()
    profileConfirmationChanged = Signal()
    settingsChanged = Signal()
    relinkConfirmationChanged = Signal()
    audioMetricsChanged = Signal()
    workflowChanged = Signal()
    downloadPlanChanged = Signal()
    runtimeToolsChanged = Signal()
    waveformDataChanged = Signal(str)
    previewRangeRequested = Signal(int, int)
    errorOccurred = Signal(str)
    errorReferenceChanged = Signal()

    @Property(QObject, constant=True)
    def assetsModel(self) -> QObject:
        return self._asset_model

    @Property(QObject, constant=True)
    def filteredAssetsModel(self) -> QObject:
        return self._filtered_asset_model

    @Slot(str)
    def setAssetSearchText(self, value: str) -> None:
        self._filtered_asset_model.setSearchText(value)

    @Property("QVariantList", notify=projectStateChanged)
    def watermarkAssetOptions(self) -> list[dict]:
        if not self._documents:
            return []
        return [
            {"label": asset.name, "value": asset.id}
            for asset in self._documents.list_assets()
            if asset.kind == AssetKind.IMAGE and asset.status.value == "online"
        ]

    @Property(str, notify=selectionChanged)
    def selectedWatermarkAssetId(self) -> str:
        return self._selected_watermark_asset_id

    @Property(str, notify=selectionChanged)
    def selectedAssetId(self) -> str:
        return self._selected_asset_ids[-1] if self._selected_asset_ids else ""

    @Property("QVariantList", notify=selectionChanged)
    def selectedAssetIds(self) -> list[str]:
        return list(self._selected_asset_ids)

    @Property("QVariantMap", notify=selectionChanged)
    def selectedAssetData(self) -> dict:
        row = self._asset_model.findRow("assetId", self.selectedAssetId)
        return self._asset_model.get(row)

    @Slot(str, result=QUrl)
    def assetUrl(self, asset_id: str) -> QUrl:
        if not asset_id or not self._documents:
            return QUrl()
        try:
            return QUrl.fromLocalFile(str(self._documents.resolve_asset_path(asset_id)))
        except (KeyError, OSError, ValueError):
            return QUrl()

    @Slot(str)
    def importMedia(self, path_url: str) -> None:
        try:
            self._import_media_paths([path_url])
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot("QVariantList")
    def importFiles(self, path_urls: list[object]) -> None:
        try:
            self._import_media_paths(path_urls)
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def importWebPackage(self, directory_url: str) -> None:
        try:
            self._require_writable()
            source = self._local_path(directory_url)
            asset = self._project.web.import_package(source)
            self._selected_asset_ids = [asset.id]
            self._projector.refresh_all()
            self.selectionChanged.emit()
            self._set_status(f"已导入可编辑网页素材 {asset.name}")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def importWatermark(self, path_url: str) -> None:
        try:
            self._require_writable()
            source = self._local_path(path_url)
            if source.suffix.lower() not in {
                ".png",
                ".jpg",
                ".jpeg",
                ".webp",
                ".bmp",
                ".tif",
                ".tiff",
            }:
                raise ValueError("水印必须是 PNG、JPEG、WebP 或其他受支持的图片")
            self._project.import_asset(
                source,
                sequence_id=self._active_sequence_id,
                purpose="watermark",
            )
            self._projector.refresh_tasks()
            self._set_status(f"正在导入水印 {source.name}")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def selectWatermarkAsset(self, asset_id: str) -> None:
        try:
            if asset_id:
                asset = self._documents.get_asset(asset_id)
                if asset.kind != AssetKind.IMAGE:
                    raise ValueError("水印必须是图片素材")
            self._selected_watermark_asset_id = asset_id
            self.selectionChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str, str)
    def relinkMedia(self, asset_id: str, path_url: str) -> None:
        try:
            self._require_writable()
            replacement = self._local_path(path_url)
            try:
                asset = self._assets.relink(asset_id, replacement)
            except ValueError as error:
                if "does not match" not in str(error):
                    raise
                self._pending_relink_asset_id = asset_id
                self._pending_relink_path = str(replacement)
                self.relinkConfirmationChanged.emit()
                return
            self._selected_asset_ids = [asset.id]
            self._projector.refresh_all()
            self.selectionChanged.emit()
            self._set_status("离线素材已重新关联")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(bool)
    def resolveRelinkReplacement(self, replace: bool) -> None:
        asset_id = self._pending_relink_asset_id
        replacement = self._pending_relink_path
        self._pending_relink_asset_id = ""
        self._pending_relink_path = ""
        self.relinkConfirmationChanged.emit()
        if not replace or not asset_id:
            return
        try:
            self._require_writable()
            self._assets.relink(
                asset_id,
                replacement,
                allow_different_content=True,
            )
            self._selected_asset_ids = [asset_id]
            self._projector.refresh_all()
            self.selectionChanged.emit()
            self._set_status("已替换素材内容，预览缓存和音频波形将重新生成")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def relinkOfflineMedia(self, directory_url: str) -> None:
        try:
            self._require_writable()
            relinked, unresolved = self._assets.relink_offline_from_directory(self._local_path(directory_url))
            self._projector.refresh_assets()
            self.projectStateChanged.emit()
            self.selectionChanged.emit()
            self._set_status(
                f"已重新关联 {len(relinked)} 个素材"
                + (f"，仍有 {len(unresolved)} 个未找到" if unresolved else "")
            )
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    @Slot(str, bool)
    def selectAsset(self, asset_id: str, toggle: bool = False) -> None:
        self._selected_asset_ids = self._updated_selection(
            self._selected_asset_ids,
            asset_id,
            toggle=toggle,
        )
        self._selected_document_id = ""
        self._selected_subtitle_segment_ids = []
        self._projector.refresh_documents()
        self.selectionChanged.emit()

    @Slot(str, result=bool)
    def isAssetSelected(self, asset_id: str) -> bool:
        return asset_id in self._selected_asset_ids

    @Slot(str, int, int, float, int, int, int, result="QVariantList")
    def waveformPeaks(
        self,
        asset_id: str,
        source_in: int,
        duration_frames: int,
        speed: float,
        visible_start_frame: int,
        visible_duration_frames: int,
        pixel_width: int,
    ) -> list[float]:
        if (
            not self._documents
            or not self._active_sequence_id
            or pixel_width <= 0
            or visible_duration_frames <= 0
        ):
            return []
        try:
            cached = self._waveform_cache.get(asset_id)
            if not cached:
                return []
            payload = cached[2]
            profile = self._editor.state.sequence.profile
            sample_rate = int(payload["sample_rate"])
            visible_start_frame = max(0, min(duration_frames - 1, visible_start_frame))
            visible_duration_frames = max(
                1,
                min(duration_frames - visible_start_frame, visible_duration_frames),
            )
            source_offset = round(visible_start_frame * abs(speed))
            source_frames = max(1, round(visible_duration_frames * abs(speed)))
            if speed >= 0:
                first_source_frame = source_in + source_offset
            else:
                last_source_frame = max(0, source_in - source_offset)
                first_source_frame = max(0, last_source_frame - source_frames + 1)
            start_sample = round(
                first_source_frame * sample_rate * profile.fps_denominator / profile.fps_numerator
            )
            end_sample = start_sample + round(
                source_frames * sample_rate * profile.fps_denominator / profile.fps_numerator
            )
            target_blocks = max(1, pixel_width // 2)
            block_sizes = sorted(int(value) for value in payload["levels"])
            required_block = max(1, (end_sample - start_sample) // target_blocks)
            block = min(block_sizes, key=lambda value: abs(value - required_block))
            peaks = payload["levels"][str(block)]
            first = max(0, start_sample // block)
            last = min(len(peaks), (end_sample + block - 1) // block)
            visible = peaks[first:last]
            if not visible:
                return []
            if speed < 0:
                visible = list(reversed(visible))
            stride = max(1, (len(visible) + target_blocks - 1) // target_blocks)
            flattened: list[float] = []
            for offset in range(0, len(visible), stride):
                group = visible[offset : offset + stride]
                flattened.extend(
                    [
                        min(float(item[0]) for item in group),
                        max(float(item[1]) for item in group),
                    ]
                )
            return flattened
        except (KeyError, ValueError):
            return []
