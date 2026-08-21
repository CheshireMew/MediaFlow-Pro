from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, JsonValue, model_validator

from .model_base import DomainModel

ResourceIdentifier = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")]
SemanticVersion = Annotated[
    str,
    Field(pattern=r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$"),
]
Sha256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
MediaResourceCategory = Literal[
    "motion-graphic",
    "sound-effect",
    "audio-effect",
    "transition",
    "visual-effect",
    "zoom",
    "lut",
]


class MediaResourceRights(DomainModel):
    status: Literal["confirmed", "not-required"]
    license: str = Field(min_length=1)
    attribution: str
    terms_url: str


class MediaResourcePreview(DomainModel):
    type: Literal["none", "image", "video", "audio"]
    path: str
    mime_type: str

    @model_validator(mode="after")
    def coherent_location(self) -> MediaResourcePreview:
        if self.type == "none":
            if self.path or self.mime_type:
                raise ValueError("Resource preview type none cannot declare a path or MIME type")
        elif not self.path or not self.mime_type:
            raise ValueError("Resource preview files require a path and MIME type")
        return self


class MediaResourceOrigin(DomainModel):
    type: Literal["builtin", "registered-library"]
    library_id: ResourceIdentifier | None
    library_version: SemanticVersion | None
    item_id: ResourceIdentifier | None
    content_sha256: Sha256 | None

    @model_validator(mode="after")
    def coherent_reference(self) -> MediaResourceOrigin:
        values = (
            self.library_id,
            self.library_version,
            self.item_id,
            self.content_sha256,
        )
        if self.type == "builtin" and any(value is not None for value in values):
            raise ValueError("Built-in resources cannot claim a registered-library origin")
        if self.type == "registered-library" and any(value is None for value in values):
            raise ValueError("Registered resources require a complete immutable origin")
        return self


class EditableMediaResourceAdoption(DomainModel):
    type: Literal["editable-media-package"]
    package: str = Field(min_length=1)
    manifest_sha256: Sha256
    package_sha256: Sha256
    default_duration_frames: int | None = Field(default=None, ge=1)


class MediaFileResourceAdoption(DomainModel):
    type: Literal["media-file"]
    file: str = Field(min_length=1)
    sha256: Sha256
    bytes: int = Field(ge=1)
    mime_type: str = Field(min_length=1)
    media_type: Literal["audio", "video", "image", "lut"]
    placement: Literal["audio-track", "video-track", "overlay", "clip-effect"]


class EditorPresetResourceAdoption(DomainModel):
    type: Literal["editor-preset"]
    target: Literal["transition", "visual-effect", "audio-effect"]
    preset_id: ResourceIdentifier
    parameters: dict[str, JsonValue]
    default_duration_frames: int | None = Field(default=None, ge=1)


MediaResourceAdoption = Annotated[
    EditableMediaResourceAdoption
    | MediaFileResourceAdoption
    | EditorPresetResourceAdoption,
    Field(discriminator="type"),
]


class MediaResourceCatalogItem(DomainModel):
    id: ResourceIdentifier
    resource_version: SemanticVersion
    category: MediaResourceCategory
    name: str = Field(min_length=1)
    description: str
    provider: str = Field(min_length=1)
    tags: list[ResourceIdentifier]
    capabilities: list[ResourceIdentifier]
    featured_rank: int | None = Field(default=None, ge=0)
    preview: MediaResourcePreview
    rights: MediaResourceRights
    origin: MediaResourceOrigin
    adoption: MediaResourceAdoption

    @property
    def stable_key(self) -> str:
        return f"{self.id}@{self.resource_version}"

    @model_validator(mode="after")
    def coherent_adoption(self) -> MediaResourceCatalogItem:
        if len(set(self.tags)) != len(self.tags):
            raise ValueError("Resource tags must be unique")
        if len(set(self.capabilities)) != len(self.capabilities):
            raise ValueError("Resource capabilities must be unique")
        adoption = self.adoption
        if isinstance(adoption, EditableMediaResourceAdoption):
            if self.category != "motion-graphic":
                raise ValueError("Editable-media resources must be motion graphics")
            return self
        if isinstance(adoption, MediaFileResourceAdoption):
            expected = {
                "sound-effect": ("audio", "audio-track"),
                "lut": ("lut", "clip-effect"),
            }.get(self.category)
            if expected != (adoption.media_type, adoption.placement):
                raise ValueError("Resource category does not match its media-file adoption")
            return self
        expected_target = {
            "transition": "transition",
            "zoom": "transition",
            "visual-effect": "visual-effect",
            "audio-effect": "audio-effect",
        }.get(self.category)
        if expected_target != adoption.target:
            raise ValueError("Resource category does not match its editor-preset target")
        return self


class MediaResourceCatalog(DomainModel):
    protocol: Literal["visual-multimedia-media-resource-catalog"]
    version: Literal[1]
    catalog_id: ResourceIdentifier
    catalog_version: SemanticVersion
    name: str = Field(min_length=1)
    description: str
    items: list[MediaResourceCatalogItem]

    @model_validator(mode="after")
    def unique_versions(self) -> MediaResourceCatalog:
        keys = [item.stable_key for item in self.items]
        if len(keys) != len(set(keys)):
            raise ValueError("Resource catalog item id and version pairs must be unique")
        return self
