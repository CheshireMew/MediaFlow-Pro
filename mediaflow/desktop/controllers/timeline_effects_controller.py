from __future__ import annotations

from PySide6.QtCore import Property, Signal, Slot

from mediaflow.domain.enums import VisualEffectKind
from mediaflow.domain.visual_effects import VISUAL_EFFECT_DEFINITIONS

from .controller_facet import ControllerFacet, report_ui_errors
from .controller_scopes import TimelinePresentationScope
from .timeline_selection import selected_clip_id


class TimelineEffectsController(ControllerFacet[TimelinePresentationScope]):
    selectionChanged = Signal()

    @Property(list, notify=selectionChanged)
    def visualEffectOptions(self) -> list[dict]:
        return [
            {"label": definition.label, "value": kind.value}
            for kind, definition in VISUAL_EFFECT_DEFINITIONS.items()
        ]

    @Property(list, notify=selectionChanged)
    def selectedClipVisualEffects(self) -> list[dict]:
        if not self._session.state.binding.timeline or not selected_clip_id(self._session):
            return []
        clip = next(
            item
            for item in self._session.state.binding.require_timeline().state.clips
            if item.id == selected_clip_id(self._session)
        )
        rows = []
        for effect in clip.visual_effects:
            definition = VISUAL_EFFECT_DEFINITIONS[effect.kind]
            rows.append(
                {
                    "effectId": effect.id,
                    "kind": effect.kind.value,
                    "label": definition.label,
                    "position": effect.position,
                    "enabled": effect.enabled,
                    "parameters": dict(effect.parameters),
                    "parameterSpecs": [
                        {
                            "path": f"visual-effects.{effect.id}.{descriptor.id}",
                            "target": "visual-effect",
                            "source_id": descriptor.id,
                            "descriptor": descriptor.model_dump(mode="json"),
                            "value": effect.parameters[descriptor.id],
                            "locked": False,
                        }
                        for descriptor in definition.descriptors
                    ],
                }
            )
        return rows

    @Slot(str)
    @report_ui_errors
    def addSelectedClipVisualEffect(self, kind: str) -> None:
        self._session._require_writable()
        if not selected_clip_id(self._session):
            raise ValueError("请先选择一个片段")
        self._session.state.binding.require_timeline().add_clip_visual_effect(
            selected_clip_id(self._session),
            VisualEffectKind(kind),
        )
        self._after_visual_effect_change("视觉效果已添加")

    @Slot(str, bool)
    @report_ui_errors
    def setSelectedClipVisualEffectEnabled(self, effect_id: str, enabled: bool) -> None:
        self._session._require_writable()
        clip, effect = self._selected_visual_effect(effect_id)
        self._session.state.binding.require_timeline().update_clip_visual_effect(
            clip.id,
            effect.id,
            enabled=enabled,
            parameters=effect.parameters,
        )
        self._after_visual_effect_change("视觉效果已更新")

    @Slot(str, str, float)
    @report_ui_errors
    def setSelectedClipVisualEffectParameter(
        self,
        effect_id: str,
        key: str,
        value: float,
    ) -> None:
        self._session._require_writable()
        clip, effect = self._selected_visual_effect(effect_id)
        parameters = dict(effect.parameters)
        if key not in parameters:
            raise ValueError("未知的视觉效果参数")
        parameters[key] = value
        self._session.state.binding.require_timeline().update_clip_visual_effect(
            clip.id,
            effect.id,
            enabled=effect.enabled,
            parameters=parameters,
        )
        self._after_visual_effect_change("视觉效果已更新")

    @Slot(str, int)
    @report_ui_errors
    def moveSelectedClipVisualEffect(self, effect_id: str, position: int) -> None:
        self._session._require_writable()
        clip, _effect = self._selected_visual_effect(effect_id)
        self._session.state.binding.require_timeline().move_clip_visual_effect(
            clip.id,
            effect_id,
            position,
        )
        self._after_visual_effect_change("视觉效果顺序已更新")

    @Slot(str)
    @report_ui_errors
    def removeSelectedClipVisualEffect(self, effect_id: str) -> None:
        self._session._require_writable()
        clip, _effect = self._selected_visual_effect(effect_id)
        self._session.state.binding.require_timeline().remove_clip_visual_effect(clip.id, effect_id)
        self._after_visual_effect_change("视觉效果已移除")

    def _selected_visual_effect(self, effect_id: str):
        if not self._session.state.binding.timeline or not selected_clip_id(self._session):
            raise ValueError("请先选择一个片段")
        clip = next(
            item
            for item in self._session.state.binding.require_timeline().state.clips
            if item.id == selected_clip_id(self._session)
        )
        effect = next(item for item in clip.visual_effects if item.id == effect_id)
        return clip, effect

    def _after_visual_effect_change(
        self,
        status_source: str,
        *status_arguments: object,
    ) -> None:
        self._session.projectors.timeline.refresh_timeline()
        self._session.projectors.timeline.schedule_preview_graph()
        self._session.updates.commit(selection=True)
        self._session.updates.commit(history=True)
        self._session._set_status(status_source, *status_arguments)
