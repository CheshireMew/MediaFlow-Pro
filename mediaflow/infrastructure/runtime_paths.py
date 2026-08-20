from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path

from mediaflow.domain.runtime import RenderRuntimeIdentity
from mediaflow.environment import (
    RUNTIME_DIRECTORY_VARIABLE,
    configured_path,
    development_root,
)
from mediaflow.infrastructure.runtime_contract import (
    PlatformTarget,
    RuntimeContract,
    load_runtime_contract,
)
from mediaflow.infrastructure.storage_budget import project_cache_identity

ROOT = Path(__file__).resolve().parents[2]


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    if not root.is_dir():
        digest.update(b"missing")
        return digest.hexdigest()
    for path in sorted(
        (candidate for candidate in root.rglob("*") if candidate.is_file()),
        key=lambda candidate: candidate.relative_to(root).as_posix(),
    ):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
    return digest.hexdigest()


def _render_identity(
    contract: RuntimeContract,
    native_qml: Path,
) -> RenderRuntimeIdentity:
    return RenderRuntimeIdentity(
        platform=contract.target.operating_system,
        architecture=contract.target.architecture,
        chromium_version=contract.playwright.browser_version,
        chromium_sha256=contract.playwright.archive_sha256,
        font_digest=_tree_digest(ROOT / "mediaflow" / "resources" / "fonts"),
        runtime_digest=contract.digest,
        native_plugin_digest=_tree_digest(native_qml),
    )


def configured_runtime_directory() -> Path | None:
    configured = configured_path(RUNTIME_DIRECTORY_VARIABLE)
    if configured is not None:
        return configured
    root = development_root(required=False)
    return (root / "runtime").resolve() if root is not None else None


def runtime_directory() -> Path:
    configured = configured_runtime_directory()
    if configured is None:
        raise RuntimeError(
            "MEDIAFLOW_RUNTIME_DIR or MEDIAFLOW_DEV_ROOT is required for the media runtime. "
            "Copy .env.example to .env and configure this machine before starting MediaFlow Pro."
        )
    return configured


@dataclass(frozen=True, slots=True)
class RuntimePaths:
    runtime_dir: Path
    ffmpeg: Path
    ffprobe: Path
    target: PlatformTarget = field(default_factory=PlatformTarget.current)
    melt: Path | None = None
    mlt_library: Path | None = None
    mlt_root: Path | None = None
    mlt_repository: Path | None = None
    mlt_preview_repository: Path | None = None
    mlt_data: Path | None = None
    native_qml: Path | None = None
    chromium: Path | None = None
    render_identity: RenderRuntimeIdentity | None = None

    @classmethod
    def from_contract(
        cls,
        contract: RuntimeContract | None = None,
        *,
        runtime_root: Path | None = None,
        target: PlatformTarget | None = None,
    ) -> RuntimePaths:
        selected = target or (contract.target if contract is not None else None)
        contract = contract or load_runtime_contract(target=selected)
        if selected is not None and contract.target != selected:
            raise ValueError("Runtime contract does not match the requested platform")
        root = (runtime_root or runtime_directory()).expanduser().resolve()
        bundle = contract.reviewed_bundle_directory(root)
        layout = contract.layout
        native_qml = (root / layout.native_qml).resolve()
        return cls(
            runtime_dir=root,
            target=contract.target,
            ffmpeg=(bundle / layout.ffmpeg).resolve(),
            ffprobe=(bundle / layout.ffprobe).resolve(),
            melt=(bundle / layout.melt).resolve(),
            mlt_library=(bundle / layout.mlt_library).resolve(),
            mlt_root=(bundle / layout.mlt_root).resolve(),
            mlt_repository=(bundle / layout.mlt_repository).resolve(),
            mlt_preview_repository=(bundle / layout.mlt_preview_repository).resolve(),
            mlt_data=(bundle / layout.mlt_data).resolve(),
            native_qml=native_qml,
            chromium=(
                contract.chromium_directory(root) / contract.playwright.executable
            ).resolve(),
            render_identity=_render_identity(contract, native_qml),
        )

    def project_cache_dir(self, project_dir: str | Path) -> Path:
        """Return the machine-local cache root for one project location."""

        identity = project_cache_identity(
            project_dir,
            case_sensitive_paths=self.target.case_sensitive_paths,
        )
        return self.runtime_dir / "cache" / "projects" / identity

    def mlt_environment(self) -> dict[str, str]:
        repository = self.mlt_repository
        data = self.mlt_data
        if repository is None or not repository.is_dir():
            raise FileNotFoundError("Pinned MLT repository is unavailable")
        if data is None or not data.is_dir():
            raise FileNotFoundError("Pinned MLT data directory is unavailable")
        environment = os.environ.copy()
        environment.pop("MLT_REPOSITORY_DENY", None)
        environment["MLT_REPOSITORY"] = str(repository)
        environment["MLT_DATA"] = str(data)
        return environment

    def require_mlt_root(self) -> Path:
        root = self.mlt_root
        if root is None or not root.is_dir():
            raise FileNotFoundError("Pinned MLT runtime root is unavailable")
        return root
