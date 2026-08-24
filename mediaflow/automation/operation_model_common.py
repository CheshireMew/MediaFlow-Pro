from __future__ import annotations

from typing import Literal

from pydantic import JsonValue, model_serializer

from mediaflow.domain.model_base import DomainModel
from mediaflow.domain.web_manifest import (
    WebAssetSpec,
    web_asset_spec_document,
)

Actor = Literal["human", "automation"]


class PublicWebAssetSpec(WebAssetSpec):
    @model_serializer(mode="plain")
    def serialize_public_document(self) -> dict[str, JsonValue]:
        return web_asset_spec_document(self)


class EmptyArguments(DomainModel):
    pass


class SequenceArguments(DomainModel):
    sequence_id: str | None = None
