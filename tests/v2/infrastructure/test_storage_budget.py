from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from mediaflow.infrastructure import (
    project_cache_budget,
    storage_budget,
    storage_estimates,
    storage_inventory,
    storage_policy,
    storage_preflight,
    storage_receipts,
)
from mediaflow.infrastructure.project_cache_reservations import cache_reservation_ledger_path
from mediaflow.infrastructure.storage_allocation import allocation_unit


def test_storage_budget_allows_known_peak_and_reports_owned_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unit = allocation_unit(tmp_path)
    (tmp_path / "cache.bin").write_bytes(b"x" * 100)
    monkeypatch.setattr(
        shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=1_000 * unit, used=200 * unit, free=800 * unit),
    )

    report = storage_preflight.require_storage_budget(
        tmp_path,
        expected_new_bytes=200 * unit,
        maximum_managed_bytes=400 * unit,
        minimum_free_bytes=500 * unit,
        label="test cache",
    )

    assert report["current_logical_bytes"] == 100
    assert report["current_managed_bytes"] == unit
    assert report["projected_managed_bytes"] == 201 * unit
    assert report["policy"]["cleanup"] == "report-only-until-authorized"


@pytest.mark.parametrize("estimate", (None, 350))
def test_storage_budget_blocks_unknown_or_excessive_peak_before_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    estimate: int | None,
) -> None:
    unit = allocation_unit(tmp_path)
    (tmp_path / "cache.bin").write_bytes(b"x" * 100)
    monkeypatch.setattr(
        shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=1_000 * unit, used=200 * unit, free=800 * unit),
    )

    with pytest.raises(RuntimeError, match="storage preflight blocked") as captured:
        storage_preflight.require_storage_budget(
            tmp_path,
            expected_new_bytes=None if estimate is None else estimate * unit,
            maximum_managed_bytes=400 * unit,
            minimum_free_bytes=500 * unit,
            label="test cache",
        )

    if estimate is not None:
        payload = json.loads(str(captured.value).splitlines()[-1])
        assert payload["cleanup_candidates"] == []
        assert payload["policy"]["over_budget"] == "block-before-large-write"


def test_inventory_does_not_follow_links_and_only_reports_cleanup_candidates(
    tmp_path: Path,
) -> None:
    terminal = tmp_path / "r-complete"
    terminal.mkdir()
    (terminal / "artifact.bin").write_bytes(b"abc")
    (terminal / "run-result.json").write_text(
        json.dumps({"schema": "mediaflow-script-run/v1", "status": "failed"}),
        encoding="utf-8",
    )

    inventory = storage_inventory.directory_inventory(tmp_path)

    assert inventory["logical_bytes"] > 3
    assert inventory["cleanup"] == "report-only-until-authorized"
    assert inventory["cleanup_candidates"][0]["path"] == "r-complete"
    assert inventory["cleanup_candidates"][0]["allocated_bytes"] == inventory["allocated_bytes"]


@pytest.mark.skipif(os.name != "nt", reason="Windows directory junction contract")
def test_inventory_does_not_follow_windows_directory_junctions(tmp_path: Path) -> None:
    import _winapi

    owned = tmp_path / "owned"
    owned.mkdir()
    (owned / "local.bin").write_bytes(b"abc")
    external = tmp_path / "external"
    external.mkdir()
    (external / "shared-tool.bin").write_bytes(b"external tool contents")
    _winapi.CreateJunction(str(external), str(owned / "shared-tools"))

    inventory = storage_inventory.directory_inventory(owned)

    assert inventory["logical_bytes"] == 3
    assert inventory["files"] == 1
    assert inventory["linked_paths_not_followed"] == ["shared-tools"]
    assert (external / "shared-tool.bin").read_bytes() == b"external tool contents"


def test_video_cache_estimate_accounts_for_partial_and_final_files() -> None:
    assert storage_estimates.estimate_video_cache_bytes(1920, 1080, 300) >= (1920 * 1080 * 300 * 4 * 2)


def test_project_cache_owner_makes_hashed_storage_attributable(tmp_path: Path) -> None:
    project = tmp_path / "Project A"
    project.mkdir()
    identity = hashlib.sha256(str(project.resolve()).casefold().encode("utf-8")).hexdigest()[:24]
    projects_root = tmp_path / "runtime" / "cache" / "projects"
    cache_root = projects_root / identity

    owner = storage_inventory.register_project_cache_owner(
        cache_root,
        project,
        case_sensitive_paths=False,
    )
    report = storage_inventory.project_cache_inventory(projects_root)

    assert owner["project_path"] == str(project.resolve())
    record = report["projects"][0]
    assert record["cache_key"] == identity
    assert record["project_path"] == str(project.resolve())
    assert record["project_exists"] is True
    assert record["owner_status"] == "known"
    assert record["logical_bytes"] > 0
    assert record["allocated_bytes"] >= record["unique_object_bytes"]
    assert record["files"] == 2


def test_project_cache_owner_accepts_case_sensitive_identity(tmp_path: Path) -> None:
    project = tmp_path / "Project A"
    project.mkdir()
    identity = storage_inventory.project_cache_identity(
        project,
        case_sensitive_paths=True,
    )
    cache_root = tmp_path / "runtime" / "cache" / "projects" / identity

    owner = storage_inventory.register_project_cache_owner(
        cache_root,
        project,
        case_sensitive_paths=True,
    )

    assert owner["project_identity"] == identity
    assert owner["project_path"] == str(project.resolve())


def test_project_cache_owner_registration_serializes_concurrent_writers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "Concurrent Project"
    project.mkdir()
    identity = storage_inventory.project_cache_identity(
        project,
        case_sensitive_paths=False,
    )
    cache_root = tmp_path / "runtime" / "cache" / "projects" / identity
    original_atomic_write = storage_inventory.atomic_write_text
    starting = threading.Barrier(2)
    write_count = 0
    count_lock = threading.Lock()

    def delayed_atomic_write(
        destination: str | Path,
        content: str,
        *,
        encoding: str = "utf-8",
        durable: bool = False,
        mode: int | None = None,
    ) -> Path:
        nonlocal write_count
        with count_lock:
            write_count += 1
        time.sleep(0.1)
        return original_atomic_write(
            destination,
            content,
            encoding=encoding,
            durable=durable,
            mode=mode,
        )

    monkeypatch.setattr(storage_inventory, "atomic_write_text", delayed_atomic_write)

    def register() -> dict[str, object]:
        starting.wait(timeout=5)
        return storage_inventory.register_project_cache_owner(
            cache_root,
            project,
            case_sensitive_paths=False,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        owners = list(pool.map(lambda _index: register(), range(2)))

    assert owners[0] == owners[1]
    assert write_count == 1
    assert (
        json.loads((cache_root / storage_policy.PROJECT_CACHE_OWNER_FILENAME).read_text(encoding="utf-8"))
        == owners[0]
    )


def test_project_cache_gate_also_enforces_all_projects_total(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unit = allocation_unit(tmp_path)
    projects = tmp_path / "projects"
    current = projects / "current"
    other = projects / "other"
    other.mkdir(parents=True)
    (other / "cache.bin").write_bytes(b"x" * 100)
    monkeypatch.setenv("MEDIAFLOW_PROJECT_CACHE_MAX_BYTES", str(10 * unit))
    monkeypatch.setenv("MEDIAFLOW_PROJECT_CACHES_MAX_BYTES", str(unit))
    monkeypatch.setenv("MEDIAFLOW_MINIMUM_FREE_BYTES", "1")
    monkeypatch.setattr(
        shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=10_000 * unit, used=100 * unit, free=9_900 * unit),
    )

    with pytest.raises(RuntimeError, match="all project-derived caches"):
        project_cache_budget.require_project_cache_budget(
            current,
            expected_new_bytes=50,
            label="current project cache",
        )

    assert not current.exists()


def test_project_cache_gate_uses_one_exact_inventory_for_both_limits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unit = allocation_unit(tmp_path)
    projects = tmp_path / "projects"
    current = projects / "current"
    other = projects / "other"
    current.mkdir(parents=True)
    other.mkdir()
    (current / "current.bin").write_bytes(b"x" * 80)
    (other / "other.bin").write_bytes(b"x" * 100)
    monkeypatch.setenv("MEDIAFLOW_PROJECT_CACHE_MAX_BYTES", str(unit))
    monkeypatch.setenv("MEDIAFLOW_PROJECT_CACHES_MAX_BYTES", str(10 * unit))
    monkeypatch.setenv("MEDIAFLOW_MINIMUM_FREE_BYTES", "1")
    monkeypatch.setattr(
        shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=10_000 * unit, used=100 * unit, free=9_900 * unit),
    )
    original_inventory = storage_inventory._scan_files
    inventory_roots: list[Path] = []

    def observed_inventory(root: str | Path, *, check_cancelled=None) -> dict[str, object]:
        inventory_roots.append(Path(root).resolve())
        return original_inventory(root, check_cancelled=check_cancelled)

    monkeypatch.setattr(storage_inventory, "_scan_files", observed_inventory)

    with pytest.raises(RuntimeError, match="current project cache"):
        project_cache_budget.require_project_cache_budget(
            current,
            expected_new_bytes=21,
            label="current project cache",
        )

    assert inventory_roots == [projects.resolve()]


def test_small_project_cache_reservations_defer_only_a_bounded_global_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unit = allocation_unit(tmp_path)
    projects = tmp_path / "runtime" / "cache" / "projects"
    current = projects / "current"
    other = projects / "other"
    other.mkdir(parents=True)
    (other / "existing.bin").write_bytes(b"x" * 100)
    monkeypatch.setenv("MEDIAFLOW_PROJECT_CACHE_MAX_BYTES", str(10 * unit))
    monkeypatch.setenv("MEDIAFLOW_PROJECT_CACHES_MAX_BYTES", str(4096 * unit))
    monkeypatch.setenv("MEDIAFLOW_MINIMUM_FREE_BYTES", "1")
    monkeypatch.setattr(
        shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=100_000 * unit, used=100 * unit, free=99_900 * unit),
    )
    original_inventory = storage_inventory._scan_files
    inventory_roots: list[Path] = []

    def observed_inventory(root: str | Path, *, check_cancelled=None) -> dict[str, object]:
        inventory_roots.append(Path(root).resolve())
        return original_inventory(root, check_cancelled=check_cancelled)

    monkeypatch.setattr(storage_inventory, "_scan_files", observed_inventory)

    initialized, first_reservation = project_cache_budget._acquire_project_cache_budget(
        current,
        expected_new_bytes=8,
        label="small waveform",
    )
    (current / "realized.bin").parent.mkdir(parents=True, exist_ok=True)
    (current / "realized.bin").write_bytes(b"x" * 8)
    deferred, second_reservation = project_cache_budget._acquire_project_cache_budget(
        current,
        expected_new_bytes=8,
        label="second small waveform",
    )
    reconciled, third_reservation = project_cache_budget._acquire_project_cache_budget(
        current,
        expected_new_bytes=3 * unit,
        label="reservation threshold",
    )

    assert "inventory_mode" not in initialized["all_projects"]
    assert deferred["all_projects"]["inventory_mode"] == ("bounded-deferred-global-inventory")
    assert deferred["all_projects"]["pending_reservation_bytes"] == 2 * unit
    assert "inventory_mode" not in reconciled["all_projects"]
    assert inventory_roots == [
        projects.resolve(),
        current.resolve(),
        projects.resolve(),
    ]
    ledger = json.loads(cache_reservation_ledger_path().read_text(encoding="utf-8"))
    assert ledger["observed_bytes"] == 2 * unit
    assert ledger["pending_bytes"] == 5 * unit
    assert ledger["observed_by_project"] == {str(current.resolve()): unit, str(other.resolve()): unit}
    assert len(ledger["reservations"]) == 3

    first_reservation.release()
    second_reservation.release()
    third_reservation.release()
    released = json.loads(cache_reservation_ledger_path().read_text(encoding="utf-8"))
    assert released["pending_bytes"] == 0
    assert released["reservations"] == {}


def test_concurrent_different_project_reservations_do_not_hold_or_cross_charge_the_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unit = allocation_unit(tmp_path)
    projects = tmp_path / "runtime" / "cache" / "projects"
    project_roots = [projects / "first", projects / "second"]
    projects.mkdir(parents=True)
    monkeypatch.setenv("MEDIAFLOW_PROJECT_CACHE_MAX_BYTES", str(unit))
    monkeypatch.setenv("MEDIAFLOW_PROJECT_CACHES_MAX_BYTES", str(4096 * unit))
    monkeypatch.setenv("MEDIAFLOW_MINIMUM_FREE_BYTES", "1")
    monkeypatch.setattr(
        shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=1_000_000 * unit, used=0, free=1_000_000 * unit),
    )
    monkeypatch.setattr(
        storage_policy,
        "PROJECT_CACHE_OWNER_LOCK_TIMEOUT_SECONDS",
        0.25,
    )
    original_inventory = storage_inventory._scan_files
    scans_ready = threading.Event()
    allow_scan = threading.Event()
    global_scans = []

    def concurrent_inventory(root: str | Path, *, check_cancelled=None) -> dict[str, object]:
        if Path(root).resolve() == projects.resolve():
            global_scans.append(Path(root).resolve())
            scans_ready.set()
            assert allow_scan.wait(timeout=2)
        return original_inventory(root, check_cancelled=check_cancelled)

    monkeypatch.setattr(storage_inventory, "_scan_files", concurrent_inventory)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                project_cache_budget._acquire_project_cache_budget,
                project_roots[index],
                expected_new_bytes=60,
                label=f"concurrent cache {index}",
            )
            for index in range(2)
        ]
        try:
            assert scans_ready.wait(timeout=2)
            ledger_lock = project_cache_budget._acquire_project_cache_reservation_lock(
                cache_reservation_ledger_path(),
            )
            ledger_lock.release()
        finally:
            allow_scan.set()
        reports_and_reservations = [future.result(timeout=3) for future in futures]

    assert len(reports_and_reservations) == 2
    assert global_scans == [projects.resolve()]
    ledger = json.loads(cache_reservation_ledger_path().read_text(encoding="utf-8"))
    assert ledger["observed_bytes"] == 0
    assert ledger["pending_bytes"] == 2 * unit
    assert sorted(reservation["project"] for reservation in ledger["reservations"].values()) == sorted(
        str(path.resolve()) for path in project_roots
    )
    for _report, reservation in reports_and_reservations:
        reservation.release()


def test_operation_budget_checks_peak_without_claiming_user_owned_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unit = allocation_unit(tmp_path)
    (tmp_path / "existing-user-media.bin").write_bytes(b"x" * 900)
    monkeypatch.setattr(
        shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=10_000 * unit, used=1_000 * unit, free=9_000 * unit),
    )

    report = storage_preflight.require_operation_storage_budget(
        tmp_path,
        expected_new_bytes=500 * unit,
        maximum_operation_bytes=1_000 * unit,
        minimum_free_bytes=8_000 * unit,
        label="delivery",
    )

    assert report["expected_new_bytes"] == 500 * unit
    assert "current_managed_bytes" not in report
    assert report["projected_free_bytes"] == 8_500 * unit


def test_download_and_proxy_estimates_cover_concurrent_outputs() -> None:
    audio = storage_estimates.estimate_download_peak_bytes(
        60,
        media_kind="audio",
        resolution="audio",
    )
    video = storage_estimates.estimate_download_peak_bytes(
        60,
        media_kind="video",
        resolution="1080p",
    )
    one_proxy = storage_estimates.estimate_proxy_peak_bytes(60)
    two_proxies = storage_estimates.estimate_proxy_peak_bytes(60, output_count=2)

    assert audio is not None and video is not None and video > audio
    assert one_proxy is not None and two_proxies is not None
    assert two_proxies >= one_proxy * 2 - 1
    assert (
        storage_estimates.estimate_download_peak_bytes(
            0,
            media_kind="video",
            resolution="best",
        )
        is None
    )


def test_storage_receipt_preserves_terminal_output_inventory(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    output = tmp_path / "delivery.bin"
    receipt = storage_receipts.start_storage_receipt(
        runtime,
        producer="media-download",
        operation_id="download-1",
        owned_root=output,
        preflight=storage_preflight.require_operation_storage_budget(
            tmp_path,
            expected_new_bytes=1024**2,
            maximum_operation_bytes=2 * 1024**2,
            minimum_free_bytes=1,
            label="receipt output",
        ),
    )
    output.write_bytes(b"observable")

    storage_receipts.finalize_storage_receipt(
        receipt,
        status="passed",
        outputs=(output,),
    )
    inventory = storage_receipts.storage_receipt_inventory(runtime)

    assert inventory["invalid_receipts"] == []
    assert inventory["records"][0]["status"] == "passed"
    assert inventory["records"][0]["observed_status"] == "passed"
    record = inventory["records"][0]
    saved = record["outputs"][0]
    assert saved["path"] == str(output.resolve())
    assert saved["exists"] is True
    assert saved["logical_bytes"] == len(b"observable")
    assert saved["unique_object_bytes"] == len(b"observable")
    assert saved["allocated_bytes"] >= len(b"observable")
    assert record["terminal_state"] == "succeeded"
    assert record["final_inventory"]["total"]["unique_objects"] == 1


def test_storage_receipt_reports_abandoned_running_owner_without_cleanup(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    owned_root = tmp_path / "owned"
    owned_root.mkdir()
    retained = owned_root / "partial.bin"
    retained.write_bytes(b"retained evidence")
    receipt = storage_receipts.start_storage_receipt(
        runtime,
        producer="media-download",
        operation_id="interrupted-download",
        owned_root=owned_root,
        preflight=storage_preflight.require_operation_storage_budget(
            owned_root,
            expected_new_bytes=1024**2,
            maximum_operation_bytes=2 * 1024**2,
            minimum_free_bytes=1,
            label="retained output",
        ),
    )
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    payload["owner"] = {"pid": 2_147_483_647, "process_started": 1.0}
    receipt.write_text(json.dumps(payload), encoding="utf-8")

    inventory = storage_receipts.storage_receipt_inventory(runtime)

    record = inventory["records"][0]
    assert record["status"] == "running"
    assert record["observed_status"] == "interrupted"
    assert record["owner_active"] is False
    assert record["retained_inventory"]["total"]["files"] == 1
    assert record["retained_inventory"]["total"]["logical_bytes"] == len(b"retained evidence")
    assert record["retained_inventory"]["cleanup"] == "report-only-until-authorized"
    assert retained.read_bytes() == b"retained evidence"


def test_project_artifact_gate_checks_owned_root_and_whole_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unit = allocation_unit(tmp_path)
    project = tmp_path / "project"
    proxies = project / "proxies"
    other = project / "generated"
    other.mkdir(parents=True)
    (other / "existing.bin").write_bytes(b"x" * 100)
    monkeypatch.setenv("MEDIAFLOW_PROJECT_ARTIFACT_MAX_BYTES", str(10 * unit))
    monkeypatch.setenv("MEDIAFLOW_PROJECT_ARTIFACTS_MAX_BYTES", str(unit))
    monkeypatch.setenv("MEDIAFLOW_MINIMUM_FREE_BYTES", "1")
    monkeypatch.setattr(
        shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=10_000 * unit, used=100 * unit, free=9_900 * unit),
    )

    with pytest.raises(RuntimeError, match="project artifacts"):
        storage_budget.require_project_artifact_budget(
            project,
            proxies,
            expected_new_bytes=50,
            label="proxy",
        )

    assert not proxies.exists()
