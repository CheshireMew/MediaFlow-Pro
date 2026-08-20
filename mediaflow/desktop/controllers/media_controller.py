from __future__ import annotations

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

from mediaflow.desktop.session_state import TimelinePlacement
from mediaflow.domain.enums import AssetKind

from .controller_facet import ControllerFacet, report_ui_errors
from .controller_scopes import MediaControllerScope


class MediaController(ControllerFacet[MediaControllerScope]):
    projectStateChanged = Signal()
    selectionChanged = Signal()
    relinkConfirmationChanged = Signal()
    waveformDataChanged = Signal(str)
    errorOccurred = Signal(str)
    sourceMonitorChanged = Signal()
    searchChanged = Signal()

    def __init__(self, session: MediaControllerScope):
        super().__init__(session)
        self._source_asset_id = ""
        self._source_graph_path = ""
        self._source_name = ""
        self._source_duration = 0
        self._source_in = 0
        self._source_out = 0
        self._asset_search_text = ""

    @Property(QObject, constant=True)
    def assetsModel(self) -> QObject:
        return self._session.models.assets

    @Property(QObject, constant=True)
    def filteredAssetsModel(self) -> QObject:
        return self._session.models.filtered_assets

    @Property(QObject, constant=True)
    def assetBinsModel(self) -> QObject:
        return self._session.models.asset_bins

    @Property(QObject, constant=True)
    def filteredAssetMomentsModel(self) -> QObject:
        return self._session.models.filtered_asset_moments

    @Property(str, notify=searchChanged)
    def assetSearchText(self) -> str:
        return self._asset_search_text

    @Slot(str)
    def setAssetSearchText(self, value: str) -> None:
        normalized = value.strip()
        if normalized == self._asset_search_text:
            return
        self._asset_search_text = normalized
        self._session.models.filtered_assets.setSearchText(value)
        self._session.models.filtered_asset_moments.setSearchText(value)
        self.searchChanged.emit()

    @Slot(str)
    def setAssetBinFilter(self, bin_id: str) -> None:
        normalized = bin_id.strip()
        descendants = {normalized} if normalized not in {"", "__unfiled__"} else set()
        rows = self._session.models.asset_bins.snapshot()
        changed = True
        while changed:
            changed = False
            for row in rows:
                if row["parentId"] in descendants and row["binId"] not in descendants:
                    descendants.add(row["binId"])
                    changed = True
        self._session.models.filtered_assets.set_bin_scope(normalized, descendants)

    @Slot(str, str)
    @report_ui_errors
    def createAssetBin(self, name: str, parent_id: str) -> None:
        self._session._require_writable()
        normalized = name.strip()
        if not normalized:
            raise ValueError("素材文件夹名称不能为空")
        self._session.state.binding.require_current().create_asset_bin(
            normalized,
            parent_id.strip() or None,
        )
        self._session.projectors.assets.refresh_assets()
        self._session.updates.commit(project=True)
        self._session._set_status("已创建素材文件夹：%1", normalized)

    @Slot(str)
    @report_ui_errors
    def moveSelectedAssetsToBin(self, bin_id: str) -> None:
        self._session._require_writable()
        if not self._session.state.selection.asset_ids:
            raise ValueError("请先选择要移动的素材")
        self._session.state.binding.require_current().move_assets_to_bin(
            self._session.state.selection.asset_ids,
            bin_id.strip() or None,
        )
        self._session.projectors.assets.refresh_assets()
        self._session.updates.commit(project=True)
        self._session.updates.commit(selection=True)
        self._session._set_status("素材文件夹已更新")

    @Property(list, notify=projectStateChanged)
    def watermarkAssetOptions(self) -> list[dict]:
        if not self._session.state.binding.current:
            return []
        return [
            {"label": asset.name, "value": asset.id}
            for asset in self._session.state.binding.require_current().list_assets()
            if asset.kind == AssetKind.IMAGE and asset.status.value == "online"
        ]

    @Property(str, notify=selectionChanged)
    def selectedWatermarkAssetId(self) -> str:
        return self._session.state.selection.watermark_asset_id

    @Property(str, notify=selectionChanged)
    def selectedAssetId(self) -> str:
        return self._session.state.selection.asset_ids[-1] if self._session.state.selection.asset_ids else ""

    @Property(list, notify=selectionChanged)
    def selectedAssetIds(self) -> list[str]:
        return list(self._session.state.selection.asset_ids)

    @Property(dict, notify=selectionChanged)
    def selectedAssetData(self) -> dict:
        row = self._session.models.assets.findRow(
            "assetId", self._session.state.selection.asset_ids[-1]
            if self._session.state.selection.asset_ids
            else ""
        )
        return self._session.models.assets.get(row)

    @Property(dict, notify=sourceMonitorChanged)
    def sourceMonitorData(self) -> dict:
        return {
            "assetId": self._source_asset_id,
            "name": self._source_name,
            "graphPath": self._source_graph_path,
            "durationFrames": self._source_duration,
            "inFrame": self._source_in,
            "outFrame": self._source_out,
        }

    @Slot(str)
    @report_ui_errors
    def openSourceMonitor(self, asset_id: str) -> None:
        if not self._session.state.binding.current:
            raise RuntimeError("当前没有打开的项目")
        asset = self._session.state.binding.require_current().get_asset(asset_id)
        if asset.kind not in {AssetKind.VIDEO, AssetKind.AUDIO, AssetKind.IMAGE}:
            raise ValueError("该素材类型不能在源监视器中播放")
        project = self._session.state.binding.require_current().get_project()
        main_profile = (
            self._session.state.binding.require_current().get_sequence(project.main_sequence_id).profile
        )
        active_profile = self._session.state.binding.require_timeline().state.sequence.profile
        timeline_asset = asset.in_frame_clock(main_profile, active_profile)
        duration = timeline_asset.metadata.duration_frames or 150
        graph = self._session._api.write_asset_preview_snapshot(
            self._session.state.binding.require_current().project_dir,
            self._session.state.binding.active_sequence_id,
            asset.id,
        )
        self._source_asset_id = asset.id
        self._source_graph_path = str(graph)
        self._source_name = asset.name
        self._source_duration = duration
        self._source_in = 0
        self._source_out = duration
        self.sourceMonitorChanged.emit()

    @Slot()
    def closeSourceMonitor(self) -> None:
        self._source_asset_id = ""
        self._source_graph_path = ""
        self._source_name = ""
        self._source_duration = 0
        self._source_in = 0
        self._source_out = 0
        self.sourceMonitorChanged.emit()

    @Slot(int)
    @report_ui_errors
    def setSourceInFrame(self, frame: int) -> None:
        if not self._source_asset_id:
            raise RuntimeError("源监视器中没有素材")
        self._source_in = max(0, min(self._source_out - 1, int(frame)))
        self.sourceMonitorChanged.emit()

    @Slot(int)
    @report_ui_errors
    def setSourceOutFrame(self, frame: int) -> None:
        if not self._source_asset_id:
            raise RuntimeError("源监视器中没有素材")
        self._source_out = max(
            self._source_in + 1,
            min(self._source_duration, int(frame) + 1),
        )
        self.sourceMonitorChanged.emit()

    @Slot(int, float, bool)
    @report_ui_errors
    def addSourceRangeToTimeline(
        self,
        start_frame: int,
        pixels_per_frame: float,
        snap_enabled: bool,
    ) -> None:
        self._session._require_writable()
        if not self._source_asset_id:
            raise RuntimeError("源监视器中没有素材")
        self._session.timeline_assets.queue_for_timeline(
            [self._source_asset_id],
            TimelinePlacement(
                start_frame=max(0, int(start_frame)),
                pixels_per_frame=float(pixels_per_frame),
                playhead_frame=max(0, int(start_frame)),
                snap_enabled=bool(snap_enabled),
                source_in_frame=self._source_in,
                source_out_frame=self._source_out,
            ),
        )

    @Slot(int)
    @report_ui_errors
    def captureSourceFrame(self, frame: int) -> None:
        self._session._require_writable()
        if not self._source_asset_id or not self._session.state.binding.current:
            raise RuntimeError("源监视器中没有素材")
        captured = self._session.state.binding.require_current().capture_asset_frame(
            self._source_asset_id,
            max(0, int(frame)),
            self._session.state.binding.active_sequence_id,
        )
        self._session.state.selection.asset_ids = [captured.id]
        self._session.projectors.assets.refresh_assets()
        self._session.updates.commit(project=True)
        self._session.updates.commit(selection=True)
        self._session._set_status("已将当前画面保存为素材：%1", captured.name)

    @Slot(str, result=QUrl)
    def assetUrl(self, asset_id: str) -> QUrl:
        if not asset_id or not self._session.state.binding.current:
            return QUrl()
        try:
            asset = self._session.state.binding.require_current().get_asset(asset_id)
            return QUrl.fromLocalFile(
                str(self._session.state.binding.require_current().resolve_asset_path(asset))
            )
        except (KeyError, OSError, ValueError):
            return QUrl()

    @Slot(str)
    @report_ui_errors
    def openAssetFolder(self, asset_id: str) -> None:
        if not asset_id or not self._session.state.binding.current:
            raise RuntimeError("当前没有可定位的素材")
        asset = self._session.state.binding.require_current().get_asset(asset_id)
        directory = self._session.state.binding.require_current().resolve_asset_path(asset).parent
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
        self._session.state.binding.require_current().import_asset(
            source,
            sequence_id=self._session.state.binding.active_sequence_id,
            purpose="watermark",
        )
        self._session.projectors.tasks.refresh_tasks()
        self._session._set_status("正在导入水印 %1", source.name)

    @Slot(str)
    @report_ui_errors
    def selectWatermarkAsset(self, asset_id: str) -> None:
        if asset_id:
            asset = self._session.state.binding.require_current().get_asset(asset_id)
            if asset.kind != AssetKind.IMAGE:
                raise ValueError("水印必须是图片素材")
        self._session.state.selection.watermark_asset_id = asset_id
        self._session.updates.commit(selection=True)

    @Slot(str, str)
    @report_ui_errors
    def relinkMedia(self, asset_id: str, path_url: str) -> None:
        self._session._require_writable()
        replacement = self._session._local_path(path_url)
        try:
            asset = self._session.state.binding.require_current().relink_asset(asset_id, replacement)
        except ValueError as error:
            if "does not match" not in str(error):
                raise
            self._session.state.assets.pending_relink_asset_id = asset_id
            self._session.state.assets.pending_relink_path = str(replacement)
            self._session.updates.commit(relink_confirmation=True)
            return
        self._session.state.selection.asset_ids = [asset.id]
        self._session.projectors.assets.refresh_assets()
        self._session.projectors.timeline.schedule_preview_graph()
        self._session.updates.commit(project=True)
        self._session.updates.commit(selection=True)
        self._session._set_status("离线素材已重新关联")

    @Slot(bool)
    @report_ui_errors
    def resolveRelinkReplacement(self, replace: bool) -> None:
        asset_id = self._session.state.assets.pending_relink_asset_id
        replacement = self._session.state.assets.pending_relink_path
        self._session.state.assets.pending_relink_asset_id = ""
        self._session.state.assets.pending_relink_path = ""
        self._session.updates.commit(relink_confirmation=True)
        if not replace or not asset_id:
            return
        self._session._require_writable()
        self._session.state.binding.require_current().relink_asset(
            asset_id,
            replacement,
            allow_different_content=True,
        )
        self._session.state.selection.asset_ids = [asset_id]
        self._session.projectors.assets.refresh_assets()
        self._session.projectors.timeline.schedule_preview_graph()
        self._session.updates.commit(project=True)
        self._session.updates.commit(selection=True)
        self._session._set_status("已替换素材内容，预览缓存和音频波形将重新生成")

    @Slot(str)
    @report_ui_errors
    def relinkOfflineMedia(self, directory_url: str) -> None:
        self._session._require_writable()
        relinked, unresolved = self._session.state.binding.require_current().relink_offline_assets(
            self._session._local_path(directory_url)
        )
        self._session.projectors.assets.refresh_assets()
        self._session.updates.commit(project=True)
        self._session.updates.commit(selection=True)
        if unresolved:
            self._session._set_status(
                "已重新关联 %1 个素材，仍有 %2 个未找到",
                len(relinked),
                len(unresolved),
            )
        else:
            self._session._set_status("已重新关联 %1 个素材", len(relinked))

    @Slot(str)
    @Slot(str, bool)
    def selectAsset(self, asset_id: str, toggle: bool = False) -> None:
        self._session.state.selection.asset_ids = self._session._updated_selection(
            self._session.state.selection.asset_ids,
            asset_id,
            toggle=toggle,
        )
        self._session.state.selection.document_id = ""
        self._session.state.selection.subtitle_segment_ids = []
        self._session.projectors.subtitles.refresh_documents()
        self._session.updates.commit(selection=True)

    @Slot(str, result=bool)
    def isAssetSelected(self, asset_id: str) -> bool:
        return asset_id in self._session.state.selection.asset_ids

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
            not self._session.state.binding.current
            or not self._session.state.binding.active_sequence_id
            or pixel_width <= 0
            or visible_duration_frames <= 0
        ):
            return []
        try:
            cached = self._session.state.assets.waveform_cache.get(asset_id)
            if not cached:
                return []
            payload = cached[2]
            profile = self._session.state.binding.require_timeline().state.sequence.profile
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
