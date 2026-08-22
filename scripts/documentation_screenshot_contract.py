from __future__ import annotations

import hashlib
import struct
import zlib
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
        ROOT / "scripts/documentation_screenshot_contract.py",
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


def _paeth(left: int, above: int, upper_left: int) -> int:
    estimate = left + above - upper_left
    left_distance = abs(estimate - left)
    above_distance = abs(estimate - above)
    upper_left_distance = abs(estimate - upper_left)
    if left_distance <= above_distance and left_distance <= upper_left_distance:
        return left
    if above_distance <= upper_left_distance:
        return above
    return upper_left


def png_rgb_rows(path: Path) -> tuple[int, int, tuple[bytes, ...]]:
    payload = path.read_bytes()
    if payload[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"Not a valid PNG file: {path}")
    offset = 8
    width = height = color_type = bit_depth = interlace = 0
    compressed = bytearray()
    while offset + 12 <= len(payload):
        size = struct.unpack(">I", payload[offset : offset + 4])[0]
        kind = payload[offset + 4 : offset + 8]
        data_start = offset + 8
        data_end = data_start + size
        if data_end + 4 > len(payload):
            raise ValueError(f"PNG chunk is incomplete: {path}")
        data = payload[data_start:data_end]
        if kind == b"IHDR":
            width, height, bit_depth, color_type, compression, filtering, interlace = (
                struct.unpack(">IIBBBBB", data)
            )
            if compression != 0 or filtering != 0:
                raise ValueError(f"PNG compression/filtering is unsupported: {path}")
        elif kind == b"IDAT":
            compressed.extend(data)
        elif kind == b"IEND":
            break
        offset = data_end + 4
    if width <= 0 or height <= 0 or bit_depth != 8 or color_type not in {2, 6} or interlace != 0:
        raise ValueError(f"PNG pixel format is unsupported: {path}")
    channels = 3 if color_type == 2 else 4
    stride = width * channels
    decoded = zlib.decompress(bytes(compressed))
    if len(decoded) != height * (stride + 1):
        raise ValueError(f"PNG scanline payload has the wrong size: {path}")
    rows: list[bytes] = []
    previous = bytearray(stride)
    cursor = 0
    for _row_index in range(height):
        filter_type = decoded[cursor]
        cursor += 1
        encoded = decoded[cursor : cursor + stride]
        cursor += stride
        row = bytearray(stride)
        for index, value in enumerate(encoded):
            left = row[index - channels] if index >= channels else 0
            above = previous[index]
            upper_left = previous[index - channels] if index >= channels else 0
            if filter_type == 0:
                reconstructed = value
            elif filter_type == 1:
                reconstructed = value + left
            elif filter_type == 2:
                reconstructed = value + above
            elif filter_type == 3:
                reconstructed = value + ((left + above) // 2)
            elif filter_type == 4:
                reconstructed = value + _paeth(left, above, upper_left)
            else:
                raise ValueError(f"PNG scanline filter is unsupported: {path}")
            row[index] = reconstructed & 0xFF
        rows.append(bytes(row))
        previous = row
    if channels == 4:
        rows = [
            bytes(channel for index, channel in enumerate(row) if index % 4 != 3)
            for row in rows
        ]
    return width, height, tuple(rows)


def png_region_metrics(
    path: Path,
    *,
    x: int,
    y: int,
    width: int,
    height: int,
    sample_step: int = 4,
) -> dict[str, object]:
    image_width, image_height, rows = png_rgb_rows(path)
    if (
        x < 0
        or y < 0
        or width <= 0
        or height <= 0
        or x + width > image_width
        or y + height > image_height
        or sample_step <= 0
    ):
        raise ValueError(f"PNG visual assertion region is outside the image: {path}")
    colors: set[tuple[int, int, int]] = set()
    luminance_min = 255
    luminance_max = 0
    non_dark = 0
    samples = 0
    for row_index in range(y, y + height, sample_step):
        row = rows[row_index]
        for column in range(x, x + width, sample_step):
            offset = column * 3
            red, green, blue = row[offset : offset + 3]
            color = (red, green, blue)
            colors.add(color)
            luminance = (54 * red + 183 * green + 19 * blue) // 256
            luminance_min = min(luminance_min, luminance)
            luminance_max = max(luminance_max, luminance)
            non_dark += int(max(color) >= 28)
            samples += 1
    return {
        "region": {"x": x, "y": y, "width": width, "height": height},
        "sample_step": sample_step,
        "sample_count": samples,
        "distinct_color_count": len(colors),
        "non_dark_ratio": round(non_dark / samples, 6),
        "luminance_range": luminance_max - luminance_min,
    }
