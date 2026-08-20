from __future__ import annotations

from typing import TYPE_CHECKING

from .web_manifest_primitives import WebLayoutContract, WebQualityRules, WebScene

if TYPE_CHECKING:
    from .web_manifest import EditableMediaManifest, WebDataField


def validate_manifest_contract(manifest: EditableMediaManifest) -> None:
    layer_ids = _validate_manifest_layers(manifest)
    data_fields, layout_contracts = _validate_manifest_layouts(manifest, layer_ids)
    scene_ids = _validate_manifest_scenes(manifest, data_fields, layout_contracts)
    variant_ids = _validate_manifest_variants(manifest, layer_ids)
    _validate_manifest_parameters(manifest)
    _validate_manifest_quality(
        manifest,
        layer_ids,
        set(data_fields),
        scene_ids,
        variant_ids,
    )


def _validate_manifest_layers(manifest: EditableMediaManifest) -> set[str]:
    layer_ids = [layer.id for layer in manifest.layers]
    if not layer_ids:
        raise ValueError("Editable media must declare at least one editable layer")
    if len(set(layer_ids)) != len(layer_ids):
        raise ValueError("Editable layer identifiers must be unique")
    parents = {layer.id: layer.parent_id for layer in manifest.layers}
    known = set(layer_ids)
    for layer_id, parent_id in parents.items():
        if parent_id == layer_id:
            raise ValueError(f"Editable layer cannot be its own parent: {layer_id}")
        if parent_id is not None and parent_id not in known:
            raise ValueError(f"Editable layer parent does not exist: {parent_id}")
        visited = {layer_id}
        while parent_id is not None:
            if parent_id in visited:
                raise ValueError("Editable layer groups cannot contain a cycle")
            visited.add(parent_id)
            parent_id = parents[parent_id]
    return known


def _validate_manifest_layouts(
    manifest: EditableMediaManifest,
    layer_ids: set[str],
) -> tuple[dict[str, WebDataField], dict[str, WebLayoutContract]]:
    data_fields = {item.id: item for item in manifest.data_fields}
    if len(data_fields) != len(manifest.data_fields):
        raise ValueError("Editable media data field identifiers must be unique")
    if manifest.accessibility.title_data_field not in data_fields:
        raise ValueError("Editable media accessibility title data field does not exist")
    contract_ids = [item.id for item in manifest.layout_contracts]
    if not contract_ids or len(set(contract_ids)) != len(contract_ids):
        raise ValueError("Editable media layout contract identifiers must be unique")
    contracts = {item.id: item for item in manifest.layout_contracts}
    for contract in manifest.layout_contracts:
        slot_ids = [item.id for item in contract.asset_slots]
        if len(set(slot_ids)) != len(slot_ids):
            raise ValueError(f"Layout contract {contract.id} asset slots must be unique")
        unknown_layers = (
            set(contract.required_layer_ids) | set(contract.title_layer_ids) | set(contract.content_layer_ids)
        ) - layer_ids
        if unknown_layers:
            raise ValueError(
                f"Layout contract {contract.id} references unknown layers: {sorted(unknown_layers)}"
            )
        unknown_data = set(contract.required_data_fields) - set(data_fields)
        if unknown_data:
            raise ValueError(
                f"Layout contract {contract.id} references unknown data fields: {sorted(unknown_data)}"
            )
    return data_fields, contracts


def _validate_manifest_scenes(
    manifest: EditableMediaManifest,
    data_fields: dict[str, WebDataField],
    contracts: dict[str, WebLayoutContract],
) -> set[str]:
    scene_ids = [item.id for item in manifest.scenes]
    if not scene_ids or len(set(scene_ids)) != len(scene_ids):
        raise ValueError("Editable media scene identifiers must be unique")
    for scene in manifest.scenes:
        contract = contracts.get(scene.layout_id)
        if contract is None:
            raise ValueError(f"Scene {scene.id} layout contract does not exist")
        if scene.page_role not in contract.page_roles:
            raise ValueError(f"Scene {scene.id} page role violates its layout contract")
        if scene.content_shape not in contract.content_shapes:
            raise ValueError(f"Scene {scene.id} content shape violates its layout contract")
        if scene.primary_blocks > contract.capacity.maximum_primary_blocks:
            raise ValueError(f"Scene {scene.id} exceeds its layout capacity")
        unknown_data = set(scene.data) - set(data_fields)
        if unknown_data:
            raise ValueError(f"Scene {scene.id} references unknown data fields: {sorted(unknown_data)}")
        missing_data = {
            field_id
            for field_id in contract.required_data_fields
            if field_id not in scene.data and data_fields[field_id].default is None
        }
        if missing_data:
            raise ValueError(f"Scene {scene.id} is missing required data fields: {sorted(missing_data)}")
        _validate_scene_asset_slots(scene, contract, data_fields)
    return set(scene_ids)


def _validate_scene_asset_slots(
    scene: WebScene,
    contract: WebLayoutContract,
    data_fields: dict[str, WebDataField],
) -> None:
    asset_slots = {item.id: item for item in contract.asset_slots}
    unknown = set(scene.asset_slots) - set(asset_slots)
    if unknown:
        raise ValueError(f"Scene {scene.id} references unknown asset slots: {sorted(unknown)}")
    missing = {item.id for item in contract.asset_slots if item.required and item.id not in scene.asset_slots}
    if missing:
        raise ValueError(f"Scene {scene.id} is missing required asset slots: {sorted(missing)}")
    for slot_id, binding in scene.asset_slots.items():
        field = data_fields.get(binding.data_field)
        if field is None:
            raise ValueError(f"Scene {scene.id} asset slot {slot_id} references an unknown data field")
        if field.kind != "media-source":
            raise ValueError(f"Scene {scene.id} asset slot {slot_id} must bind a media-source field")
        value = scene.data.get(field.id, field.default)
        if asset_slots[slot_id].required and (not isinstance(value, str) or not value):
            raise ValueError(f"Scene {scene.id} asset slot {slot_id} has no media source")


def _validate_manifest_variants(
    manifest: EditableMediaManifest,
    layer_ids: set[str],
) -> set[str]:
    variant_ids = [item.id for item in manifest.variants]
    if not variant_ids or len(set(variant_ids)) != len(variant_ids):
        raise ValueError("Editable media variant identifiers must be unique")
    known_variants = set(variant_ids)
    if manifest.default_variant_id not in known_variants:
        raise ValueError("Editable media default variant does not exist")
    for variant in manifest.variants:
        unknown_layers = set(variant.layers) - layer_ids
        if unknown_layers:
            raise ValueError(f"Variant {variant.id} references unknown layers: {sorted(unknown_layers)}")
    return known_variants


def _validate_manifest_parameters(manifest: EditableMediaManifest) -> None:
    if len({item.id for item in manifest.theme_variables}) != len(manifest.theme_variables):
        raise ValueError("Editable media theme variable identifiers must be unique")
    theme_css = {item.css_variable for item in manifest.theme_variables}
    if len(theme_css) != len(manifest.theme_variables):
        raise ValueError("Editable media theme CSS variables must be unique")
    parameter_ids = [item.descriptor.id for item in manifest.parameters]
    if len(set(parameter_ids)) != len(parameter_ids):
        raise ValueError("Editable media parameter identifiers must be unique")
    parameter_css = [
        item.binding.css_variable for item in manifest.parameters if item.binding.css_variable is not None
    ]
    if len(set(parameter_css)) != len(parameter_css):
        raise ValueError("Editable media parameter CSS variables must be unique")
    overlap = theme_css.intersection(parameter_css)
    if overlap:
        raise ValueError(f"Editable media theme and parameter CSS variables overlap: {sorted(overlap)}")
    parameters = {item.descriptor.id: item for item in manifest.parameters}
    for scene in manifest.scenes:
        unknown = set(scene.parameters) - set(parameters)
        if unknown:
            raise ValueError(f"Scene {scene.id} references unknown parameters: {sorted(unknown)}")
        for parameter_id, value in scene.parameters.items():
            definition = parameters[parameter_id]
            if definition.binding.scope != "scene":
                raise ValueError(f"Scene {scene.id} cannot override global parameter {parameter_id}")
            definition.descriptor.validate_value(value)


def _quality_layer_ids(rules: WebQualityRules) -> set[str]:
    return (
        set(rules.required_layer_ids)
        | set(rules.required_title_layer_ids)
        | set(rules.allow_overflow_layer_ids)
        | set(rules.safe_area_layer_ids)
        | set(rules.minimum_font_px)
        | set(rules.content_bounds_layer_ids)
        | (set(rules.thumbnail.text_layer_ids) if rules.thumbnail is not None else set())
        | (set(rules.navigation_safe_area.layer_ids) if rules.navigation_safe_area is not None else set())
        | (
            {rules.title_to_content.title_layer_id} | set(rules.title_to_content.content_layer_ids)
            if rules.title_to_content is not None
            else set()
        )
        | (set(rules.bottom_whitespace.content_layer_ids) if rules.bottom_whitespace is not None else set())
        | ({rules.roundtrip.layer_id} if rules.roundtrip is not None else set())
        | {layer_id for gap in rules.minimum_gaps for layer_id in (gap.above, gap.below)}
    )


def _validate_manifest_quality(
    manifest: EditableMediaManifest,
    layer_ids: set[str],
    data_ids: set[str],
    scene_ids: set[str],
    variant_ids: set[str],
) -> None:
    unknown_variants = set(manifest.quality.variant_overrides) - variant_ids
    if unknown_variants:
        raise ValueError(
            f"Editable media quality variant overrides reference unknown variants: {sorted(unknown_variants)}"
        )
    unknown_scenes = set(manifest.quality.scene_overrides) - scene_ids
    if unknown_scenes:
        raise ValueError(
            f"Editable media quality scene overrides reference unknown scenes: {sorted(unknown_scenes)}"
        )
    quality_rules: list[tuple[str, WebQualityRules]] = [
        ("quality", manifest.quality),
        *[
            (f"quality.variant_overrides.{variant_id}", rules)
            for variant_id, rules in manifest.quality.variant_overrides.items()
        ],
        *[
            (f"quality.scene_overrides.{scene_id}", rules)
            for scene_id, rules in manifest.quality.scene_overrides.items()
        ],
    ]
    for label, rules in quality_rules:
        unknown_layers = _quality_layer_ids(rules) - layer_ids
        if unknown_layers:
            raise ValueError(f"{label} references unknown layers: {sorted(unknown_layers)}")
        if rules.roundtrip is not None and rules.roundtrip.data_field not in data_ids:
            raise ValueError(f"{label} roundtrip data field does not exist")
        if (
            rules.canvas_selector is not None
            and rules.canvas_selector != manifest.accessibility.canvas_selector
        ):
            raise ValueError("Editable media canvas selectors must use one canonical value")
    if manifest.accessibility.canvas_selector != manifest.quality.canvas_selector:
        raise ValueError("Editable media canvas selectors must use one canonical value")
