from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from concurrent.futures import CancelledError
from pathlib import Path

import pytest

from mediaflow.infrastructure import project_cache_budget, storage_inventory
from mediaflow.infrastructure.project_cache_reservations import cache_reservation_ledger_path
from mediaflow.infrastructure.storage_allocation import allocation_unit
from mediaflow.infrastructure.storage_preflight import require_storage_budget
from mediaflow.infrastructure.storage_receipts import storage_receipt_inventory


def test_hard_links_preserve_logical_bytes_but_do_not_duplicate_allocation(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"one physical object")
    before = storage_inventory.directory_inventory(tmp_path)
    (tmp_path / "alias.bin").hardlink_to(source)
    after = storage_inventory.directory_inventory(tmp_path)

    assert after["logical_bytes"] == 2 * source.stat().st_size
    assert after["unique_object_bytes"] == source.stat().st_size
    assert after["allocated_bytes"] == before["allocated_bytes"]
    assert after["files"] == 2
    assert after["unique_objects"] == 1


def test_registered_roots_deduplicate_hard_links_and_overlapping_paths(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    source = first / "source.bin"
    source.write_bytes(b"shared data")
    (second / "alias.bin").hardlink_to(source)
    report = storage_inventory.registered_roots_inventory(
        {"parent": tmp_path, "first": first, "second": second}
    )

    assert report["total"]["files"] == 2
    assert report["total"]["logical_bytes"] == 2 * source.stat().st_size
    assert report["total"]["unique_objects"] == 1
    assert report["total"]["allocated_bytes"] == report["roots"]["first"]["allocated_bytes"]


@pytest.mark.skipif(os.name != "nt", reason="Windows sparse allocation contract")
def test_windows_sparse_file_budget_uses_real_allocation_not_logical_length(tmp_path: Path) -> None:
    import win32file
    import winioctlcon

    path = tmp_path / "sparse.bin"
    path.touch()
    handle = win32file.CreateFile(str(path), 0x40000000, 7, None, 3, 0x80, None)
    try:
        win32file.DeviceIoControl(handle, winioctlcon.FSCTL_SET_SPARSE, None, 0)
    finally:
        handle.Close()
    with path.open("r+b") as stream:
        stream.seek(8 * 1024**2 - 1)
        stream.write(b"x")
    report = require_storage_budget(
        tmp_path,
        expected_new_bytes=1,
        maximum_managed_bytes=1024**2,
        minimum_free_bytes=1,
        label="sparse file capacity",
    )
    assert report["current_logical_bytes"] == 8 * 1024**2
    assert report["current_managed_bytes"] < 1024**2
    assert report["current_managed_bytes"] < report["current_logical_bytes"]


def test_unreadable_subtree_blocks_preflight_instead_of_becoming_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    denied = tmp_path / "denied"
    denied.mkdir()
    (tmp_path / "readable").mkdir()
    (denied / "real.bin").write_bytes(b"must be accounted")
    original = storage_inventory.os.scandir

    def unreadable(path):
        if Path(path).resolve() == denied.resolve():
            raise PermissionError("controlled unreadable subtree")
        return original(path)

    monkeypatch.setattr(storage_inventory.os, "scandir", unreadable)
    with pytest.raises(storage_inventory.StorageInventoryError, match="controlled unreadable subtree"):
        require_storage_budget(
            tmp_path,
            expected_new_bytes=1,
            maximum_managed_bytes=1024**2,
            minimum_free_bytes=1,
            label="incomplete inventory",
        )


def test_unknown_allocation_blocks_without_falling_back_to_file_length(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "real.bin").write_bytes(b"data")

    def unavailable(*_args, **_kwargs):
        raise OSError("controlled allocation failure")

    monkeypatch.setattr(storage_inventory, "file_allocation", unavailable)
    with pytest.raises(storage_inventory.StorageInventoryError, match="controlled allocation failure"):
        storage_inventory.directory_inventory(tmp_path)


def test_parallel_inventory_cancels_on_the_calling_thread_and_joins_readers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    for index in range(16):
        directory = tmp_path / str(index)
        directory.mkdir()
        (directory / "real.bin").write_bytes(b"account this allocation")
    entered = threading.Event()
    release = threading.Event()
    lock = threading.Lock()
    active_readers = 0
    calling_thread = threading.get_ident()
    original = storage_inventory.file_allocation

    def measure(*args, **kwargs):
        nonlocal active_readers
        with lock:
            active_readers += 1
        entered.set()
        try:
            assert release.wait(3)
            return original(*args, **kwargs)
        finally:
            with lock:
                active_readers -= 1

    def cancel() -> None:
        assert threading.get_ident() == calling_thread
        if entered.is_set():
            release.set()
            raise CancelledError("controlled inventory cancellation")

    monkeypatch.setattr(storage_inventory, "file_allocation", measure)
    try:
        with pytest.raises(CancelledError, match="controlled inventory cancellation"):
            storage_inventory.directory_inventory(tmp_path, check_cancelled=cancel)
    finally:
        release.set()
    assert active_readers == 0


def test_disappeared_entry_is_not_an_unreadable_subtree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disappearing = tmp_path / "removed.bin"
    disappearing.write_bytes(b"gone before measurement")

    def remove_then_measure(path, *_args, **_kwargs):
        Path(path).unlink()
        raise FileNotFoundError(str(path))

    monkeypatch.setattr(storage_inventory, "file_allocation", remove_then_measure)
    report = storage_inventory.directory_inventory(tmp_path)
    assert report["files"] == 0
    assert report["allocated_bytes"] == 0


def _nearly_full_cache_pair(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    runtime = tmp_path / "runtime"
    projects = runtime / "cache" / "projects"
    roots = []
    for name in ("a", "b"):
        project = tmp_path / f"project-{name}"
        project.mkdir()
        cache = projects / storage_inventory.project_cache_identity(project, case_sensitive_paths=False)
        storage_inventory.register_project_cache_owner(cache, project, case_sensitive_paths=False)
        roots.append((cache, project))
    unit = allocation_unit(tmp_path)
    maximum = max(4 * 1024**2, unit * 1024)
    if maximum > 128 * 1024**2:
        pytest.skip("The filesystem allocation unit would require an oversized capacity fixture")
    monkeypatch.setenv("MEDIAFLOW_PROJECT_CACHE_MAX_BYTES", str(2 * maximum))
    monkeypatch.setenv("MEDIAFLOW_PROJECT_CACHES_MAX_BYTES", str(maximum))
    monkeypatch.setenv("MEDIAFLOW_MINIMUM_FREE_BYTES", "1")
    occupied = storage_inventory.directory_inventory(projects)["allocated_bytes"]
    (roots[0][0] / "baseline.bin").write_bytes(b"x" * (maximum - unit - occupied))
    assert storage_inventory.directory_inventory(projects)["allocated_bytes"] == maximum - unit
    project_cache_budget.require_project_cache_budget(
        roots[0][0], expected_new_bytes=0, label="initial inventory"
    )
    return runtime, projects, roots, maximum


def test_completed_cache_write_is_charged_before_another_projects_small_reservation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _runtime, projects, roots, maximum = _nearly_full_cache_pair(tmp_path, monkeypatch)
    with project_cache_budget.reserve_project_cache(
        *roots[0],
        expected_new_bytes=1,
        label="first cache producer",
        case_sensitive_paths=False,
    ):
        (roots[0][0] / "new.bin").write_bytes(b"completed allocation")

    assert storage_inventory.directory_inventory(projects)["allocated_bytes"] == maximum
    with pytest.raises(RuntimeError, match="all project-derived caches"):
        project_cache_budget.reserve_project_cache(
            *roots[1],
            expected_new_bytes=1,
            label="second cache producer",
            case_sensitive_paths=False,
        )
    assert not (roots[1][0] / "new.bin").exists()


def test_cancelled_cache_producer_settles_retained_bytes_and_records_interruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _projects, roots, _maximum = _nearly_full_cache_pair(tmp_path, monkeypatch)
    with (
        pytest.raises(CancelledError),
        project_cache_budget.reserve_project_cache(
            *roots[0],
            expected_new_bytes=1,
            label="cancelled producer",
            case_sensitive_paths=False,
        ) as reservation,
    ):
        (roots[0][0] / "partial.bin").write_bytes(b"retained partial")
        raise CancelledError("controlled cancellation")

    receipt = next(
        record
        for record in storage_receipt_inventory(runtime)["records"]
        if record["operation_id"] == reservation.reservation_id
    )
    assert receipt["terminal_state"] == "interrupted"
    ledger = json.loads(cache_reservation_ledger_path().read_text())
    assert ledger["pending_bytes"] == 0
    assert (
        ledger["observed_by_project"][str(roots[0][0])]
        == storage_inventory.directory_inventory(roots[0][0])["allocated_bytes"]
    )
    with pytest.raises(RuntimeError, match="all project-derived caches"):
        project_cache_budget.reserve_project_cache(
            *roots[1],
            expected_new_bytes=1,
            label="after cancellation",
            case_sensitive_paths=False,
        )


def test_deleted_cache_capacity_is_reconciled_without_a_permanent_high_water_mark(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _runtime, _projects, roots, _maximum = _nearly_full_cache_pair(tmp_path, monkeypatch)
    output = roots[0][0] / "new.bin"
    with project_cache_budget.reserve_project_cache(
        *roots[0],
        expected_new_bytes=1,
        label="write then remove",
        case_sensitive_paths=False,
    ):
        output.write_bytes(b"removable output")
    output.unlink()
    with project_cache_budget.reserve_project_cache(
        *roots[0],
        expected_new_bytes=1,
        label="reclaimed capacity",
        case_sensitive_paths=False,
    ):
        pass


def test_other_projects_freed_capacity_is_reconciled_before_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _runtime, _projects, roots, _maximum = _nearly_full_cache_pair(tmp_path, monkeypatch)
    output = roots[0][0] / "new.bin"
    with project_cache_budget.reserve_project_cache(
        *roots[0], expected_new_bytes=1, label="first cache", case_sensitive_paths=False
    ):
        output.write_bytes(b"subsequently removed")
    output.unlink()
    with project_cache_budget.reserve_project_cache(
        *roots[1], expected_new_bytes=1, label="other project after cleanup", case_sensitive_paths=False
    ):
        pass


def test_abandoned_cache_producer_is_reconciled_from_real_files_after_process_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _runtime, _projects, roots, _maximum = _nearly_full_cache_pair(tmp_path, monkeypatch)
    script = (
        "import os,sys; from pathlib import Path; "
        "from mediaflow.infrastructure.project_cache_budget import reserve_project_cache; "
        "r=reserve_project_cache(sys.argv[1],sys.argv[2],expected_new_bytes=1,"
        "label='crashed producer',case_sensitive_paths=False); "
        "(Path(sys.argv[1])/'abandoned.bin').write_bytes(b'retained after crash'); os._exit(0)"
    )
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script, *(str(path) for path in roots[0])],
        cwd=Path(__file__).resolve().parents[3],
        capture_output=True,
        text=True,
        timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert completed.returncode == 0, completed.stderr
    assert (roots[0][0] / "abandoned.bin").is_file()
    with pytest.raises(RuntimeError, match="all project-derived caches"):
        project_cache_budget.reserve_project_cache(
            *roots[1],
            expected_new_bytes=1,
            label="after process exit",
            case_sensitive_paths=False,
        )


def test_failed_settlement_keeps_charge_until_a_complete_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _runtime, _projects, roots, _maximum = _nearly_full_cache_pair(tmp_path, monkeypatch)
    reservation = project_cache_budget.reserve_project_cache(
        *roots[0], expected_new_bytes=1, label="settlement fault", case_sensitive_paths=False
    )
    (roots[0][0] / "partial.bin").write_bytes(b"retained after I/O failure")
    original = storage_inventory.directory_inventory

    def unavailable(root):
        if Path(root).resolve() == roots[0][0]:
            raise storage_inventory.StorageInventoryError(f"controlled denial: {root}")
        return original(root)

    monkeypatch.setattr(storage_inventory, "directory_inventory", unavailable)
    original_error = RuntimeError("producer failed")
    reservation.release(error=original_error)
    ledger = json.loads(cache_reservation_ledger_path().read_text())
    assert ledger["reservations"][reservation.reservation_id]["state"] == "settlement-required"
    assert ledger["pending_bytes"] > 0
    assert any("settlement remains pending" in note for note in original_error.__notes__)
    monkeypatch.setattr(storage_inventory, "directory_inventory", original)
    with pytest.raises(RuntimeError, match="all project-derived caches"):
        project_cache_budget.reserve_project_cache(
            *roots[1], expected_new_bytes=1, label="after failed settlement", case_sensitive_paths=False
        )


def test_cache_quota_includes_previously_registered_runtime_locations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _runtime, _projects, roots, _maximum = _nearly_full_cache_pair(tmp_path, monkeypatch)
    with project_cache_budget.reserve_project_cache(
        *roots[0], expected_new_bytes=1, label="first location", case_sensitive_paths=False
    ):
        (roots[0][0] / "complete.bin").write_bytes(b"completed in old location")
    new_project = tmp_path / "third-project"
    new_project.mkdir()
    new_cache = (
        tmp_path
        / "other-runtime"
        / "cache"
        / "projects"
        / storage_inventory.project_cache_identity(new_project, case_sensitive_paths=False)
    )
    with pytest.raises(RuntimeError, match="all project-derived caches"):
        project_cache_budget.reserve_project_cache(
            new_cache, new_project, expected_new_bytes=1, label="changed runtime", case_sensitive_paths=False
        )
    assert not (new_cache / "complete.bin").exists()


def test_managed_root_requires_an_explicit_project_namespace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mediaflow.infrastructure.storage_roots import managed_root_ownership, project_root_ownership

    namespace = tmp_path / ("MediaFlow" if os.name == "nt" else "mediaflow")
    monkeypatch.setenv("MEDIAFLOW_DEV_ROOT", str(namespace))
    monkeypatch.delenv("MEDIAFLOW_RUNTIME_DIR", raising=False)
    monkeypatch.delenv("MEDIAFLOW_TEST_ROOT", raising=False)
    proof = managed_root_ownership(namespace / "runtime" / "cache")
    assert proof["project_namespace"] == "mediaflow"
    assert proof["namespace_root"] == str(namespace.resolve())
    with pytest.raises(RuntimeError, match="outside"):
        managed_root_ownership(tmp_path / "other-project" / "cache")
    monkeypatch.setenv("MEDIAFLOW_DEV_ROOT", str(tmp_path / "unowned"))
    with pytest.raises(RuntimeError, match="namespace"):
        managed_root_ownership(tmp_path / "unowned" / "cache")
    monkeypatch.delenv("MEDIAFLOW_DEV_ROOT")
    with pytest.raises(RuntimeError, match="outside"):
        managed_root_ownership(namespace / "runtime")
    with pytest.raises(RuntimeError, match="outside"):
        managed_root_ownership(Path(__file__).resolve().parents[3] / "unconfigured-runtime")
    media_project = tmp_path / "media-project"
    assert project_root_ownership(media_project, media_project / "proxies")["scope"] == "project-root"
    with pytest.raises(RuntimeError, match="escapes"):
        project_root_ownership(media_project, tmp_path / "outside-project")
    assert not namespace.exists()


@pytest.mark.parametrize("directory", ["Portable MediaFlow Pro/runtime", "Chosen Runtime"])
def test_explicit_runtime_root_does_not_require_a_development_directory_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, directory: str,
) -> None:
    from mediaflow.infrastructure.storage_roots import (
        managed_root_ownership,
        receipt_owns_path,
        storage_registry_path,
    )

    # No file is written here: use a path outside the developer's MediaFlow
    # namespace to represent a portable install or a user-selected runtime.
    runtime = (Path(tmp_path.anchor) / directory).resolve()
    for variable in ("MEDIAFLOW_DEV_ROOT", "MEDIAFLOW_TEST_ROOT", "MEDIAFLOW_STORAGE_REGISTRY_PATH"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("MEDIAFLOW_RUNTIME_DIR", str(runtime))
    proof = managed_root_ownership(runtime / "cache" / "projects")
    assert receipt_owns_path(proof, runtime / "cache" / "projects" / "cache-key")
    assert not receipt_owns_path(proof, runtime.parent / "other-project")
    assert storage_registry_path() == runtime / "storage" / "registered-roots.json"
    with pytest.raises(RuntimeError, match="outside"):
        managed_root_ownership(runtime.parent / "other-project")


def test_runtime_switch_preserves_registered_cache_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mediaflow.infrastructure.project_cache_reservations import (
        ProjectCacheLedger,
        _project_cache_reservation_state,
        _write_project_cache_pending_reservations,
    )
    from mediaflow.infrastructure.storage_roots import managed_root_ownership, register_owned_storage_roots

    # The old runtime need not exist: its validated registration remains the
    # accounting boundary after the user selects another runtime directory.
    previous = Path(tmp_path.anchor) / "Previous Portable Runtime"
    groups = previous / "cache" / "projects"
    monkeypatch.setenv("MEDIAFLOW_RUNTIME_DIR", str(previous))
    register_owned_storage_roots(
        (groups,), ownership=managed_root_ownership(groups), producer="cache-projects",
    )
    ledger_path = cache_reservation_ledger_path()
    _write_project_cache_pending_reservations(
        ledger_path, groups, ProjectCacheLedger({}, {}, {}, set(), registered_groups=(str(groups),)),
    )
    monkeypatch.setenv("MEDIAFLOW_RUNTIME_DIR", str(Path(tmp_path.anchor) / "New Portable Runtime"))
    state = _project_cache_reservation_state(ledger_path, groups)
    assert state.valid
    assert state.registered_groups == (str(groups),)


def test_real_public_build_reuses_cache_blocks_over_budget_and_reports_cancelled_residue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time
    from dataclasses import replace

    from mediaflow.composition import EditorProject
    from mediaflow.domain.enums import ExportFormat, TaskStatus
    from mediaflow.domain.project import ProjectProfile
    from mediaflow.domain.settings import ServiceSettings
    from mediaflow.domain.task_commands import BuildSequenceCommand, SequenceBuildUnit
    from mediaflow.environment import test_run_root
    from mediaflow.infrastructure.project_repository import ProjectRepository
    from mediaflow.infrastructure.runtime_context import RuntimeContext
    from scripts.report_storage import build_report
    from tests.v2.infrastructure.test_segmented_export_contract import _frame, _preset, _timeline, _video

    paths = replace(RuntimeContext.discover().paths, runtime_dir=test_run_root() / "rt")
    monkeypatch.setenv("MEDIAFLOW_RUNTIME_DIR", str(paths.runtime_dir))
    source = tmp_path / "source.mp4"
    _video(source, paths, "red")
    repository = ProjectRepository.create(
        tmp_path / "Project", "Storage contract", ProjectProfile(width=160, height=90, fps_numerator=25)
    )
    project = EditorProject(repository, settings=ServiceSettings(), paths=paths)
    try:
        editor, asset = _timeline(repository, paths, source)

        def start(name, units):
            return project.start_task(
                BuildSequenceCommand(
                    sequence_id=editor.sequence_id,
                    output_path=str(repository.project_dir / "exports" / f"{name}.mp4"),
                    format=ExportFormat.H264,
                    preset=_preset(),
                    units=units,
                ),
                [asset.id],
                sequence_id=editor.sequence_id,
            )

        units = [SequenceBuildUnit(id="all", start_frame=0, end_frame=50)]
        first_task = start("first", units)
        first = project.wait_for_task(first_task.id, timeout=60)
        assert first.status == TaskStatus.COMPLETED, first.error
        assert first.outcome.units[0].status == "rendered"
        cache = paths.project_cache_dir(repository.project_dir)
        before = storage_inventory.directory_inventory(cache)
        second_task = start("reuse", units)
        second = project.wait_for_task(second_task.id, timeout=60)
        assert second.status == TaskStatus.COMPLETED, second.error
        assert second.outcome.units[0].status == "reused"
        assert second.outcome.units[0].sha256 == first.outcome.units[0].sha256
        assert (
            storage_inventory.directory_inventory(cache)["object_identity_sha256"]
            == before["object_identity_sha256"]
        )
        assert _frame(repository.project_dir / "exports" / "first.mp4", paths) == _frame(
            repository.project_dir / "exports" / "reuse.mp4", paths
        )

        monkeypatch.setenv("MEDIAFLOW_PROJECT_CACHES_MAX_BYTES", "1")
        refused_task = start("refused", units)
        refused = project.wait_for_task(refused_task.id, timeout=30)
        assert refused.status == TaskStatus.FAILED
        assert "storage preflight blocked" in refused.error
        assert not (repository.project_dir / "exports" / "refused.mp4").exists()
        monkeypatch.setenv("MEDIAFLOW_PROJECT_CACHES_MAX_BYTES", str(1024**3))

        existing = {path for path in cache.rglob("*") if path.is_file()}

        def has_new_media():
            for path in cache.rglob("*.mp4"):
                try:
                    if path not in existing and path.stat().st_size > 0:
                        return True
                except FileNotFoundError:
                    continue
            return False

        interrupted_task = start(
            "cancelled",
            [
                SequenceBuildUnit(id=f"part-{index}", start_frame=index * 5, end_frame=(index + 1) * 5)
                for index in range(10)
            ],
        )
        deadline = time.monotonic() + 30
        while not has_new_media():
            if time.monotonic() >= deadline:
                pytest.fail("The real build did not reach its media write boundary")
            time.sleep(0.01)
        project.cancel_task(interrupted_task.id)
        interrupted = project.wait_for_task(interrupted_task.id, timeout=30)
        assert interrupted.status == TaskStatus.CANCELLED, interrupted.error
        report = build_report()
        receipts = report["operation_receipts"]["records"]
        cache_receipts = [record for record in receipts if record["producer"] == "project-derived-cache"]
        assert any(record["terminal_state"] == "succeeded" for record in cache_receipts)
        assert any(record["terminal_state"] == "interrupted" for record in cache_receipts)
        assert all(record["ownership_status"] == "registered" for record in cache_receipts)
        assert report["total"]["allocated_bytes"] > 0
        assert report["total"]["unique_objects"] > 0
        assert report["cleanup"] == "report-only-until-authorized"
        assert has_new_media()
    finally:
        project.close()
