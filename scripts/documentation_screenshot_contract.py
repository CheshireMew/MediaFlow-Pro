from __future__ import annotations

import hashlib
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "docs/images/screenshot-manifest.json"
DOCUMENTATION_SCREENSHOTS = {
    "docs/images/mediaflow-home-zh-cn.png": "empty-home-zh-cn",
    "docs/images/mediaflow-workspace-zh-cn.png": "sample-workspace-zh-cn",
}


def documentation_ui_sources() -> tuple[Path, ...]:
    qml = tuple(
        sorted(
            (ROOT / "mediaflow/desktop/qml").rglob("*.qml"),
            key=lambda path: path.relative_to(ROOT).as_posix(),
        )
    )
    fixed = (
        ROOT / "mediaflow/desktop/app.py",
        ROOT / "mediaflow/desktop/presentation_catalogs.py",
        ROOT / "mediaflow/application/sample_project_service.py",
        ROOT / "scripts/update_documentation_screenshots.py",
    )
    return (*qml, *fixed)


def documentation_ui_digest() -> str:
    digest = hashlib.sha256()
    for path in documentation_ui_sources():
        relative = path.relative_to(ROOT).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        contents = normalized_source_contents(path.read_bytes())
        digest.update(len(contents).to_bytes(8, "big"))
        digest.update(contents)
    return digest.hexdigest()


def normalized_source_contents(contents: bytes) -> bytes:
    """Return text source bytes with one repository-independent newline form."""
    return contents.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def png_dimensions(path: Path) -> tuple[int, int]:
    with path.open("rb") as source:
        header = source.read(24)
    if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        raise ValueError(f"Not a valid PNG file: {path}")
    return struct.unpack(">II", header[16:24])
