from __future__ import annotations

import json
import logging
import threading
from collections.abc import Iterator
from contextlib import AbstractContextManager, ExitStack, contextmanager
from pathlib import Path
from typing import Any

from mediaflow.atomic_file import atomic_write_text
from mediaflow.domain.enums import TaskStatus
from mediaflow.domain.storage_names import python_io_path
from mediaflow.domain.task_commands import (
    BuildSequenceCommand,
    ExportFcpxmlCommand,
    ExportHighlightsCommand,
    ExportSequenceCommand,
    ExportWebClipCommand,
)
from mediaflow.domain.tasks import Task
from mediaflow.file_digest import sha256_file
from mediaflow.infrastructure.output_reservation import (
    OutputSetTransaction,
    PreparedOutput,
    archive_failed_output,
    publish_python_output,
    reserve_outputs,
    task_output_owner,
)
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.task_repository import TaskRepository

logger = logging.getLogger(__name__)


class TaskOutputJournal:
    """Retain output locks and old files until the task's SQLite commit."""

    def __init__(self, task: Task, root: Path):
        self.task = task
        self.receipt = root / f"{task.id}.json"
        self.reservations = ExitStack()
        self.entries: list[dict[str, Any]] = []
        self.transactions: list[tuple[OutputSetTransaction, str | Path | None]] = []
        self.closed = False

    def owns(self, destinations: tuple[Path, ...]) -> bool:
        command = self.task.command
        if isinstance(command, ExportHighlightsCommand):
            return all(path.parent == Path(command.output_dir).resolve() for path in destinations)
        if not isinstance(
            command,
            (
                ExportSequenceCommand,
                BuildSequenceCommand,
                ExportFcpxmlCommand,
                ExportWebClipCommand,
            ),
        ):
            return False
        output = Path(command.output_path).resolve()
        if isinstance(command, ExportFcpxmlCommand):
            output = output.with_suffix(".fcpxml")
        return output in destinations

    def reserve(self, reservation: AbstractContextManager[None]) -> None:
        self.reservations.enter_context(reservation)

    def prepare(self, outputs: tuple[PreparedOutput, ...]) -> None:
        # The receipt precedes the first rename, so recovery also covers a crash
        # halfway through publishing a multi-file export.
        for output in outputs:
            self.entries.append(
                {
                    "destination": str(output.destination),
                    "staged": str(output.staged),
                    "backup": str(output.backup) if output.backup is not None else None,
                    "sha256": sha256_file(output.staged),
                    "previous_sha256": (
                        sha256_file(output.destination)
                        if python_io_path(output.destination).is_file()
                        else None
                    ),
                }
            )
        self._save()

    def defer_finalization(
        self,
        transaction: OutputSetTransaction,
        archive_replaced_to: str | Path | None,
    ) -> None:
        self.transactions.append((transaction, archive_replaced_to))

    def _save(self) -> None:
        atomic_write_text(
            self.receipt,
            json.dumps(
                {
                    "version": 1,
                    "task_id": self.task.id,
                    "command": self.task.command.model_dump(mode="json"),
                    "outputs": self.entries,
                },
                ensure_ascii=False,
            ),
        )

    def commit(self) -> None:
        if self.closed:
            return
        try:
            for transaction, archive in self.transactions:
                transaction.accept_deferred_publication(archive_replaced_to=archive)
            self.receipt.unlink(missing_ok=True)
            self.closed = True
        finally:
            self.reservations.close()

    def rollback(self) -> None:
        if self.closed:
            return
        try:
            for transaction, _archive in reversed(self.transactions):
                transaction.rollback_deferred_publication()
            # A transaction that failed during publish has already restored its
            # files. Only staged remnants remain in that case.
            for entry in self.entries:
                archive_failed_output(entry["staged"], entry["destination"])
            self.receipt.unlink(missing_ok=True)
            self.closed = True
        finally:
            self.reservations.close()


class TaskOutputCoordinator:
    def __init__(self, repository: ProjectRepository):
        self.repository = repository
        self.root = repository.project_dir / "generated" / "task-output-receipts"
        self._journals: dict[str, TaskOutputJournal] = {}
        self._lock = threading.Lock()
        if not repository.read_only:
            self._recover()

    @contextmanager
    def preparation(self, task: Task) -> Iterator[None]:
        journal = TaskOutputJournal(task, self.root)
        try:
            with task_output_owner(journal):
                yield
        except BaseException:
            journal.rollback()
            raise
        else:
            with self._lock:
                self._journals[task.id] = journal

    def enlist(self, task: Task) -> None:
        with self._lock:
            journal = self._journals.pop(task.id, None)
        if journal is None:
            return
        self.repository.enlist_transaction_publication(
            on_commit=journal.commit if task.status == TaskStatus.COMPLETED else journal.rollback,
            on_rollback=lambda _error: journal.rollback(),
        )

    def discard(self, task_id: str) -> None:
        with self._lock:
            journal = self._journals.pop(task_id, None)
        if journal is not None:
            journal.rollback()

    def _recover(self) -> None:
        for receipt in self.root.glob("*.json"):
            payload = json.loads(receipt.read_text(encoding="utf-8"))
            if payload.get("version") != 1:
                raise RuntimeError(f"Unsupported task output receipt: {receipt}")
            if payload.get("recovery_conflict"):
                continue
            task = TaskRepository(self.repository).get(payload["task_id"])
            if payload["command"] != task.command.model_dump(mode="json"):
                raise RuntimeError(f"Task output receipt does not match its command: {receipt}")
            entries = payload["outputs"]
            python_only = isinstance(task.command, ExportFcpxmlCommand)
            with reserve_outputs((entry["destination"] for entry in entries), python_only=python_only):
                conflict = next(
                    (
                        entry
                        for entry in entries
                        if (
                            task.status != TaskStatus.COMPLETED
                            and python_io_path(entry["destination"]).exists()
                            and sha256_file(entry["destination"])
                            not in {
                                entry["sha256"],
                                entry["previous_sha256"],
                            }
                        )
                    ),
                    None,
                )
                if conflict is not None:
                    message = f"恢复任务不会覆盖已更改的文件：{conflict['destination']}"
                    payload["recovery_conflict"] = message
                    atomic_write_text(receipt, json.dumps(payload, ensure_ascii=False))
                    if task.status == TaskStatus.RUNNING and task.execution_owner_id:
                        TaskRepository(self.repository).update_owned(
                            task.model_copy(update={"status": TaskStatus.FAILED, "error": message}),
                            task.execution_owner_id,
                            event_type="failed",
                            release_owner=True,
                        )
                    logger.warning("%s; retained receipt %s", message, receipt)
                    continue
                for entry in reversed(entries):
                    destination, staged = Path(entry["destination"]), Path(entry["staged"])
                    backup = Path(entry["backup"]) if entry["backup"] else None
                    if task.status == TaskStatus.COMPLETED:
                        if backup is not None:
                            backup.unlink(missing_ok=True)
                        continue
                    if not staged.exists() and python_io_path(destination).exists():
                        if sha256_file(destination) == entry["sha256"]:
                            archive_failed_output(destination, destination, python_only=python_only)
                        elif entry["previous_sha256"] is not None:
                            continue
                    if backup is not None and backup.exists():
                        if python_io_path(destination).exists():
                            raise RuntimeError(f"恢复任务不会覆盖已有文件：{destination}")
                        publish_python_output(backup, destination)
                    archive_failed_output(staged, destination)
            receipt.unlink()
            logger.info("Recovered task output publication: %s", task.id)
