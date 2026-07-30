from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

PACKAGE_SOURCES = (
    ("assets/web-media-starter", "editable-media-v3"),
    (
        "assets/web-card-cases/warm-paper-project-list",
        "editable-media-v3-cases/warm-paper-project-list",
    ),
    (
        "assets/web-card-cases/social-evidence-variants",
        "editable-media-v3-cases/social-evidence-variants",
    ),
)
SCHEMA_SOURCE = "schemas/editable-media.v3.schema.json"


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


def sync_package(
    source: Path,
    destination: Path,
    *,
    producer: str,
) -> dict[str, object]:
    source = source.resolve(strict=True)
    destination = destination.expanduser().resolve()
    manifest = json.loads((source / "editable-media.json").read_text(encoding="utf-8"))
    if manifest.get("protocol") != "editable-media" or manifest.get("version") != 3:
        raise ValueError("The producer fixture must use editable-media v3")
    files = package_files(source)
    destination.mkdir(parents=True, exist_ok=True)
    for source_file in files:
        relative = source_file.relative_to(source)
        destination_file = destination / relative
        destination_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_file, destination_file)
    hashes = {
        path.relative_to(source).as_posix(): sha256(destination / path.relative_to(source))
        for path in files
    }
    origin = {
        "protocol": "mediaflow-generated-test-fixture",
        "version": 1,
        "producer": producer,
        "editable_media_version": 3,
        "files": hashes,
    }
    (destination / "fixture-origin.json").write_text(
        f"{json.dumps(origin, ensure_ascii=False, indent=2)}\n",
        encoding="utf-8",
    )
    return origin


def sync_corpus(skill_root: Path, destination: Path) -> dict[str, object]:
    skill_root = skill_root.expanduser().resolve(strict=True)
    destination = destination.expanduser().resolve()
    packages = {}
    for source_relative, destination_relative in PACKAGE_SOURCES:
        packages[source_relative] = sync_package(
            skill_root / source_relative,
            destination / destination_relative,
            producer=f"visual-multimedia/{source_relative}",
        )
    schema_source = skill_root / SCHEMA_SOURCE
    schema_destination = destination / "editable-media-v3-contract" / schema_source.name
    schema_destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(schema_source, schema_destination)
    return {
        "protocol": "mediaflow-editable-media-test-corpus",
        "version": 1,
        "schema_sha256": sha256(schema_destination),
        "packages": packages,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sync MediaFlow's editable-media v3 corpus from visual-multimedia."
    )
    parser.add_argument("visual_multimedia_root", type=Path)
    parser.add_argument(
        "--destination",
        type=Path,
        default=Path("tests/fixtures"),
    )
    args = parser.parse_args()
    print(
        json.dumps(
            sync_corpus(args.visual_multimedia_root, args.destination),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
