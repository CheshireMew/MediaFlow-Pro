from __future__ import annotations

from dataclasses import replace
from typing import Any, cast

from PySide6.QtCore import Property, Signal, Slot

from mediaflow.desktop.session_state import TimelinePlacement
from mediaflow.domain.enums import ColorMode
from mediaflow.domain.project import ProjectProfile
from mediaflow.domain.sequence_variants import SequenceVariantSpec, VariantReframeMode

from .controller_facet import ControllerFacet, report_ui_errors
from .controller_scopes import WorkspaceSequenceScope


class WorkspaceSequenceController(ControllerFacet[WorkspaceSequenceScope]):
    """Active-sequence selection, profile, preview, and placement commands."""

    variantPlanChanged = Signal()

    def __init__(self, scope: WorkspaceSequenceScope) -> None:
        super().__init__(scope)
        self._variant_plan: dict[str, Any] = {}

    @Property(dict, notify=variantPlanChanged)
    def variantPlan(self) -> dict[str, Any]:
        return dict(self._variant_plan)

    @Slot("QVariantList", str)
    @report_ui_errors
    def planDeliveryVariants(
        self,
        preset_ids: list[object],
        reframe_mode: str,
    ) -> None:
        source_id, specs = self._delivery_variant_specs(preset_ids, reframe_mode)
        plan = self._session.state.binding.require_current().plan_sequence_variants(
            source_id, specs
        )
        self._variant_plan = plan.model_dump(mode="json")
        self.variantPlanChanged.emit()
        conflict_count = sum(len(item.conflicts) for item in plan.items)
        self._session._set_status(
            "交付版本变更计划已生成，%1 个冲突", conflict_count
        )

    @Slot(str)
    @report_ui_errors
    def selectSequence(self, sequence_id: str) -> None:
        if not self._session.state.binding.current:
            return
        self._session.state.binding.require_current().get_sequence(sequence_id)
        self._session.state.binding.active_sequence_id = sequence_id
        self._session.state.binding.timeline = self._session.state.binding.require_current().timeline(
            sequence_id
        )
        self._session.state.selection.clip_ids = []
        self._session.state.selection.compound_id = ""
        self._session.projectors.refresh_active_sequence()

    @Slot(str)
    @report_ui_errors
    def createShortSequence(self, name: str) -> None:
        self._session._require_writable()
        selected_name = name.strip()
        if not selected_name:
            existing_names = {
                item.name
                for item in self._session.state.binding.require_current().list_sequences(
                    include_archived=True
                )
            }
            sequence_number = 1
            while f"短视频 {sequence_number}" in existing_names:
                sequence_number += 1
            selected_name = f"短视频 {sequence_number}"
        sequence = self._session.state.binding.require_current().create_short_sequence(selected_name)
        self._session.state.binding.active_sequence_id = sequence.id
        self._session.state.binding.timeline = self._session.state.binding.require_current().timeline(
            sequence.id
        )
        self._session.projectors.refresh_active_sequence(refresh_sequences=True)
        self._session._set_status("短视频序列已创建")

    @Slot("QVariantList", str, bool)
    @report_ui_errors
    def generateDeliveryVariants(
        self,
        preset_ids: list[object],
        reframe_mode: str,
        force: bool,
    ) -> None:
        self._session._require_writable()
        source_id, specs = self._delivery_variant_specs(preset_ids, reframe_mode)
        result = self._session.state.binding.require_current().generate_sequence_variants(
            source_id,
            specs,
            force=force,
        )
        self._variant_plan = {
            "source_sequence_id": source_id,
            "items": [item.model_dump(mode="json") for item in result.plans],
        }
        self.variantPlanChanged.emit()
        self._session.projectors.timeline.refresh_sequences()
        self._session.updates.commit(project=True, history=True)
        created = len(result.created_sequence_ids)
        refreshed = len(result.refreshed_sequence_ids)
        reused = len(result.reused_sequence_ids)
        if created or refreshed:
            self._session._set_status(
                "已新建 %1 个、同步 %2 个交付版本", created, refreshed
            )
        else:
            self._session._set_status("%1 个交付版本已是最新，无需重复生成", reused)

    def _delivery_variant_specs(
        self,
        preset_ids: list[object],
        reframe_mode: str,
    ) -> tuple[str, list[SequenceVariantSpec]]:
        presets = {
            "landscape_16_9": ("横屏 16:9", 1920, 1080),
            "portrait_9_16": ("竖屏 9:16", 1080, 1920),
            "square_1_1": ("方形 1:1", 1080, 1080),
            "portrait_4_5": ("竖屏 4:5", 1080, 1350),
        }
        selected = [str(item) for item in preset_ids]
        if not selected:
            raise ValueError("请至少选择一个交付版本")
        unknown = [item for item in selected if item not in presets]
        if unknown:
            raise ValueError(f"未知的交付版本：{', '.join(unknown)}")
        if reframe_mode not in {"fit", "center_fill"}:
            raise ValueError("未知的画面适配方式")
        source_id = self._session.state.binding.active_sequence_id
        source = self._session.state.binding.require_current().get_sequence(source_id)
        specs = [
            SequenceVariantSpec(
                preset_id=preset_id,
                name=f"{source.name} · {presets[preset_id][0]}",
                width=presets[preset_id][1],
                height=presets[preset_id][2],
                reframe_mode=cast(VariantReframeMode, reframe_mode),
            )
            for preset_id in selected
        ]
        return source_id, specs

    @Slot()
    @report_ui_errors
    def archiveActiveSequence(self) -> None:
        self._session._require_writable()
        project = self._session.state.binding.require_current().get_project()
        sequence_id = self._session.state.binding.active_sequence_id
        if sequence_id == project.main_sequence_id:
            raise ValueError("主序列不能删除")
        self._session.state.binding.require_current().archive_short_sequence(sequence_id)
        self._session.state.binding.active_sequence_id = project.main_sequence_id
        self._session.state.binding.timeline = self._session.state.binding.require_current().timeline(
            project.main_sequence_id
        )
        self._session.state.selection.clip_ids = []
        self._session.state.selection.compound_id = ""
        self._session.projectors.refresh_active_sequence(refresh_sequences=True)
        self._session._set_status("短视频序列已移除；可使用撤销恢复")

    @Slot(bool)
    def resolveProfileAdoption(self, adopt: bool) -> None:
        asset_id = self._session.state.assets.pending_profile_asset_id
        placement = self._session.state.assets.pending_profile_placement
        self._session.state.assets.pending_profile_asset_id = ""
        self._session.state.assets.pending_profile_label = ""
        self._session.state.assets.pending_profile_placement = TimelinePlacement()
        self._session.updates.commit(profile_confirmation=True)
        if not asset_id:
            return
        try:
            self._session._require_writable()
            if adopt:
                self._session.state.binding.require_current().adopt_main_profile_from_video(asset_id)
                self._session.state.binding.require_timeline().reload()
                self._session.projectors.timeline.refresh_sequences()
                self._session.updates.commit(project=True)
            placed = self._session.timeline_assets.place_on_timeline(
                self._session.state.binding.require_current().get_asset(asset_id),
                placement,
            )
            if self._session.state.assets.pending_batch_placement.start_frame is not None:
                self._session.state.assets.pending_batch_placement = replace(
                    self._session.state.assets.pending_batch_placement,
                    track_id=placed.track_id,
                    start_frame=placed.end_frame,
                    force_new_track=False,
                )
            self._session.timeline_assets.continue_batch()
        except Exception as error:
            self._session.state.assets.pending_batch_ids = []
            self._session.updates.report_error(str(error))

    @Slot(int)
    def reportPreviewDroppedFrames(self, dropped_frames: int) -> None:
        if (
            dropped_frames < self._session.state.service_settings.preview.dropped_frame_proxy_threshold
            or not self._session.state.binding.current
            or not self._session.state.binding.timeline
            or self._session.state.binding.require_current().read_only
        ):
            return
        asset_ids = {clip.asset_id for clip in self._session.state.binding.require_timeline().state.clips}
        for asset_id in asset_ids:
            asset = self._session.state.binding.require_current().get_asset(asset_id)
            if not asset.proxy_path:
                self._session.timeline_assets.schedule_background(asset, dropped_frames=dropped_frames)

    @Slot(bool)
    def reportHdrPreviewActive(self, active: bool) -> None:
        if self._session.state.presentation.hdr_preview_active == active:
            return
        self._session.state.presentation.hdr_preview_active = active
        self._session.projectors.timeline.schedule_preview_graph()

    @Slot(int, int, int, int, str, int)
    @report_ui_errors
    def updateSequenceProfile(
        self,
        width: int,
        height: int,
        fps_numerator: int,
        fps_denominator: int,
        color_mode: str,
        audio_channels: int,
    ) -> None:
        self._session._require_writable()
        mode = ColorMode(color_mode)
        self._session.state.binding.require_timeline().set_sequence_profile(
            ProjectProfile(
                width=width,
                height=height,
                fps_numerator=fps_numerator,
                fps_denominator=fps_denominator,
                color_mode=mode,
                bit_depth=10 if mode == ColorMode.HDR10_BT2020_PQ else 8,
                audio_channels=audio_channels,
            )
        )
        self._session.projectors.assets.refresh_assets()
        self._session.projectors.timeline.refresh_sequences()
        self._session.projectors.timeline.refresh_timeline()
        self._session.projectors.subtitles.refresh_documents()
        self._session.projectors.timeline.refresh_preview_subtitles()
        self._session.projectors.timeline.schedule_preview_graph()
        self._session.updates.commit(project=True, history=True)
        self._session._set_status("序列配置已更新")
