from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pydantic import JsonValue

from mediaflow.domain.audio import AUDIO_EFFECT_DEFINITIONS
from mediaflow.domain.effect_registry import (
    TRANSITION_CAPABILITIES,
    transition_is_available,
)
from mediaflow.domain.enums import AudioEffectKind, ColorMode, TransitionKind
from mediaflow.domain.media_resources import (
    EditableMediaResourceAdoption,
    EditorPresetResourceAdoption,
    MediaFileResourceAdoption,
    MediaResourceCatalog,
    MediaResourceCatalogItem,
    MediaResourceCategory,
    MediaResourceOrigin,
    MediaResourcePreview,
    MediaResourceRights,
)
from mediaflow.domain.visual_effects import (
    VISUAL_EFFECT_DEFINITIONS,
    visual_effect_defaults,
)


class LoadedCatalog(Protocol):
    @property
    def root(self) -> Path: ...

    @property
    def path(self) -> Path: ...

    @property
    def catalog(self) -> MediaResourceCatalog: ...


CatalogLoader = Callable[[str | Path], LoadedCatalog]


@dataclass(frozen=True, slots=True)
class MediaResourceEntry:
    catalog_id: str
    catalog_version: str
    catalog_path: str | None
    catalog_root: Path | None
    item: MediaResourceCatalogItem

    @property
    def resource_key(self) -> str:
        return f"{self.catalog_id}:{self.item.stable_key}"

    def document(self) -> dict[str, object]:
        preview_path = (
            str((self.catalog_root / self.item.preview.path).resolve())
            if self.catalog_root is not None and self.item.preview.path
            else ""
        )
        adoption = self.item.adoption
        adoption_relative = (
            adoption.package
            if isinstance(adoption, EditableMediaResourceAdoption)
            else adoption.file
            if isinstance(adoption, MediaFileResourceAdoption)
            else ""
        )
        adoption_path = (
            str((self.catalog_root / adoption_relative).resolve())
            if self.catalog_root is not None and adoption_relative
            else ""
        )
        return {
            "resource_key": self.resource_key,
            "catalog_id": self.catalog_id,
            "catalog_version": self.catalog_version,
            "catalog_path": self.catalog_path,
            "preview_path": preview_path,
            "adoption_path": adoption_path,
            **self.item.model_dump(mode="json"),
        }


def _builtin_rights() -> MediaResourceRights:
    return MediaResourceRights(
        status="not-required",
        license="MediaFlow Pro GPL-3.0-or-later built-in preset",
        attribution="",
        terms_url="",
    )


def _builtin_origin() -> MediaResourceOrigin:
    return MediaResourceOrigin(
        type="builtin",
        library_id=None,
        library_version=None,
        item_id=None,
        content_sha256=None,
    )


def _no_preview() -> MediaResourcePreview:
    return MediaResourcePreview(type="none", path="", mime_type="")


def builtin_media_resource_catalog(color_mode: ColorMode) -> MediaResourceCatalog:
    items: list[MediaResourceCatalogItem] = []
    for transition_kind, capability in TRANSITION_CAPABILITIES.items():
        if not transition_is_available(transition_kind, color_mode):
            continue
        category: MediaResourceCategory = (
            "zoom" if transition_kind == TransitionKind.ZOOM else "transition"
        )
        items.append(
            MediaResourceCatalogItem(
                id=transition_kind.value,
                resource_version="1.0.0",
                category=category,
                name=capability.label_key,
                description=capability.description_key,
                provider="MediaFlow Pro",
                tags=[capability.category],
                capabilities=["timeline-ready", "realtime-preview"],
                featured_rank=(
                    0 if transition_kind == TransitionKind.DISSOLVE else None
                ),
                preview=_no_preview(),
                rights=_builtin_rights(),
                origin=_builtin_origin(),
                adoption=EditorPresetResourceAdoption(
                    type="editor-preset",
                    target="transition",
                    preset_id=transition_kind.value,
                    parameters={},
                    default_duration_frames=capability.default_duration_frames,
                ),
            )
        )
    for effect_kind, definition in VISUAL_EFFECT_DEFINITIONS.items():
        if definition.resource_asset_kind is not None:
            continue
        parameters: dict[str, JsonValue] = dict(visual_effect_defaults(effect_kind))
        items.append(
            MediaResourceCatalogItem(
                id=effect_kind.value,
                resource_version="1.0.0",
                category="visual-effect",
                name=definition.label,
                description="可编辑参数并进入片段视觉效果链。",
                provider="MediaFlow Pro",
                tags=["builtin"],
                capabilities=["editable", "realtime-preview", "timeline-ready"],
                featured_rank=None,
                preview=_no_preview(),
                rights=_builtin_rights(),
                origin=_builtin_origin(),
                adoption=EditorPresetResourceAdoption(
                    type="editor-preset",
                    target="visual-effect",
                    preset_id=effect_kind.value,
                    parameters=parameters,
                    default_duration_frames=None,
                ),
            )
        )
    for audio_effect_kind, audio_definition in AUDIO_EFFECT_DEFINITIONS.items():
        audio_parameters: dict[str, JsonValue] = {
            descriptor.id: descriptor.default
            for descriptor in audio_definition.descriptors
        }
        items.append(
            MediaResourceCatalogItem(
                id=audio_effect_kind.value,
                resource_version="1.0.0",
                category="audio-effect",
                name=audio_definition.label,
                description="应用到所选音频总线，并保留完整可编辑参数。",
                provider="MediaFlow Pro",
                tags=["audio", "builtin"],
                capabilities=["editable", "realtime-preview", "timeline-ready"],
                featured_rank=(
                    50
                    if audio_effect_kind == AudioEffectKind.LOUDNESS_NORMALIZE
                    else None
                ),
                preview=_no_preview(),
                rights=_builtin_rights(),
                origin=_builtin_origin(),
                adoption=EditorPresetResourceAdoption(
                    type="editor-preset",
                    target="audio-effect",
                    preset_id=audio_effect_kind.value,
                    parameters=audio_parameters,
                    default_duration_frames=None,
                ),
            )
        )
    return MediaResourceCatalog(
        protocol="visual-multimedia-media-resource-catalog",
        version=1,
        catalog_id="mediaflow-builtins",
        catalog_version="1.0.0",
        name="MediaFlow Pro 内置资源",
        description="由当前运行时能力生成的转场、缩放、视觉效果和音频效果。",
        items=items,
    )


class MediaResourceService:
    def __init__(
        self,
        loader: CatalogLoader,
        configured_paths: Callable[[], Iterable[str]],
    ) -> None:
        self._loader = loader
        self._configured_paths = configured_paths

    def entries(
        self,
        *,
        color_mode: ColorMode,
        catalog_paths: Iterable[str] | None = None,
    ) -> tuple[list[MediaResourceEntry], list[dict[str, object]]]:
        builtin = builtin_media_resource_catalog(color_mode)
        entries = [
            MediaResourceEntry(
                catalog_id=builtin.catalog_id,
                catalog_version=builtin.catalog_version,
                catalog_path=None,
                catalog_root=None,
                item=item,
            )
            for item in builtin.items
        ]
        sources: list[dict[str, object]] = [
            {
                "catalog_id": builtin.catalog_id,
                "catalog_version": builtin.catalog_version,
                "catalog_path": None,
                "item_count": len(builtin.items),
                "error": None,
            }
        ]
        requested = list(catalog_paths) if catalog_paths is not None else list(self._configured_paths())
        for raw_path in dict.fromkeys(value.strip() for value in requested if value.strip()):
            try:
                loaded = self._loader(raw_path)
            except (OSError, ValueError, RuntimeError) as error:
                sources.append(
                    {
                        "catalog_id": None,
                        "catalog_version": None,
                        "catalog_path": str(Path(raw_path).expanduser().resolve()),
                        "item_count": 0,
                        "error": str(error),
                    }
                )
                continue
            sources.append(
                {
                    "catalog_id": loaded.catalog.catalog_id,
                    "catalog_version": loaded.catalog.catalog_version,
                    "catalog_path": str(loaded.path),
                    "item_count": len(loaded.catalog.items),
                    "error": None,
                }
            )
            entries.extend(
                MediaResourceEntry(
                    catalog_id=loaded.catalog.catalog_id,
                    catalog_version=loaded.catalog.catalog_version,
                    catalog_path=str(loaded.path),
                    catalog_root=loaded.root,
                    item=item,
                )
                for item in loaded.catalog.items
            )
        keys = [entry.resource_key for entry in entries]
        if len(keys) != len(set(keys)):
            duplicates = sorted(key for key in set(keys) if keys.count(key) > 1)
            raise ValueError(f"Media resource sources repeat stable keys: {duplicates}")
        return entries, sources

    def search(
        self,
        *,
        color_mode: ColorMode,
        catalog_paths: Iterable[str] | None = None,
        category: str | None = None,
        query: str = "",
        tags: Iterable[str] = (),
        capabilities: Iterable[str] = (),
    ) -> dict[str, object]:
        entries, sources = self.entries(
            color_mode=color_mode,
            catalog_paths=catalog_paths,
        )
        normalized_query = " ".join(query.split()).casefold()
        required_tags = set(tags)
        required_capabilities = set(capabilities)
        filtered = []
        for entry in entries:
            item = entry.item
            if category and item.category != category:
                continue
            if not required_tags.issubset(item.tags):
                continue
            if not required_capabilities.issubset(item.capabilities):
                continue
            searchable = " ".join(
                [
                    item.id,
                    item.name,
                    item.description,
                    item.provider,
                    item.category,
                    *item.tags,
                    *item.capabilities,
                ]
            ).casefold()
            if normalized_query and normalized_query not in searchable:
                continue
            filtered.append(entry)
        filtered.sort(
            key=lambda entry: (
                entry.item.featured_rank is None,
                entry.item.featured_rank or 0,
                entry.item.category,
                entry.item.name.casefold(),
                entry.resource_key,
            )
        )
        return {
            "sources": sources,
            "categories": sorted({entry.item.category for entry in entries}),
            "tags": sorted({tag for entry in entries for tag in entry.item.tags}),
            "featured_count": sum(
                entry.item.featured_rank is not None for entry in entries
            ),
            "result_count": len(filtered),
            "items": [entry.document() for entry in filtered],
        }

    def require_entry(
        self,
        resource_key: str,
        *,
        color_mode: ColorMode,
        catalog_paths: Iterable[str] | None = None,
    ) -> MediaResourceEntry:
        entries, _sources = self.entries(
            color_mode=color_mode,
            catalog_paths=catalog_paths,
        )
        try:
            return next(entry for entry in entries if entry.resource_key == resource_key)
        except StopIteration as error:
            raise KeyError(resource_key) from error
