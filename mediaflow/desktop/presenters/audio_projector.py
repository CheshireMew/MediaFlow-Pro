from __future__ import annotations

from mediaflow.desktop.presentation_catalogs import audio_effect_label, system_name
from mediaflow.domain.audio import audio_effect_definition

from .base import Projector


class AudioProjector(Projector):
    def refresh_audio_buses(self) -> None:
        if not self._session.binding.current or not self._session.binding.active_sequence_id:
            self._session.models.audio_buses.set_items([])
            return
        self._session.models.audio_buses.set_items(
            [
                {
                    "busId": bus.id,
                    "name": bus.name,
                    "displayName": system_name(bus.name),
                    "parentBusId": bus.parent_bus_id or "",
                    "gainDb": bus.gain_db,
                    "muted": bus.muted,
                    "solo": bus.solo,
                    "channelLayout": bus.channel_layout,
                }
                for bus in self._session.binding.current.list_audio_buses(
                    self._session.binding.active_sequence_id
                )
            ]
        )
        bus_ids = {
            bus.id
            for bus in self._session.binding.current.list_audio_buses(
                self._session.binding.active_sequence_id
            )
        }
        if self._session.selection.audio_bus_id not in bus_ids:
            self._session.selection.audio_bus_id = ""
        self.refresh_audio_effects()

    def refresh_audio_effects(self) -> None:
        if not self._session.binding.current or not self._session.selection.audio_bus_id:
            self._session.models.audio_effects.set_items([])
            self._session.selection.audio_effect_id = ""
            self.refresh_audio_effect_parameters()
            return
        effects = self._session.binding.current.list_audio_effects(self._session.selection.audio_bus_id)
        self._session.models.audio_effects.set_items(
            [
                {
                    "effectId": effect.id,
                    "busId": effect.bus_id,
                    "kind": effect.kind.value,
                    "displayName": audio_effect_label(effect.kind),
                    "position": effect.position,
                    "enabled": effect.enabled,
                    "parameters": effect.parameters,
                }
                for effect in effects
            ]
        )
        if self._session.selection.audio_effect_id not in {effect.id for effect in effects}:
            self._session.selection.audio_effect_id = ""
        self.refresh_audio_effect_parameters()

    def refresh_audio_effect_parameters(self) -> None:
        if (
            not self._session.binding.current
            or not self._session.selection.audio_bus_id
            or not self._session.selection.audio_effect_id
        ):
            self._session.models.audio_effect_parameters.set_items([])
            return
        try:
            effect = next(
                effect
                for effect in self._session.binding.current.list_audio_effects(
                    self._session.selection.audio_bus_id
                )
                if effect.id == self._session.selection.audio_effect_id
            )
        except StopIteration:
            self._session.models.audio_effect_parameters.set_items([])
            return
        buses = self._session.binding.current.list_audio_buses(
            self._session.binding.active_sequence_id
        )
        self._session.models.audio_effect_parameters.set_items(
            [
                {
                    "key": descriptor.id,
                    "descriptor": descriptor.model_dump(mode="json"),
                    "value": effect.parameters[descriptor.id],
                    "options": (
                        [
                            {"label": system_name(bus.name), "value": bus.id}
                            for bus in buses
                        ]
                        if descriptor.options_source == "audio-buses"
                        else []
                    ),
                }
                for descriptor in audio_effect_definition(effect.kind).descriptors
            ]
        )

    def refresh_audio_metrics(self) -> None:
        self.invalidate_audio_metrics()
        request_id = self._session.requests.audio_metrics_id
        if not self._session.binding.current or not self._session.binding.active_sequence_id:
            return
        generation = self._session.binding.generation
        project = self._session.binding.current
        sequence_id = self._session.binding.active_sequence_id
        self._session.background.submit(
            "audio_metrics",
            (generation, request_id, sequence_id),
            lambda: project.read_loudness_metrics(sequence_id),
        )

    def invalidate_audio_metrics(self) -> None:
        self._session.requests.audio_metrics_id += 1
        if self._session.presentation.audio_metrics:
            self._session.presentation.audio_metrics = {}
        self._session.events.audioMetricsChanged.emit()
