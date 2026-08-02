from __future__ import annotations

import csv
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Literal

from mediaflow.application.ports import WebApplicationDocuments
from mediaflow.application.sequence_service import SequenceService
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.application.web_clip_editing_service import WebClipEditingService
from mediaflow.domain.enums import AssetKind
from mediaflow.domain.web_media import (
    WebVariantResult,
)


class WebBatchService:
    """Creates project sequences from structured editable-media records."""

    def __init__(
        self,
        repository: WebApplicationDocuments,
        timeline: Callable[[str], TimelineEditor],
        clips: WebClipEditingService,
    ) -> None:
        self.repository = repository
        self._timeline = timeline
        self._clips = clips

    def create_variants(
        self,
        source_sequence_id: str,
        clip_id: str,
        records: list[Mapping[str, object]],
        bindings: Mapping[str, str],
        *,
        name_template: str = "版本 {index}",
        actor: Literal["human", "automation"] = "automation",
    ) -> list[WebVariantResult]:
        if not records:
            raise ValueError("Batch variants require at least one record")
        source = self.repository.timeline.load_timeline(source_sequence_id)
        try:
            source_clip = next(item for item in source.clips if item.id == clip_id)
        except StopIteration as error:
            raise KeyError(clip_id) from error
        source_asset = self.repository.catalog.get_asset(source_clip.asset_id)
        if source_asset.kind != AssetKind.WEB:
            raise ValueError("Batch variants require an editable web clip")
        results: list[WebVariantResult] = []
        sequences = SequenceService(self.repository)
        for index, raw_record in enumerate(records, start=1):
            record = {str(key): value for key, value in raw_record.items()}
            try:
                name = name_template.format(index=index, **record).strip()
            except (KeyError, ValueError) as error:
                raise ValueError(f"Invalid variant name template: {error}") from error
            name = name or f"版本 {index}"
            sequence = sequences.create_short_from_bounds(
                source_sequence_id,
                source_clip.timeline_start,
                source_clip.timeline_end,
                name=name,
            )
            copied = self.repository.timeline.load_timeline(sequence.id)
            candidates = [
                item
                for item in copied.clips
                if item.asset_id == source_clip.asset_id and item.timeline_start == 0
            ]
            if len(candidates) != 1:
                raise RuntimeError(f"Could not identify the copied editable web clip for variant {index}")
            copied_clip = candidates[0]
            scene_layer_updates: dict[str, dict[str, dict[str, object]]] = {}
            theme_updates: dict[str, str | float] = {}
            scene_data_updates: dict[str, dict[str, object]] = {}
            selected_variant_id: str | None = None
            for source_key, target_path in bindings.items():
                if source_key not in record:
                    raise ValueError(f"Variant record is missing field: {source_key}")
                value = record[source_key]
                parts = target_path.split(".")
                if len(parts) == 5 and parts[0] == "scenes" and parts[2] == "layers":
                    scene_layer_updates.setdefault(parts[1], {}).setdefault(parts[3], {})[parts[4]] = value
                elif len(parts) == 2 and parts[0] == "theme":
                    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
                        raise ValueError(f"Theme binding {target_path} needs text or a number")
                    theme_updates[parts[1]] = value
                elif len(parts) == 4 and parts[0] == "scenes" and parts[2] == "data":
                    scene_data_updates.setdefault(parts[1], {})[parts[3]] = value
                elif target_path == "variant.id":
                    selected_variant_id = str(value)
                else:
                    raise ValueError(f"Unsupported variant binding target: {target_path}")
            state = self._clips.get_clip(copied_clip.id)
            for target_scene_id, layer_updates in scene_layer_updates.items():
                state = self._clips.update_clip(
                    sequence.id,
                    copied_clip.id,
                    layer_updates,
                    scene_id=target_scene_id,
                    expected_revision=state.revision,
                    actor=actor,
                )
            if theme_updates:
                state = self._clips.update_theme(
                    sequence.id,
                    copied_clip.id,
                    theme_updates,
                    expected_revision=state.revision,
                )
            for target_scene_id, data_updates in scene_data_updates.items():
                state = self._clips.update_data(
                    sequence.id,
                    copied_clip.id,
                    data_updates,
                    scene_id=target_scene_id,
                    expected_revision=state.revision,
                )
            if selected_variant_id is not None:
                state = self._clips.select_variant(
                    sequence.id,
                    copied_clip.id,
                    selected_variant_id,
                    expected_revision=state.revision,
                )
            state = self._clips.set_batch_name(
                sequence.id,
                copied_clip.id,
                name,
                expected_revision=state.revision,
            )
            results.append(
                WebVariantResult(
                    sequence_id=sequence.id,
                    clip_id=copied_clip.id,
                    name=name,
                    revision=state.revision,
                )
            )
        return results

    @staticmethod
    def read_variant_records(source: str | Path) -> list[Mapping[str, object]]:
        path = Path(source).expanduser().resolve(strict=True)
        if path.suffix.lower() == ".json":
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
                raise ValueError("Batch variant JSON must be an array of objects")
            return [
                {str(key): value for key, value in item.items()} for item in payload if isinstance(item, dict)
            ]
        if path.suffix.lower() == ".csv":
            with path.open("r", encoding="utf-8-sig", newline="") as stream:
                return list(csv.DictReader(stream))
        raise ValueError("Batch variant sources accept .json or .csv files")
