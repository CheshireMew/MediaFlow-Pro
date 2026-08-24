from __future__ import annotations

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot

from mediaflow.desktop.asset_list_models import MediaResourceListModel
from mediaflow.desktop.presentation_resources import (
    builtin_media_resource_tags,
    builtin_media_resource_text,
    media_resource_ui_label,
)
from mediaflow.desktop.session_state import TimelinePlacement
from mediaflow.domain.audio import AudioEffect
from mediaflow.domain.enums import (
    AudioEffectKind,
    ColorMode,
    TransitionKind,
    VisualEffectKind,
)

from .controller_facet import ControllerFacet, report_ui_errors
from .controller_scopes import MediaControllerScope


class ResourceLibraryController(ControllerFacet[MediaControllerScope]):
    resourcesChanged = Signal()

    _CATEGORY_LABELS = {
        "motion-graphic": "MG 动画",
        "sound-effect": "音效素材",
        "audio-effect": "音频效果",
        "transition": "转场",
        "visual-effect": "特效",
        "zoom": "缩放",
        "lut": "LUT",
    }
    _SEMANTIC_COLLECTIONS = (
        ("text", "文字"),
        ("progress", "进度"),
        ("cinematic", "电影感"),
        ("technology", "科技"),
        ("audio", "音频"),
        ("overlay", "叠加"),
        ("motion", "动效"),
        ("builtin", "内置"),
    )

    def __init__(self, session: MediaControllerScope):
        super().__init__(session)
        self._model = MediaResourceListModel(self)
        self._entries: dict[str, dict] = {}
        self._source_errors: list[str] = []
        self._available_categories: list[str] = []
        self._available_tags: set[str] = set()
        self._featured_count = 0
        self._last_category = ""
        self._last_query = ""
        self._last_collection = ""

    @Property(QObject, constant=True)
    def resourcesModel(self) -> QObject:
        return self._model

    @Property(list, notify=resourcesChanged)
    def categoryOptions(self) -> list[dict[str, str]]:
        return [{"value": "", "label": media_resource_ui_label("全部")}] + [
            {
                "value": value,
                "label": media_resource_ui_label(self._CATEGORY_LABELS.get(value, value)),
            }
            for value in self._CATEGORY_LABELS
            if value in self._available_categories
        ]

    @Property(list, notify=resourcesChanged)
    def collectionOptions(self) -> list[dict[str, str]]:
        options = [{"value": "favorites", "label": media_resource_ui_label("收藏夹")}]
        if self._featured_count:
            options.append({"value": "featured", "label": media_resource_ui_label("热门")})
        options.extend(
            {"value": f"tag:{tag}", "label": media_resource_ui_label(label)}
            for tag, label in self._SEMANTIC_COLLECTIONS
            if tag in self._available_tags
        )
        return options

    @Property(list, notify=resourcesChanged)
    def sourceErrors(self) -> list[str]:
        return list(self._source_errors)

    @Property(int, notify=resourcesChanged)
    def resultCount(self) -> int:
        return self._model.rowCount()

    @Slot(str, str)
    @Slot(str, str, str)
    @report_ui_errors
    def refresh(
        self,
        category: str = "",
        query: str = "",
        collection: str = "",
    ) -> None:
        self._last_category = category
        self._last_query = query
        self._last_collection = collection
        color_mode = (
            self._session.state.binding.require_timeline().state.sequence.profile.color_mode
            if self._session.state.binding.timeline is not None
            else ColorMode.SDR_BT709
        )
        required_tags = [collection.removeprefix("tag:")] if collection.startswith("tag:") else []
        result = self._session._api.search_media_resources(
            color_mode=color_mode.value,
            category=category or None,
            query=query,
            tags=required_tags,
        )
        items = result.get("items", [])
        if not isinstance(items, list):
            raise RuntimeError("资源目录返回了无效的资源列表")
        categories = result.get("categories", [])
        self._available_categories = [str(value) for value in categories if isinstance(value, str)]
        tags = result.get("tags", [])
        self._available_tags = {str(value) for value in tags if isinstance(value, str)}
        self._featured_count = int(result.get("featured_count") or 0)
        favorites = set(self._session.state.desktop_settings.ui.favorite_resource_keys)
        rows = []
        entries: dict[str, dict] = {}
        for raw in items:
            if not isinstance(raw, dict):
                continue
            resource_key = str(raw.get("resource_key") or "")
            catalog_id = str(raw.get("catalog_id") or "")
            featured_rank_value = raw.get("featured_rank")
            is_favorite = resource_key in favorites
            if collection == "favorites" and not is_favorite:
                continue
            if collection == "featured" and featured_rank_value is None:
                continue
            adoption_value = raw.get("adoption")
            adoption = adoption_value if isinstance(adoption_value, dict) else {}
            preview_value = raw.get("preview")
            preview = preview_value if isinstance(preview_value, dict) else {}
            rights_value = raw.get("rights")
            rights = rights_value if isinstance(rights_value, dict) else {}
            adoption_path = str(raw.get("adoption_path") or "")
            preview_path = str(raw.get("preview_path") or "")
            preview_url = QUrl.fromLocalFile(preview_path).toString() if preview_path else ""
            adoption_type = str(adoption.get("type") or "")
            can_adopt = adoption_type == "editor-preset" or (
                adoption_type in {"editable-media-package", "media-file"} and bool(adoption_path)
            )
            rows.append(
                {
                    "resourceKey": resource_key,
                    "category": str(raw.get("category") or ""),
                    "name": builtin_media_resource_text(
                        catalog_id, str(raw.get("name") or "")
                    ),
                    "description": builtin_media_resource_text(
                        catalog_id, str(raw.get("description") or "")
                    ),
                    "provider": str(raw.get("provider") or ""),
                    "tags": builtin_media_resource_tags(catalog_id, raw.get("tags") or []),
                    "capabilities": list(raw.get("capabilities") or []),
                    "previewType": str(preview.get("type") or "none"),
                    "previewUrl": preview_url,
                    "license": str(rights.get("license") or ""),
                    "adoptionType": adoption_type,
                    "adoptionTarget": str(adoption.get("target") or ""),
                    "presetId": str(adoption.get("preset_id") or ""),
                    "parameters": dict(adoption.get("parameters") or {}),
                    "defaultDurationFrames": int(adoption.get("default_duration_frames") or 0),
                    "adoptionPath": adoption_path,
                    "featuredRank": (int(featured_rank_value) if featured_rank_value is not None else -1),
                    "isFavorite": is_favorite,
                    "canAdopt": can_adopt,
                }
            )
            entries[resource_key] = raw
        self._entries = entries
        self._model.set_items(rows)
        sources = result.get("sources", [])
        self._source_errors = [
            str(source.get("error")) for source in sources if isinstance(source, dict) and source.get("error")
        ]
        self.resourcesChanged.emit()

    @Slot(str)
    @report_ui_errors
    def toggleFavorite(self, resource_key: str) -> None:
        candidate = self._session.state.desktop_settings.model_copy(deep=True)
        favorites = list(candidate.ui.favorite_resource_keys)
        if resource_key in favorites:
            favorites.remove(resource_key)
            removed = True
        else:
            favorites.append(resource_key)
            removed = False
        candidate.ui.favorite_resource_keys = favorites
        self._session.settings_persistence.commit(candidate)
        if removed:
            self._session._set_status("已取消收藏资源")
        else:
            self._session._set_status("已收藏资源")
        self.refresh(
            self._last_category,
            self._last_query,
            self._last_collection,
        )

    @Slot(str, int, float, bool)
    @report_ui_errors
    def adoptResource(
        self,
        resource_key: str,
        playhead_frame: int,
        pixels_per_frame: float,
        snap_enabled: bool,
    ) -> None:
        self._session._require_writable()
        entry = self._entries.get(resource_key)
        if entry is None:
            raise KeyError(resource_key)
        adoption = entry.get("adoption")
        if not isinstance(adoption, dict):
            raise ValueError("资源缺少可执行的采用说明")
        adoption_type = str(adoption.get("type") or "")
        if adoption_type == "editor-preset":
            self._adopt_editor_preset(adoption)
            return
        adoption_path = str(entry.get("adoption_path") or "")
        if not adoption_path:
            raise ValueError("资源目录没有提供可导入的本地文件")
        if str(adoption.get("media_type") or "") == "lut":
            self._adopt_lut(adoption_path)
            return
        force_new_track = (
            adoption_type == "editable-media-package" or str(adoption.get("placement") or "") == "overlay"
        )
        self._session.timeline_assets.import_media_paths(
            [QUrl.fromLocalFile(adoption_path)],
            placement=TimelinePlacement(
                start_frame=max(0, int(playhead_frame)),
                playhead_frame=max(0, int(playhead_frame)),
                pixels_per_frame=max(0.01, float(pixels_per_frame)),
                snap_enabled=bool(snap_enabled),
                force_new_track=force_new_track,
            ),
        )

    def _adopt_lut(self, source: str) -> None:
        selection = self._session.state.selection
        if not selection.clip_ids:
            raise ValueError("请先选择一个视频或图片片段")
        project = self._session.state.binding.require_current()
        timeline = self._session.state.binding.require_timeline()
        lut_asset = project.import_lut_asset(source)
        clip_id = selection.clip_ids[-1]
        timeline.add_clip_visual_effect(
            clip_id,
            VisualEffectKind.LUT_3D,
            resource_asset_id=lut_asset.id,
        )
        self._session.projectors.assets.refresh_assets()
        self._session.projectors.timeline.refresh_timeline()
        self._session.projectors.timeline.schedule_preview_graph()
        self._session.updates.commit(selection=True)
        self._session.updates.commit(history=True)
        self._session._set_status("LUT 已从资源库添加")

    def _adopt_editor_preset(self, adoption: dict) -> None:
        timeline = self._session.state.binding.require_timeline()
        selection = self._session.state.selection
        target = str(adoption.get("target") or "")
        preset_id = str(adoption.get("preset_id") or "")
        if target == "transition":
            if not selection.clip_ids:
                raise ValueError("请先选择转场左侧的片段")
            state = timeline.state
            left = next(clip for clip in state.clips if clip.id == selection.clip_ids[-1])
            try:
                right = next(
                    clip
                    for clip in state.clips_for_track(left.track_id)
                    if clip.timeline_start == left.timeline_end
                )
            except StopIteration as error:
                raise ValueError("所选片段后没有同轨道的相邻片段") from error
            transition = timeline.create_transition(
                left.id,
                right.id,
                TransitionKind(preset_id),
                max(1, int(adoption.get("default_duration_frames") or 1)),
            )
            selection.transition_id = transition.id
            selection.clip_ids = []
        elif target == "visual-effect":
            if not selection.clip_ids:
                raise ValueError("请先选择一个片段")
            clip_id = selection.clip_ids[-1]
            effect = timeline.add_clip_visual_effect(clip_id, VisualEffectKind(preset_id))
            requested = adoption.get("parameters")
            if isinstance(requested, dict) and requested:
                parameters = dict(effect.parameters)
                parameters.update(
                    {
                        key: float(value)
                        for key, value in requested.items()
                        if key in parameters and isinstance(value, int | float)
                    }
                )
                timeline.update_clip_visual_effect(
                    clip_id,
                    effect.id,
                    enabled=effect.enabled,
                    parameters=parameters,
                )
        elif target == "audio-effect":
            self._adopt_audio_effect(preset_id, adoption)
            return
        else:
            raise ValueError(f"当前资源预设类型暂不受支持：{target}")
        self._session.projectors.timeline.refresh_timeline()
        self._session.projectors.timeline.schedule_preview_graph()
        self._session.updates.commit(selection=True)
        self._session.updates.commit(history=True)
        if target == "transition":
            self._session._set_status("转场已从资源库添加")
        else:
            self._session._set_status("视觉效果已从资源库添加")

    def _adopt_audio_effect(self, preset_id: str, adoption: dict) -> None:
        project = self._session.state.binding.require_current()
        buses = project.list_audio_buses(self._session.state.binding.active_sequence_id)
        selected_bus_id = self._session.state.selection.audio_bus_id
        bus = next((item for item in buses if item.id == selected_bus_id), None)
        if bus is None:
            bus = next((item for item in buses if item.parent_bus_id is None), None)
        if bus is None:
            raise RuntimeError("序列缺少可应用效果的音频总线")
        effect_kind = AudioEffectKind(preset_id)
        requested = adoption.get("parameters")
        parameters = dict(requested) if isinstance(requested, dict) else {}
        if effect_kind == AudioEffectKind.LOUDNESS_NORMALIZE:
            parameters.update(
                {
                    "target_lufs": self._session.state.service_settings.audio.loudness_target_lufs,
                    "true_peak_db": self._session.state.service_settings.audio.true_peak_db,
                }
            )
        elif effect_kind == AudioEffectKind.DUCKING and not parameters.get("driver_bus_id"):
            parameters["driver_bus_id"] = next(
                (item.id for item in buses if item.name in {"对白", "Dialogue"}),
                "",
            )
        effects = project.list_audio_effects(bus.id)
        effect = project.save_audio_effect(
            AudioEffect(
                bus_id=bus.id,
                kind=effect_kind,
                position=len(effects),
                parameters=parameters,
            )
        )
        self._session.state.selection.audio_bus_id = bus.id
        self._session.state.selection.audio_effect_id = effect.id
        self._session.projectors.audio.refresh_audio_effects()
        self._session.projectors.audio.invalidate_audio_metrics()
        self._session.projectors.timeline.schedule_preview_graph()
        self._session.updates.commit(selection=True)
        self._session.updates.commit(history=True)
        self._session._set_status("音频效果已从资源库添加")
