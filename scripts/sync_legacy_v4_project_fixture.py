from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from pathlib import Path

from mediaflow.file_digest import sha256_file


def _validate_source(source: Path) -> None:
    database = source / "project.mfp"
    if not database.is_file():
        raise FileNotFoundError(database)
    connection = sqlite3.connect(database)
    try:
        version_row = connection.execute(
            "SELECT version FROM schema_info WHERE component='project'"
        ).fetchone()
        if version_row is None or int(version_row[0]) != 35:
            raise ValueError(
                "Legacy editable-media project fixture must use project "
                "schema 35"
            )
        manifests = connection.execute(
            "SELECT manifest_json FROM web_asset ORDER BY asset_id"
        ).fetchall()
        if not manifests:
            raise ValueError(
                "Legacy editable-media project fixture has no web assets"
            )
        for (manifest_json,) in manifests:
            manifest = json.loads(str(manifest_json))
            if (
                not isinstance(manifest, dict)
                or manifest.get("protocol") != "editable-media"
                or manifest.get("version") != 4
            ):
                raise ValueError(
                    "Legacy project fixture must contain only final v4 "
                    "editable-media manifests"
                )
    finally:
        connection.close()


def _source_files(source: Path) -> tuple[Path, ...]:
    files = [source / "project.mfp"]
    web_root = source / "sources" / "web"
    if not web_root.is_dir():
        raise FileNotFoundError(web_root)
    files.extend(
        sorted(
            (path for path in web_root.rglob("*") if path.is_file()),
            key=lambda path: path.relative_to(source).as_posix(),
        )
    )
    return tuple(files)


def sync_fixture(source: Path, destination: Path) -> dict[str, object]:
    source = source.expanduser().resolve(strict=True)
    destination = destination.expanduser().resolve()
    _validate_source(source)
    source_files = _source_files(source)
    staging = destination.with_name(
        f".{destination.name}.sync-{sha256_file(source / 'project.mfp')[:12]}"
    )
    if staging.exists():
        raise FileExistsError(staging)
    staging.mkdir(parents=True)
    hashes: dict[str, str] = {}
    for source_file in source_files:
        relative = source_file.relative_to(source)
        target = staging / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_file, target)
        hashes[relative.as_posix()] = sha256_file(target)
    origin = {
        "protocol": "mediaflow-real-project-fixture",
        "version": 1,
        "producer": "MediaFlow Pro v2 + visual-multimedia editable-media v4",
        "captured_at": "2026-07-30",
        "project_schema_version": 35,
        "editable_media_version": 4,
        "files": hashes,
    }
    (staging / "fixture-origin.json").write_text(
        json.dumps(origin, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if destination.exists():
        raise FileExistsError(
            "Legacy fixture destination already exists; archive it explicitly "
            f"before resynchronizing: {destination}"
        )
    staging.replace(destination)
    return origin


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Snapshot a real schema-35 editable-media v4 project for the "
            "one-time v5 migration acceptance test."
        )
    )
    parser.add_argument("source", type=Path)
    parser.add_argument(
        "--destination",
        type=Path,
        default=Path("tests/fixtures/editable-media-v4-project"),
    )
    args = parser.parse_args()
    print(
        json.dumps(
            sync_fixture(args.source, args.destination),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
