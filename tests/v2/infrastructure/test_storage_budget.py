from __future__ import annotations

import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from mediaflow.infrastructure import storage_budget


def test_storage_budget_allows_known_peak_and_reports_owned_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "cache.bin").write_bytes(b"x" * 100)
    monkeypatch.setattr(
        storage_budget.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=1_000, used=200, free=800),
    )

    report = storage_budget.require_storage_budget(
        tmp_path,
        expected_new_bytes=200,
        maximum_managed_bytes=400,
        minimum_free_bytes=500,
        label="test cache",
    )

    assert report["current_managed_bytes"] == 100
    assert report["projected_managed_bytes"] == 300
    assert report["policy"]["cleanup"] == "report-only-until-authorized"


@pytest.mark.parametrize("estimate", (None, 350))
def test_storage_budget_blocks_unknown_or_excessive_peak_before_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    estimate: int | None,
) -> None:
    (tmp_path / "cache.bin").write_bytes(b"x" * 100)
    monkeypatch.setattr(
        storage_budget.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=1_000, used=200, free=800),
    )

    with pytest.raises(RuntimeError, match="storage preflight blocked") as captured:
        storage_budget.require_storage_budget(
            tmp_path,
            expected_new_bytes=estimate,
            maximum_managed_bytes=400,
            minimum_free_bytes=500,
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
        json.dumps({"status": "failed"}),
        encoding="utf-8",
    )

    inventory = storage_budget.directory_inventory(tmp_path)

    assert inventory["bytes"] > 3
    assert inventory["cleanup"] == "report-only-until-authorized"
    assert inventory["cleanup_candidates"][0]["path"] == "r-complete"
    assert inventory["cleanup_candidates"][0]["bytes"] == inventory["bytes"]


def test_video_cache_estimate_accounts_for_partial_and_final_files() -> None:
    assert storage_budget.estimate_video_cache_bytes(1920, 1080, 300) == (
        1920 * 1080 * 300 * 2
    )


def test_project_cache_owner_makes_hashed_storage_attributable(tmp_path: Path) -> None:
    project = tmp_path / "Project A"
    project.mkdir()
    identity = hashlib.sha256(
        str(project.resolve()).casefold().encode("utf-8")
    ).hexdigest()[:24]
    projects_root = tmp_path / "runtime" / "cache" / "projects"
    cache_root = projects_root / identity

    owner = storage_budget.register_project_cache_owner(
        cache_root,
        project,
        case_sensitive_paths=False,
    )
    report = storage_budget.project_cache_inventory(projects_root)

    assert owner["project_path"] == str(project.resolve())
    assert report["projects"] == [
        {
            "cache_key": identity,
            "project_path": str(project.resolve()),
            "project_exists": True,
            "owner_status": "known",
            "bytes": report["projects"][0]["bytes"],
            "files": 2,
        }
    ]


def test_project_cache_owner_accepts_case_sensitive_identity(tmp_path: Path) -> None:
    project = tmp_path / "Project A"
    project.mkdir()
    identity = storage_budget.project_cache_identity(
        project,
        case_sensitive_paths=True,
    )
    cache_root = tmp_path / "runtime" / "cache" / "projects" / identity

    owner = storage_budget.register_project_cache_owner(
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
    identity = storage_budget.project_cache_identity(
        project,
        case_sensitive_paths=False,
    )
    cache_root = tmp_path / "runtime" / "cache" / "projects" / identity
    original_atomic_write = storage_budget.atomic_write_text
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

    monkeypatch.setattr(storage_budget, "atomic_write_text", delayed_atomic_write)

    def register() -> dict[str, object]:
        starting.wait(timeout=5)
        return storage_budget.register_project_cache_owner(
            cache_root,
            project,
            case_sensitive_paths=False,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        owners = list(pool.map(lambda _index: register(), range(2)))

    assert owners[0] == owners[1]
    assert write_count == 1
    assert json.loads(
        (cache_root / storage_budget.PROJECT_CACHE_OWNER_FILENAME).read_text(
            encoding="utf-8"
        )
    ) == owners[0]


def test_project_cache_gate_also_enforces_all_projects_total(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projects = tmp_path / "projects"
    current = projects / "current"
    other = projects / "other"
    other.mkdir(parents=True)
    (other / "cache.bin").write_bytes(b"x" * 100)
    monkeypatch.setenv("MEDIAFLOW_PROJECT_CACHE_MAX_BYTES", "1000")
    monkeypatch.setenv("MEDIAFLOW_PROJECT_CACHES_MAX_BYTES", "120")
    monkeypatch.setenv("MEDIAFLOW_MINIMUM_FREE_BYTES", "1")
    monkeypatch.setattr(
        storage_budget.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=10_000, used=100, free=9_900),
    )

    with pytest.raises(RuntimeError, match="all project-derived caches"):
        storage_budget.require_project_cache_budget(
            current,
            expected_new_bytes=50,
            label="current project cache",
        )

    assert not current.exists()


def test_project_cache_gate_uses_one_exact_inventory_for_both_limits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projects = tmp_path / "projects"
    current = projects / "current"
    other = projects / "other"
    current.mkdir(parents=True)
    other.mkdir()
    (current / "current.bin").write_bytes(b"x" * 80)
    (other / "other.bin").write_bytes(b"x" * 100)
    monkeypatch.setenv("MEDIAFLOW_PROJECT_CACHE_MAX_BYTES", "100")
    monkeypatch.setenv("MEDIAFLOW_PROJECT_CACHES_MAX_BYTES", "1000")
    monkeypatch.setenv("MEDIAFLOW_MINIMUM_FREE_BYTES", "1")
    monkeypatch.setattr(
        storage_budget.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=10_000, used=100, free=9_900),
    )
    original_inventory = storage_budget.directory_inventory
    inventory_roots: list[Path] = []

    def observed_inventory(root: str | Path) -> dict[str, object]:
        inventory_roots.append(Path(root).resolve())
        return original_inventory(root)

    monkeypatch.setattr(storage_budget, "directory_inventory", observed_inventory)

    with pytest.raises(RuntimeError, match="current project cache"):
        storage_budget.require_project_cache_budget(
            current,
            expected_new_bytes=21,
            label="current project cache",
        )

    assert inventory_roots == [projects.resolve()]


def test_small_project_cache_reservations_defer_only_a_bounded_global_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projects = tmp_path / "runtime" / "cache" / "projects"
    current = projects / "current"
    other = projects / "other"
    other.mkdir(parents=True)
    (other / "existing.bin").write_bytes(b"x" * 100)
    monkeypatch.setenv("MEDIAFLOW_PROJECT_CACHE_MAX_BYTES", "100000")
    monkeypatch.setenv("MEDIAFLOW_PROJECT_CACHES_MAX_BYTES", "65536")
    monkeypatch.setenv("MEDIAFLOW_MINIMUM_FREE_BYTES", "1")
    monkeypatch.setattr(
        storage_budget.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=100_000, used=100, free=99_900),
    )
    original_inventory = storage_budget.directory_inventory
    inventory_roots: list[Path] = []

    def observed_inventory(root: str | Path) -> dict[str, object]:
        inventory_roots.append(Path(root).resolve())
        return original_inventory(root)

    monkeypatch.setattr(storage_budget, "directory_inventory", observed_inventory)

    initialized = storage_budget.require_project_cache_budget(
        current,
        expected_new_bytes=8,
        label="small waveform",
    )
    (current / "realized.bin").parent.mkdir(parents=True, exist_ok=True)
    (current / "realized.bin").write_bytes(b"x" * 8)
    deferred = storage_budget.require_project_cache_budget(
        current,
        expected_new_bytes=8,
        label="second small waveform",
    )
    reconciled = storage_budget.require_project_cache_budget(
        current,
        expected_new_bytes=49,
        label="reservation threshold",
    )

    assert "inventory_mode" not in initialized["all_projects"]
    assert deferred["all_projects"]["inventory_mode"] == (
        "bounded-deferred-global-inventory"
    )
    assert deferred["all_projects"]["pending_reservation_bytes"] == 16
    assert "inventory_mode" not in reconciled["all_projects"]
    assert inventory_roots == [
        projects.resolve(),
        current.resolve(),
        projects.resolve(),
    ]
    ledger = json.loads(
        (
            projects.parent
            / storage_budget.PROJECT_CACHE_RESERVATION_LEDGER_FILENAME
        ).read_text(encoding="utf-8")
    )
    assert ledger["observed_bytes"] == 108
    assert ledger["pending_bytes"] == 57


def test_operation_budget_checks_peak_without_claiming_user_owned_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "existing-user-media.bin").write_bytes(b"x" * 900)
    monkeypatch.setattr(
        storage_budget.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=10_000, used=1_000, free=9_000),
    )

    report = storage_budget.require_operation_storage_budget(
        tmp_path,
        expected_new_bytes=500,
        maximum_operation_bytes=1_000,
        minimum_free_bytes=8_000,
        label="delivery",
    )

    assert report["expected_new_bytes"] == 500
    assert "current_managed_bytes" not in report
    assert report["projected_free_bytes"] == 8_500


def test_download_and_proxy_estimates_cover_concurrent_outputs() -> None:
    audio = storage_budget.estimate_download_peak_bytes(
        60,
        media_kind="audio",
        resolution="audio",
    )
    video = storage_budget.estimate_download_peak_bytes(
        60,
        media_kind="video",
        resolution="1080p",
    )
    one_proxy = storage_budget.estimate_proxy_peak_bytes(60)
    two_proxies = storage_budget.estimate_proxy_peak_bytes(60, output_count=2)

    assert audio is not None and video is not None and video > audio
    assert one_proxy is not None and two_proxies is not None
    assert two_proxies >= one_proxy * 2 - 1
    assert storage_budget.estimate_download_peak_bytes(
        0,
        media_kind="video",
        resolution="best",
    ) is None


def test_storage_receipt_preserves_terminal_output_inventory(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    output = tmp_path / "delivery.bin"
    output.write_bytes(b"observable")
    receipt = storage_budget.start_storage_receipt(
        runtime,
        producer="media-download",
        operation_id="download-1",
        owned_root=tmp_path,
        preflight={"schema": "mediaflow-storage-preflight/v1"},
    )

    storage_budget.finalize_storage_receipt(
        receipt,
        status="passed",
        outputs=(output,),
    )
    inventory = storage_budget.storage_receipt_inventory(runtime)

    assert inventory["invalid_receipts"] == []
    assert inventory["records"][0]["status"] == "passed"
    assert inventory["records"][0]["observed_status"] == "passed"
    assert inventory["records"][0]["outputs"] == [
        {"path": str(output.resolve()), "exists": True, "bytes": len(b"observable")}
    ]


def test_storage_receipt_reports_abandoned_running_owner_without_cleanup(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    owned_root = tmp_path / "owned"
    owned_root.mkdir()
    retained = owned_root / "partial.bin"
    retained.write_bytes(b"retained evidence")
    receipt = storage_budget.start_storage_receipt(
        runtime,
        producer="media-download",
        operation_id="interrupted-download",
        owned_root=owned_root,
        preflight={"schema": "mediaflow-storage-preflight/v1"},
    )
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    payload["owner"] = {"pid": 2_147_483_647, "process_started": 1.0}
    receipt.write_text(json.dumps(payload), encoding="utf-8")

    inventory = storage_budget.storage_receipt_inventory(runtime)

    record = inventory["records"][0]
    assert record["status"] == "running"
    assert record["observed_status"] == "interrupted"
    assert record["owner_active"] is False
    assert record["retained_inventory"]["files"] == 1
    assert record["retained_inventory"]["bytes"] == len(b"retained evidence")
    assert record["retained_inventory"]["cleanup"] == "report-only-until-authorized"
    assert retained.read_bytes() == b"retained evidence"


def test_project_artifact_gate_checks_owned_root_and_whole_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    proxies = project / "proxies"
    other = project / "generated"
    other.mkdir(parents=True)
    (other / "existing.bin").write_bytes(b"x" * 100)
    monkeypatch.setenv("MEDIAFLOW_PROJECT_ARTIFACT_MAX_BYTES", "1000")
    monkeypatch.setenv("MEDIAFLOW_PROJECT_ARTIFACTS_MAX_BYTES", "120")
    monkeypatch.setenv("MEDIAFLOW_MINIMUM_FREE_BYTES", "1")
    monkeypatch.setattr(
        storage_budget.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=10_000, used=100, free=9_900),
    )

    with pytest.raises(RuntimeError, match="project artifacts"):
        storage_budget.require_project_artifact_budget(
            project,
            proxies,
            expected_new_bytes=50,
            label="proxy",
        )

    assert not proxies.exists()
