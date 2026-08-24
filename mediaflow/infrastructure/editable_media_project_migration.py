from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from mediaflow.application import web_package_contract as web_contract
from mediaflow.application import web_package_files as web_files
from mediaflow.atomic_file import atomic_write_text
from mediaflow.domain.model_base import new_id, now_ms
from mediaflow.domain.web_manifest import (
    EditableMediaManifest,
    editable_media_manifest_document,
    parse_editable_media_manifest,
)
from mediaflow.domain.web_media_sources import WebMediaSourcesManifest
from mediaflow.domain.web_state import WebClipState
from mediaflow.file_digest import sha256_file
from mediaflow.infrastructure import web_package_storage as web_storage
from mediaflow.infrastructure.editable_media_contract import editable_media_contract
from mediaflow.infrastructure.project_serialization import json_value
from mediaflow.infrastructure.web_browser import BrowserWebPackageValidator

V6_RUNTIME_PATH = (
    Path(__file__).resolve().parents[1] / "resources" / "contracts" / "editable-media-runtime.v6.js"
)
STANDARD_RUNTIME_PACKAGE_PATH = "editable-media-runtime.js"
V4_STANDARD_RUNTIME_SHA256 = "d31871c244816a6ee0065d0b57e487d461cdd2074e96c33d8d34482ca2cdb755"
V5_STANDARD_RUNTIME_SHA256 = "460ad7a3b16738659dccbcc6cfb325470472a43d8ef16666c92a858a8798403a"
LEGACY_STANDARD_RUNTIME_SHA256 = {
    4: V4_STANDARD_RUNTIME_SHA256,
    5: V5_STANDARD_RUNTIME_SHA256,
}
_WEB_PACKAGE_STORAGE = web_storage.LocalWebPackageStorage()
_EDITABLE_MEDIA_CONTRACT = editable_media_contract()


def _read_package_media_sources(
    tree: web_files.WebPackageTree,
    manifest: EditableMediaManifest,
) -> WebMediaSourcesManifest:
    path = tree.root.joinpath(*PurePosixPath(manifest.media_sources).parts)
    return WebMediaSourcesManifest.model_validate_json(path.read_text(encoding="utf-8"))


@dataclass(frozen=True, slots=True)
class _LegacyPackageArchive:
    package: Path
    destination: Path
    receipt: Path | None
    receipt_destination: Path | None

    def archive(self) -> None:
        if self.destination.exists() and self.package.exists():
            raise FileExistsError(self.destination)
        if self.receipt_destination is not None:
            if self.receipt_destination.exists() and self.receipt is not None and self.receipt.exists():
                raise FileExistsError(self.receipt_destination)
            if self.receipt is not None and self.receipt.exists():
                self.receipt_destination.parent.mkdir(
                    parents=True,
                    exist_ok=True,
                )
                self.receipt.replace(self.receipt_destination)
        if self.package.exists():
            self.destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                self.package.replace(self.destination)
            except BaseException:
                if (
                    self.receipt is not None
                    and self.receipt_destination is not None
                    and self.receipt_destination.exists()
                    and not self.receipt.exists()
                ):
                    self.receipt_destination.replace(self.receipt)
                raise


@dataclass(frozen=True, slots=True)
class _EditableMediaUpgradePlan:
    row: Any
    source_version: int
    manifest: EditableMediaManifest
    source_tree: web_files.WebPackageTree
    package: Path
    receipt: Path
    clip_rows: tuple[Any, ...]


def _required_object(value: object, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{location} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise ValueError(f"{location} keys must be strings")
    return value


def _required_array(value: object, location: str) -> list[object]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{location} must be a non-empty array")
    return value


def _without_legacy_nulls(value: object) -> object:
    if isinstance(value, list):
        return [_without_legacy_nulls(item) for item in value]
    if isinstance(value, dict):
        return {key: _without_legacy_nulls(item) for key, item in value.items() if item is not None}
    return value


def migrate_editable_media_manifest_to_v6(document: object) -> EditableMediaManifest:
    """Convert only the final standard v4/v5 manifests to the single v6 contract."""

    manifest = _required_object(document, "editable-media manifest")
    if manifest.get("protocol") != "editable-media":
        raise ValueError("Project web asset is not editable-media")
    version = manifest.get("version")
    if version == 6:
        return parse_editable_media_manifest(manifest, _EDITABLE_MEDIA_CONTRACT)
    if version not in {4, 5}:
        raise ValueError(f"Project web asset uses unsupported editable-media v{version}")
    migrated = _required_object(
        _without_legacy_nulls(json.loads(json.dumps(manifest, ensure_ascii=False))),
        "editable-media manifest",
    )
    migrated["version"] = 6
    migrated["frame_readiness"] = {
        "default_timeout_ms": 10_000,
        "maximum_timeout_ms": 30_000,
        "retry_limit": 1,
    }
    if version == 5:
        raw_parameters = migrated.get("parameters", [])
        if not isinstance(raw_parameters, list):
            raise ValueError("editable-media v5 parameters must be an array")
        converted: list[dict[str, object]] = []
        for index, raw_parameter in enumerate(raw_parameters):
            parameter = _required_object(raw_parameter, f"manifest.parameters[{index}]")
            required = {
                "id",
                "name",
                "kind",
                "scope",
                "default",
                "animatable",
                "control",
            }
            missing = required - set(parameter)
            if missing:
                raise ValueError(f"editable-media v5 parameter is incomplete: {sorted(missing)}")
            constraints = _required_object(
                parameter.get("constraints", {}),
                f"manifest.parameters[{index}].constraints",
            )
            choices = constraints.get("choices", [])
            if not isinstance(choices, list):
                raise ValueError("editable-media v5 parameter choices must be an array")
            converted.append(
                {
                    "descriptor": {
                        "id": parameter["id"],
                        "label": parameter["name"],
                        "description": parameter.get("description", ""),
                        "group": parameter.get("group") or "自定义参数",
                        "kind": parameter["kind"],
                        "control": parameter["control"],
                        "default": parameter["default"],
                        "unit": parameter.get("unit") or None,
                        "constraints": {
                            **{key: value for key, value in constraints.items() if key != "choices"},
                            "choices": [{"value": value, "label": str(value)} for value in choices],
                        },
                        "options_source": None,
                        "timeline": ("keyframe" if parameter["animatable"] else "none"),
                    },
                    "binding": {
                        "scope": parameter["scope"],
                        "css_variable": parameter.get("css_variable"),
                    },
                }
            )
        migrated["parameters"] = converted
        scenes = _required_array(migrated.get("scenes"), "manifest.scenes")
        for scene_index, raw_scene in enumerate(scenes):
            scene = _required_object(
                raw_scene,
                f"manifest.scenes[{scene_index}]",
            )
            motion = _required_object(
                scene.get("motion"),
                f"manifest.scenes[{scene_index}].motion",
            )
            motion.setdefault("camera", None)
        return parse_editable_media_manifest(migrated, _EDITABLE_MEDIA_CONTRACT)

    if "parameters" in manifest:
        raise ValueError("editable-media v4 manifest unexpectedly contains parameters")
    migrated["parameters"] = []
    scenes = _required_array(migrated.get("scenes"), "manifest.scenes")
    for scene_index, raw_scene in enumerate(scenes):
        scene = _required_object(raw_scene, f"manifest.scenes[{scene_index}]")
        for field in ("parameters", "motion"):
            if field in scene:
                raise ValueError(
                    f"editable-media v4 scene unexpectedly contains {field}: {scene.get('id', scene_index)}"
                )
        scene["parameters"] = {}
        steps = _required_array(scene.get("steps"), f"manifest.scenes[{scene_index}].steps")
        last_index = len(steps) - 1
        for step_index, raw_step in enumerate(steps):
            step = _required_object(
                raw_step,
                f"manifest.scenes[{scene_index}].steps[{step_index}]",
            )
            for field in ("state_kind", "review", "description"):
                if field in step:
                    raise ValueError(
                        f"editable-media v4 step unexpectedly contains {field}: {step.get('id', step_index)}"
                    )
            step["state_kind"] = (
                "start" if step_index == 0 else "result" if step_index == last_index else "change"
            )
            label = str(step.get("label", "")).strip()
            if not label:
                raise ValueError("editable-media v4 step label cannot be empty")
            step["review"] = False
            step["description"] = label
        multi_step = len(steps) > 1
        scene_name = str(scene.get("name", "")).strip()
        if not scene_name:
            raise ValueError("editable-media v4 scene name cannot be empty")
        scene["motion"] = {
            "complexity": "simple" if multi_step else "static",
            "driver": "object" if multi_step else "none",
            "semantic_purpose": scene_name,
            "key_state_review": "none",
            "camera": None,
        }
    return parse_editable_media_manifest(migrated, _EDITABLE_MEDIA_CONTRACT)


def _managed_asset_path(project_dir: Path, stored_path: str) -> Path:
    relative = PurePosixPath(stored_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Managed editable-media asset path left the project directory")
    path = project_dir.joinpath(*relative.parts).resolve()
    try:
        path.relative_to(project_dir.resolve())
    except ValueError as error:
        raise ValueError("Managed editable-media asset path left the project directory") from error
    return path


def _copy_migrated_package(
    source_tree: web_files.WebPackageTree,
    staging: Path,
    manifest: EditableMediaManifest,
    source_version: int,
) -> web_files.WebPackageTree:
    if web_files.MANIFEST_FILE_NAME not in source_tree.files:
        raise FileNotFoundError(source_tree.root / web_files.MANIFEST_FILE_NAME)
    if STANDARD_RUNTIME_PACKAGE_PATH not in source_tree.files:
        raise FileNotFoundError(source_tree.root / STANDARD_RUNTIME_PACKAGE_PATH)
    if STANDARD_RUNTIME_PACKAGE_PATH not in manifest.resources:
        raise ValueError("editable-media v4 package does not declare its standard runtime")
    _validate_legacy_standard_runtime(source_tree, source_version)
    V6_RUNTIME_PATH.resolve(strict=True)
    staging.mkdir(parents=True)
    for relative in source_tree.directories:
        staging.joinpath(*PurePosixPath(relative).parts).mkdir(parents=True)
    try:
        for relative in source_tree.files:
            destination = staging.joinpath(*PurePosixPath(relative).parts)
            if relative == web_files.MANIFEST_FILE_NAME:
                atomic_write_text(
                    destination,
                    json.dumps(
                        editable_media_manifest_document(manifest),
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n",
                )
            elif relative == STANDARD_RUNTIME_PACKAGE_PATH:
                web_storage.copy_web_package_file(
                    str(V6_RUNTIME_PATH),
                    str(destination),
                )
            else:
                web_storage.copy_web_package_file(
                    str(source_tree.root.joinpath(*PurePosixPath(relative).parts)),
                    str(destination),
                )
        return web_storage.scan_web_package(staging)
    except BaseException:
        raise


def _validate_legacy_standard_runtime(
    source_tree: web_files.WebPackageTree,
    source_version: int,
) -> None:
    expected = LEGACY_STANDARD_RUNTIME_SHA256.get(source_version)
    if expected is None:
        raise ValueError(f"Unsupported legacy editable-media runtime version: {source_version}")
    actual = sha256_file(source_tree.root / STANDARD_RUNTIME_PACKAGE_PATH)
    if actual != expected:
        raise RuntimeError(
            f"Third-party editable-media v{source_version} runtime cannot be upgraded automatically; "
            f"republish this package as v6: {source_tree.root}"
        )


def _publication_paths(
    project_dir: Path,
    asset_id: str,
) -> tuple[str, Path, Path, Path, Path, Path]:
    token = new_id().replace("-", "")[: web_files.PUBLICATION_TOKEN_HEX_CHARS]
    staging = project_dir / "staging" / "web" / f"s-{token}"
    final = project_dir / "sources" / "web" / f"p-{token}"
    failure = project_dir / "archive" / "web" / f"f-{token}"
    receipt = project_dir / "sources" / "web" / "receipts" / f"r-{token}.json"
    failed_receipt = project_dir / "archive" / "web" / f"r-{token}.json"
    return token, staging, final, failure, receipt, failed_receipt


def _stage_v6_publication(
    project_dir: Path,
    asset_id: str,
    source_tree: web_files.WebPackageTree,
    manifest: EditableMediaManifest,
    source_version: int,
    chromium: Path,
) -> web_files.WebPackagePublication:
    (
        token,
        staging,
        final,
        failure,
        receipt,
        failed_receipt,
    ) = _publication_paths(project_dir, asset_id)
    for path in (staging, final, failure, receipt, failed_receipt):
        if path.exists():
            raise FileExistsError(path)
    web_files.validate_web_package_paths(
        source_tree,
        staging,
        final,
        failure,
    )
    publication: web_files.WebPackagePublication | None = None
    try:
        migrated_tree = _copy_migrated_package(
            source_tree,
            staging,
            manifest,
            source_version,
        )
        media_sources = _read_package_media_sources(migrated_tree, manifest)
        web_contract.validate_package_files(migrated_tree, manifest, media_sources)
        BrowserWebPackageValidator(chromium, _EDITABLE_MEDIA_CONTRACT).validate(
            staging,
            manifest,
        )
        publication = web_files.WebPackagePublication(
            asset_id=asset_id,
            manifest=manifest,
            media_sources=media_sources,
            source_hash=migrated_tree.source_hash,
            token=token,
            staging=staging,
            final=final,
            failure=failure,
            receipt=receipt,
            failed_receipt=failed_receipt,
        )
        return publication
    except BaseException as error:
        package = staging
        if package.exists():
            try:
                failure.parent.mkdir(parents=True, exist_ok=True)
                package.replace(failure)
            except BaseException as archive_error:
                error.add_note(f"editable-media v6 migration staging archival failed: {archive_error}")
        raise


def _find_publication_receipt(
    project_dir: Path,
    asset_id: str,
    package: Path,
    source_hash: str | None = None,
    *,
    allow_pending: bool = False,
) -> Path | None:
    receipt_root = project_dir / "sources" / "web" / "receipts"
    if not receipt_root.is_dir():
        return None
    matches: list[Path] = []
    for receipt in receipt_root.glob("r-*.json"):
        payload = web_storage.read_publication_receipt(receipt).as_dict()
        if payload.get("asset_id") == asset_id and payload.get("directory") == package.name:
            if payload.get("status") != "committed" and not (
                allow_pending and payload.get("status") == "pending"
            ):
                raise RuntimeError(f"Editable-media publication is not committed: {package}")
            if source_hash is not None and payload.get("source_hash") != source_hash:
                raise RuntimeError(f"Editable-media publication receipt hash does not match: {package}")
            matches.append(receipt)
    if len(matches) > 1:
        raise RuntimeError(f"Editable-media package has more than one publication receipt: {package}")
    return matches[0] if matches else None


def _required_publication_receipt(
    project_dir: Path,
    asset_id: str,
    package: Path,
    source_hash: str,
    *,
    allow_pending: bool = False,
) -> Path:
    receipt = _find_publication_receipt(
        project_dir,
        asset_id,
        package,
        source_hash,
        allow_pending=allow_pending,
    )
    if receipt is None:
        raise RuntimeError(
            f"Editable-media package has no committed publication receipt: {asset_id}/{package}"
        )
    return receipt


def _upgrade_publication_is_pending_in_this_transaction(
    connection: Any,
    project_dir: Path,
    asset_id: str,
    package: Path,
    source_hash: str,
) -> bool:
    table = connection.execute(
        """SELECT name FROM sqlite_master
           WHERE type='table' AND name='editable_media_upgrade'"""
    ).fetchone()
    if table is None:
        return False
    row = connection.execute(
        """SELECT new_source_hash, new_package_path
           FROM editable_media_upgrade WHERE asset_id=?""",
        (asset_id,),
    ).fetchone()
    if row is None:
        return False
    return (
        str(row["new_source_hash"]) == source_hash
        and _managed_asset_path(
            project_dir,
            str(row["new_package_path"]),
        )
        == package
    )


def _legacy_archive(
    project_dir: Path,
    asset_id: str,
    package: Path,
    token: str,
    source_version: int,
    receipt: Path,
) -> _LegacyPackageArchive:
    archive_root = project_dir / "archive" / "web"
    return _LegacyPackageArchive(
        package=package,
        destination=archive_root / f"v{source_version}-{token}",
        receipt=receipt,
        receipt_destination=archive_root / f"v{source_version}-r-{token}.json",
    )


def _migrated_clip_state_json(
    row: Any,
    manifest: EditableMediaManifest,
    old_source_hash: str,
    new_source_hash: str,
    media_sources: WebMediaSourcesManifest,
) -> str:
    payload = _required_object(
        json.loads(str(row["state_json"])),
        f"web_clip_state[{row['clip_id']}]",
    )
    if payload.get("source_hash") != old_source_hash:
        raise RuntimeError(f"Web clip state source hash does not match its asset: {row['clip_id']}")
    payload.setdefault("parameters", {})
    payload.setdefault("parameter_locks", [])
    scenes = payload.get("scenes", {})
    if not isinstance(scenes, dict):
        raise ValueError(f"web_clip_state[{row['clip_id']}].scenes must be an object")
    for scene_id, raw_scene in scenes.items():
        scene = _required_object(
            raw_scene,
            f"web_clip_state[{row['clip_id']}].scenes[{scene_id}]",
        )
        scene.setdefault("parameters", {})
        scene.setdefault("parameter_animations", {})
        scene.setdefault("parameter_locks", [])
    state = WebClipState.model_validate(
        {
            **payload,
            "clip_id": str(row["clip_id"]),
            "source_hash": new_source_hash,
            "revision": int(row["revision"]),
        }
    )
    web_contract.validate_media_bindings(
        manifest,
        media_sources,
        state,
    )
    return json_value(
        state.model_dump(
            mode="json",
            exclude={"clip_id", "revision"},
            exclude_none=True,
        )
    )


def migrate_project_editable_media_to_v6(
    workspace: Any,
    *,
    chromium: Path | None,
    target_project_schema_version: int,
) -> None:
    project_dir = Path(workspace.project_dir).resolve()
    connection = workspace._connection
    plans = _preflight_project_web_assets(
        workspace,
        chromium=chromium,
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS editable_media_upgrade (
               asset_id TEXT PRIMARY KEY
                   REFERENCES asset(id) ON DELETE CASCADE,
               source_version INTEGER NOT NULL,
               target_version INTEGER NOT NULL,
               old_source_hash TEXT NOT NULL,
               new_source_hash TEXT NOT NULL,
               old_package_path TEXT NOT NULL,
               new_package_path TEXT NOT NULL,
               archive_package_path TEXT NOT NULL,
               migrated_at INTEGER NOT NULL
           )"""
    )
    migrated_any = False
    for plan in plans:
        row = plan.row
        if chromium is None:
            raise RuntimeError("Editable-media v6 migration requires the service RuntimeContext")
        publication = _stage_v6_publication(
            project_dir,
            str(row["asset_id"]),
            plan.source_tree,
            plan.manifest,
            plan.source_version,
            chromium,
        )
        try:
            archive = _legacy_archive(
                project_dir,
                str(row["asset_id"]),
                plan.package,
                publication.token,
                plan.source_version,
                plan.receipt,
            )
            migrated_states = [
                (
                    _migrated_clip_state_json(
                        clip_row,
                        publication.manifest,
                        str(row["source_hash"]),
                        publication.source_hash,
                        publication.media_sources,
                    ),
                    clip_row["clip_id"],
                )
                for clip_row in plan.clip_rows
            ]
            _WEB_PACKAGE_STORAGE.publish(publication)

            def commit_publication(
                publication: web_files.WebPackagePublication = publication,
                archive: _LegacyPackageArchive = archive,
            ) -> None:
                _WEB_PACKAGE_STORAGE.mark_committed(publication)
                archive.archive()

            workspace.enlist_transaction_publication(
                on_commit=commit_publication,
                on_rollback=lambda _error, publication=publication: (
                    _WEB_PACKAGE_STORAGE.archive_failed(publication)
                ),
            )
        except BaseException as error:
            try:
                _WEB_PACKAGE_STORAGE.archive_failed(publication)
            except BaseException as archive_error:
                error.add_note(f"editable-media v6 migration failure archival failed: {archive_error}")
            raise
        new_path = publication.entry.relative_to(project_dir).as_posix()
        connection.execute(
            """UPDATE asset SET path=? WHERE id=?""",
            (new_path, row["asset_id"]),
        )
        connection.execute(
            """UPDATE web_asset
               SET manifest_json=?, source_hash=?
               WHERE asset_id=?""",
            (
                json_value(editable_media_manifest_document(publication.manifest)),
                publication.source_hash,
                row["asset_id"],
            ),
        )
        connection.executemany(
            "UPDATE web_clip_state SET state_json=? WHERE clip_id=?",
            migrated_states,
        )
        connection.execute(
            """INSERT INTO editable_media_upgrade(
                   asset_id, source_version, target_version,
                   old_source_hash, new_source_hash,
                   old_package_path, new_package_path,
                   archive_package_path, migrated_at
               ) VALUES (?, ?, 6, ?, ?, ?, ?, ?, ?)""",
            (
                row["asset_id"],
                plan.source_version,
                row["source_hash"],
                publication.source_hash,
                plan.package.relative_to(project_dir).as_posix(),
                publication.final.relative_to(project_dir).as_posix(),
                archive.destination.relative_to(project_dir).as_posix(),
                now_ms(),
            ),
        )
        migrated_any = True
    if migrated_any:
        connection.execute(
            """UPDATE project
               SET content_revision=content_revision+1, updated_at=?""",
            (now_ms(),),
        )
    connection.execute(
        "UPDATE schema_info SET version=? WHERE component='project'",
        (target_project_schema_version,),
    )


def _validate_v6_clip_state(
    row: Any,
    manifest: EditableMediaManifest,
    source_hash: str,
    media_sources: WebMediaSourcesManifest,
) -> None:
    payload = _required_object(
        json.loads(str(row["state_json"])),
        f"web_clip_state[{row['clip_id']}]",
    )
    state = WebClipState.model_validate(
        {
            **payload,
            "clip_id": str(row["clip_id"]),
            "revision": int(row["revision"]),
        }
    )
    if state.source_hash != source_hash:
        raise RuntimeError(f"Web clip state source hash does not match its asset: {row['clip_id']}")
    web_contract.validate_media_bindings(manifest, media_sources, state)


def _asset_clip_rows(connection: Any, asset_id: str) -> tuple[Any, ...]:
    return tuple(
        connection.execute(
            """SELECT state.clip_id, state.state_json, state.revision
               FROM web_clip_state AS state
               JOIN clip ON clip.id=state.clip_id
               WHERE clip.asset_id=?
               ORDER BY state.clip_id""",
            (asset_id,),
        ).fetchall()
    )


def _preflight_project_web_assets(
    workspace: Any,
    *,
    chromium: Path | None,
) -> tuple[_EditableMediaUpgradePlan, ...]:
    project_dir = Path(workspace.project_dir).resolve()
    connection = workspace._connection
    rows = connection.execute(
        """SELECT asset.id AS asset_id, asset.path, asset.managed,
                  web_asset.manifest_json, web_asset.source_hash
           FROM web_asset
           JOIN asset ON asset.id=web_asset.asset_id
           ORDER BY asset.id"""
    ).fetchall()
    plans: list[_EditableMediaUpgradePlan] = []
    for row in rows:
        asset_id = str(row["asset_id"])
        raw_manifest = _required_object(
            json.loads(str(row["manifest_json"])),
            f"web_asset[{asset_id}].manifest",
        )
        source_version = int(raw_manifest.get("version", 0))
        if int(row["managed"]) != 1:
            raise RuntimeError(
                f"Editable-media assets must be project-managed before project upgrade: {asset_id}"
            )
        manifest = migrate_editable_media_manifest_to_v6(raw_manifest)
        entry = _managed_asset_path(project_dir, str(row["path"]))
        package = web_files.web_package_root_for_entry(
            entry,
            str(raw_manifest.get("entry", "")),
        )
        source_tree = web_storage.scan_web_package(package)
        source_hash = str(row["source_hash"])
        if source_tree.source_hash != source_hash:
            raise RuntimeError(f"Editable-media package changed after import: {asset_id}")
        receipt = _required_publication_receipt(
            project_dir,
            asset_id,
            package,
            source_hash,
            allow_pending=(
                source_version == 6
                and _upgrade_publication_is_pending_in_this_transaction(
                    connection,
                    project_dir,
                    asset_id,
                    package,
                    source_hash,
                )
            ),
        )
        package_manifest_path = package / web_files.MANIFEST_FILE_NAME
        try:
            package_manifest = migrate_editable_media_manifest_to_v6(
                json.loads(package_manifest_path.read_text(encoding="utf-8"))
            )
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise RuntimeError(
                f"Editable-media package manifest is invalid: {asset_id}/{package_manifest_path}"
            ) from error
        if editable_media_manifest_document(package_manifest) != editable_media_manifest_document(manifest):
            raise RuntimeError(f"Editable-media project and package manifests do not match: {asset_id}")
        if source_version not in {4, 5, 6}:
            raise ValueError(f"Project web asset uses unsupported editable-media v{source_version}")
        if source_version in {4, 5}:
            if chromium is None:
                raise RuntimeError("Editable-media v6 migration requires the service RuntimeContext")
            if STANDARD_RUNTIME_PACKAGE_PATH not in manifest.resources:
                raise ValueError(
                    f"editable-media v{source_version} package does not declare its standard runtime"
                )
            _validate_legacy_standard_runtime(source_tree, source_version)
        media_sources = _read_package_media_sources(source_tree, manifest)
        web_contract.validate_package_files(source_tree, manifest, media_sources)
        clip_rows = _asset_clip_rows(connection, asset_id)
        if source_version == 6:
            for clip_row in clip_rows:
                _validate_v6_clip_state(
                    clip_row,
                    manifest,
                    source_hash,
                    media_sources,
                )
            continue
        for clip_row in clip_rows:
            _migrated_clip_state_json(
                clip_row,
                manifest,
                source_hash,
                source_hash,
                media_sources,
            )
        plans.append(
            _EditableMediaUpgradePlan(
                row=row,
                source_version=source_version,
                manifest=manifest,
                source_tree=source_tree,
                package=package,
                receipt=receipt,
                clip_rows=clip_rows,
            )
        )
    return tuple(plans)


def reconcile_editable_media_v4_archives(workspace: Any) -> None:
    if workspace.read_only:
        return
    rows = workspace._fetchall(
        """SELECT upgrade.asset_id, upgrade.old_package_path,
                  upgrade.archive_package_path
           FROM editable_media_upgrade AS upgrade
           ORDER BY upgrade.asset_id"""
    )
    project_dir = Path(workspace.project_dir).resolve()
    for row in rows:
        package = _managed_asset_path(
            project_dir,
            str(row["old_package_path"]),
        )
        destination = _managed_asset_path(
            project_dir,
            str(row["archive_package_path"]),
        )
        if not package.exists():
            continue
        archive = _LegacyPackageArchive(
            package=package,
            destination=destination,
            receipt=_find_publication_receipt(
                project_dir,
                str(row["asset_id"]),
                package,
            ),
            receipt_destination=(
                destination.parent
                / (f"{destination.name.split('-', 1)[0]}-r-{destination.name.split('-', 1)[1]}.json")
            ),
        )
        archive.archive()
