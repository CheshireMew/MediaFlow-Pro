from __future__ import annotations

import json

from mediaflow.domain.audio import AudioBus, AudioEffect

from .project_serialization import json_value as _json


class AudioRepository:
    def list_audio_buses(self, sequence_id: str) -> list[AudioBus]:
        rows = self._fetchall(
            "SELECT * FROM audio_bus WHERE sequence_id=? ORDER BY position, id",
            (sequence_id,),
        )
        return [
            AudioBus(
                id=row["id"],
                sequence_id=row["sequence_id"],
                name=row["name"],
                parent_bus_id=row["parent_bus_id"],
                position=row["position"],
                gain_db=row["gain_db"],
                muted=bool(row["muted"]),
                solo=bool(row["solo"]),
                channel_layout=row["channel_layout"],
            )
            for row in rows
        ]

    def save_audio_bus(self, bus: AudioBus) -> AudioBus:
        sequence = self.get_sequence(bus.sequence_id)
        del sequence
        buses = {item.id: item for item in self.list_audio_buses(bus.sequence_id)}
        if bus.parent_bus_id == bus.id:
            raise ValueError("Audio bus cannot route to itself")
        if bus.parent_bus_id:
            parent = buses.get(bus.parent_bus_id)
            if parent is None:
                raise ValueError("Audio bus parent does not exist in this sequence")
            seen = {bus.id}
            cursor: AudioBus | None = parent
            while cursor is not None:
                if cursor.id in seen:
                    raise ValueError("Audio bus routing cannot contain a cycle")
                seen.add(cursor.id)
                cursor = buses.get(cursor.parent_bus_id) if cursor.parent_bus_id else None
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO audio_bus(
                    id, sequence_id, name, parent_bus_id, position, gain_db,
                    muted, solo, channel_layout
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name, parent_bus_id=excluded.parent_bus_id,
                    position=excluded.position, gain_db=excluded.gain_db,
                    muted=excluded.muted, solo=excluded.solo,
                    channel_layout=excluded.channel_layout""",
                (
                    bus.id,
                    bus.sequence_id,
                    bus.name,
                    bus.parent_bus_id,
                    bus.position,
                    bus.gain_db,
                    int(bus.muted),
                    int(bus.solo),
                    bus.channel_layout,
                ),
            )
            self._touch_project(connection)
        return next(item for item in self.list_audio_buses(bus.sequence_id) if item.id == bus.id)

    def save_audio_effect(self, effect: AudioEffect) -> AudioEffect:
        with self.transaction() as connection:
            bus = connection.execute(
                "SELECT sequence_id FROM audio_bus WHERE id=?", (effect.bus_id,)
            ).fetchone()
            if bus is None:
                raise KeyError(effect.bus_id)
            connection.execute(
                """INSERT INTO audio_effect(
                    id, bus_id, kind, position, enabled, parameters_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    bus_id=excluded.bus_id, kind=excluded.kind,
                    position=excluded.position, enabled=excluded.enabled,
                    parameters_json=excluded.parameters_json""",
                (
                    effect.id,
                    effect.bus_id,
                    effect.kind.value,
                    effect.position,
                    int(effect.enabled),
                    _json(effect.parameters),
                ),
            )
            self._touch_project(connection)
        return effect

    def list_audio_effects(self, bus_id: str) -> list[AudioEffect]:
        rows = self._fetchall("SELECT * FROM audio_effect WHERE bus_id=? ORDER BY position, id", (bus_id,))
        return [
            AudioEffect(
                id=row["id"],
                bus_id=row["bus_id"],
                kind=row["kind"],
                position=row["position"],
                enabled=bool(row["enabled"]),
                parameters=json.loads(row["parameters_json"]),
            )
            for row in rows
        ]

    def save_audio_effect_chain(self, bus_id: str, effects: list[AudioEffect]) -> list[AudioEffect]:
        existing_ids = {effect.id for effect in self.list_audio_effects(bus_id)}
        if {effect.id for effect in effects} != existing_ids:
            raise ValueError("Audio effect reordering must preserve the complete chain")
        if any(effect.bus_id != bus_id for effect in effects):
            raise ValueError("Audio effect chain contains an effect from another bus")
        if [effect.position for effect in effects] != list(range(len(effects))):
            raise ValueError("Audio effect positions must be contiguous")
        with self.transaction() as connection:
            for effect in effects:
                connection.execute(
                    """UPDATE audio_effect SET position=?, enabled=?, parameters_json=?
                       WHERE id=? AND bus_id=?""",
                    (
                        effect.position,
                        int(effect.enabled),
                        _json(effect.parameters),
                        effect.id,
                        bus_id,
                    ),
                )
            self._touch_project(connection)
        return self.list_audio_effects(bus_id)

    def remove_audio_effect(self, effect_id: str) -> None:
        row = self._fetchone("SELECT bus_id FROM audio_effect WHERE id=?", (effect_id,))
        if row is None:
            raise KeyError(effect_id)
        bus_id = row["bus_id"]
        with self.transaction() as connection:
            connection.execute("DELETE FROM audio_effect WHERE id=?", (effect_id,))
            remaining = connection.execute(
                "SELECT id FROM audio_effect WHERE bus_id=? ORDER BY position, id",
                (bus_id,),
            ).fetchall()
            for position, effect in enumerate(remaining):
                connection.execute(
                    "UPDATE audio_effect SET position=? WHERE id=?",
                    (position, effect["id"]),
                )
            self._touch_project(connection)
