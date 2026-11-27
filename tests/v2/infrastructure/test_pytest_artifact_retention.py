from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mediaflow.domain.storage_names import python_io_path, utf16_units
from tests.pytest_artifacts import (
    SCHEMA,
    RetentionPolicy,
    collect_runs,
    process_owner,
    remove_owned,
    trim_run_cases,
)


def make_run(
    root: Path, number: int, *, status: str = "failed", days: int = 0,
    active: bool = False, size: int = 20,
) -> Path:
    run = root / f"r-{number:012x}"
    case = run / "c" / "c-000000000001"
    case.mkdir(parents=True)
    (case / "artifact.bin").write_bytes(b"x" * size)
    (run / "run-result.json").write_text(json.dumps({
        "schema": SCHEMA, "run_id": run.name, "managed": True, "status": status,
        "finished_at": (datetime.now(UTC) - timedelta(days=days)).isoformat(),
        "owner": process_owner() if active else {"pid": 2_147_483_647, "process_started": 1.0},
        "cases": {"test_example": "c/c-000000000001"},
        "failed_nodes": [] if status == "passed" else ["test_example"],
    }), encoding="utf-8")
    return run


def test_successful_legacy_run_releases_artifacts_but_keeps_small_receipt(tmp_path: Path) -> None:
    root = tmp_path / "p"
    run = make_run(root, 1, status="passed")

    collect_runs(root, RetentionPolicy())

    assert not (run / "c" / "c-000000000001").exists()
    receipt = json.loads((run / "run-result.json").read_text())
    assert receipt["cases"] == {}
    assert receipt["retained_case_count"] == 0


def test_retention_keeps_only_recent_failed_runs_and_bounded_success_receipts(tmp_path: Path) -> None:
    root = tmp_path / "p"
    old = make_run(root, 1, days=8)
    previous = make_run(root, 2, days=2)
    latest = make_run(root, 3)
    passed_old = make_run(root, 4, status="passed", days=2)
    passed_new = make_run(root, 5, status="passed")

    result = collect_runs(root, RetentionPolicy(passed_runs=1, failed_runs=1))

    assert not result["errors"]
    assert not old.exists()
    assert not previous.exists()
    assert latest.exists()
    assert not passed_old.exists()
    assert passed_new.exists()


def test_active_and_unregistered_directories_are_never_collected(tmp_path: Path) -> None:
    root = tmp_path / "p"
    active = make_run(root, 1, status="running", days=30, active=True)
    unknown = root / "user-media"
    unknown.mkdir()
    (unknown / "original.wav").write_bytes(b"original")
    malformed = make_run(root, 2)
    (malformed / "run-result.json").write_text("broken json")

    collect_runs(root, RetentionPolicy(passed_runs=0, failed_runs=0, failed_bytes=0))

    assert active.exists()
    assert (unknown / "original.wav").read_bytes() == b"original"
    assert malformed.exists()


def test_stale_running_process_can_be_reclaimed_after_retention_expires(tmp_path: Path) -> None:
    root = tmp_path / "p"
    dead = make_run(root, 1, status="running", days=9)

    collect_runs(root, RetentionPolicy())

    assert not dead.exists()


def test_retained_failures_respect_total_byte_budget(tmp_path: Path) -> None:
    root = tmp_path / "p"
    previous = make_run(root, 1, days=1, size=2000)
    latest = make_run(root, 2, size=2000)

    collect_runs(root, RetentionPolicy(failed_bytes=3500))

    assert latest.exists()
    assert not previous.exists()


def test_current_failure_counts_toward_the_same_retention_budget(tmp_path: Path) -> None:
    root = tmp_path / "p"
    previous = make_run(root, 1, days=1, size=2000)
    current = make_run(root, 2, active=True, size=2000)

    collect_runs(root, RetentionPolicy(failed_bytes=3500), exclude=current)

    assert current.exists()
    assert not previous.exists()


def test_current_success_counts_toward_the_receipt_limit(tmp_path: Path) -> None:
    root = tmp_path / "p"
    previous = make_run(root, 1, status="passed", days=1)
    current = make_run(root, 2, status="passed", active=True)

    collect_runs(root, RetentionPolicy(passed_runs=1), exclude=current)

    assert current.exists()
    assert not previous.exists()


def test_single_large_failure_discards_heavy_artifact_and_keeps_manifest(tmp_path: Path) -> None:
    root = tmp_path / "p"
    run = make_run(root, 1, size=2000)

    errors = trim_run_cases(root, run, RetentionPolicy(failed_bytes=1000))

    assert errors == []
    assert (run / "run-result.json").is_file()
    assert not (run / "c" / "c-000000000001").exists()


def test_cleanup_rejects_paths_outside_registered_run(tmp_path: Path) -> None:
    root = tmp_path / "p"
    run = make_run(root, 1)
    source = tmp_path / "source"
    source.mkdir()
    (source / "valuable.txt").write_text("keep")

    with pytest.raises(ValueError, match="outside"):
        remove_owned(root, run, source)

    assert (source / "valuable.txt").read_text() == "keep"


@pytest.mark.parametrize("long_path", [False, True])
def test_readonly_copied_git_object_is_reclaimed(tmp_path: Path, long_path: bool) -> None:
    root = tmp_path / "p"
    run = make_run(root, 1)
    copied_object = run / "c" / "c-000000000001" / "git-object"
    if long_path:
        copied_object = copied_object.with_name("x" * (264 - utf16_units(str(copied_object.parent)) - 1))
        assert utf16_units(str(copied_object)) == 264
    python_io_path(copied_object).write_bytes(b"readonly copied object")
    python_io_path(copied_object).chmod(stat.S_IREAD)

    result = collect_runs(root, RetentionPolicy(failed_runs=0))

    assert result["errors"] == []
    assert not run.exists()


def test_legacy_receipt_with_reused_live_pid_is_not_kept_forever(tmp_path: Path) -> None:
    root = tmp_path / "p"
    run = make_run(root, 1, days=30)
    path = run / "run-result.json"
    receipt = json.loads(path.read_text())
    receipt.pop("owner")
    receipt["process_id"] = os.getpid()
    path.write_text(json.dumps(receipt))

    collect_runs(root, RetentionPolicy())

    assert not run.exists()


@pytest.mark.skipif(os.name != "nt", reason="Current platform is Windows")
def test_cleanup_unlinks_child_junction_without_touching_shared_target(tmp_path: Path) -> None:
    import _winapi

    root = tmp_path / "p"
    run = make_run(root, 1)
    shared = tmp_path / "shared-models"
    shared.mkdir()
    (shared / "model.bin").write_bytes(b"shared model")
    _winapi.CreateJunction(str(shared), str(run / "linked-models"))

    collect_runs(root, RetentionPolicy(failed_runs=0))

    assert not run.exists()
    assert (shared / "model.bin").read_bytes() == b"shared model"


@pytest.mark.skipif(os.name != "nt", reason="Current platform is Windows")
def test_cleanup_rejects_linked_managed_root(tmp_path: Path) -> None:
    import _winapi

    actual = tmp_path / "actual"
    actual.mkdir()
    linked = tmp_path / "p"
    _winapi.CreateJunction(str(actual), str(linked))

    with pytest.raises(ValueError, match="directory link"):
        collect_runs(linked, RetentionPolicy())


@pytest.mark.parametrize("phase", ["call", "teardown"])
def test_real_pytest_retains_failure_and_immediately_reclaims_passed_case(
    tmp_path: Path, phase: str,
) -> None:
    source = tmp_path / "sample.py"
    failure = (
        "def test_failure(tmp_path):\n    (tmp_path / 'failure.txt').write_text('diagnostic')\n"
        "    assert False, 'intentional failure'\n"
        if phase == "call" else
        "@pytest.fixture\ndef bad_teardown(tmp_path):\n"
        "    (tmp_path / 'failure.txt').write_text('diagnostic')\n    yield\n"
        "    raise RuntimeError('intentional teardown failure')\n"
        "def test_failure(bad_teardown):\n    pass\n"
    )
    source.write_text(
        "import pytest\ndef test_success(tmp_path):\n"
        "    (tmp_path / 'success.txt').write_text('temporary')\n" + failure,
        encoding="utf-8",
    )
    root = tmp_path / "child-artifacts"
    environment = dict(os.environ, MEDIAFLOW_TEST_ROOT=str(root), MEDIAFLOW_MINIMUM_FREE_BYTES="1")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "tests.v2.conftest", "--confcutdir", str(tmp_path),
         "-o", "addopts=", "-q", str(source)],
        cwd=Path(__file__).resolve().parents[3], env=environment,
        capture_output=True, text=True, timeout=45,
    )

    assert result.returncode == 1, result.stdout[-3000:] + result.stderr[-3000:]
    manifests = list((root / "p").glob("r-*/run-result.json"))
    assert len(manifests) == 1
    receipt = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert receipt["status"] == "failed"
    assert receipt["retained_case_count"] == 1
    assert list(root.rglob("success.txt")) == []
    assert len(list(root.rglob("failure.txt"))) == 1
