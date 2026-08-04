from __future__ import annotations

import os
import re
from pathlib import Path

ENV_FILE_VARIABLE = "MEDIAFLOW_ENV_FILE"
DEVELOPMENT_ROOT_VARIABLE = "MEDIAFLOW_DEV_ROOT"
PROJECT_ROOT_VARIABLE = "MEDIAFLOW_PROJECT_ROOT"
MEDIA_ROOT_VARIABLE = "MEDIAFLOW_MEDIA_ROOT"
RUNTIME_DIRECTORY_VARIABLE = "MEDIAFLOW_RUNTIME_DIR"
TEST_ROOT_VARIABLE = "MEDIAFLOW_TEST_ROOT"
TEST_FIXTURE_ROOT_VARIABLE = "MEDIAFLOW_TEST_FIXTURE_ROOT"
SERVICE_STATE_DIRECTORY_VARIABLE = "MEDIAFLOW_SERVICE_STATE_DIR"
SERVICE_SETTINGS_PATH_VARIABLE = "MEDIAFLOW_SERVICE_SETTINGS_PATH"
DESKTOP_SETTINGS_PATH_VARIABLE = "MEDIAFLOW_DESKTOP_SETTINGS_PATH"

_ASSIGNMENT = re.compile(r"^[A-Z][A-Z0-9_]*$")


def repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def environment_file_path() -> tuple[Path, bool]:
    configured = os.environ.get(ENV_FILE_VARIABLE, "").strip()
    if configured:
        return Path(configured).expanduser().resolve(), True
    return (repository_root() / ".env").resolve(), False


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def read_environment_file(path: str | Path) -> dict[str, str]:
    source = Path(path).expanduser().resolve()
    values: dict[str, str] = {}
    for line_number, original in enumerate(
        source.read_text(encoding="utf-8-sig").splitlines(),
        start=1,
    ):
        line = original.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, raw_value = line.partition("=")
        name = name.strip()
        if not separator or not _ASSIGNMENT.fullmatch(name):
            raise ValueError(f"Invalid environment entry at {source}:{line_number}")
        if name in values:
            raise ValueError(f"Duplicate environment variable {name} at {source}:{line_number}")
        values[name] = _unquote(raw_value.strip())
    return values


def load_project_environment(path: str | Path | None = None) -> Path | None:
    if path is None:
        selected, explicit = environment_file_path()
    else:
        selected = Path(path).expanduser().resolve()
        explicit = True
    if not explicit and not (repository_root() / ".env.example").is_file():
        return None
    if not selected.is_file():
        if explicit:
            raise FileNotFoundError(f"MediaFlow environment file was not found: {selected}")
        return None
    for name, value in read_environment_file(selected).items():
        os.environ.setdefault(name, value)
    return selected


def configured_path(variable: str) -> Path | None:
    value = os.environ.get(variable, "").strip()
    return Path(value).expanduser().resolve() if value else None


def required_path(variable: str, purpose: str) -> Path:
    configured = configured_path(variable)
    if configured is None:
        raise RuntimeError(
            f"{variable} is required for {purpose}. Copy .env.example to .env "
            "and configure this machine before starting MediaFlow Pro."
        )
    return configured


def development_root(*, required: bool = True) -> Path | None:
    configured = configured_path(DEVELOPMENT_ROOT_VARIABLE)
    if configured is None and required:
        return required_path(DEVELOPMENT_ROOT_VARIABLE, "development tools and artifacts")
    return configured


def test_run_root() -> Path:
    configured = configured_path(TEST_ROOT_VARIABLE)
    if configured is not None:
        return configured
    root = development_root()
    if root is None:
        raise AssertionError("Required development root resolution returned no path")
    return (root / "test-runs").resolve()


def test_fixture_root() -> Path:
    configured = configured_path(TEST_FIXTURE_ROOT_VARIABLE)
    if configured is not None:
        return configured
    root = development_root()
    if root is None:
        raise AssertionError("Required development root resolution returned no path")
    return (root / "test-fixtures").resolve()
