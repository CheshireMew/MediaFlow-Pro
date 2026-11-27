from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from mediaflow.atomic_file import atomic_write_text
from mediaflow.environment import (
    DEVELOPMENT_ROOT_VARIABLE,
    RUNTIME_DIRECTORY_VARIABLE,
    STORAGE_REGISTRY_PATH_VARIABLE,
    TEST_ROOT_VARIABLE,
    configured_path,
)
from mediaflow.infrastructure.project_lock import ProcessFileLock
from mediaflow.infrastructure.storage_policy import load_storage_policy

PROJECT_NAMESPACE = "mediaflow"
ROOT_REGISTRY_SCHEMA = "mediaflow-storage-roots/v1"
MAXIMUM_REGISTRY_BYTES = 32 * 1024**2


def _namespace_name(value: str) -> str:
    return value.casefold() if os.name == "nt" else value


def _development_namespace() -> Path | None:
    configured = configured_path(DEVELOPMENT_ROOT_VARIABLE)
    if configured is not None and _namespace_name(configured.name) != PROJECT_NAMESPACE:
        raise RuntimeError(
            "MEDIAFLOW_DEV_ROOT must name the mediaflow project namespace; "
            "managed artifacts cannot fall back to another root"
        )
    return configured


def development_storage_parent() -> Path:
    namespace = _development_namespace()
    if namespace is None:
        raise RuntimeError("MEDIAFLOW_DEV_ROOT is required for development storage ownership")
    return namespace.parent


def _configured_namespace(variable: str) -> Path | None:
    configured = configured_path(variable)
    if configured is None:
        return None
    return next(
        (
            path
            for path in (configured, *configured.parents)
            if _namespace_name(path.name) == PROJECT_NAMESPACE
        ),
        None,
    )


def runtime_storage_parent() -> Path:
    namespace = _configured_namespace(RUNTIME_DIRECTORY_VARIABLE)
    if namespace is not None:
        return namespace.parent
    runtime = configured_path(RUNTIME_DIRECTORY_VARIABLE)
    if runtime is not None and runtime != Path(runtime.anchor):
        return runtime.parent
    return development_storage_parent()


def verification_storage_parent() -> Path:
    namespace = _configured_namespace(TEST_ROOT_VARIABLE)
    if namespace is not None:
        return namespace.parent
    return development_storage_parent()


def managed_root_ownership(root: str | Path) -> dict[str, str]:
    selected = Path(root).expanduser().resolve()
    development = _development_namespace()
    candidates = (
        (development, "runtime-config:development_storage_parent"),
        (_configured_namespace(RUNTIME_DIRECTORY_VARIABLE), "runtime-config:runtime_storage_parent"),
        (_configured_namespace(TEST_ROOT_VARIABLE), "user-environment-policy:verification_storage_parent"),
    )
    for namespace, source in candidates:
        if namespace is not None and selected.is_relative_to(namespace):
            return {
                "scope": "project-owned-external",
                "project_namespace": PROJECT_NAMESPACE,
                "root": str(selected),
                "namespace_root": str(namespace),
                "approved_root": str(namespace.parent),
                "approved_root_source": source,
                "fallback": "block",
            }
    runtime = configured_path(RUNTIME_DIRECTORY_VARIABLE)
    if runtime is not None and runtime != Path(runtime.anchor) and selected.is_relative_to(runtime):
        # Portable installs and the desktop's runtime chooser explicitly own
        # this exact directory, even when it has no development namespace.
        return {
            **project_root_ownership(runtime, selected),
            "source": "runtime-config:MEDIAFLOW_RUNTIME_DIR",
        }
    raise RuntimeError(f"Storage root is outside the configured mediaflow namespace: {selected}")


def project_root_ownership(project_dir: str | Path, root: str | Path) -> dict[str, str]:
    project = Path(project_dir).expanduser().resolve()
    selected = Path(root).expanduser().resolve()
    if project == Path(project.anchor) or not selected.is_relative_to(project):
        raise RuntimeError(f"Artifact root escapes its media project: {selected}")
    return {
        "scope": "project-root",
        "project_namespace": PROJECT_NAMESPACE,
        "root": str(selected),
        "project_root": str(project),
        "fallback": "block",
    }


def delivery_root_ownership(root: str | Path) -> dict[str, str]:
    """A user-selected delivery is a runtime project root, not an application cache."""
    selected = Path(root).expanduser().resolve()
    if selected == Path(selected.anchor):
        raise RuntimeError("A filesystem volume cannot be used as a delivery project root")
    return {
        "scope": "project-root",
        "project_namespace": PROJECT_NAMESPACE,
        "root": str(selected),
        "project_root": str(selected),
        "source": "user-selected-delivery",
        "fallback": "block",
    }


def download_staging_ownership(destination: Path, staging: Path) -> dict[str, str]:
    destination = destination.expanduser().resolve()
    staging = staging.expanduser().resolve()
    delivery_root_ownership(destination)
    if staging.parent != destination.parent or re.fullmatch(r"\.mf-dl-[0-9a-f]{8}", staging.name) is None:
        raise RuntimeError(f"Download staging does not match its declared producer: {staging}")
    return {
        "scope": "project-root",
        "project_namespace": PROJECT_NAMESPACE,
        "root": str(staging),
        "project_root": str(staging),
        "delivery_root": str(destination),
        "source": "download-staging-plan",
        "fallback": "block",
    }


def receipt_owns_path(ownership: dict[str, str], path: str | Path) -> bool:
    selected = Path(path).expanduser().resolve()
    if selected == Path(selected.anchor) or ownership.get("project_namespace") != PROJECT_NAMESPACE:
        return False
    if ownership.get("scope") == "project-owned-external":
        namespace = Path(ownership["namespace_root"]).resolve()
        boundary = Path(ownership["root"]).resolve()
        if _namespace_name(namespace.name) != PROJECT_NAMESPACE:
            return False
        return (
            boundary.is_relative_to(namespace)
            and selected.is_relative_to(boundary)
            and namespace.parent == Path(ownership["approved_root"]).resolve()
        )
    if ownership.get("scope") == "project-root":
        roots = [Path(ownership["project_root"]).resolve()]
        if "delivery_root" in ownership:
            roots.append(Path(ownership["delivery_root"]).resolve())
        return any(root != Path(root.anchor) and selected.is_relative_to(root) for root in roots)
    return False


def storage_registry_path(runtime_dir: str | Path | None = None) -> Path:
    configured = configured_path(STORAGE_REGISTRY_PATH_VARIABLE)
    if configured is not None:
        managed_root_ownership(configured.parent)
        return configured
    development = _development_namespace()
    if development is not None:
        return development / "storage" / "registered-roots.json"
    namespace = _configured_namespace(RUNTIME_DIRECTORY_VARIABLE)
    if namespace is not None:
        return namespace / "storage" / "registered-roots.json"
    runtime = configured_path(RUNTIME_DIRECTORY_VARIABLE)
    if runtime is not None:
        managed_root_ownership(runtime)
        return runtime / "storage" / "registered-roots.json"
    if runtime_dir is not None:
        selected = Path(runtime_dir).expanduser().resolve()
        managed_root_ownership(selected)
        return selected / "storage" / "registered-roots.json"
    raise RuntimeError("A configured mediaflow namespace is required for the storage root registry")


def read_storage_root_registry(runtime_dir: str | Path | None = None) -> dict[str, Any]:
    registry = storage_registry_path(runtime_dir)
    if not registry.exists():
        return {"schema": ROOT_REGISTRY_SCHEMA, "project_namespace": PROJECT_NAMESPACE, "roots": {}}
    if registry.stat().st_size > MAXIMUM_REGISTRY_BYTES:
        raise RuntimeError(f"Storage root registry exceeds its bounded size: {registry}")
    try:
        payload = json.loads(registry.read_text(encoding="utf-8"))
        if (
            not isinstance(payload, dict)
            or payload.get("schema") != ROOT_REGISTRY_SCHEMA
            or payload.get("project_namespace") != PROJECT_NAMESPACE
            or not isinstance(payload.get("roots"), dict)
        ):
            raise ValueError("unsupported registry structure")
        for record in payload["roots"].values():
            if (
                not isinstance(record, dict)
                or not isinstance(record.get("ownership"), dict)
                or not isinstance(record.get("producer"), str)
                or not receipt_owns_path(record["ownership"], record["path"])
            ):
                raise ValueError("invalid registered root ownership")
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise RuntimeError(f"Storage root registry cannot be verified: {registry}: {error}") from error
    return payload


def register_owned_storage_roots(
    roots: tuple[Path, ...],
    *,
    ownership: dict[str, str],
    producer: str,
    receipt: Path | None = None,
    runtime_dir: str | Path | None = None,
) -> None:
    selected = tuple(path.expanduser().resolve() for path in roots)
    if any(not receipt_owns_path(ownership, path) for path in selected):
        raise RuntimeError("A producer attempted to register a root outside its ownership boundary")
    registry = storage_registry_path(runtime_dir)
    lock = ProcessFileLock(registry.with_suffix(".lock"))
    if not lock.acquire_until(timeout_seconds=15):
        raise RuntimeError(f"Timed out registering storage roots: {registry}")
    try:
        payload = read_storage_root_registry(runtime_dir)
        changed = False
        for path in selected:
            key = hashlib.sha256(os.path.normcase(str(path)).encode("utf-8")).hexdigest()
            record = {
                "path": str(path),
                "ownership": ownership,
                "producer": producer,
                "receipt": None if receipt is None else str(receipt),
            }
            if payload["roots"].get(key) != record:
                payload["roots"][key] = record
                changed = True
        if not changed:
            return
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        if len(text.encode("utf-8")) > MAXIMUM_REGISTRY_BYTES:
            raise RuntimeError("Storage root registry requires review before it can grow further")
        volume = registry.parent
        while not volume.exists() and volume.parent != volume:
            volume = volume.parent
        if (
            shutil.disk_usage(volume).free - len(text.encode("utf-8"))
            < load_storage_policy().minimum_free_bytes
        ):
            raise RuntimeError(f"Storage root registry would cross the free-space safety line: {registry}")
        atomic_write_text(registry, text, durable=True)
    finally:
        lock.release()


def forget_missing_storage_roots(roots: tuple[Path, ...], *, receipt: Path, runtime_dir: str | Path) -> None:
    registry = storage_registry_path(runtime_dir)
    lock = ProcessFileLock(registry.with_suffix(".lock"))
    if not lock.acquire_until(timeout_seconds=15):
        raise RuntimeError(f"Timed out finalizing storage roots: {registry}")
    try:
        payload = read_storage_root_registry(runtime_dir)
        changed = False
        for path in roots:
            selected = path.expanduser().resolve()
            key = hashlib.sha256(os.path.normcase(str(selected)).encode("utf-8")).hexdigest()
            record = payload["roots"].get(key)
            if record is not None and record["receipt"] == str(receipt) and not selected.exists():
                payload["roots"].pop(key)
                changed = True
        if changed:
            atomic_write_text(
                registry, json.dumps(payload, ensure_ascii=False, indent=2) + "\n", durable=True
            )
    finally:
        lock.release()


def registered_storage_roots(
    *,
    test_root: Path,
    runtime_dir: Path | None,
    receipts: dict[str, Any],
    media_projects: tuple[Path, ...] = (),
) -> dict[str, Path]:
    """Enumerate exact owned roots and published files, never entire user ancestors."""
    managed_root_ownership(test_root)
    roots = {"test_artifacts": test_root.resolve()}
    if runtime_dir is not None:
        managed_root_ownership(runtime_dir)
        roots["runtime"] = runtime_dir.resolve()
    registry = read_storage_root_registry(runtime_dir)
    roots["root_registry"] = storage_registry_path(runtime_dir)
    for key, record in registry["roots"].items():
        roots[f"registered-{key}"] = Path(record["path"])
    for index, record in enumerate(receipts.get("records", ())):
        if record.get("ownership_status") != "registered":
            continue
        for number, value in enumerate(record["registered_roots"]):
            path = Path(value).expanduser().resolve()
            if not receipt_owns_path(record["ownership"], path):
                raise RuntimeError(f"Receipt root is outside its ownership: {path}")
            roots[f"operation-{index}-{number}"] = path
    for index, project in enumerate(media_projects):
        for name in ("proxies", "failed-proxies", "cache"):
            path = project / name
            project_root_ownership(project, path)
            roots[f"project-{index}-{name}"] = path
    return roots


def registered_cache_groups() -> tuple[Path, ...]:
    return tuple(
        sorted(
            {
                Path(record["path"]).resolve()
                for record in read_storage_root_registry()["roots"].values()
                if record["producer"] == "cache-projects"
            },
            key=lambda path: os.path.normcase(str(path)),
        )
    )


def registered_producer_roots(producers: set[str], *defaults: Path) -> dict[str, Path]:
    paths = {path.resolve() for path in defaults}
    paths.update(
        Path(record["path"]).resolve()
        for record in read_storage_root_registry()["roots"].values()
        if record["producer"] in producers
    )
    return {str(index): path for index, path in enumerate(sorted(paths, key=str))}
