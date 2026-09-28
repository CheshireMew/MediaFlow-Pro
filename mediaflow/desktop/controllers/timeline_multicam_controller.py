from __future__ import annotations

from typing import Any

from PySide6.QtCore import Property, Signal, Slot

from mediaflow.domain.enums import AssetKind
from mediaflow.domain.multicam import MulticamAngleSyncSpec

from .controller_facet import ControllerFacet, report_ui_errors
from .controller_scopes import TimelinePresentationScope


class TimelineMulticamController(ControllerFacet[TimelinePresentationScope]):
    multicamChanged = Signal()
    syncAnalysisChanged = Signal()

    def __init__(self, scope: TimelinePresentationScope) -> None:
        super().__init__(scope)
        self._sync_analysis: dict[str, Any] = {}
        self._sync_request_id = 0
        self._sync_running = False

    @Property(dict, notify=syncAnalysisChanged)
    def syncAnalysis(self) -> dict[str, Any]:
        return dict(self._sync_analysis)

    @Property(bool, notify=syncAnalysisChanged)
    def syncRunning(self) -> bool:
        return self._sync_running

    @Property(list, notify=multicamChanged)
    def selectedVideoAssets(self) -> list[dict[str, Any]]:
        return self._selected_video_assets()

    def _selected_video_assets(self) -> list[dict[str, Any]]:
        current = self._session.state.binding.current
        if current is None:
            return []
        rows = []
        for asset_id in self._session.state.selection.asset_ids:
            asset = current.get_asset(asset_id)
            if asset.kind == AssetKind.VIDEO and asset.metadata.duration_frames > 0:
                rows.append(
                    {
                        "assetId": asset.id,
                        "name": asset.name,
                        "durationFrames": asset.metadata.duration_frames,
                    }
                )
        return rows

    @Property(list, notify=multicamChanged)
    def groups(self) -> list[dict[str, Any]]:
        timeline = self._session.state.binding.timeline
        if timeline is None:
            return []
        return [
            {
                "groupId": group.id,
                "name": group.name,
                "timelineStart": group.timeline_start,
                "duration": group.duration,
                "syncMethod": group.sync_method,
                "syncConfidence": group.sync_confidence if group.sync_confidence is not None else -1.0,
                "audioStrategy": group.audio_strategy,
                "masterAudioAngleId": group.master_audio_angle_id or "",
                "angles": [
                    {"angleId": angle.id, "name": angle.name, "assetId": angle.asset_id}
                    for angle in group.angles
                ],
                "cuts": [item.model_dump(mode="json") for item in group.cuts],
            }
            for group in timeline.list_multicam_groups()
        ]

    @Slot(str, list, int, int, int)
    @report_ui_errors
    def createGroup(
        self,
        name: str,
        angle_values: list,
        timeline_start: int,
        duration: int,
        sync_offset: int,
    ) -> None:
        self._session._require_writable()
        angles = [
            MulticamAngleSyncSpec(
                asset_id=str(item["assetId"]),
                name=str(item["name"]),
                sync_frame=int(item["syncFrame"]),
            )
            for item in angle_values
        ]
        self._session.state.binding.require_timeline().create_multicam_group(
            name,
            angles,
            timeline_start=max(0, timeline_start),
            duration=duration,
            sync_offset=sync_offset,
        )
        self._after_change()
        self._session._set_status("多机位节目轨已创建")

    @Slot(str, list, int, int, int, str, float, str, str)
    @report_ui_errors
    def createGroupAdvanced(
        self,
        name: str,
        angle_values: list,
        timeline_start: int,
        duration: int,
        sync_offset: int,
        sync_method: str,
        sync_confidence: float,
        audio_strategy: str,
        master_audio_angle_id: str,
    ) -> None:
        self._session._require_writable()
        angles = [
            MulticamAngleSyncSpec.model_validate(
                {
                    **(
                        {"id": str(item.get("angleId") or item.get("id"))}
                        if item.get("angleId") or item.get("id")
                        else {}
                    ),
                    "asset_id": str(item["assetId"]),
                    "name": str(item["name"]),
                    "sync_frame": int(item["syncFrame"]),
                }
            )
            for item in angle_values
        ]
        self._session.state.binding.require_timeline().create_multicam_group(
            name,
            angles,
            timeline_start=max(0, timeline_start),
            duration=duration,
            sync_offset=sync_offset,
            sync_method=sync_method,
            sync_confidence=(sync_confidence if sync_confidence >= 0 else None),
            audio_strategy=audio_strategy,
            master_audio_angle_id=(
                next(
                    (item.id for item in angles if item.asset_id == master_audio_angle_id),
                    None,
                )
                or master_audio_angle_id
                or None
            ),
        )
        self._after_change()
        self._session._set_status("多机位节目轨已创建")

    @Slot(str)
    @report_ui_errors
    def analyzeSync(self, method: str) -> None:
        current = self._session.state.binding.require_current()
        asset_ids = [
            str(item["assetId"]) for item in self._selected_video_assets()
        ]
        if len(asset_ids) < 2:
            raise ValueError("请先选择至少两个视频素材")
        self._sync_request_id += 1
        request_id = self._sync_request_id
        self._sync_running = True
        self.syncAnalysisChanged.emit()
        self._session.background.submit_project_callback(
            "multicam_sync",
            request_id,
            lambda: self._session._api.execute_read_operation(
                "multicam.sync.analyze",
                current.project_dir,
                {
                    "sequence_id": self._session.state.binding.active_sequence_id,
                    "asset_ids": asset_ids,
                    "method": method,
                },
            ),
            on_result=lambda result: self._finish_sync(request_id, result),
            on_error=lambda error: self._fail_sync(request_id, error),
        )

    @Slot(str, str, int)
    @report_ui_errors
    def switchAngle(self, group_id: str, angle_id: str, timeline_frame: int) -> None:
        self._session._require_writable()
        self._session.state.binding.require_timeline().switch_multicam_angle(
            group_id,
            angle_id,
            timeline_frame,
        )
        self._after_change()
        self._session._set_status("多机位角度已切换")

    def _after_change(self) -> None:
        self._session.projectors.timeline.refresh_timeline()
        self._session.updates.commit(history=True, selection=True)
        self.multicamChanged.emit()

    def _finish_sync(self, request_id: int, result: object | None) -> None:
        if request_id != self._sync_request_id:
            return
        if not isinstance(result, dict) or not isinstance(result.get("angles"), list):
            self._fail_sync(request_id, RuntimeError("多机位同步分析返回了无效结果"))
            return
        self._sync_analysis = result
        self._sync_running = False
        self.syncAnalysisChanged.emit()
        self._session._set_status(
            "多机位同步分析完成，置信度 %1%",
            round(float(result.get("confidence", 0.0)) * 100),
        )

    def _fail_sync(self, request_id: int, error: BaseException) -> None:
        if request_id != self._sync_request_id:
            return
        self._sync_running = False
        self.syncAnalysisChanged.emit()
        self._session.updates.report_error(str(error))
