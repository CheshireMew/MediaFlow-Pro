from __future__ import annotations

from pathlib import Path

from mediaflow.domain.settings import AsrSettings

from .runtime_paths import RuntimePaths

HUGGING_FACE_CACHE_PREFIX = "models--Systran--faster-whisper-"
DIRECT_MODEL_PREFIX = "faster-whisper-"


class FasterWhisperModelStore:
    """The single model-storage boundary shared by every ASR backend and the UI."""

    def __init__(self, settings: AsrSettings, paths: RuntimePaths):
        self.settings = settings
        self.paths = paths

    @property
    def root(self) -> Path:
        configured = str(self.settings.model_directory or "").strip()
        if configured:
            return Path(configured).expanduser().resolve()
        return (self.paths.runtime_dir / "models" / "faster-whisper").resolve()

    def prepare(self) -> Path:
        root = self.root
        root.mkdir(parents=True, exist_ok=True)
        return root

    def local_model_path(self) -> Path | None:
        selected_model = self.settings.model.strip()
        selected_path = Path(selected_model).expanduser()
        if self._is_complete_model(selected_path):
            return selected_path.resolve()
        for candidate in (
            self.root / f"{DIRECT_MODEL_PREFIX}{selected_model}",
            self.root / selected_model,
        ):
            if self._is_complete_model(candidate):
                return candidate.resolve()
        snapshots = self.root / f"{HUGGING_FACE_CACHE_PREFIX}{selected_model}" / "snapshots"
        if snapshots.is_dir():
            snapshot = next(
                (
                    candidate
                    for candidate in snapshots.iterdir()
                    if self._is_complete_model(candidate)
                ),
                None,
            )
            if snapshot is not None:
                return snapshot.resolve()
        return None

    def builtin_model_reference(self) -> str:
        local_path = self.local_model_path()
        return str(local_path) if local_path is not None else self.settings.model.strip()

    def installed_models(self) -> frozenset[str]:
        root = self.root
        if not root.is_dir():
            return frozenset()
        installed: set[str] = set()
        for candidate in root.iterdir():
            if not candidate.is_dir():
                continue
            model = self._model_name(candidate.name)
            direct_model = candidate / "model.bin"
            snapshots = candidate / "snapshots"
            has_snapshot = snapshots.is_dir() and any(
                (snapshot / "model.bin").is_file() for snapshot in snapshots.iterdir()
            )
            if direct_model.is_file() or has_snapshot:
                installed.add(model)
        selected_model = self.settings.model.strip()
        if selected_model and self._is_complete_model(Path(selected_model).expanduser()):
            installed.add(selected_model)
        return frozenset(installed)

    @staticmethod
    def _is_complete_model(path: Path) -> bool:
        return path.is_dir() and (path / "model.bin").is_file()

    @staticmethod
    def _model_name(directory_name: str) -> str:
        if directory_name.startswith(HUGGING_FACE_CACHE_PREFIX):
            return directory_name.removeprefix(HUGGING_FACE_CACHE_PREFIX)
        if directory_name.startswith(DIRECT_MODEL_PREFIX):
            return directory_name.removeprefix(DIRECT_MODEL_PREFIX)
        return directory_name
