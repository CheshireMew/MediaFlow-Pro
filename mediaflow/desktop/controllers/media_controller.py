from __future__ import annotations

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

from mediaflow.domain.enums import AssetKind

from .controller_facet import ControllerFacet, report_ui_errors


class MediaController(ControllerFacet):
    projectStateChanged = Signal()
    selectionChanged = Signal()
    relinkConfirmationChanged = Signal()
    waveformDataChanged = Signal(str)
    errorOccurred = Signal(str)

    @Property(QObject, constant=True)
    def assetsModel(self) -> QObject:
        return self._session.models.assets

    @Property(QObject, constant=True)
    def filteredAssetsModel(self) -> QObject:
        return self._session.models.filtered_assets

    @Slot(str)
    def setAssetSearchText(self, value: str) -> None:
        self._session.models.filtered_assets.setSearchText(value)

    @Property("QVariantList", notify=projectStateChanged)
    def watermarkAssetOptions(self) -> list[dict]:
        if not self._session.binding.current:
            return []
        return [
            {"label": asset.name, "value": asset.id}
            for asset in self._session.binding.current.list_assets()
            if asset.kind == AssetKind.IMAGE and asset.status.value == "online"
        ]

    @Property(str, notify=selectionChanged)
    def selectedWatermarkAssetId(self) -> str:
        return self._session.selection.watermark_asset_id

    @Property(str, notify=selectionChanged)
    def selectedAssetId(self) -> str:
        return self._session.selection.asset_ids[-1] if self._session.selection.asset_ids else ""

    @Property("QVariantList", notify=selectionChanged)
    def selectedAssetIds(self) -> list[str]:
        return list(self._session.selection.asset_ids)

    @Property("QVariantMap", notify=selectionChanged)
    def selectedAssetData(self) -> dict:
        row = self._session.models.assets.findRow("assetId", self.selectedAssetId)
        return self._session.models.assets.get(row)

    @Slot(str, result=QUrl)
    def assetUrl(self, asset_id: str) -> QUrl:
        if not asset_id or not self._session.binding.current:
            return QUrl()
        try:
            asset = self._session.binding.current.get_asset(asset_id)
            return QUrl.fromLocalFile(str(self._session.binding.current.resolve_asset_path(asset)))
        except (KeyError, OSError, ValueError):
            return QUrl()

    @Slot(str)
    @report_ui_errors
    def openAssetFolder(self, asset_id: str) -> None:
        if not asset_id or not self._session.binding.current:
            raise RuntimeError("当前没有可定位的素材")
        asset = self._session.binding.current.get_asset(asset_id)
        directory = self._session.binding.current.resolve_asset_path(asset).parent
        if not directory.is_dir():
            raise FileNotFoundError(f"素材所在文件夹不存在：{directory}")
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory))):
            raise RuntimeError(f"无法打开素材所在文件夹：{directory}")

    @Slot("QVariantList")
    @report_ui_errors
    def importFiles(self, path_urls: list[object]) -> None:
        self._session.timeline_assets.import_media_paths(path_urls)

    @Slot(str)
    @report_ui_errors
    def importWatermark(self, path_url: str) -> None:
        self._session._require_writable()
        source = self._session._local_path(path_url)
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
        self._session.binding.current.import_asset(
            source,
            sequence_id=self._session.binding.active_sequence_id,
            purpose="watermark",
        )
        self._session.projectors.tasks.refresh_tasks()
        self._session._set_status(f"正在导入水印 {source.name}")

    @Slot(str)
    @report_ui_errors
    def selectWatermarkAsset(self, asset_id: str) -> None:
        if asset_id:
            asset = self._session.binding.current.get_asset(asset_id)
            if asset.kind != AssetKind.IMAGE:
                raise ValueError("水印必须是图片素材")
        self._session.selection.watermark_asset_id = asset_id
        self._session.events.selectionChanged.emit()

    @Slot(str, str)
    @report_ui_errors
    def relinkMedia(self, asset_id: str, path_url: str) -> None:
        self._session._require_writable()
        replacement = self._session._local_path(path_url)
        try:
            asset = self._session.binding.current.relink_asset(asset_id, replacement)
        except ValueError as error:
            if "does not match" not in str(error):
                raise
            self._session.asset_state.pending_relink_asset_id = asset_id
            self._session.asset_state.pending_relink_path = str(replacement)
            self._session.events.relinkConfirmationChanged.emit()
            return
        self._session.selection.asset_ids = [asset.id]
        self._session.projectors.assets.refresh_assets()
        self._session.projectors.timeline.schedule_preview_graph()
        self._session.events.projectStateChanged.emit()
        self._session.events.selectionChanged.emit()
        self._session._set_status("离线素材已重新关联")

    @Slot(bool)
    @report_ui_errors
    def resolveRelinkReplacement(self, replace: bool) -> None:
        asset_id = self._session.asset_state.pending_relink_asset_id
        replacement = self._session.asset_state.pending_relink_path
        self._session.asset_state.pending_relink_asset_id = ""
        self._session.asset_state.pending_relink_path = ""
        self._session.events.relinkConfirmationChanged.emit()
        if not replace or not asset_id:
            return
        self._session._require_writable()
        self._session.binding.current.relink_asset(
            asset_id,
            replacement,
            allow_different_content=True,
        )
        self._session.selection.asset_ids = [asset_id]
        self._session.projectors.assets.refresh_assets()
        self._session.projectors.timeline.schedule_preview_graph()
        self._session.events.projectStateChanged.emit()
        self._session.events.selectionChanged.emit()
        self._session._set_status("已替换素材内容，预览缓存和音频波形将重新生成")

    @Slot(str)
    @report_ui_errors
    def relinkOfflineMedia(self, directory_url: str) -> None:
        self._session._require_writable()
        relinked, unresolved = self._session.binding.current.relink_offline_assets(
            self._session._local_path(directory_url)
        )
        self._session.projectors.assets.refresh_assets()
        self._session.events.projectStateChanged.emit()
        self._session.events.selectionChanged.emit()
        self._session._set_status(
            f"已重新关联 {len(relinked)} 个素材"
            + (f"，仍有 {len(unresolved)} 个未找到" if unresolved else "")
        )

    @Slot(str)
    @Slot(str, bool)
    def selectAsset(self, asset_id: str, toggle: bool = False) -> None:
        self._session.selection.asset_ids = self._session._updated_selection(
            self._session.selection.asset_ids,
            asset_id,
            toggle=toggle,
        )
        self._session.selection.document_id = ""
        self._session.selection.subtitle_segment_ids = []
        self._session.projectors.subtitles.refresh_documents()
        self._session.events.selectionChanged.emit()

    @Slot(str, result=bool)
    def isAssetSelected(self, asset_id: str) -> bool:
        return asset_id in self._session.selection.asset_ids

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
            not self._session.binding.current
            or not self._session.binding.active_sequence_id
            or pixel_width <= 0
            or visible_duration_frames <= 0
        ):
            return []
        try:
            cached = self._session.asset_state.waveform_cache.get(asset_id)
            if not cached:
                return []
            payload = cached[2]
            profile = self._session.binding.timeline.state.sequence.profile
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
