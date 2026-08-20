from __future__ import annotations

import json
from pathlib import Path

import pytest

from mediaflow.desktop import runtime_directory_management as runtime_directories


class _MemorySettings:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}

    def value(self, key: str, default: object = "") -> object:
        return self.values.get(key, default)

    def setValue(self, key: str, value: object) -> None:
        self.values[key] = value

    def remove(self, key: str) -> None:
        self.values.pop(key, None)

    def sync(self) -> None:
        pass


class _CompletingProgress:
    def __init__(self, *_args, **_kwargs) -> None:
        self.values: list[int] = []

    def setWindowTitle(self, _value: str) -> None:
        pass

    def setMinimumDuration(self, _value: int) -> None:
        pass

    def wasCanceled(self) -> bool:
        return False

    def setLabelText(self, _value: str) -> None:
        pass

    def setValue(self, value: int) -> None:
        self.values.append(value)


def test_runtime_directory_destination_rejects_unsafe_migration_layouts(tmp_path: Path) -> None:
    current = tmp_path / "current"
    current.mkdir()

    with pytest.raises(ValueError, match="当前目录相同"):
        runtime_directories.validate_runtime_change_destination(
            current,
            current,
            migrate_existing=True,
        )
    with pytest.raises(ValueError, match="不能互相包含"):
        runtime_directories.validate_runtime_change_destination(
            current,
            current / "nested",
            migrate_existing=True,
        )

    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "unrelated.bin").write_bytes(b"occupied")
    with pytest.raises(ValueError, match="必须是空文件夹"):
        runtime_directories.validate_runtime_change_destination(
            current,
            occupied,
            migrate_existing=True,
        )

    assert (
        runtime_directories.validate_runtime_change_destination(
            current,
            occupied,
            migrate_existing=False,
        )
        == occupied.resolve()
    )
    with pytest.raises(ValueError, match="不是完整的 MediaFlow Pro 运行环境"):
        runtime_directories.validate_existing_runtime_directory(occupied)


def test_pending_runtime_migration_copies_and_verifies_without_deleting_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _MemorySettings()
    monkeypatch.setattr(runtime_directories, "_bootstrap_settings", lambda: settings)
    monkeypatch.setattr(runtime_directories, "QProgressDialog", _CompletingProgress)
    monkeypatch.setattr(
        runtime_directories,
        "validate_existing_runtime_directory",
        lambda path: path.resolve(),
    )
    messages: list[str] = []
    monkeypatch.setattr(
        runtime_directories.QMessageBox,
        "information",
        lambda _parent, _title, message: messages.append(message),
    )
    monkeypatch.setattr(
        runtime_directories.QMessageBox,
        "critical",
        lambda *_args: pytest.fail("successful migration must not show an error"),
    )

    source = tmp_path / "runtime-old"
    nested = source / "models" / "speech"
    nested.mkdir(parents=True)
    source_file = nested / "model.bin"
    source_file.write_bytes(b"verified runtime payload")
    destination = tmp_path / "runtime-new"

    runtime_directories.set_saved_runtime_directory(source)
    runtime_directories.schedule_runtime_directory_change(destination, migrate_existing=True)

    assert runtime_directories.apply_pending_runtime_directory_change() == destination.resolve()
    assert source_file.read_bytes() == b"verified runtime payload"
    assert (destination / "models" / "speech" / "model.bin").read_bytes() == source_file.read_bytes()
    marker = json.loads((destination / runtime_directories.MIGRATION_MARKER).read_text("utf-8"))
    assert marker["status"] == "complete"
    assert marker["fileCount"] == 1
    assert runtime_directories.saved_runtime_directory() == destination.resolve()
    assert runtime_directories.pending_runtime_directory_change() is None
    assert messages and "旧目录没有删除" in messages[-1]


def test_orphaned_pending_runtime_change_is_cleared_before_first_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _MemorySettings()
    monkeypatch.setattr(runtime_directories, "_bootstrap_settings", lambda: settings)
    runtime_directories.schedule_runtime_directory_change(
        tmp_path / "stale-target",
        migrate_existing=True,
    )

    assert runtime_directories.apply_pending_runtime_directory_change() is None
    assert runtime_directories.pending_runtime_directory_change() is None
