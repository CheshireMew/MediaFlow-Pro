from __future__ import annotations

import json
import re
import uuid
import zipfile
from collections.abc import Callable
from email.parser import BytesParser
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from mediaflow.atomic_file import atomic_write_text
from mediaflow.domain.progress import OperationProgress
from mediaflow.file_digest import sha256_file

from .resumable_download import download_with_resume

PYPI_EJS_URL = "https://pypi.org/pypi/yt-dlp-ejs/{version}/json"


def _checkpoint(check_cancelled: Callable[[], None] | None) -> None:
    if check_cancelled is not None:
        check_cancelled()


def _release(url: str) -> tuple[str, dict[str, Any]]:
    with urlopen(Request(url, headers={"User-Agent": "MediaFlow Pro setup"}), timeout=60) as response:
        payload = json.load(response)
    version = str(payload.get("info", {}).get("version") or "")
    wheel = next(
        (
            item
            for item in payload.get("urls", [])
            if str(item.get("filename", "")).endswith("-py3-none-any.whl")
            and item.get("packagetype") == "bdist_wheel"
        ),
        None,
    )
    if not version or wheel is None:
        raise RuntimeError("PyPI 没有返回可安装的 Python wheel")
    return version, wheel


def _download_wheel(
    wheel: dict[str, Any],
    directory: Path,
    progress: Callable[[OperationProgress], None] | None,
    check_cancelled: Callable[[], None] | None,
) -> Path:
    _checkpoint(check_cancelled)
    filename = str(wheel["filename"])
    if Path(filename).name != filename or "/" in filename or "\\" in filename:
        raise RuntimeError("Invalid wheel filename")
    digest = str(wheel.get("digests", {}).get("sha256") or "")
    if not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise RuntimeError("PyPI 没有返回 wheel 的 SHA-256")
    destination = directory / filename

    def report(completed: int, total: int) -> None:
        if progress:
            progress(
                OperationProgress.determinate(
                    "runtime_tool_downloading",
                    completed=completed,
                    total=total,
                    unit="bytes",
                )
            )

    download_with_resume(
        str(wheel["url"]),
        destination,
        int(wheel.get("size") or 0),
        progress=report,
        check_cancelled=check_cancelled,
    )
    if sha256_file(destination) != digest:
        raise RuntimeError(f"下载组件校验失败：{filename}")
    return destination


def _metadata(wheel: Path, name: str, version: str) -> Any:
    with zipfile.ZipFile(wheel) as archive:
        candidates = [
            path
            for path in archive.namelist()
            if path.startswith(name + "-") and path.endswith(".dist-info/METADATA")
        ]
        if len(candidates) != 1:
            raise RuntimeError(f"{name} wheel 元数据缺失")
        metadata = BytesParser().parsebytes(archive.read(candidates[0]))
    if metadata.get("Version") != version or str(metadata.get("Name")).replace("-", "_") != name:
        raise RuntimeError(f"{name} wheel 版本不匹配")
    return metadata


def _ejs_version(metadata: Any) -> str:
    for requirement in metadata.get_all("Requires-Dist", []):
        match = re.match(r"yt[-_]dlp[-_]ejs\s*==\s*([\w.]+)", requirement, re.IGNORECASE)
        if match:
            return match[1]
    raise RuntimeError("yt-dlp 未声明匹配的 EJS 版本，更新未生效")


def _extract(wheel: Path, destination: Path, package: str) -> None:
    with zipfile.ZipFile(wheel) as archive:
        for member in archive.infolist():
            if not (
                member.filename.startswith(package + "/")
                or (member.filename.startswith(package + "-") and ".dist-info/" in member.filename)
            ):
                continue
            if not (destination / member.filename).resolve().is_relative_to(destination.resolve()):
                raise RuntimeError("Invalid wheel member path")
            archive.extract(member, destination)
    if not (destination / package / "__init__.py").is_file():
        raise RuntimeError(f"{package} 安装不完整，更新未生效")


def install_ytdlp(
    runtime_dir: Path,
    metadata_url: str,
    *,
    ejs_metadata_url: str = PYPI_EJS_URL,
    progress: Callable[[OperationProgress], None] | None = None,
    check_cancelled: Callable[[], None] | None = None,
) -> dict[str, str]:
    """Publish an immutable yt-dlp + matching EJS installation together.

    The active pointer is the only commit point. Incomplete downloads remain in
    the runtime directory for inspection; existing installations are untouched.
    """
    _checkpoint(check_cancelled)
    if progress:
        progress(OperationProgress.indeterminate("ytdlp_update_checking"))
    version, wheel = _release(metadata_url)
    transaction = uuid.uuid4().hex
    downloads = runtime_dir / "downloads" / f"yt-dlp-{transaction}"
    downloads.mkdir(parents=True)
    ytdlp_wheel = _download_wheel(wheel, downloads, progress, check_cancelled)
    required_ejs = _ejs_version(_metadata(ytdlp_wheel, "yt_dlp", version))
    _checkpoint(check_cancelled)
    ejs_version, ejs_wheel = _release(ejs_metadata_url.format(version=required_ejs))
    if ejs_version != required_ejs:
        raise RuntimeError("EJS 版本与 yt-dlp 不匹配，更新未生效")
    ejs_path = _download_wheel(ejs_wheel, downloads, progress, check_cancelled)
    _metadata(ejs_path, "yt_dlp_ejs", required_ejs)
    _checkpoint(check_cancelled)
    if progress:
        progress(OperationProgress.indeterminate("ytdlp_update_installing"))
    target = runtime_dir / "tools" / "python" / f"yt-dlp-{transaction}"
    target.mkdir(parents=True)
    _extract(ytdlp_wheel, target, "yt_dlp")
    _extract(ejs_path, target, "yt_dlp_ejs")
    _checkpoint(check_cancelled)
    result = {"version": version, "ejs_version": ejs_version, "path": str(target.resolve())}
    pointer = runtime_dir / "tools" / "yt-dlp-active.json"
    atomic_write_text(pointer, json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result
