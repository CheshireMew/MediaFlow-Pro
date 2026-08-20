from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

from mediaflow.domain.editable_media_contract import EditableMediaContract

EDITABLE_MEDIA_SCHEMA_PATH = (
    Path(__file__).resolve().parents[1]
    / "resources"
    / "contracts"
    / "editable-media.v6.schema.json"
)


@lru_cache(maxsize=1)
def editable_media_contract() -> EditableMediaContract:
    document = json.loads(EDITABLE_MEDIA_SCHEMA_PATH.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise RuntimeError("editable-media v6 schema root must be an object")
    return EditableMediaContract(cast(dict[str, Any], document))


def validate_editable_media_document(document: object) -> None:
    editable_media_contract().validate(document)
