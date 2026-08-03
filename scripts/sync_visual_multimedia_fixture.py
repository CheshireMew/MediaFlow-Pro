from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
import time
import uuid
from pathlib import Path

PACKAGE_SOURCES = (
    ("assets/web-media-starter", "editable-media-v5"),
    (
        "assets/web-card-cases/warm-paper-project-list",
        "editable-media-v5-cases/warm-paper-project-list",
    ),
    (
        "assets/web-card-cases/social-evidence-variants",
        "editable-media-v5-cases/social-evidence-variants",
    ),
    (
        "assets/web-card-cases/text-card-glossary",
        "editable-media-v5-cases/text-card-glossary",
    ),
)
MEDIA_BUILD_CASE_SOURCES = (
    (
        "assets/media-build-cases/segmented-video",
        "media-build-cases/segmented-video",
    ),
)
SCHEMA_SOURCE = "schemas/editable-media.v5.schema.json"
RUNTIME_SOURCE = "assets/web-media-starter/editable-media-runtime.js"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def package_files(source: Path) -> tuple[Path, ...]:
    files = tuple(
        sorted(
            (path for path in source.rglob("*") if path.is_file()),
            key=lambda path: path.relative_to(source).as_posix(),
        )
    )
    if any(path.is_symlink() for path in source.rglob("*")):
        raise ValueError(f"Producer fixture cannot contain symbolic links: {source}")
    return files


def _publish_package(staging: Path, destination: Path) -> None:
    archived: Path | None = None
    if destination.exists():
        if not destination.is_dir():
            raise ValueError(f"Fixture destination is not a directory: {destination}")
        archive_root = (
            Path(__file__).resolve().parents[1]
            / "archive"
            / "synced-visual-fixtures"
        )
        archive_root.mkdir(parents=True, exist_ok=True)
        archived = archive_root / (
            f"{destination.name}-{time.time_ns()}-{uuid.uuid4().hex[:8]}"
        )
        destination.replace(archived)
    try:
        staging.replace(destination)
    except BaseException:
        if archived is not None and not destination.exists():
            archived.replace(destination)
        raise


def sync_editable_package(
    source: Path,
    destination: Path,
    *,
    producer: str,
) -> dict[str, object]:
    source = source.resolve(strict=True)
    destination = destination.expanduser().resolve()
    manifest = json.loads((source / "editable-media.json").read_text(encoding="utf-8"))
    if manifest.get("protocol") != "editable-media" or manifest.get("version") != 5:
        raise ValueError("The producer fixture must use editable-media v5")
    files = package_files(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{destination.name}.sync-",
            dir=destination.parent,
        )
    )
    try:
        for source_file in files:
            relative = source_file.relative_to(source)
            destination_file = staging / relative
            destination_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_file, destination_file)
        hashes = {
            path.relative_to(source).as_posix(): sha256(
                staging / path.relative_to(source)
            )
            for path in files
        }
        origin = {
            "protocol": "mediaflow-generated-test-fixture",
            "version": 1,
            "producer": producer,
            "editable_media_version": 5,
            "files": hashes,
        }
        (staging / "fixture-origin.json").write_text(
            f"{json.dumps(origin, ensure_ascii=False, indent=2)}\n",
            encoding="utf-8",
        )
        existing_origin = destination / "fixture-origin.json"
        if existing_origin.is_file():
            current = json.loads(existing_origin.read_text(encoding="utf-8"))
            if current == origin:
                shutil.rmtree(staging)
                return origin
        _publish_package(staging, destination)
        return origin
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def sync_media_build_case(
    source: Path,
    destination: Path,
    *,
    producer: str,
) -> dict[str, object]:
    source = source.resolve(strict=True)
    destination = destination.expanduser().resolve()
    plan = json.loads((source / "media-build-plan.json").read_text(encoding="utf-8"))
    if (
        plan.get("protocol") != "visual-multimedia-media-build-plan"
        or plan.get("version") != 1
    ):
        raise ValueError("The producer fixture must use media-build-plan v1")
    files = package_files(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{destination.name}.sync-",
            dir=destination.parent,
        )
    )
    try:
        for source_file in files:
            relative = source_file.relative_to(source)
            destination_file = staging / relative
            destination_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_file, destination_file)
        hashes = {
            path.relative_to(source).as_posix(): sha256(
                staging / path.relative_to(source)
            )
            for path in files
        }
        origin = {
            "protocol": "mediaflow-generated-test-fixture",
            "version": 1,
            "producer": producer,
            "media_build_plan_version": 1,
            "files": hashes,
        }
        (staging / "fixture-origin.json").write_text(
            f"{json.dumps(origin, ensure_ascii=False, indent=2)}\n",
            encoding="utf-8",
        )
        existing_origin = destination / "fixture-origin.json"
        if existing_origin.is_file():
            current = json.loads(existing_origin.read_text(encoding="utf-8"))
            if current == origin:
                shutil.rmtree(staging)
                return origin
        _publish_package(staging, destination)
        return origin
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def sync_schema(source: Path, destination: Path) -> Path:
    source = source.resolve(strict=True)
    destination = destination.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    current = destination / source.name
    if current.is_file() and sha256(source) == sha256(current):
        return current
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{destination.name}.sync-",
            dir=destination.parent,
        )
    )
    try:
        copied = staging / source.name
        shutil.copyfile(source, copied)
        destination.mkdir(parents=True, exist_ok=True)
        copied.replace(destination / source.name)
        return destination / source.name
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def sync_runtime_contract(source: Path, destination_name: str) -> Path:
    destination = (
        Path(__file__).resolve().parents[1]
        / "mediaflow"
        / "resources"
        / "contracts"
        / destination_name
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    source = source.resolve(strict=True)
    if destination.is_file() and sha256(source) == sha256(destination):
        return destination
    temporary = destination.with_name(f".{destination.name}.sync")
    shutil.copyfile(source, temporary)
    temporary.replace(destination)
    return destination


def sync_contracts(skill_root: Path, destination: Path) -> dict[str, object]:
    schema_source = skill_root / SCHEMA_SOURCE
    schema_destination = sync_schema(
        schema_source,
        destination / "editable-media-v5-contract",
    )
    runtime_contract = sync_runtime_contract(
        schema_source,
        "editable-media.v5.schema.json",
    )
    runtime_script = sync_runtime_contract(
        skill_root / RUNTIME_SOURCE,
        "editable-media-runtime.v5.js",
    )
    return {
        "schema_sha256": sha256(schema_destination),
        "runtime_contract_sha256": sha256(runtime_contract),
        "runtime_script_sha256": sha256(runtime_script),
    }


def sync_corpus(skill_root: Path, destination: Path) -> dict[str, object]:
    skill_root = skill_root.expanduser().resolve(strict=True)
    destination = destination.expanduser().resolve()
    packages = {}
    for source_relative, destination_relative in PACKAGE_SOURCES:
        packages[source_relative] = sync_editable_package(
            skill_root / source_relative,
            destination / destination_relative,
            producer=f"visual-multimedia/{source_relative}",
        )
    media_build_cases = {}
    for source_relative, destination_relative in MEDIA_BUILD_CASE_SOURCES:
        media_build_cases[source_relative] = sync_media_build_case(
            skill_root / source_relative,
            destination / destination_relative,
            producer=f"visual-multimedia/{source_relative}",
        )
    contracts = sync_contracts(skill_root, destination)
    return {
        "protocol": "mediaflow-editable-media-test-corpus",
        "version": 1,
        **contracts,
        "packages": packages,
        "media_build_cases": media_build_cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sync MediaFlow Pro's editable-media v5 corpus from visual-multimedia."
    )
    parser.add_argument("visual_multimedia_root", type=Path)
    parser.add_argument(
        "--destination",
        type=Path,
        default=Path("tests/fixtures"),
    )
    parser.add_argument(
        "--contracts-only",
        action="store_true",
        help="Sync the schema and standard runtime without replacing fixtures.",
    )
    args = parser.parse_args()
    skill_root = args.visual_multimedia_root.expanduser().resolve(strict=True)
    destination = args.destination.expanduser().resolve()
    result = (
        {
            "protocol": "mediaflow-editable-media-contracts",
            "version": 1,
            **sync_contracts(skill_root, destination),
        }
        if args.contracts_only
        else sync_corpus(skill_root, destination)
    )
    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
