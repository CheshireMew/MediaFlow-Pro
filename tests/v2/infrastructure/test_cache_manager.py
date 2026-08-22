from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from mediaflow.infrastructure.cache_manager import CacheManager


def test_cache_run_owned_by_current_process_cleans_up_normally(
    tmp_path: Path,
) -> None:
    manager = CacheManager(tmp_path / "cache")
    run = manager.create_run("exports")

    payload = json.loads((run / manager.RUN_MANIFEST).read_text(encoding="utf-8"))
    assert payload == {
        "schema_version": manager.RUN_SCHEMA_VERSION,
        "owner_pid": os.getpid(),
        "created_at_ns": payload["created_at_ns"],
    }
    assert payload["created_at_ns"] > 0

    manager.cleanup_run(run)

    assert not run.exists()


def test_cache_run_category_is_one_direct_child_of_cache_root(
    tmp_path: Path,
) -> None:
    manager = CacheManager(tmp_path / "cache")

    with pytest.raises(ValueError, match="one direct child"):
        manager.create_run("external/models")


def test_prune_runs_does_not_walk_opaque_nested_caches(tmp_path: Path) -> None:
    manager = CacheManager(tmp_path / "cache")
    opaque_run = manager.root / "huggingface" / "models" / "runs" / str(uuid.uuid4())
    opaque_run.mkdir(parents=True)
    (opaque_run / manager.RUN_MANIFEST).write_text(
        json.dumps(
            {
                "schema_version": manager.RUN_SCHEMA_VERSION,
                "owner_pid": 2_147_483_647,
                "created_at_ns": 1,
            }
        ),
        encoding="utf-8",
    )

    manager.prune_runs(max_age_seconds=0)

    assert opaque_run.is_dir()


def test_cache_run_manifest_failure_archives_unpublished_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mediaflow.infrastructure import cache_manager

    manager = CacheManager(tmp_path / "cache")

    def fail_manifest(*_args, **_kwargs) -> None:
        raise OSError("injected manifest write failure")

    monkeypatch.setattr(
        cache_manager,
        "atomic_write_text",
        fail_manifest,
    )
    with pytest.raises(
        OSError,
        match="manifest write failure",
    ):
        manager.create_run("exports")

    runs = manager.root / "exports" / "runs"
    assert not runs.exists() or not list(runs.iterdir())
    archived = list((manager.root / "archive" / "failed-run-creation").iterdir())
    assert len(archived) == 1
    assert archived[0].is_dir()


def test_prune_runs_observes_real_child_process_without_terminating_it(
    tmp_path: Path,
) -> None:
    cache_root = tmp_path / "cache"
    manager = CacheManager(cache_root)
    script = "\n".join(
        (
            "from pathlib import Path",
            "import sys",
            "from mediaflow.infrastructure.cache_manager import CacheManager",
            "run = CacheManager(Path(sys.argv[1])).create_run('child')",
            "print(run, flush=True)",
            "sys.stdin.readline()",
        )
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-c",
            script,
            str(cache_root),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        line = process.stdout.readline().strip()
        if not line:
            assert process.stderr is not None
            pytest.fail("Child did not create a cache run: " + process.stderr.read())
        run = Path(line)

        manager.prune_runs(max_age_seconds=0)

        assert process.poll() is None
        assert run.is_dir()

        assert process.stdin is not None
        process.stdin.write("\n")
        process.stdin.flush()
        process.wait(timeout=10)
        assert process.returncode == 0

        manager.prune_runs(max_age_seconds=0)

        assert not run.exists()
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
        if process.stdin is not None:
            process.stdin.close()
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()


def test_prune_and_cleanup_preserve_unowned_run_like_directories(
    tmp_path: Path,
) -> None:
    manager = CacheManager(tmp_path / "cache")
    runs = manager.root / "imports" / "runs"
    missing_manifest = runs / "missing-manifest"
    corrupt_manifest = runs / "corrupt-manifest"
    missing_manifest.mkdir(parents=True)
    corrupt_manifest.mkdir()
    (corrupt_manifest / manager.RUN_MANIFEST).write_text(
        "{not-json",
        encoding="utf-8",
    )

    wrong_schema = runs / str(uuid.uuid4())
    wrong_schema.mkdir()
    (wrong_schema / manager.RUN_MANIFEST).write_text(
        json.dumps(
            {
                "schema_version": manager.RUN_SCHEMA_VERSION + 1,
                "owner_pid": os.getpid(),
                "created_at_ns": time.time_ns(),
            }
        ),
        encoding="utf-8",
    )

    non_uuid = runs / "looks-like-a-run"
    non_uuid.mkdir()
    (non_uuid / manager.RUN_MANIFEST).write_text(
        json.dumps(
            {
                "schema_version": manager.RUN_SCHEMA_VERSION,
                "owner_pid": os.getpid(),
                "created_at_ns": time.time_ns(),
            }
        ),
        encoding="utf-8",
    )
    unowned = (
        missing_manifest,
        corrupt_manifest,
        wrong_schema,
        non_uuid,
    )

    manager.prune_runs(max_age_seconds=0)

    assert all(path.is_dir() for path in unowned)
    for path in unowned:
        with pytest.raises(
            ValueError,
            match="not owned by the current run-manifest schema",
        ):
            manager.cleanup_run(path)
        assert path.is_dir()


def test_prune_files_tolerates_a_candidate_disappearing_during_collection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = CacheManager(tmp_path / "cache")
    directory = manager.root / "previews"
    directory.mkdir(parents=True)
    unstable = directory / "unstable.mlt"
    unstable.write_text("preview", encoding="utf-8")
    (directory / "stable.mlt").write_text("preview", encoding="utf-8")
    original_stat = Path.stat
    unstable_stat_calls = 0

    def occasionally_missing(path: Path, *args, **kwargs):
        nonlocal unstable_stat_calls
        if path == unstable:
            unstable_stat_calls += 1
            if unstable_stat_calls == 2:
                raise FileNotFoundError(unstable)
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", occasionally_missing)

    manager.prune_files(
        "previews",
        "*.mlt",
        keep=10,
        max_age_seconds=0,
    )

    assert unstable_stat_calls == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows extended path syntax")
def test_managed_path_accepts_the_same_root_with_windows_extended_prefix(
    tmp_path: Path,
) -> None:
    manager = CacheManager(tmp_path / "cache")
    managed = Path("\\\\?\\" + str(manager.root / "asr" / "runs"))
    manager._require_managed(managed)

    outside = Path("\\\\?\\" + str(tmp_path.parent / "outside"))
    with pytest.raises(ValueError, match="outside the managed root"):
        manager._require_managed(outside)


def test_size_pruning_is_throttled_across_frequent_cache_requests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = CacheManager(tmp_path / "cache")
    scans: list[tuple[Path, int]] = []

    def capture_scan(relative_directory: Path, *, maximum_bytes: int) -> None:
        scans.append((relative_directory, maximum_bytes))

    monkeypatch.setattr(manager, "prune_directory_to_size", capture_scan)

    assert manager.prune_directory_to_size_throttled(
        Path("filmstrips"),
        maximum_bytes=1024,
    )
    assert not manager.prune_directory_to_size_throttled(
        Path("filmstrips"),
        maximum_bytes=1024,
    )
    assert scans == [(Path("filmstrips"), 1024)]
