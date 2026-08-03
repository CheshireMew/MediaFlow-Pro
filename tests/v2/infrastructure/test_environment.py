from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from mediaflow.environment import load_project_environment, read_environment_file
from mediaflow.infrastructure.chromium_runtime import discover_chromium_executable
from mediaflow.infrastructure.storage_paths import default_media_root, default_project_root

ROOT = Path(__file__).resolve().parents[3]


def test_environment_file_is_strict_and_never_overrides_the_parent_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "machine.env"
    source.write_text(
        "\n".join(
            [
                "# machine-local values",
                "MEDIAFLOW_ENV_TEST_FROM_FILE='value with spaces'",
                "MEDIAFLOW_ENV_TEST_PARENT=file-value",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("MEDIAFLOW_ENV_TEST_FROM_FILE", raising=False)
    monkeypatch.setenv("MEDIAFLOW_ENV_TEST_PARENT", "parent-value")

    assert load_project_environment(source) == source.resolve()
    assert os.environ["MEDIAFLOW_ENV_TEST_FROM_FILE"] == "value with spaces"
    assert os.environ["MEDIAFLOW_ENV_TEST_PARENT"] == "parent-value"


def test_environment_file_rejects_duplicate_or_malformed_entries(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.env"
    duplicate.write_text("MEDIAFLOW_DUP=one\nMEDIAFLOW_DUP=two\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate environment variable"):
        read_environment_file(duplicate)

    malformed = tmp_path / "malformed.env"
    malformed.write_text("not an assignment\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid environment entry"):
        read_environment_file(malformed)


def test_storage_roots_have_no_machine_specific_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MEDIAFLOW_PROJECT_ROOT")
    monkeypatch.delenv("MEDIAFLOW_MEDIA_ROOT")

    with pytest.raises(RuntimeError, match="MEDIAFLOW_PROJECT_ROOT"):
        default_project_root()
    with pytest.raises(RuntimeError, match="MEDIAFLOW_MEDIA_ROOT"):
        default_media_root()


def test_chromium_override_is_owned_by_the_environment_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "browser" / "chrome.exe"
    executable.parent.mkdir()
    executable.write_bytes(b"reviewed-browser")
    monkeypatch.setenv("MEDIAFLOW_CHROMIUM", str(executable))

    assert discover_chromium_executable() == executable.resolve()

    monkeypatch.setenv("MEDIAFLOW_CHROMIUM", str(tmp_path / "missing.exe"))
    assert discover_chromium_executable() is None


def test_machine_paths_are_owned_by_the_environment_contract() -> None:
    formal_sources = [
        ROOT / "README.md",
        ROOT / "ARCHITECTURE.md",
        ROOT / "AGENTS.md",
        ROOT / "启动 MediaFlow Pro.bat",
        *sorted((ROOT / "mediaflow").rglob("*.py")),
        *sorted((ROOT / "scripts").glob("*.py")),
        *sorted((ROOT / "scripts").glob("*.ps1")),
    ]
    machine_path = re.compile(r"(?i)(?<![A-Z0-9_])(?!I:\\s)[A-Z]:[\\/]")
    offenders = {
        str(path.relative_to(ROOT)): sorted(set(machine_path.findall(path.read_text(encoding="utf-8"))))
        for path in formal_sources
        if machine_path.search(path.read_text(encoding="utf-8"))
    }
    assert offenders == {}
    assert "MEDIAFLOW_APP_ROOT" not in "\n".join(
        path.read_text(encoding="utf-8") for path in formal_sources
    )
    assert (ROOT / ".env.example").is_file()
    assert ".env" in (ROOT / ".gitignore").read_text(encoding="utf-8")
