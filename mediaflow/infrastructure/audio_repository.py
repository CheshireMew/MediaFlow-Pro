from __future__ import annotations

import json
import sqlite3

from mediaflow.domain.audio import AudioBus, AudioEffect
from mediaflow.domain.enums import AudioEffectKind

from .project_repository_component import ProjectRepositoryComponent
from .project_serialization import json_value as _json


class AudioRepository(ProjectRepositoryComponent):
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
        sequence = self._relations.sequences.get_sequence(bus.sequence_id)
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

    def replace_audio_graph(
        self,
        sequence_id: str,
        buses: list[AudioBus],
        effects: list[AudioEffect],
    ) -> None:
        """Replace one sequence's complete routing graph inside the caller transaction."""

        self._relations.sequences.get_sequence(sequence_id)
        by_id = {bus.id: bus for bus in buses}
        if not buses or len(by_id) != len(buses):
            raise ValueError("Audio graph buses must be non-empty and unique")
        if any(bus.sequence_id != sequence_id for bus in buses):
            raise ValueError("Audio graph contains a bus from another sequence")
        roots = [bus for bus in buses if bus.parent_bus_id is None]
        if len(roots) != 1:
            raise ValueError("An audio graph must have exactly one master bus")
        for bus in buses:
            seen = {bus.id}
            parent_id = bus.parent_bus_id
            while parent_id is not None:
                if parent_id not in by_id:
                    raise ValueError("Audio graph references an unknown parent bus")
                if parent_id in seen:
                    raise ValueError("Audio graph routing cannot contain a cycle")
                seen.add(parent_id)
                parent_id = by_id[parent_id].parent_bus_id

        effect_ids = {effect.id for effect in effects}
        if len(effect_ids) != len(effects):
            raise ValueError("Audio graph effects must be unique")
        if any(effect.bus_id not in by_id for effect in effects):
            raise ValueError("Audio graph effect references an unknown bus")
        for effect in effects:
            if effect.kind != AudioEffectKind.DUCKING:
                continue
            driver_bus_id = str(effect.parameters.get("driver_bus_id", ""))
            if driver_bus_id and driver_bus_id not in by_id:
                raise ValueError("Audio ducking effect references an unknown driver bus")

        def depth(bus: AudioBus) -> int:
            result = 0
            parent_id = bus.parent_bus_id
            while parent_id is not None:
                result += 1
                parent_id = by_id[parent_id].parent_bus_id
            return result

        with self.transaction() as connection:
            existing = self.list_audio_buses(sequence_id)
            connection.execute(
                "UPDATE track SET audio_bus_id=NULL WHERE sequence_id=?",
                (sequence_id,),
            )
            for bus in sorted(
                existing,
                key=lambda item: self._stored_bus_depth(item, existing),
                reverse=True,
            ):
                connection.execute("DELETE FROM audio_bus WHERE id=?", (bus.id,))
            for bus in sorted(buses, key=depth):
                self.insert_bus_record(connection, bus)
            for effect in sorted(effects, key=lambda item: (item.bus_id, item.position, item.id)):
                connection.execute(
                    """INSERT INTO audio_effect(
                        id, bus_id, kind, position, enabled, parameters_json
                    ) VALUES (?, ?, ?, ?, ?, ?)""",
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

    @staticmethod
    def insert_bus_record(connection: sqlite3.Connection, bus: AudioBus) -> None:
        connection.execute(
            """INSERT INTO audio_bus(
                id, sequence_id, name, parent_bus_id, position, gain_db,
                muted, solo, channel_layout
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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

    @staticmethod
    def _stored_bus_depth(bus: AudioBus, buses: list[AudioBus]) -> int:
        by_id = {item.id: item for item in buses}
        result = 0
        seen = {bus.id}
        parent_id = bus.parent_bus_id
        while parent_id is not None:
            if parent_id in seen or parent_id not in by_id:
                raise ValueError("Stored audio graph is invalid")
            seen.add(parent_id)
            result += 1
            parent_id = by_id[parent_id].parent_bus_id
        return result
