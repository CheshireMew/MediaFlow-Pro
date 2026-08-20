from __future__ import annotations

import json
import re
import subprocess
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.runtime_capabilities import RuntimeComponentInstallResult
from mediaflow.domain.settings import ServiceSettings
from mediaflow.file_digest import sha256_file

from .resumable_download import DownloadSizeError, DownloadTransferError, download_with_resume
from .runtime_paths import RuntimePaths
from .storage_budget import (
    finalize_storage_receipt,
    require_runtime_budget,
    start_storage_receipt,
)
from .subprocess_runner import run_cancellable

ComponentProgress = Callable[[OperationProgress], None]
DEFAULT_COMPONENT_LOCK = Path(__file__).resolve().parents[1] / "resources" / "runtime-components.lock.json"


@dataclass(frozen=True, slots=True)
class RuntimeComponentDefinition:
    id: str
    display_name: str
    version: str
    homepage: str
    license: str
    distribution: str
    bundle_allowed: bool
    license_notes: str
    targets: tuple[str, ...]
    archive_file: str
    archive_url: str
    archive_size: int
    archive_sha256: str
    install_root: str
    entrypoint: str
    required_paths: tuple[str, ...]
    minimum_free_bytes: int
    peak_install_bytes: int


@dataclass(frozen=True, slots=True)
class RuntimeComponentInstallation:
    definition: RuntimeComponentDefinition
    root: Path
    entrypoint: Path


def load_runtime_component_catalog(
    path: str | Path = DEFAULT_COMPONENT_LOCK,
) -> dict[str, RuntimeComponentDefinition]:
    source = Path(path).resolve()
    document: Any = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("schema_version") != 3:
        raise ValueError("Runtime component lock schema is not supported")
    records = document.get("components")
    if not isinstance(records, list) or not records:
        raise ValueError("Runtime component lock has no components")
    catalog: dict[str, RuntimeComponentDefinition] = {}
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("Runtime component record must be an object")
        archive = record.get("archive")
        install = record.get("install")
        if not isinstance(archive, dict) or not isinstance(install, dict):
            raise ValueError("Runtime component archive and install sections are required")
        component = RuntimeComponentDefinition(
            id=str(record.get("id") or ""),
            display_name=str(record.get("display_name") or ""),
            version=str(record.get("version") or ""),
            homepage=str(record.get("homepage") or ""),
            license=str(record.get("license") or ""),
            distribution=str(record.get("distribution") or ""),
            bundle_allowed=record.get("bundle_allowed") is True,
            license_notes=str(record.get("license_notes") or ""),
            targets=tuple(str(item) for item in record.get("targets") or ()),
            archive_file=str(archive.get("file_name") or ""),
            archive_url=str(archive.get("url") or ""),
            archive_size=int(archive.get("size_bytes") or 0),
            archive_sha256=str(archive.get("sha256") or "").lower(),
            install_root=str(install.get("root") or ""),
            entrypoint=str(install.get("entrypoint") or ""),
            required_paths=tuple(str(item) for item in install.get("required_paths") or ()),
            minimum_free_bytes=int(install.get("minimum_free_bytes") or 0),
            peak_install_bytes=int(
                install.get("peak_install_bytes") or int(archive.get("size_bytes") or 0) * 3
            ),
        )
        required_values = (
            component.id,
            component.display_name,
            component.version,
            component.homepage,
            component.license,
            component.distribution,
            component.archive_file,
            component.archive_url,
            component.install_root,
            component.entrypoint,
        )
        if not all(required_values) or not component.required_paths or not component.targets:
            raise ValueError("Runtime component record is incomplete")
        if component.distribution not in {"upstream-download-only", "redistributable"}:
            raise ValueError(
                f"Runtime component {component.id!r} has an unsupported distribution policy"
            )
        if component.bundle_allowed and component.license == "NOASSERTION":
            raise ValueError(
                f"Runtime component {component.id!r} cannot be bundled without a license assertion"
            )
        if re.fullmatch(r"[0-9a-f]{64}", component.archive_sha256) is None:
            raise ValueError(f"Runtime component {component.id!r} requires a lowercase SHA-256")
        if component.peak_install_bytes < component.archive_size:
            raise ValueError(
                f"Runtime component {component.id!r} peak install bytes must include its archive"
            )
        if component.id in catalog:
            raise ValueError(f"Duplicate runtime component id: {component.id}")
        catalog[component.id] = component
    return catalog


class RuntimeComponentService:
    def __init__(
        self,
        settings: ServiceSettings,
        paths: RuntimePaths,
        *,
        catalog_path: str | Path = DEFAULT_COMPONENT_LOCK,
    ) -> None:
        self.settings = settings
        self.paths = paths
        self.catalog = load_runtime_component_catalog(catalog_path)

    def resolve(self, component_id: str) -> RuntimeComponentInstallation | None:
        definition = self._definition(component_id)
        if self.paths.target.key not in definition.targets:
            return None
        if component_id == "faster-whisper-xxl":
            configured = self.settings.asr.cli_path
            if configured:
                executable = Path(configured).expanduser()
                if executable.is_file():
                    return RuntimeComponentInstallation(
                        definition=definition,
                        root=executable.resolve().parent,
                        entrypoint=executable.resolve(),
                    )
        for candidate in self._candidate_roots(component_id, definition):
            root = candidate.expanduser()
            if self._valid_installation(root, definition):
                resolved_root = root.resolve()
                return RuntimeComponentInstallation(
                    definition=definition,
                    root=resolved_root,
                    entrypoint=(resolved_root / definition.entrypoint).resolve(),
                )
        return None

    def status(self, *, probe: bool = False) -> dict[str, dict[str, Any]]:
        return {component_id: self._status(component_id, probe=probe) for component_id in self.catalog}

    def install_selected(
        self,
        component_ids: Iterable[str],
        *,
        progress: ComponentProgress | None = None,
        check_cancelled: Callable[[], None] | None = None,
    ) -> dict[str, RuntimeComponentInstallResult]:
        selected = list(dict.fromkeys(str(item) for item in component_ids))
        if not selected:
            raise ValueError("请至少选择一个运行组件")
        for component_id in selected:
            definition = self._definition(component_id)
            if self.paths.target.key not in definition.targets:
                raise RuntimeError(f"{definition.display_name} is not available for {self.paths.target.key}")
        installed: dict[str, RuntimeComponentInstallResult] = {}
        for component_id in selected:
            installation = self.resolve(component_id)
            if installation is None:
                installation = self._install(
                    self._definition(component_id),
                    progress=progress,
                    check_cancelled=check_cancelled,
                )
            installed[component_id] = RuntimeComponentInstallResult(
                component_id=component_id,
                root=str(installation.root),
                entrypoint=str(installation.entrypoint),
            )
        return installed

    def _status(self, component_id: str, *, probe: bool) -> dict[str, Any]:
        definition = self._definition(component_id)
        supported = self.paths.target.key in definition.targets
        installation = self.resolve(component_id)
        result: dict[str, Any] = {
            "id": definition.id,
            "displayName": definition.display_name,
            "version": definition.version,
            "supported": supported,
            "downloadBytes": definition.archive_size,
            "downloadGiB": round(definition.archive_size / 1024**3, 2),
            "homepage": definition.homepage,
            "license": definition.license,
            "distribution": definition.distribution,
            "bundleAllowed": definition.bundle_allowed,
            "licenseNotes": definition.license_notes,
            "installed": installation is not None,
            "ready": False,
            "path": str(installation.root) if installation else "",
            "entrypoint": str(installation.entrypoint) if installation else "",
            "reason": ("该可选组件没有当前平台的受支持构建" if not supported else "尚未安装或选择本地目录"),
        }
        if installation is None:
            return result
        if not probe:
            result.update(ready=True, reason="")
            return result
        ready, version, reason = self._probe(installation)
        result.update(ready=ready, version=version or definition.version, reason=reason)
        return result

    def _probe(self, installation: RuntimeComponentInstallation) -> tuple[bool, str, str]:
        try:
            if installation.definition.id == "faster-whisper-xxl":
                completed = run_cancellable(
                    [str(installation.entrypoint), "--version"],
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=30,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                output = "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()
                first_line = next((line.strip() for line in output.splitlines() if line.strip()), "")
                if completed.returncode != 0 or not first_line:
                    return False, "", first_line or f"探测退出码 {completed.returncode}"
                return True, first_line, ""
            python = installation.root / "runtime" / "python.exe"
            completed = run_cancellable(
                [str(python), "-c", "import fastapi, torch; print(torch.__version__)"],
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                cwd=str(installation.root),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            output = "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()
            if completed.returncode != 0:
                return False, "", output or f"探测退出码 {completed.returncode}"
            return True, installation.definition.version, ""
        except (OSError, subprocess.TimeoutExpired) as error:
            return False, "", f"探测失败：{error}"

    def _install(
        self,
        definition: RuntimeComponentDefinition,
        *,
        progress: ComponentProgress | None,
        check_cancelled: Callable[[], None] | None,
    ) -> RuntimeComponentInstallation:
        downloads = self.paths.runtime_dir / "downloads"
        preflight = require_runtime_budget(
            self.paths.runtime_dir,
            expected_new_bytes=definition.peak_install_bytes,
            label=f"MediaFlow runtime component {definition.display_name}",
        )
        receipt = start_storage_receipt(
            self.paths.runtime_dir,
            producer="runtime-component",
            operation_id=f"{definition.id}:{definition.version}",
            owned_root=self.paths.runtime_dir,
            preflight=preflight,
        )
        downloads.mkdir(parents=True, exist_ok=True)
        archive_path = downloads / definition.archive_file

        def report_download(completed: int, total: int) -> None:
            if progress:
                progress(
                    OperationProgress.determinate(
                        "runtime_component_downloading",
                        completed=completed,
                        total=total,
                        unit="bytes",
                    )
                )

        try:
            try:
                download_with_resume(
                    definition.archive_url,
                    archive_path,
                    definition.archive_size,
                    progress=report_download,
                    check_cancelled=check_cancelled,
                )
            except DownloadSizeError as error:
                raise RuntimeError(
                    f"运行组件下载不完整：{error.actual} / {error.expected}"
                ) from error
            except DownloadTransferError as error:
                raise RuntimeError(f"运行组件下载失败：{error}") from error
            self._verify_archive(archive_path, definition)
            if progress:
                progress(OperationProgress.indeterminate("runtime_component_extracting"))
            tools_root = self.paths.runtime_dir / "tools"
            tools_root.mkdir(parents=True, exist_ok=True)
            if zipfile.is_zipfile(archive_path):
                with zipfile.ZipFile(archive_path) as archive:
                    archive.extractall(tools_root)
            else:
                completed = run_cancellable(
                    ["tar", "-xf", str(archive_path), "-C", str(tools_root)],
                    check_cancelled=check_cancelled,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=3600,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if completed.returncode != 0:
                    raise RuntimeError(
                        (completed.stderr or completed.stdout or "运行组件解压失败").strip()
                    )
            installation = self.resolve(definition.id)
            if installation is None:
                expected = tools_root / definition.install_root / definition.entrypoint
                raise RuntimeError(f"解压完成后没有找到组件入口：{expected}")
        except BaseException as error:
            partial = (archive_path,) if archive_path.exists() else ()
            finalize_storage_receipt(
                receipt,
                status=("interrupted" if isinstance(error, (KeyboardInterrupt, SystemExit)) else "failed"),
                outputs=partial,
                error=str(error),
            )
            raise
        finalize_storage_receipt(
            receipt,
            status="passed",
            outputs=(archive_path, installation.root),
        )
        return installation

    @staticmethod
    def _verify_archive(path: Path, definition: RuntimeComponentDefinition) -> None:
        actual_size = path.stat().st_size
        if definition.archive_size and actual_size != definition.archive_size:
            raise RuntimeError(f"运行组件压缩包大小错误：{actual_size}")
        actual = sha256_file(path)
        if actual != definition.archive_sha256:
            raise RuntimeError(f"运行组件压缩包 SHA-256 不匹配：{actual}")

    def _candidate_roots(
        self,
        component_id: str,
        definition: RuntimeComponentDefinition,
    ) -> tuple[Path, ...]:
        candidates: list[Path] = []
        if component_id == "gpt-sovits-v2pro":
            configured = self.settings.speech_synthesis.gpt_sovits_root
            if configured:
                candidates.append(Path(configured))
        candidates.append(self.paths.runtime_dir / "tools" / definition.install_root)
        return tuple(candidates)

    @staticmethod
    def _valid_installation(root: Path, definition: RuntimeComponentDefinition) -> bool:
        return root.is_dir() and all((root / relative).is_file() for relative in definition.required_paths)

    def _definition(self, component_id: str) -> RuntimeComponentDefinition:
        try:
            return self.catalog[component_id]
        except KeyError as error:
            raise ValueError(f"未知运行组件：{component_id}") from error
