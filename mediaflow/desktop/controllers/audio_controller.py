from __future__ import annotations

from PySide6.QtCore import Property, QObject, Signal, Slot

from mediaflow.desktop.presentation_catalogs import (
    audio_preset_options,
)
from mediaflow.domain.audio import AudioBus, AudioEffect
from mediaflow.domain.audio_effect_presets import audio_effect_preset
from mediaflow.domain.enums import (
    AudioEffectKind,
)
from mediaflow.domain.task_commands import (
    AnalyzeLoudnessCommand,
)

from .controller_facet import ControllerFacet, report_ui_errors


class AudioController(ControllerFacet):
    selectionChanged = Signal()
    tasksChanged = Signal()
    audioMetricsChanged = Signal()
    errorOccurred = Signal(str)

    @Property(QObject, constant=True)
    def audioBusesModel(self) -> QObject:
        return self._session.models.audio_buses

    @Property(QObject, constant=True)
    def audioEffectsModel(self) -> QObject:
        return self._session.models.audio_effects

    @Property(QObject, constant=True)
    def audioEffectParametersModel(self) -> QObject:
        return self._session.models.audio_effect_parameters

    @Property(str, notify=selectionChanged)
    def selectedAudioBusId(self) -> str:
        return self._session.selection.audio_bus_id

    @Property(str, notify=selectionChanged)
    def selectedAudioEffectId(self) -> str:
        return self._session.selection.audio_effect_id

    @Property("QVariantMap", notify=audioMetricsChanged)
    def audioMetrics(self) -> dict:
        return self._session.presentation.audio_metrics

    @Property(bool, notify=audioMetricsChanged)
    def audioAnalysisRunning(self) -> bool:
        if not self._session.binding.current or not self._session.binding.active_sequence_id:
            return False
        return any(
            isinstance(task.command, AnalyzeLoudnessCommand)
            and task.command.sequence_id == self._session.binding.active_sequence_id
            and task.status.is_in_flight
            for task in self._session.task_state.items.values()
        )

    @Slot(str)
    def selectAudioBus(self, bus_id: str) -> None:
        self._session.selection.audio_bus_id = bus_id
        self._session.selection.audio_effect_id = ""
        self._session.projectors.audio.refresh_audio_effects()
        self._session.events.selectionChanged.emit()

    @Slot(str)
    def selectAudioEffect(self, effect_id: str) -> None:
        self._session.selection.audio_effect_id = effect_id
        self._session.projectors.audio.refresh_audio_effect_parameters()
        self._session.events.selectionChanged.emit()

    @Slot(str, float, bool, bool)
    @Slot(str, float, bool, bool, str, str)
    @report_ui_errors
    def updateAudioBus(
        self,
        bus_id: str,
        gain_db: float,
        muted: bool,
        solo: bool,
        parent_bus_id: str = "",
        channel_layout: str = "",
    ) -> None:
        self._session._require_writable()
        bus = next(
            item
            for item in self._session.binding.current.list_audio_buses(
                self._session.binding.active_sequence_id
            )
            if item.id == bus_id
        )
        self._session.binding.current.save_audio_bus(
            bus.model_copy(
                update={
                    "gain_db": max(-60.0, min(12.0, gain_db)),
                    "muted": muted,
                    "solo": solo,
                    "parent_bus_id": (
                        parent_bus_id or None if parent_bus_id or channel_layout else bus.parent_bus_id
                    ),
                    "channel_layout": channel_layout or bus.channel_layout,
                }
            )
        )
        self._session.projectors.audio.refresh_audio_buses()
        self._session.projectors.audio.invalidate_audio_metrics()
        self._session.projectors.timeline.schedule_preview_graph()
        self._session._set_status(f"已更新 {bus.name} 总线")

    @Slot(str)
    @report_ui_errors
    def addAudioBus(self, name: str) -> None:
        self._session._require_writable()
        buses = self._session.binding.current.list_audio_buses(self._session.binding.active_sequence_id)
        master = next((item for item in buses if item.parent_bus_id is None), None)
        if master is None:
            raise RuntimeError("序列缺少主总线")
        label = name.strip() or f"总线 {len(buses)}"
        bus = self._session.binding.current.save_audio_bus(
            AudioBus(
                sequence_id=self._session.binding.active_sequence_id,
                name=label,
                parent_bus_id=master.id,
                position=len(buses),
                channel_layout=master.channel_layout,
            )
        )
        self._session.selection.audio_bus_id = bus.id
        self._session.projectors.audio.refresh_audio_buses()
        self._session.events.selectionChanged.emit()
        self._session.projectors.audio.invalidate_audio_metrics()
        self._session.projectors.timeline.schedule_preview_graph()

    @Slot(str, str)
    @report_ui_errors
    def addAudioEffect(self, bus_id: str, kind: str) -> None:
        self._session._require_writable()
        effect_kind = AudioEffectKind(kind)
        effects = self._session.binding.current.list_audio_effects(bus_id)
        parameters: dict = {}
        if effect_kind == AudioEffectKind.LOUDNESS_NORMALIZE:
            parameters = {
                "target_lufs": self._session.service_settings.audio.loudness_target_lufs,
                "true_peak_db": self._session.service_settings.audio.true_peak_db,
            }
        elif effect_kind == AudioEffectKind.DUCKING:
            parameters = {
                "driver_bus_id": next(
                    (
                        bus.id
                        for bus in self._session.binding.current.list_audio_buses(
                            self._session.binding.active_sequence_id
                        )
                        if bus.name in {"对白", "Dialogue"}
                    ),
                    "",
                ),
            }
        effect = self._session.binding.current.save_audio_effect(
            AudioEffect(
                bus_id=bus_id,
                kind=effect_kind,
                position=len(effects),
                parameters=parameters,
            )
        )
        self._session.selection.audio_bus_id = bus_id
        self._session.selection.audio_effect_id = effect.id
        self._session.projectors.audio.refresh_audio_effects()
        self._session.projectors.audio.invalidate_audio_metrics()
        self._session.projectors.timeline.schedule_preview_graph()
        self._session.events.selectionChanged.emit()

    @Slot(str, bool)
    @report_ui_errors
    def setAudioEffectEnabled(self, effect_id: str, enabled: bool) -> None:
        self._session._require_writable()
        effects = self._session.binding.current.list_audio_effects(self._session.selection.audio_bus_id)
        effect = next(item for item in effects if item.id == effect_id)
        self._session.binding.current.save_audio_effect(effect.model_copy(update={"enabled": enabled}))
        self._session.projectors.audio.refresh_audio_effects()
        self._session.projectors.audio.invalidate_audio_metrics()
        self._session.projectors.timeline.schedule_preview_graph()

    @Slot(str, result="QVariantList")
    def audioEffectPresets(self, kind: str) -> list[dict]:
        try:
            return audio_preset_options(AudioEffectKind(kind))
        except ValueError:
            return []

    @Slot(str, str)
    @report_ui_errors
    def applyAudioEffectPreset(self, effect_id: str, preset_id: str) -> None:
        self._session._require_writable()
        effects = self._session.binding.current.list_audio_effects(self._session.selection.audio_bus_id)
        effect = next(item for item in effects if item.id == effect_id)
        validated = AudioEffect.model_validate(
            {
                **effect.model_dump(mode="python"),
                "parameters": audio_effect_preset(effect.kind, preset_id),
            }
        )
        self._session.binding.current.save_audio_effect(validated)
        self._session.selection.audio_effect_id = effect_id
        self._session.projectors.audio.refresh_audio_effects()
        self._session.projectors.audio.invalidate_audio_metrics()
        self._session.projectors.timeline.schedule_preview_graph()
        self._session.events.selectionChanged.emit()

    @Slot(str)
    @report_ui_errors
    def removeAudioEffect(self, effect_id: str) -> None:
        self._session._require_writable()
        self._session.binding.current.remove_audio_effect(effect_id)
        self._session.selection.audio_effect_id = ""
        self._session.projectors.audio.refresh_audio_effects()
        self._session.projectors.audio.invalidate_audio_metrics()
        self._session.projectors.timeline.schedule_preview_graph()
        self._session.events.selectionChanged.emit()

    @Slot(str, int)
    @report_ui_errors
    def moveAudioEffect(self, effect_id: str, position: int) -> None:
        self._session._require_writable()
        effects = self._session.binding.current.list_audio_effects(self._session.selection.audio_bus_id)
        source_index = next(index for index, effect in enumerate(effects) if effect.id == effect_id)
        destination = max(0, min(len(effects) - 1, position))
        effect = effects.pop(source_index)
        effects.insert(destination, effect)
        effects = [item.model_copy(update={"position": index}) for index, item in enumerate(effects)]
        self._session.binding.current.save_audio_effect_chain(self._session.selection.audio_bus_id, effects)
        self._session.selection.audio_effect_id = effect_id
        self._session.projectors.audio.refresh_audio_effects()
        self._session.projectors.audio.invalidate_audio_metrics()
        self._session.projectors.timeline.schedule_preview_graph()

    @Slot(str, str, object)
    @report_ui_errors
    def setAudioEffectParameter(self, effect_id: str, key: str, value: object) -> None:
        self._session._require_writable()
        effects = self._session.binding.current.list_audio_effects(self._session.selection.audio_bus_id)
        effect = next(item for item in effects if item.id == effect_id)
        parameters = dict(effect.parameters)
        parameters[key] = value
        validated = AudioEffect.model_validate(
            {
                **effect.model_dump(mode="python"),
                "parameters": parameters,
            }
        )
        self._session.binding.current.save_audio_effect(validated)
        self._session.selection.audio_effect_id = effect_id
        self._session.projectors.audio.refresh_audio_effects()
        self._session.projectors.audio.invalidate_audio_metrics()
        self._session.projectors.timeline.schedule_preview_graph()

    @Slot()
    @report_ui_errors
    def analyzeLoudness(self) -> None:
        self._session._require_writable()
        if not self._session.binding.active_sequence_id:
            raise RuntimeError("请先打开一个序列")
        self._session.tasks.start(
            AnalyzeLoudnessCommand(sequence_id=self._session.binding.active_sequence_id),
            sequence_id=self._session.binding.active_sequence_id,
        )
