from __future__ import annotations

import hashlib
import os
from collections.abc import Iterable, Iterator
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from mediaflow.atomic_file import unique_temporary_sibling
from mediaflow.domain.product_identity import PRODUCT_NAME
from mediaflow.domain.storage_names import (
    OUTPUT_WORKSPACE_COMPONENT_RESERVE_UTF16_UNITS,
    content_addressed_child_path,
    python_io_path,
    require_windows_interop_path,
    safe_path_component,
)

from .project_lock import ProcessFileLock
from .runtime_paths import runtime_directory

_ROLLBACK_ATTEMPTS = 3
_FAILED_EXPORT_DIRECTORY_NAME = f"{PRODUCT_NAME} Failed Exports"
_MAXIMUM_TEMPORARY_LABEL = "workspace-max"
_PYTHON_LONG_PATH_UTF16_LIMIT = 32_000


@dataclass(frozen=True, slots=True)
class PreparedOutput:
    destination: Path
    staged: Path
    backup: Path | None


class OutputTransactionOwner(Protocol):
    def owns(self, destinations: tuple[Path, ...]) -> bool: ...
    def reserve(self, reservation: AbstractContextManager[None]) -> None: ...
    def prepare(self, outputs: tuple[PreparedOutput, ...]) -> None: ...
    def defer_finalization(
        self,
        transaction: OutputSetTransaction,
        archive_replaced_to: str | Path | None,
    ) -> None: ...


_OUTPUT_OWNER: ContextVar[OutputTransactionOwner | None] = ContextVar("output_owner", default=None)


@contextmanager
def task_output_owner(owner: OutputTransactionOwner) -> Iterator[None]:
    token = _OUTPUT_OWNER.set(owner)
    try:
        yield
    finally:
        _OUTPUT_OWNER.reset(token)


def require_output_transaction_path(
    destination: str | Path,
    *,
    failure_archive_directory_name: str = _FAILED_EXPORT_DIRECTORY_NAME,
) -> Path:
    """Validate every path shape used by one atomic output transaction."""

    archive_directory_name = _require_archive_directory_name(failure_archive_directory_name)
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
    failure_archive_directory_name: str = (_FAILED_EXPORT_DIRECTORY_NAME),
) -> Path:
    """Validate a Python-written output and its short atomic workspace."""

    archive_directory_name = _require_archive_directory_name(failure_archive_directory_name)
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
    python_only: bool = False,
) -> Iterator[None]:
    """Exclusively reserve a complete output set across processes."""
    outputs_by_key: dict[str, Path] = {}
    for destination in destinations:
        validate = require_python_output_transaction_path if python_only else require_output_transaction_path
        output = validate(destination)
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
                raise RuntimeError(f"Another export is already writing destination: {output}")
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

    with reserve_outputs((destination,), runtime_dir=runtime_dir, python_only=True):
        yield


class OutputSetTransaction:
    def __init__(
        self,
        destinations: Iterable[str | Path],
        *,
        overwrite: bool,
        python_only: bool = False,
        failure_archive_directory_name: str = (_FAILED_EXPORT_DIRECTORY_NAME),
    ) -> None:
        self._failure_archive_directory_name = _require_archive_directory_name(failure_archive_directory_name)
        self._destinations_by_key: dict[str, Path] = {}
        for destination in destinations:
            validate = (
                require_python_output_transaction_path if python_only else require_output_transaction_path
            )
            output = validate(
                destination,
                failure_archive_directory_name=(self._failure_archive_directory_name),
            )
            key = _output_key(output)
            if key in self._destinations_by_key:
                raise ValueError(f"Output set contains a duplicate destination: {output}")
            self._destinations_by_key[key] = output
        if not self._destinations_by_key:
            raise ValueError("At least one output destination must be committed")
        self.destinations = tuple(self._destinations_by_key.values())
        self.overwrite = overwrite
        self._python_only = python_only
        self._staged: dict[str, Path] = {}
        self._archived: list[Path] = []
        self._backups: dict[str, Path] = {}
        self._published_keys: list[str] = []
        self._replaced_output_archives: dict[Path, Path] = {}
        self._publication_open = False
        self._committed = False
        self._deferred = False
        owner = _OUTPUT_OWNER.get()
        self._owner = owner if owner is not None and owner.owns(self.destinations) else None

    @property
    def archived_outputs(self) -> tuple[Path, ...]:
        return tuple(self._archived)

    @property
    def replaced_output_archives(self) -> dict[Path, Path]:
        return dict(self._replaced_output_archives)

    def check_conflicts(self) -> None:
        if self.overwrite:
            return
        existing = [destination for destination in self.destinations if self._io_path(destination).exists()]
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

        backups = {
            key: temporary_output_path(destination, "previous")
            for key, destination in self._destinations_by_key.items()
            if self._io_path(destination).exists()
        }
        if self._owner is not None:
            self._owner.prepare(
                tuple(
                    PreparedOutput(destination, self._staged[key], backups.get(key))
                    for key, destination in self._destinations_by_key.items()
                )
            )
        published: list[str] = []
        moved_backups: dict[str, Path] = {}
        try:
            for key, destination in self._destinations_by_key.items():
                if key in backups:
                    backup = backups[key]
                    self._replace_output(destination, backup)
                    moved_backups[key] = backup
            for key, destination in self._destinations_by_key.items():
                temporary = self._staged[key]
                self._replace_output(temporary, destination)
                self._staged.pop(key)
                published.append(key)
        except BaseException as error:
            rollback_errors = self._rollback_commit(
                moved_backups,
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
        if self._owner is not None:
            self._owner.defer_finalization(self, archive_replaced_to)
            self._deferred = True
            return ()
        return self._finalize_publication(archive_replaced_to)

    def accept_deferred_publication(
        self,
        *,
        archive_replaced_to: str | Path | None = None,
    ) -> tuple[Path, ...]:
        """Let the enlisted owner confirm publication after its database commit."""

        if not self._deferred or not self._publication_open:
            raise RuntimeError("Output set has no deferred publication to accept")
        return self._finalize_publication(archive_replaced_to)

    def rollback_deferred_publication(self) -> None:
        """Withdraw an enlisted publication, including one already withdrawn by its scope."""

        if self._deferred and self._publication_open:
            self.rollback_published()

    def _finalize_publication(self, archive_replaced_to: str | Path | None) -> tuple[Path, ...]:
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
        self._deferred = False
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
        self._deferred = False
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
                python_only=self._python_only,
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
                python_only=self._python_only,
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
        *,
        python_only: bool = False,
    ) -> OSError | None:
        failure: OSError | None = None
        for _attempt in range(_ROLLBACK_ATTEMPTS):
            try:
                if python_only:
                    publish_python_output(source, destination)
                else:
                    source.replace(destination)
                return None
            except OSError as error:
                failure = error
        return failure

    def _io_path(self, path: Path) -> Path:
        return python_io_path(path) if self._python_only else path

    def _replace_output(self, source: Path, destination: Path) -> None:
        if self._python_only:
            publish_python_output(source, destination)
        else:
            source.replace(destination)

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
            archive_directory_name=(self._failure_archive_directory_name),
            python_only=self._python_only,
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
    python_only: bool = False,
    runtime_dir: str | Path | None = None,
    failure_archive_directory_name: str = (_FAILED_EXPORT_DIRECTORY_NAME),
) -> Iterator[OutputSetTransaction]:
    transaction = OutputSetTransaction(
        destinations,
        overwrite=overwrite,
        python_only=python_only,
        failure_archive_directory_name=(failure_archive_directory_name),
    )
    reservation = _reserve_resolved_outputs(
        transaction._destinations_by_key,
        runtime_dir=runtime_dir,
    )
    owner = transaction._owner
    if owner is not None:
        owner.reserve(reservation)
    with reservation if owner is None else _retained_reservation():
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
        if not transaction._committed and not transaction._deferred:
            if transaction._publication_open:
                transaction.rollback_published()
            transaction.archive_all_staged()
            raise RuntimeError("Output set transaction ended without a commit")


@contextmanager
def _retained_reservation() -> Iterator[None]:
    yield


def _output_key(output: Path) -> str:
    return os.path.normcase(str(output))


def temporary_output_path(destination: str | Path, label: str) -> Path:
    output = Path(destination).expanduser().resolve()
    temporary = unique_temporary_sibling(output, label=label)
    return require_windows_interop_path(temporary)


def publish_python_output(temporary: str | Path, destination: str | Path) -> None:
    """Atomically publish a Python-owned file to a validated long destination."""

    os.replace(python_io_path(temporary), python_io_path(destination))


def archive_failed_output(
    temporary: str | Path,
    destination: str | Path,
    *,
    archive_directory_name: str = _FAILED_EXPORT_DIRECTORY_NAME,
    python_only: bool = False,
) -> Path | None:
    partial = Path(temporary).resolve()
    output = Path(destination).resolve()
    io_partial = python_io_path(partial) if python_only else partial
    if not io_partial.exists():
        return None
    archive_dir = output.parent / _require_archive_directory_name(archive_directory_name)
    archived = archive_dir / partial.name.lstrip(".")
    io_archive_dir = python_io_path(archive_dir) if python_only else archive_dir
    io_archived = python_io_path(archived) if python_only else archived
    for _attempt in range(_ROLLBACK_ATTEMPTS):
        try:
            io_archive_dir.mkdir(parents=True, exist_ok=True)
            if python_only:
                publish_python_output(partial, archived)
            else:
                partial.replace(archived)
            return archived
        except OSError:
            if not io_partial.exists():
                return archived if io_archived.exists() else None
    return partial if io_partial.exists() else None


def archive_published_outputs(
    destinations: Iterable[str | Path],
    *,
    runtime_dir: str | Path | None = None,
    archive_directory_name: str = _FAILED_EXPORT_DIRECTORY_NAME,
) -> tuple[Path, ...]:
    """Withdraw a published output set without deleting any generated file."""

    archive_name = _require_archive_directory_name(archive_directory_name)
    outputs_by_key: dict[str, Path] = {}
    for destination in destinations:
        output = require_output_transaction_path(destination)
        owner = _OUTPUT_OWNER.get()
        if owner is not None and owner.owns((output,)):
            continue
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
        raise ValueError("Failure archive directory must be one relative path component")
    return name
