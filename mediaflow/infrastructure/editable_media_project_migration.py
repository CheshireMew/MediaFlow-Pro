from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from mediaflow.application import web_package_contract as web_contract
from mediaflow.application import web_package_files as web_files
from mediaflow.atomic_file import atomic_write_text
from mediaflow.domain.model_base import new_id, now_ms
from mediaflow.domain.web_media import (
    EditableMediaManifest,
    WebClipState,
    WebMediaSourcesManifest,
    editable_media_manifest_document,
    parse_editable_media_manifest,
)
from mediaflow.infrastructure.project_serialization import json_value
from mediaflow.infrastructure.web_browser import BrowserWebPackageValidator

V5_RUNTIME_PATH = (
    Path(__file__).resolve().parents[1] / "resources" / "contracts" / "editable-media-runtime.v5.js"
)
V5_RUNTIME_PACKAGE_PATH = "editable-media-runtime.js"


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


def migrate_editable_media_v4_manifest(
    document: object,
) -> EditableMediaManifest:
    """Convert the final v4 contract to one explicit, neutral v5 contract.

    The conversion does not infer editable parameters or camera movement. It
    preserves every v4 field and step time, classifies existing steps by their
    stable order, and declares only the object timing already exposed by v4.
    """

    manifest = _required_object(document, "editable-media manifest")
    if manifest.get("protocol") != "editable-media":
        raise ValueError("Project web asset is not editable-media")
    version = manifest.get("version")
    if version == 5:
        return parse_editable_media_manifest(manifest)
    if version != 4:
        raise ValueError(f"Project web asset uses unsupported editable-media v{version}")
    for field in ("parameters",):
        if field in manifest:
            raise ValueError(f"editable-media v4 manifest unexpectedly contains {field}")
    migrated = _required_object(
        _without_legacy_nulls(json.loads(json.dumps(manifest, ensure_ascii=False))),
        "editable-media manifest",
    )
    migrated["version"] = 5
    migrated["parameters"] = []
    scenes = _required_array(migrated.get("scenes"), "manifest.scenes")
    for scene_index, raw_scene in enumerate(scenes):
        scene = _required_object(
            raw_scene,
            f"manifest.scenes[{scene_index}]",
        )
        for field in ("parameters", "motion"):
            if field in scene:
                raise ValueError(
                    f"editable-media v4 scene unexpectedly contains {field}: {scene.get('id', scene_index)}"
                )
        scene["parameters"] = {}
        steps = _required_array(
            scene.get("steps"),
            f"manifest.scenes[{scene_index}].steps",
        )
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
            if step_index == 0:
                state_kind = "start"
            elif step_index == last_index:
                state_kind = "result"
            else:
                state_kind = "change"
            label = str(step.get("label", "")).strip()
            if not label:
                raise ValueError("editable-media v4 step label cannot be empty")
            step["state_kind"] = state_kind
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
    return parse_editable_media_manifest(migrated)


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
) -> web_files.WebPackageTree:
    if web_files.MANIFEST_FILE_NAME not in source_tree.files:
        raise FileNotFoundError(source_tree.root / web_files.MANIFEST_FILE_NAME)
    if V5_RUNTIME_PACKAGE_PATH not in source_tree.files:
        raise FileNotFoundError(source_tree.root / V5_RUNTIME_PACKAGE_PATH)
    if V5_RUNTIME_PACKAGE_PATH not in manifest.resources:
        raise ValueError("editable-media v4 package does not declare its standard runtime")
    V5_RUNTIME_PATH.resolve(strict=True)
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
            elif relative == V5_RUNTIME_PACKAGE_PATH:
                web_files.copy_web_package_file(
                    str(V5_RUNTIME_PATH),
                    str(destination),
                )
            else:
                web_files.copy_web_package_file(
                    str(source_tree.root.joinpath(*PurePosixPath(relative).parts)),
                    str(destination),
                )
        return web_files.scan_web_package(staging)
    except BaseException:
        raise


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


def _stage_v5_publication(
    project_dir: Path,
    asset_id: str,
    source_tree: web_files.WebPackageTree,
    manifest: EditableMediaManifest,
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
        )
        media_sources = web_contract.validate_package_files(
            migrated_tree,
            manifest,
        )
        BrowserWebPackageValidator().validate(staging, manifest)
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
                error.add_note(f"editable-media v5 migration staging archival failed: {archive_error}")
        raise


def _find_publication_receipt(
    project_dir: Path,
    asset_id: str,
    package: Path,
) -> Path | None:
    receipt_root = project_dir / "sources" / "web" / "receipts"
    if not receipt_root.is_dir():
        return None
    matches: list[Path] = []
    for receipt in receipt_root.glob("r-*.json"):
        try:
            payload = json.loads(receipt.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Invalid editable-media publication receipt: {receipt}") from error
        if (
            isinstance(payload, dict)
            and payload.get("asset_id") == asset_id
            and payload.get("directory") == package.name
        ):
            matches.append(receipt)
    if len(matches) > 1:
        raise RuntimeError(f"Editable-media package has more than one publication receipt: {package}")
    return matches[0] if matches else None


def _legacy_archive(
    project_dir: Path,
    asset_id: str,
    package: Path,
    token: str,
) -> _LegacyPackageArchive:
    receipt = _find_publication_receipt(
        project_dir,
        asset_id,
        package,
    )
    archive_root = project_dir / "archive" / "web"
    return _LegacyPackageArchive(
        package=package,
        destination=archive_root / f"v4-{token}",
        receipt=receipt,
        receipt_destination=(archive_root / f"v4-r-{token}.json" if receipt is not None else None),
    )


def _migrated_clip_state_json(
    row: Any,
    manifest: EditableMediaManifest,
    source_hash: str,
    media_sources: WebMediaSourcesManifest,
) -> str:
    payload = _required_object(
        json.loads(str(row["state_json"])),
        f"web_clip_state[{row['clip_id']}]",
    )
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
            "source_hash": source_hash,
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


def migrate_project_editable_media_to_v5(workspace: Any) -> None:
    project_dir = Path(workspace.project_dir).resolve()
    connection = workspace._connection
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
    rows = connection.execute(
        """SELECT asset.id AS asset_id, asset.path, asset.managed,
                  web_asset.manifest_json, web_asset.source_hash
           FROM web_asset
           JOIN asset ON asset.id=web_asset.asset_id
           ORDER BY asset.id"""
    ).fetchall()
    migrated_any = False
    for row in rows:
        raw_manifest = json.loads(str(row["manifest_json"]))
        raw_object = _required_object(
            raw_manifest,
            f"web_asset[{row['asset_id']}].manifest",
        )
        if raw_object.get("version") == 5:
            parse_editable_media_manifest(raw_object)
            continue
        if int(row["managed"]) != 1:
            raise RuntimeError(
                f"Legacy editable-media assets must be project-managed before v5 migration: {row['asset_id']}"
            )
        manifest = migrate_editable_media_v4_manifest(raw_object)
        entry = _managed_asset_path(project_dir, str(row["path"]))
        package = web_files.web_package_root_for_entry(
            entry,
            str(raw_object.get("entry", "")),
        )
        source_tree = web_files.scan_web_package(package)
        if source_tree.source_hash != str(row["source_hash"]):
            raise RuntimeError(f"Legacy editable-media package changed after import: {row['asset_id']}")
        publication = _stage_v5_publication(
            project_dir,
            str(row["asset_id"]),
            source_tree,
            manifest,
        )
        try:
            archive = _legacy_archive(
                project_dir,
                str(row["asset_id"]),
                package,
                publication.token,
            )
            clip_rows = connection.execute(
                """SELECT state.clip_id, state.state_json, state.revision
                   FROM web_clip_state AS state
                   JOIN clip ON clip.id=state.clip_id
                   WHERE clip.asset_id=?
                   ORDER BY state.clip_id""",
                (row["asset_id"],),
            ).fetchall()
            migrated_states = [
                (
                    _migrated_clip_state_json(
                        clip_row,
                        publication.manifest,
                        publication.source_hash,
                        publication.media_sources,
                    ),
                    clip_row["clip_id"],
                )
                for clip_row in clip_rows
            ]
            publication.publish()

            def commit_publication(
                publication: web_files.WebPackagePublication = publication,
                archive: _LegacyPackageArchive = archive,
            ) -> None:
                publication.mark_committed()
                archive.archive()

            workspace.enlist_transaction_publication(
                on_commit=commit_publication,
                on_rollback=lambda _error, publication=publication: (publication.archive_failed()),
            )
        except BaseException as error:
            try:
                publication.archive_failed()
            except BaseException as archive_error:
                error.add_note(f"editable-media v5 migration failure archival failed: {archive_error}")
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
               ) VALUES (?, 4, 5, ?, ?, ?, ?, ?, ?)""",
            (
                row["asset_id"],
                row["source_hash"],
                publication.source_hash,
                package.relative_to(project_dir).as_posix(),
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
    connection.execute("UPDATE schema_info SET version=36 WHERE component='project'")


def reconcile_editable_media_v4_archives(workspace: Any) -> None:
    if workspace.read_only:
        return
    try:
        rows = workspace._fetchall(
            """SELECT upgrade.asset_id, upgrade.old_package_path,
                      upgrade.archive_package_path
               FROM editable_media_upgrade AS upgrade
               ORDER BY upgrade.asset_id"""
        )
    except Exception:
        return
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
            receipt_destination=(destination.parent / f"v4-r-{destination.name.removeprefix('v4-')}.json"),
        )
        archive.archive()
