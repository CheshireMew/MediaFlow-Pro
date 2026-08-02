from __future__ import annotations

import json

from mediaflow.domain.web_media import (
    WebAssetSpec,
    WebClipState,
    editable_media_manifest_document,
)

from .project_repository_component import ProjectRepositoryComponent
from .project_serialization import json_value as _json


class WebMediaRepository(ProjectRepositoryComponent):
    def save_web_asset_spec(self, spec: WebAssetSpec) -> WebAssetSpec:
        asset = self._owner.catalog.get_asset(spec.asset_id)
        if asset.kind.value != "web":
            raise ValueError("Editable media metadata can only belong to a web asset")
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO web_asset(asset_id, manifest_json, source_hash)
                   VALUES (?, ?, ?)
                   ON CONFLICT(asset_id) DO UPDATE SET
                       manifest_json=excluded.manifest_json,
                       source_hash=excluded.source_hash""",
                (
                    spec.asset_id,
                    _json(
                        editable_media_manifest_document(spec.manifest)
                    ),
                    spec.source_hash,
                ),
            )
            self._touch_project(connection)
        return spec

    def get_web_asset_spec(self, asset_id: str) -> WebAssetSpec:
        row = self._fetchone("SELECT * FROM web_asset WHERE asset_id=?", (asset_id,))
        if row is None:
            raise KeyError(asset_id)
        return WebAssetSpec(
            asset_id=row["asset_id"],
            manifest=json.loads(row["manifest_json"]),
            source_hash=row["source_hash"],
        )

    def list_web_asset_specs(self) -> list[WebAssetSpec]:
        return [
            WebAssetSpec(
                asset_id=row["asset_id"],
                manifest=json.loads(row["manifest_json"]),
                source_hash=row["source_hash"],
            )
            for row in self._fetchall("SELECT * FROM web_asset ORDER BY asset_id")
        ]

    def get_web_clip_state(self, clip_id: str) -> WebClipState:
        row = self._fetchone("SELECT * FROM web_clip_state WHERE clip_id=?", (clip_id,))
        if row is None:
            raise KeyError(clip_id)
        payload = json.loads(row["state_json"])
        return WebClipState.model_validate(
            {**payload, "clip_id": clip_id, "revision": row["revision"]}
        )

    def list_web_clip_states(self, sequence_id: str) -> dict[str, WebClipState]:
        rows = self._fetchall(
            """SELECT state.* FROM web_clip_state AS state
               JOIN clip ON clip.id=state.clip_id
               JOIN track ON track.id=clip.track_id
               WHERE track.sequence_id=? ORDER BY state.clip_id""",
            (sequence_id,),
        )
        return {
            row["clip_id"]: WebClipState.model_validate(
                {
                    **json.loads(row["state_json"]),
                    "clip_id": row["clip_id"],
                    "revision": row["revision"],
                }
            )
            for row in rows
        }

    def save_web_clip_states(self, states: list[WebClipState]) -> None:
        if not states:
            return
        clip_ids = [state.clip_id for state in states]
        if len(set(clip_ids)) != len(clip_ids):
            raise ValueError("Editable media clip states must be unique")
        placeholders = ",".join("?" for _ in clip_ids)
        rows = self._fetchall(
            f"""SELECT clip.id, asset.kind FROM clip
                  JOIN asset ON asset.id=clip.asset_id
                  WHERE clip.id IN ({placeholders})""",
            clip_ids,
        )
        if {row["id"] for row in rows} != set(clip_ids) or any(
            row["kind"] != "web" for row in rows
        ):
            raise ValueError("Editable media state must belong to existing web clips")
        with self.transaction() as connection:
            for state in states:
                self._upsert_web_clip_state(connection, state)
            self._touch_project(connection)

    @staticmethod
    def _upsert_web_clip_state(connection, state: WebClipState) -> None:
        payload = state.model_dump(
            mode="json",
            exclude={"clip_id", "revision"},
            exclude_none=True,
        )
        connection.execute(
            """INSERT INTO web_clip_state(clip_id, state_json, revision)
               VALUES (?, ?, ?)
               ON CONFLICT(clip_id) DO UPDATE SET
                   state_json=excluded.state_json,
                   revision=excluded.revision""",
            (state.clip_id, _json(payload), state.revision),
        )
