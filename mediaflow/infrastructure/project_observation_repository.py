from __future__ import annotations

import json
from typing import Any

from mediaflow.domain.collaboration import ProjectWritePath

from .project_observation import ObservedProjectValue, ProjectObservation
from .project_repository_component import ProjectRepositoryComponent


class ProjectObservationRepository(ProjectRepositoryComponent):
    """Read the user-observable project document at declared mutation scopes."""

    def capture(self, scopes: list[str]) -> ProjectObservation:
        documents: dict[str, Any] = {}
        values: dict[str, ObservedProjectValue] = {}
        for scope in sorted(set(scopes)):
            path = ProjectWritePath.parse(scope)
            if not path.segments:
                raise ValueError("The project root is too broad for change observation")
            targeted = self._targeted_value(path)
            if targeted is not None:
                values[path.value] = targeted
                continue
            root = path.segments[0]
            document = documents.setdefault(root, self._root_document(root))
            values[path.value] = self._resolve(document, path.segments[1:])
        return ProjectObservation(values)

    def _targeted_value(self, path: ProjectWritePath) -> ObservedProjectValue | None:
        segments = tuple(self._pointer_value(segment) for segment in path.segments)
        if len(segments) == 3 and segments[0] == "sequences" and segments[2] == "tracks":
            return ObservedProjectValue(
                True,
                self._entity_map(self._relations.timeline.list_tracks(segments[1])),
            )
        if len(segments) == 3 and segments[0] == "sequences" and segments[2] == "transitions":
            return ObservedProjectValue(
                True,
                self._entity_map(self._relations.timeline.list_transitions(segments[1])),
            )
        if len(segments) < 4 or segments[0] != "sequences" or segments[2] != "clips":
            return None
        try:
            clip = self._relations.timeline.get_clip(segments[1], segments[3])
        except KeyError:
            return ObservedProjectValue(False)
        document = self._model(clip)
        if len(segments) == 4:
            return ObservedProjectValue(True, document)
        return self._resolve(document, path.segments[4:])

    def capture_schema_upgrade_baseline(self) -> ProjectObservation | None:
        """Capture v4 editable-media values before the schema can parse v6 models."""

        required_tables = {"asset", "web_asset", "web_clip_state", "clip"}
        available_tables = {
            str(row["name"])
            for row in self._fetchall("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        }
        if not required_tables.issubset(available_tables):
            return None
        web_rows = self._fetchall(
            """SELECT asset.id, asset.path, web_asset.manifest_json,
                      web_asset.source_hash
               FROM web_asset
               JOIN asset ON asset.id=web_asset.asset_id
               ORDER BY asset.id"""
        )
        values: dict[str, ObservedProjectValue] = {}
        asset_ids: list[str] = []
        for row in web_rows:
            manifest = json.loads(str(row["manifest_json"]))
            if manifest.get("version") != 4:
                continue
            asset_id = str(row["id"])
            asset_ids.append(asset_id)
            root = f"/assets/{self._pointer_segment(asset_id)}"
            values[f"{root}/path"] = ObservedProjectValue(True, str(row["path"]))
            values[f"{root}/web-package/manifest"] = ObservedProjectValue(True, manifest)
            values[f"{root}/web-package/source_hash"] = ObservedProjectValue(
                True,
                str(row["source_hash"]),
            )
        if not asset_ids:
            return None
        placeholders = ",".join("?" for _ in asset_ids)
        clip_rows = self._fetchall(
            f"""SELECT state.clip_id, state.state_json, state.revision
                  FROM web_clip_state AS state
                  JOIN clip ON clip.id=state.clip_id
                  WHERE clip.asset_id IN ({placeholders})
                  ORDER BY state.clip_id""",
            tuple(asset_ids),
        )
        for row in clip_rows:
            clip_id = str(row["clip_id"])
            state = json.loads(str(row["state_json"]))
            values[f"/web/clips/{self._pointer_segment(clip_id)}"] = ObservedProjectValue(
                True,
                {
                    **state,
                    "clip_id": clip_id,
                    "revision": int(row["revision"]),
                },
            )
        return ProjectObservation(values)

    def _root_document(self, root: str) -> Any:
        readers = {
            "project": self._project_document,
            "assets": self._assets_document,
            "asset-bins": self._asset_bins_document,
            "sequences": self._sequences_document,
            "subtitles": self._subtitles_document,
            "audio": self._audio_document,
            "web": self._web_document,
            "highlights": self._highlights_document,
            "tasks": self._tasks_document,
            "records": self._records_document,
        }
        try:
            return readers[root]()
        except KeyError as error:
            raise RuntimeError(f"Project mutation scope has no observable state reader: /{root}") from error

    def _project_document(self) -> dict[str, Any]:
        project = self._model(self._relations.projects.get_project())
        project["versions"] = self._entity_map(self._relations.records.list_project_versions())
        project["content"] = {
            "assets": self._assets_document(),
            "asset-bins": self._asset_bins_document(),
            "sequences": self._sequences_document(),
            "subtitles": self._subtitles_document(),
            "audio": self._audio_document(),
            "web": self._web_document(),
            "highlights": self._highlights_document(),
            "tasks": self._tasks_document(),
            "records": self._records_document(),
        }
        return project

    def _assets_document(self) -> dict[str, Any]:
        assets = self._entity_map(self._relations.assets.list_assets())
        web_specs = {item.asset_id: self._model(item) for item in self._relations.web.list_web_asset_specs()}
        for asset_id, spec in web_specs.items():
            if asset_id in assets:
                assets[asset_id]["web-package"] = spec
        return assets

    def _asset_bins_document(self) -> dict[str, Any]:
        return self._entity_map(self._relations.assets.list_asset_bins())

    def _sequences_document(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for sequence in self._relations.sequences.list_sequences(include_archived=True):
            state = self._relations.timeline.load_timeline(sequence.id)
            tracks = self._entity_map(state.tracks)
            tracks["order"] = [track.id for track in state.tracks]
            result[sequence.id] = {
                "settings": self._model(sequence, exclude={"timeline_revision"}),
                "tracks": tracks,
                "clips": self._entity_map(state.clips),
                "compounds": self._entity_map(state.compounds),
                "transitions": self._entity_map(state.transitions),
                "markers": self._entity_map(state.markers),
                "ranges": self._entity_map(state.ranges),
                "web-states": {clip_id: self._model(value) for clip_id, value in state.web_states.items()},
                "subtitles": self._sequence_subtitles(sequence.id),
            }
        return result

    def _subtitles_document(self) -> dict[str, Any]:
        documents: dict[str, Any] = {}
        all_segments: dict[str, Any] = {}
        all_words: dict[str, Any] = {}
        for document in self._relations.subtitles.list_subtitle_documents():
            segments = self._relations.subtitles.list_subtitle_segments(document.id)
            words = self._relations.subtitles.list_subtitle_words(document.id)
            all_segments.update(self._entity_map(segments))
            all_words.update(self._entity_map(words))
            documents[document.id] = {
                **self._model(document),
                "segments": self._entity_map(segments),
                "words": self._entity_map(words),
            }
        placement_rows = self._rows("SELECT * FROM subtitle_placement ORDER BY id")
        link_rows = self._rows("SELECT * FROM subtitle_track_document ORDER BY track_id, document_id")
        return {
            "documents": documents,
            "segments": all_segments,
            "words": all_words,
            "placements": {str(row["id"]): row for row in placement_rows},
            "track-links": {f"{row['track_id']}~{row['document_id']}": row for row in link_rows},
        }

    def _sequence_subtitles(self, sequence_id: str) -> dict[str, Any]:
        documents = self._relations.subtitles.list_subtitle_documents(sequence_id=sequence_id)
        return self._entity_map(documents)

    def _audio_document(self) -> dict[str, Any]:
        buses: dict[str, Any] = {}
        effects: dict[str, Any] = {}
        for sequence in self._relations.sequences.list_sequences(include_archived=True):
            for bus in self._relations.audio.list_audio_buses(sequence.id):
                buses[bus.id] = self._model(bus)
                for effect in self._relations.audio.list_audio_effects(bus.id):
                    effects[effect.id] = self._model(effect)
        return {"buses": buses, "effects": effects}

    def _web_document(self) -> dict[str, Any]:
        clips: dict[str, Any] = {}
        for sequence in self._relations.sequences.list_sequences(include_archived=True):
            for clip_id, state in self._relations.web.list_web_clip_states(sequence.id).items():
                clips[clip_id] = self._model(state)
        return {"clips": clips}

    def _highlights_document(self) -> dict[str, Any]:
        return self._entity_map(self._relations.highlights.list_highlights())

    def _tasks_document(self) -> dict[str, Any]:
        tasks = {str(row["id"]): row for row in self._rows("SELECT * FROM task ORDER BY id")}
        workflows = {str(row["id"]): row for row in self._rows("SELECT * FROM workflow_run ORDER BY id")}
        return {**tasks, "workflows": workflows}

    def _records_document(self) -> dict[str, Any]:
        return {
            "exports": {
                str(row["id"]): row for row in self._rows("SELECT * FROM export_history ORDER BY id")
            },
            "versions": self._entity_map(self._relations.records.list_project_versions()),
        }

    @staticmethod
    def _resolve(document: Any, segments: tuple[str, ...]) -> ObservedProjectValue:
        current = document
        for encoded in segments:
            segment = ProjectObservationRepository._pointer_value(encoded)
            if not isinstance(current, dict) or segment not in current:
                return ObservedProjectValue(False)
            current = current[segment]
        return ObservedProjectValue(True, current)

    def _rows(self, sql: str) -> list[dict[str, Any]]:
        return [{key: self._decode(row[key]) for key in row.keys()} for row in self._fetchall(sql)]

    @staticmethod
    def _decode(value: Any) -> Any:
        if not isinstance(value, str):
            return value
        if not value or value[0] not in "[{":
            return value
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value

    @staticmethod
    def _model(value: Any, *, exclude: set[str] | None = None) -> dict[str, Any]:
        return value.model_dump(
            mode="json",
            exclude=exclude,
            exclude_computed_fields=True,
        )

    @classmethod
    def _entity_map(cls, values: list[Any]) -> dict[str, Any]:
        return {str(value.id): cls._model(value) for value in values}

    @staticmethod
    def _pointer_segment(value: str) -> str:
        return value.replace("~", "~0").replace("/", "~1")

    @staticmethod
    def _pointer_value(value: str) -> str:
        return value.replace("~1", "/").replace("~0", "~")
