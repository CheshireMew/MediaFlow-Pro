from __future__ import annotations

import json
from typing import Any

from json_repair import loads as repair_json
from openai import OpenAI

from mediaflow.domain.settings import LlmProviderSettings


class OpenAIJsonClient:
    def __init__(self, provider: LlmProviderSettings):
        if not provider.enabled:
            raise ValueError("LLM provider is disabled")
        if not provider.api_key:
            raise ValueError("LLM provider API key is missing")
        self.provider = provider
        self.client = OpenAI(api_key=provider.api_key, base_url=provider.base_url)

    def complete_json(self, *, system: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = self.client.chat.completions.create(
            model=self.provider.model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                },
            ],
        )
        content = response.choices[0].message.content
        if not content:
            raise RuntimeError("LLM returned an empty response")
        parsed = repair_json(content)
        if not isinstance(parsed, dict):
            raise RuntimeError("LLM response must be a JSON object")
        return parsed
