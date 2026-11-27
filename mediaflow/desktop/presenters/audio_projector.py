from __future__ import annotations

from mediaflow.desktop.component_contexts import AudioProjectorContext
from mediaflow.desktop.presentation_messages import system_name
from mediaflow.desktop.presentation_timeline import audio_effect_label
from mediaflow.domain.audio import audio_effect_definition

from .base import Projector


class AudioProjector(Projector[AudioProjectorContext]):
    def refresh_audio_buses(self) -> None:
        if not self._context.state.binding.current or not self._context.state.binding.active_sequence_id:
            self._context.models.audio_buses.set_items([])
            return
        self._context.models.audio_buses.set_items(
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
                for bus in self._context.state.binding.require_current().list_audio_buses(
                    self._context.state.binding.active_sequence_id
                )
            ]
        )
        bus_ids = {
            bus.id
            for bus in self._context.state.binding.require_current().list_audio_buses(
                self._context.state.binding.active_sequence_id
            )
        }
        if self._context.state.selection.audio_bus_id not in bus_ids:
            self._context.state.selection.audio_bus_id = ""
        self.refresh_audio_effects()

    def refresh_audio_effects(self) -> None:
        if not self._context.state.binding.current or not self._context.state.selection.audio_bus_id:
            self._context.models.audio_effects.set_items([])
            self._context.state.selection.audio_effect_id = ""
            self.refresh_audio_effect_parameters()
            return
        effects = self._context.state.binding.require_current().list_audio_effects(
            self._context.state.selection.audio_bus_id
        )
        self._context.models.audio_effects.set_items(
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
        if self._context.state.selection.audio_effect_id not in {effect.id for effect in effects}:
            self._context.state.selection.audio_effect_id = ""
        self.refresh_audio_effect_parameters()

    def refresh_audio_effect_parameters(self) -> None:
        if (
            not self._context.state.binding.current
            or not self._context.state.selection.audio_bus_id
            or not self._context.state.selection.audio_effect_id
        ):
            self._context.models.audio_effect_parameters.set_items([])
            return
        try:
            effect = next(
                effect
                for effect in self._context.state.binding.require_current().list_audio_effects(
                    self._context.state.selection.audio_bus_id
                )
                if effect.id == self._context.state.selection.audio_effect_id
            )
        except StopIteration:
            self._context.models.audio_effect_parameters.set_items([])
            return
        buses = self._context.state.binding.require_current().list_audio_buses(
            self._context.state.binding.active_sequence_id
        )
        self._context.models.audio_effect_parameters.set_items(
            [
                {
                    "key": descriptor.id,
                    "descriptor": descriptor.model_dump(mode="json"),
                    "value": effect.parameters[descriptor.id],
                    "options": (
                        [{"label": system_name(bus.name), "value": bus.id} for bus in buses]
                        if descriptor.options_source == "audio-buses"
                        else []
                    ),
                }
                for descriptor in audio_effect_definition(effect.kind).descriptors
            ]
        )

    def refresh_audio_metrics(self) -> None:
        self.invalidate_audio_metrics()
        request_id = self._context.state.requests.audio_metrics_id
        if not self._context.state.binding.current or not self._context.state.binding.active_sequence_id:
            return
        generation = self._context.state.binding.generation
        project = self._context.state.binding.current
        sequence_id = self._context.state.binding.active_sequence_id
        self._context.submit_background(
            "audio_metrics",
            (generation, request_id, sequence_id),
            lambda: project.read_loudness_metrics(sequence_id),
        )

    def invalidate_audio_metrics(self) -> None:
        self._context.state.requests.audio_metrics_id += 1
        if self._context.state.presentation.audio_metrics:
            self._context.state.presentation.audio_metrics = {}
        self._context.updates.commit(audio_metrics=True)
