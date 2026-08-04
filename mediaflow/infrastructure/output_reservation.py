from __future__ import annotations

import hashlib
import os
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

from mediaflow.atomic_file import unique_temporary_sibling
from mediaflow.domain.product_identity import PRODUCT_NAME
from mediaflow.domain.storage_names import (
    OUTPUT_WORKSPACE_COMPONENT_RESERVE_UTF16_UNITS,
    content_addressed_child_path,
    require_windows_interop_path,
    safe_path_component,
)

from .project_lock import ProcessFileLock
from .runtime_paths import runtime_directory

_ROLLBACK_ATTEMPTS = 3
_FAILED_EXPORT_DIRECTORY_NAME = f"{PRODUCT_NAME} Failed Exports"
_MAXIMUM_TEMPORARY_LABEL = "workspace-max"
_PYTHON_LONG_PATH_UTF16_LIMIT = 32_000


def require_output_transaction_path(
    destination: str | Path,
    *,
    failure_archive_directory_name: str = _FAILED_EXPORT_DIRECTORY_NAME,
) -> Path:
    """Validate every path shape used by one atomic output transaction."""

    archive_directory_name = _require_archive_directory_name(
        failure_archive_directory_name
    )
    output = require_windows_interop_path(
        destination,
        required_sibling_component_utf16_units=(OUTPUT_WORKSPACE_COMPONENT_RESERVE_UTF16_UNITS),
    )
    require_windows_interop_path(
        unique_temporary_sibling(
            output,
            label=_MAXIMUM_TEMPORARY_LABEL,
        )
    )
    archive_root = output.parent / archive_directory_name
    require_windows_interop_path(
        unique_temporary_sibling(
            archive_root / output.name,
            label=_MAXIMUM_TEMPORARY_LABEL,
        )
    )
    return output


def require_python_output_transaction_path(
    destination: str | Path,
    *,
    failure_archive_directory_name: str = (
        _FAILED_EXPORT_DIRECTORY_NAME
    ),
) -> Path:
    """Validate a Python-written output and its short atomic workspace."""

    archive_directory_name = _require_archive_directory_name(
        failure_archive_directory_name
    )
    output = require_windows_interop_path(
        destination,
        max_path_utf16_units=_PYTHON_LONG_PATH_UTF16_LIMIT,
    )
    require_windows_interop_path(
        unique_temporary_sibling(
            output,
            label=_MAXIMUM_TEMPORARY_LABEL,
        )
    )
    archive_root = output.parent / archive_directory_name
    require_windows_interop_path(
        unique_temporary_sibling(
            archive_root / output.name,
            label=_MAXIMUM_TEMPORARY_LABEL,
        )
    )
    return output


@contextmanager
def reserve_outputs(
    destinations: Iterable[str | Path],
    *,
    runtime_dir: str | Path | None = None,
) -> Iterator[None]:
    """Exclusively reserve a complete output set across processes."""
    outputs_by_key: dict[str, Path] = {}
    for destination in destinations:
        output = require_output_transaction_path(destination)
        outputs_by_key.setdefault(_output_key(output), output)
    if not outputs_by_key:
        raise ValueError("At least one output destination must be reserved")
    with _reserve_resolved_outputs(
        outputs_by_key,
        runtime_dir=runtime_dir,
    ):
        yield


@contextmanager
def _reserve_resolved_outputs(
    outputs_by_key: dict[str, Path],
    *,
    runtime_dir: str | Path | None = None,
) -> Iterator[None]:
    root = Path(runtime_dir).expanduser().resolve() if runtime_dir is not None else runtime_directory()
    lock_root = root / "cache" / "output-locks"
    lock_root.mkdir(parents=True, exist_ok=True)
    locks: list[ProcessFileLock] = []
    try:
        for output_key, output in sorted(outputs_by_key.items()):
            lock_key = hashlib.sha256(output_key.encode("utf-8")).hexdigest()
            lock_path = lock_root / f"{lock_key}.lock"
            lock = ProcessFileLock(lock_path)
            if not lock.acquire():
                raise RuntimeError(
                    f"Another export is already writing destination: {output}"
                )
            locks.append(lock)
        yield
    finally:
        for lock in reversed(locks):
            lock.release()


@contextmanager
def reserve_output(
    destination: str | Path,
    *,
    runtime_dir: str | Path | None = None,
) -> Iterator[None]:
    """Exclusively reserve one final output path across processes."""
    with reserve_outputs((destination,), runtime_dir=runtime_dir):
        yield


@contextmanager
def reserve_python_output(
    destination: str | Path,
    *,
    runtime_dir: str | Path | None = None,
) -> Iterator[None]:
    """Reserve one long-path-capable output written only by Python."""

    output = require_python_output_transaction_path(destination)
    with _reserve_resolved_outputs(
        {_output_key(output): output},
        runtime_dir=runtime_dir,
    ):
        yield


class OutputSetTransaction:
    def __init__(
        self,
        destinations: Iterable[str | Path],
        *,
        overwrite: bool,
        failure_archive_directory_name: str = (
            _FAILED_EXPORT_DIRECTORY_NAME
        ),
    ) -> None:
        self._failure_archive_directory_name = (
            _require_archive_directory_name(
                failure_archive_directory_name
            )
        )
        self._destinations_by_key: dict[str, Path] = {}
        for destination in destinations:
            output = require_output_transaction_path(
                destination,
                failure_archive_directory_name=(
                    self._failure_archive_directory_name
                ),
            )
            key = _output_key(output)
            if key in self._destinations_by_key:
                raise ValueError(f"Output set contains a duplicate destination: {output}")
            self._destinations_by_key[key] = output
        if not self._destinations_by_key:
            raise ValueError("At least one output destination must be committed")
        self.destinations = tuple(self._destinations_by_key.values())
        self.overwrite = overwrite
        self._staged: dict[str, Path] = {}
        self._archived: list[Path] = []
        self._backups: dict[str, Path] = {}
        self._published_keys: list[str] = []
        self._replaced_output_archives: dict[Path, Path] = {}
        self._publication_open = False
        self._committed = False

    @property
    def archived_outputs(self) -> tuple[Path, ...]:
        return tuple(self._archived)

    @property
    def replaced_output_archives(self) -> dict[Path, Path]:
        return dict(self._replaced_output_archives)

    def check_conflicts(self) -> None:
        if self.overwrite:
            return
        existing = [destination for destination in self.destinations if destination.exists()]
        if existing:
            joined = ", ".join(str(path) for path in existing)
            raise FileExistsError(f"Export destination already exists: {joined}")

    def temporary_path(
        self,
        destination: str | Path,
        label: str,
    ) -> Path:
        output = self._destination(destination)
        key = _output_key(output)
        if key in self._staged:
            raise RuntimeError(f"Output already has staged content: {output}")
        temporary = temporary_output_path(output, label)
        self._staged[key] = temporary
        return temporary

    def archive_staged(
        self,
        destination: str | Path,
    ) -> Path | None:
        output = self._destination(destination)
        temporary = self._staged.pop(_output_key(output), None)
        if temporary is None:
            return None
        return self._archive(temporary, output)

    def archive_all_staged(self) -> tuple[Path, ...]:
        for key, temporary in tuple(self._staged.items()):
            self._staged.pop(key, None)
            self._archive(
                temporary,
                self._destinations_by_key[key],
            )
        return self.archived_outputs

    def commit(self) -> None:
        self.publish()
        self.finalize()

    def publish(self) -> None:
        """Install the staged set while retaining every previous output."""

        if self._committed:
            raise RuntimeError("Output set was already committed")
        if self._publication_open:
            raise RuntimeError("Output set was already published")
        self.check_conflicts()
        missing = [
            destination for key, destination in self._destinations_by_key.items() if key not in self._staged
        ]
        if missing:
            raise RuntimeError(
                "Output set is incomplete; no content was staged for: "
                + ", ".join(str(path) for path in missing)
            )
        invalid = [
            temporary
            for temporary in self._staged.values()
            if not temporary.is_file() or temporary.stat().st_size == 0
        ]
        if invalid:
            raise RuntimeError(
                "Output set contains missing or empty staged files: "
                + ", ".join(str(path) for path in invalid)
            )

        backups: dict[str, Path] = {}
        published: list[str] = []
        try:
            for key, destination in self._destinations_by_key.items():
                if destination.exists():
                    backup = temporary_output_path(
                        destination,
                        "previous",
                    )
                    destination.replace(backup)
                    backups[key] = backup
            for key, destination in self._destinations_by_key.items():
                temporary = self._staged[key]
                temporary.replace(destination)
                self._staged.pop(key)
                published.append(key)
        except BaseException as error:
            rollback_errors = self._rollback_commit(
                backups,
                published,
            )
            self.archive_all_staged()
            if rollback_errors:
                raise RuntimeError(
                    "Output set commit failed and its previous files "
                    "could not be fully restored: " + "; ".join(rollback_errors)
                ) from error
            raise
        self._backups = backups
        self._published_keys = published
        self._publication_open = True

    def finalize(
        self,
        *,
        archive_replaced_to: str | Path | None = None,
    ) -> tuple[Path, ...]:
        """Accept a published set after its external commit succeeds."""

        if not self._publication_open:
            raise RuntimeError("Output set has not been published")
        archived: list[Path] = []
        if archive_replaced_to is None:
            for backup in self._backups.values():
                try:
                    backup.unlink(missing_ok=True)
                except OSError:
                    continue
        else:
            archive_directory = Path(archive_replaced_to).expanduser().resolve()
            for key, backup in self._backups.items():
                destination = self._destinations_by_key[key]
                archived_path = self._archive_replaced(
                    backup,
                    archive_directory,
                    destination,
                )
                archived.append(archived_path)
                self._replaced_output_archives[destination] = archived_path
            self._archived.extend(archived)
        self._backups = {}
        self._published_keys = []
        self._publication_open = False
        self._committed = True
        return tuple(archived)

    def rollback_published(self) -> None:
        """Withdraw the new set and restore every retained previous output."""

        if not self._publication_open:
            raise RuntimeError("Output set has not been published")
        rollback_errors = self._rollback_commit(
            self._backups,
            self._published_keys,
        )
        self._backups = {}
        self._published_keys = []
        self._publication_open = False
        if rollback_errors:
            raise RuntimeError(
                "Published output set could not be fully rolled back: " + "; ".join(rollback_errors)
            )

    def _rollback_commit(
        self,
        backups: dict[str, Path],
        published: list[str],
    ) -> list[str]:
        rollback_errors: list[str] = []
        rolled_back_new: list[tuple[Path, Path]] = []
        for key in reversed(published):
            destination = self._destinations_by_key[key]
            rollback_path = temporary_output_path(
                destination,
                "rollback",
            )
            error = self._rollback_replace(
                destination,
                rollback_path,
            )
            if error is None:
                rolled_back_new.append((rollback_path, destination))
            else:
                rollback_errors.append(f"could not remove new output {destination}: {error}")
        for key, backup in reversed(tuple(backups.items())):
            destination = self._destinations_by_key[key]
            error = self._rollback_replace(
                backup,
                destination,
            )
            if error is not None:
                rollback_errors.append(f"could not restore {destination}: {error}")
        for temporary, destination in rolled_back_new:
            self._archive(temporary, destination)
        return rollback_errors

    @staticmethod
    def _rollback_replace(
        source: Path,
        destination: Path,
    ) -> OSError | None:
        failure: OSError | None = None
        for _attempt in range(_ROLLBACK_ATTEMPTS):
            try:
                source.replace(destination)
                return None
            except OSError as error:
                failure = error
        return failure

    def _destination(self, destination: str | Path) -> Path:
        output = Path(destination).expanduser().resolve()
        key = _output_key(output)
        try:
            return self._destinations_by_key[key]
        except KeyError as error:
            raise ValueError(f"Path is not part of this output set: {output}") from error

    def _archive(
        self,
        temporary: Path,
        destination: Path,
    ) -> Path | None:
        archived = archive_failed_output(
            temporary,
            destination,
            archive_directory_name=(
                self._failure_archive_directory_name
            ),
        )
        if archived is not None:
            self._archived.append(archived)
        return archived

    @staticmethod
    def _archive_replaced(
        backup: Path,
        archive_directory: Path,
        destination: Path,
    ) -> Path:
        try:
            archived = content_addressed_child_path(
                archive_directory,
                (f"replaced-output:{destination}:{backup.name}"),
                namespace="replaced",
                suffix=destination.suffix,
            )
        except (OSError, ValueError):
            return backup
        for _attempt in range(_ROLLBACK_ATTEMPTS):
            try:
                archived.parent.mkdir(parents=True, exist_ok=True)
                backup.replace(archived)
                return archived
            except OSError:
                if not backup.exists():
                    return archived if archived.exists() else backup
        return backup


@contextmanager
def output_set_transaction(
    destinations: Iterable[str | Path],
    *,
    overwrite: bool,
    runtime_dir: str | Path | None = None,
    failure_archive_directory_name: str = (
        _FAILED_EXPORT_DIRECTORY_NAME
    ),
) -> Iterator[OutputSetTransaction]:
    transaction = OutputSetTransaction(
        destinations,
        overwrite=overwrite,
        failure_archive_directory_name=(
            failure_archive_directory_name
        ),
    )
    with reserve_outputs(
        transaction.destinations,
        runtime_dir=runtime_dir,
    ):
        transaction.check_conflicts()
        try:
            yield transaction
        except BaseException as error:
            if transaction._publication_open:
                try:
                    transaction.rollback_published()
                except BaseException as rollback_error:
                    error.add_note(f"Published output rollback failed: {rollback_error}")
            transaction.archive_all_staged()
            raise
        if not transaction._committed:
            if transaction._publication_open:
                transaction.rollback_published()
            transaction.archive_all_staged()
            raise RuntimeError("Output set transaction ended without a commit")


def _output_key(output: Path) -> str:
    return os.path.normcase(str(output))


def temporary_output_path(destination: str | Path, label: str) -> Path:
    output = Path(destination).expanduser().resolve()
    temporary = unique_temporary_sibling(output, label=label)
    return require_windows_interop_path(temporary)


def archive_failed_output(
    temporary: str | Path,
    destination: str | Path,
    *,
    archive_directory_name: str = _FAILED_EXPORT_DIRECTORY_NAME,
) -> Path | None:
    partial = Path(temporary).resolve()
    output = Path(destination).resolve()
    if not partial.exists():
        return None
    archive_dir = output.parent / _require_archive_directory_name(
        archive_directory_name
    )
    archived = archive_dir / partial.name.lstrip(".")
    for _attempt in range(_ROLLBACK_ATTEMPTS):
        try:
            archive_dir.mkdir(parents=True, exist_ok=True)
            partial.replace(archived)
            return archived
        except OSError:
            if not partial.exists():
                return archived if archived.exists() else None
    return partial if partial.exists() else None


def archive_published_outputs(
    destinations: Iterable[str | Path],
    *,
    runtime_dir: str | Path | None = None,
    archive_directory_name: str = _FAILED_EXPORT_DIRECTORY_NAME,
) -> tuple[Path, ...]:
    """Withdraw a published output set without deleting any generated file."""

    archive_name = _require_archive_directory_name(
        archive_directory_name
    )
    outputs_by_key: dict[str, Path] = {}
    for destination in destinations:
        output = require_output_transaction_path(destination)
        outputs_by_key.setdefault(_output_key(output), output)
    if not outputs_by_key:
        return ()
    outputs = tuple(outputs_by_key.values())
    archive_targets: dict[str, Path] = {}
    for output in outputs:
        archive_root = output.parent / archive_name
        archive_target = require_windows_interop_path(
            unique_temporary_sibling(
                archive_root / output.name,
                label="unrecorded",
            )
        )
        archive_targets[_output_key(output)] = archive_target

    moved: list[tuple[Path, Path]] = []
    with reserve_outputs(outputs, runtime_dir=runtime_dir):
        try:
            for output in outputs:
                if not output.exists():
                    continue
                if not output.is_file():
                    raise RuntimeError(f"Published export is not a file: {output}")
                archive_target = archive_targets[_output_key(output)]
                archive_target.parent.mkdir(
                    parents=True,
                    exist_ok=True,
                )
                output.replace(archive_target)
                moved.append((archive_target, output))
        except BaseException as error:
            rollback_errors: list[str] = []
            for archive_target, output in reversed(moved):
                rollback_error = OutputSetTransaction._rollback_replace(
                    archive_target,
                    output,
                )
                if rollback_error is not None:
                    rollback_errors.append(f"could not restore {output}: {rollback_error}")
            if rollback_errors:
                error.add_note(
                    "Published export withdrawal rollback was incomplete: " + "; ".join(rollback_errors)
                )
            raise
    return tuple(archive_target for archive_target, _output in moved)


def _require_archive_directory_name(value: str) -> str:
    name = str(value).strip()
    if (
        not name
        or name in {".", ".."}
        or Path(name).name != name
        or Path(name).is_absolute()
        or safe_path_component(name) != name
    ):
        raise ValueError(
            "Failure archive directory must be one relative path component"
        )
    return name
