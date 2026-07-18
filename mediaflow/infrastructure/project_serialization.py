from __future__ import annotations

import json
from typing import Any


def json_value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def model_json(model: Any) -> str:
    return json_value(model.model_dump(mode="json"))
