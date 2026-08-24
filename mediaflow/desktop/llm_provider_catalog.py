from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from mediaflow.desktop.presentation_llm import llm_provider_label, llm_reasoning_label

LLM_PROVIDER_CATALOG = (
    Path(__file__).resolve().parents[1]
    / "resources"
    / "llm-provider-presets.v1.json"
)


@dataclass(frozen=True, slots=True)
class LlmProviderPreset:
    id: str
    label: str
    base_url: str
    standard_model: str
    reasoning_model: str
    custom: bool = False

    def presentation(self) -> dict[str, object]:
        return {
            "text": llm_provider_label(self.label),
            "value": self.id,
            "baseUrl": self.base_url,
            "standardModel": self.standard_model,
            "reasoningModel": self.reasoning_model,
            "reasoningLabel": llm_reasoning_label(self.reasoning_model),
            "custom": self.custom,
        }


@lru_cache(maxsize=1)
def load_llm_provider_catalog(
    path: str | Path = LLM_PROVIDER_CATALOG,
) -> tuple[LlmProviderPreset, ...]:
    source = Path(path).resolve()
    document: Any = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise ValueError("LLM provider preset catalog schema is not supported")
    records = document.get("providers")
    if not isinstance(records, list) or not records:
        raise ValueError("LLM provider preset catalog has no providers")
    presets: list[LlmProviderPreset] = []
    identities: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("LLM provider preset must be an object")
        preset = LlmProviderPreset(
            id=str(record.get("id") or ""),
            label=str(record.get("label") or ""),
            base_url=str(record.get("base_url") or ""),
            standard_model=str(record.get("standard_model") or ""),
            reasoning_model=str(record.get("reasoning_model") or ""),
            custom=record.get("custom") is True,
        )
        if not preset.id or preset.id in identities:
            raise ValueError(f"Invalid or duplicate LLM provider preset id: {preset.id!r}")
        if not preset.custom and not all(
            (preset.label, preset.base_url, preset.standard_model)
        ):
            raise ValueError(f"LLM provider preset is incomplete: {preset.id}")
        identities.add(preset.id)
        presets.append(preset)
    custom = [preset for preset in presets if preset.custom]
    if len(custom) != 1:
        raise ValueError("LLM provider preset catalog requires exactly one custom preset")
    return tuple(presets)


def llm_provider_presets() -> list[dict[str, object]]:
    return [preset.presentation() for preset in load_llm_provider_catalog()]
