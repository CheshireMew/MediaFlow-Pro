from __future__ import annotations

from pathlib import Path
from typing import Literal, cast

from .model_base import DomainModel

WebExportFormat = Literal["png", "gif", "alpha_video", "video", "overlay"]

WEB_EXPORT_FORMATS: tuple[WebExportFormat, ...] = (
    "png",
    "gif",
    "alpha_video",
    "video",
    "overlay",
)
_EXPORT_SUFFIXES: dict[WebExportFormat, tuple[str, ...]] = {
    "png": (".png",),
    "gif": (".gif",),
    "alpha_video": (".mkv",),
    "video": (".mp4", ".mov", ".mkv"),
    "overlay": (".png", ".mkv"),
}
_EXPORT_DEFAULT_SUFFIXES: dict[WebExportFormat, str] = {
    "png": ".png",
    "gif": ".gif",
    "alpha_video": ".mkv",
    "video": ".mp4",
    "overlay": ".png",
}


def web_export_suffixes(
    format_name: str,
    *,
    overlay_suffix: str | None = None,
) -> tuple[str, ...]:
    if format_name not in _EXPORT_SUFFIXES:
        raise ValueError(f"未知的网页导出格式：{format_name}")
    export_format = cast(WebExportFormat, format_name)
    if export_format != "overlay" or overlay_suffix is None:
        return _EXPORT_SUFFIXES[export_format]
    normalized = overlay_suffix.strip().lower()
    if normalized not in _EXPORT_SUFFIXES["overlay"]:
        raise ValueError(f"网页叠加层不支持输出扩展名：{overlay_suffix}")
    return (normalized,)


def default_web_export_suffix(
    format_name: str,
    *,
    overlay_suffix: str | None = None,
) -> str:
    suffixes = web_export_suffixes(
        format_name,
        overlay_suffix=overlay_suffix,
    )
    if format_name == "overlay" and overlay_suffix is not None:
        return suffixes[0]
    return _EXPORT_DEFAULT_SUFFIXES[cast(WebExportFormat, format_name)]


def require_web_export_destination(
    output_path: str | Path,
    format_name: str,
    *,
    overlay_suffix: str | None = None,
) -> Path:
    destination = Path(output_path).expanduser().resolve()
    suffixes = web_export_suffixes(
        format_name,
        overlay_suffix=overlay_suffix,
    )
    if destination.suffix.lower() not in suffixes:
        readable = "、".join(suffixes)
        raise ValueError(f"网页导出格式“{format_name}”需要使用以下扩展名：{readable}")
    return destination


class WebClipExportResult(DomainModel):
    clip_id: str
    format: WebExportFormat
    output_path: str
    cache_path: str
