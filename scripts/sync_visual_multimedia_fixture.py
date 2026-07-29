from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

FILES = (
    "editable-media.json",
    "editable-media-runtime.js",
    "index.html",
    "media-sources.json",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sync_fixture(source: Path, destination: Path) -> dict[str, object]:
    source = source.expanduser().resolve(strict=True)
    destination = destination.expanduser().resolve()
    manifest = json.loads((source / "editable-media.json").read_text(encoding="utf-8"))
    if manifest.get("protocol") != "editable-media" or manifest.get("version") != 3:
        raise ValueError("The producer fixture must use editable-media v3")
    missing = [name for name in FILES if not (source / name).is_file()]
    if missing:
        raise FileNotFoundError(f"Producer fixture is incomplete: {missing}")
    destination.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        shutil.copyfile(source / name, destination / name)
    hashes = {name: sha256(destination / name) for name in FILES}
    origin = {
        "protocol": "mediaflow-generated-test-fixture",
        "version": 1,
        "producer": "visual-multimedia/assets/web-media-starter",
        "editable_media_version": 3,
        "files": hashes,
    }
    (destination / "fixture-origin.json").write_text(
        f"{json.dumps(origin, ensure_ascii=False, indent=2)}\n",
        encoding="utf-8",
    )
    return origin


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sync MediaFlow's generated editable-media fixture from visual-multimedia."
    )
    parser.add_argument("source", type=Path)
    parser.add_argument(
        "--destination",
        type=Path,
        default=Path("tests/fixtures/editable-media-v3"),
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
