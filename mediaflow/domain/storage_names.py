from __future__ import annotations

import hashlib
import unicodedata
from pathlib import Path


def utf16_units(value: str) -> int:
    """Count the UTF-16 code units used by one Windows path component."""

    try:
        return len(value.encode("utf-16-le")) // 2
    except UnicodeEncodeError as error:
        raise ValueError("路径包含 Windows 无法表示的 Unicode 字符") from error


WINDOWS_INTEROP_PATH_UTF16_LIMIT = 240
WINDOWS_COMPONENT_UTF16_LIMIT = 240
OUTPUT_WORKSPACE_COMPONENT_RESERVE_UTF16_UNITS = 80
DEFAULT_HIGHLIGHT_EXPORT_RELATIVE_DIRECTORY = Path("exports") / "Shorts"
# The deepest automatic output directory and one more separator must leave
# room for the complete output transaction workspace.
PROJECT_INTERNAL_PATH_RESERVE_UTF16_UNITS = (
    1
    + utf16_units(str(DEFAULT_HIGHLIGHT_EXPORT_RELATIVE_DIRECTORY))
    + 1
    + OUTPUT_WORKSPACE_COMPONENT_RESERVE_UTF16_UNITS
)
PROJECT_ROOT_PATH_UTF16_LIMIT = (
    WINDOWS_INTEROP_PATH_UTF16_LIMIT
    - PROJECT_INTERNAL_PATH_RESERVE_UTF16_UNITS
)
PROJECT_DIRECTORY_COMPONENT_UTF16_LIMIT = 120
EXPORT_QUALITY_DIRECTORY_DIGEST_HEX_CHARS = 28

_WINDOWS_INVALID_CHARACTERS = frozenset('<>:"/\\|?*')
_WINDOWS_RESERVED_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "CONIN$",
        "CONOUT$",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }
)


def export_quality_directory(project_dir: str | Path, report_id: str) -> Path:
    """Return the stable path-bounded directory for one export QA report."""

    identity = str(report_id).strip()
    if not identity:
        raise ValueError("Export quality report id cannot be empty")
    key = hashlib.sha256(identity.encode()).hexdigest()[
        :EXPORT_QUALITY_DIRECTORY_DIGEST_HEX_CHARS
    ]
    return Path(project_dir).resolve() / "generated" / "export-qa" / f"qa-{key}"


def require_windows_interop_path(
    value: str | Path,
    *,
    required_sibling_component_utf16_units: int = 0,
    max_path_utf16_units: int = WINDOWS_INTEROP_PATH_UTF16_LIMIT,
    max_component_utf16_units: int = WINDOWS_COMPONENT_UTF16_LIMIT,
) -> Path:
    """Validate a path shared with Windows-native media tools.

    Media tools do not all honor Python's long-path behavior consistently, so
    externally consumed paths stay below a conservative common boundary.
    """

    path = Path(value).expanduser().resolve()
    component_units = utf16_units(path.name)
    path_units = utf16_units(str(path))
    if component_units > max_component_utf16_units:
        raise ValueError("文件名过长，请缩短名称后重试")
    if path_units > max_path_utf16_units:
        raise ValueError("文件路径过深，请选择更靠近磁盘根目录的位置")
    if required_sibling_component_utf16_units < 0:
        raise ValueError("Sibling path reservation cannot be negative")
    if required_sibling_component_utf16_units:
        sibling_units = (
            utf16_units(str(path.parent))
            + 1
            + required_sibling_component_utf16_units
        )
        if sibling_units > max_path_utf16_units:
            raise ValueError("文件目录过深，无法安全创建导出临时文件")
    return path


def require_project_root_path(value: str | Path) -> Path:
    """Validate the one durable boundary shared by every project entry point."""

    return require_windows_interop_path(
        value,
        max_path_utf16_units=PROJECT_ROOT_PATH_UTF16_LIMIT,
        max_component_utf16_units=PROJECT_DIRECTORY_COMPONENT_UTF16_LIMIT,
    )


def safe_child_path(
    parent: str | Path,
    value: str,
    *,
    prefix: str = "",
    suffix: str = "",
    fallback: str | None = None,
    max_path_utf16_units: int = WINDOWS_INTEROP_PATH_UTF16_LIMIT,
    max_component_utf16_units: int = WINDOWS_COMPONENT_UTF16_LIMIT,
    required_sibling_component_utf16_units: int = 0,
) -> Path:
    """Build one safe child path while preserving system-owned affixes."""

    directory = Path(parent).expanduser().resolve()
    for affix in (prefix, suffix):
        if any(
            character in _WINDOWS_INVALID_CHARACTERS
            or ord(character) < 32
            or 0xD800 <= ord(character) <= 0xDFFF
            for character in affix
        ):
            raise ValueError("Path component prefix or suffix is not Windows-safe")
    if suffix.endswith((" ", ".")):
        raise ValueError("Path component suffix cannot end in a space or period")
    fixed_units = utf16_units(prefix + suffix)
    value_budget = min(
        max_component_utf16_units - fixed_units,
        max_path_utf16_units - utf16_units(str(directory)) - 1 - fixed_units,
    )
    if value_budget < 8:
        raise ValueError("文件目录过深，无法生成安全的文件名")
    component = (
        prefix
        + safe_path_component(
            value,
            fallback=fallback,
            max_utf16_units=value_budget,
        )
        + suffix
    )
    return require_windows_interop_path(
        directory / component,
        required_sibling_component_utf16_units=(
            required_sibling_component_utf16_units
        ),
        max_path_utf16_units=max_path_utf16_units,
        max_component_utf16_units=max_component_utf16_units,
    )


def content_addressed_child_path(
    parent: str | Path,
    identity: str | bytes,
    *,
    namespace: str,
    suffix: str,
    minimum_digest_hex_chars: int = 24,
    required_descendant_component_utf16_units: int = 0,
    max_path_utf16_units: int = WINDOWS_INTEROP_PATH_UTF16_LIMIT,
) -> Path:
    """Build a short stable path whose identity survives dynamic truncation.

    Rebuildable internal artifacts use a hash of their complete identity
    instead of truncating UUIDs or user text. The digest expands into whatever
    space the real parent path leaves, with a collision-resistant minimum.
    """

    if minimum_digest_hex_chars < 16:
        raise ValueError("Content-addressed paths require at least 16 digest characters")
    if required_descendant_component_utf16_units < 0:
        raise ValueError("Descendant path reservation cannot be negative")
    encoded_identity = (
        identity
        if isinstance(identity, bytes)
        else str(identity).encode("utf-8")
    )
    digest = hashlib.sha256(encoded_identity).hexdigest()
    prefix = f"{safe_path_component(namespace, max_utf16_units=24)}-"
    directory = Path(parent).expanduser().resolve()
    fixed_units = utf16_units(prefix + suffix)
    effective_path_limit = (
        max_path_utf16_units
        - (
            required_descendant_component_utf16_units + 1
            if required_descendant_component_utf16_units
            else 0
        )
    )
    digest_budget = min(
        len(digest),
        WINDOWS_COMPONENT_UTF16_LIMIT - fixed_units,
        effective_path_limit - utf16_units(str(directory)) - 1 - fixed_units,
    )
    if digest_budget < minimum_digest_hex_chars:
        raise ValueError(
            "项目目录过深，无法为媒体缓存保留安全的原生工具路径"
        )
    return safe_child_path(
        directory,
        digest[:digest_budget],
        prefix=prefix,
        suffix=suffix,
        fallback=digest,
        max_path_utf16_units=effective_path_limit,
        max_component_utf16_units=WINDOWS_COMPONENT_UTF16_LIMIT,
    )


def safe_path_component(
    value: str,
    *,
    fallback: str | None = None,
    max_utf16_units: int = 120,
) -> str:
    """Return one portable Windows path component with a bounded length."""

    if max_utf16_units < 8:
        raise ValueError("Path component limit must be at least 8 UTF-16 units")
    candidate = _normalize_component(value)
    if not candidate:
        if fallback is None:
            raise ValueError("Path component contains no usable characters")
        candidate = _normalize_component(fallback)
    if not candidate:
        raise ValueError("Path component fallback contains no usable characters")

    if _is_windows_reserved(candidate):
        candidate = f"_{candidate}"
    candidate = _truncate_utf16(candidate, max_utf16_units).rstrip(" .")
    if not candidate:
        raise ValueError("Path component is empty after applying its length limit")
    if _is_windows_reserved(candidate):
        candidate = _truncate_utf16(
            f"_{candidate}",
            max_utf16_units,
        ).rstrip(" .")
    return candidate


def _normalize_component(value: str) -> str:
    normalized = unicodedata.normalize("NFC", str(value).strip())
    output: list[str] = []
    replacing = False
    for character in normalized:
        invalid = (
            character in _WINDOWS_INVALID_CHARACTERS
            or ord(character) < 32
            # Windows filenames cannot represent UTF-16 surrogate code points.
            # Python strings can still contain them, so reject them before a
            # later UTF-16 length check or filesystem operation raises a codec
            # error.
            or 0xD800 <= ord(character) <= 0xDFFF
        )
        if invalid:
            if not replacing:
                output.append("_")
            replacing = True
        else:
            output.append(character)
            replacing = False
    return "".join(output).strip(" .")


def _is_windows_reserved(value: str) -> bool:
    device_name = value.split(".", maxsplit=1)[0].upper()
    return device_name in _WINDOWS_RESERVED_NAMES


def _truncate_utf16(value: str, maximum_units: int) -> str:
    output: list[str] = []
    used_units = 0
    for character in value:
        character_units = 2 if ord(character) > 0xFFFF else 1
        if used_units + character_units > maximum_units:
            break
        output.append(character)
        used_units += character_units
    return "".join(output)
