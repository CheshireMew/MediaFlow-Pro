from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TEST_ROOT = REPOSITORY_ROOT / "tests" / "v2"
DEFAULT_TIMINGS_FILE = Path(__file__).with_name("test_timings.windows.json")
TIMINGS_SCHEMA = "mediaflow-pytest-timings/v1"


def source_file_for_node(node_id: str) -> str:
    source, separator, _ = node_id.partition("::")
    if not separator:
        raise ValueError(f"Invalid pytest node id: {node_id}")
    return source.replace("\\", "/")


def normalize_collected_node_id(node_id: str) -> str:
    source, separator, test_id = node_id.partition("::")
    if not separator:
        raise ValueError(f"Invalid pytest node id: {node_id}")
    return f"{source.replace('\\', '/')}::{test_id}"


def discover_test_nodes(
    *,
    marker: str | None = None,
    excluded_files: Sequence[str] = (),
) -> tuple[str, ...]:
    command = [
        sys.executable,
        "-m",
        "pytest",
        TEST_ROOT.relative_to(REPOSITORY_ROOT).as_posix(),
        "--collect-only",
        "-q",
        "-o",
        "addopts=",
    ]
    if marker:
        command.extend(("-m", marker))
    completed = subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        details = completed.stdout + completed.stderr
        raise RuntimeError(f"pytest collection failed:\n{details}")
    excluded = {path.replace("\\", "/") for path in excluded_files}
    nodes = set()
    for line in completed.stdout.splitlines():
        candidate = line.strip()
        if "::" not in candidate:
            continue
        normalized = normalize_collected_node_id(candidate)
        if normalized.startswith("tests/v2/"):
            nodes.add(normalized)
    selected = tuple(
        sorted(node for node in nodes if source_file_for_node(node) not in excluded)
    )
    if not selected:
        raise RuntimeError("pytest collection selected no test nodes")
    return selected


def load_timing_weights(path: Path) -> tuple[dict[str, float], float]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != TIMINGS_SCHEMA:
        raise ValueError(f"Unsupported pytest timing schema: {path}")
    default_seconds = payload.get("default_seconds")
    raw_nodes = payload.get("nodes")
    if not isinstance(default_seconds, int | float) or default_seconds <= 0:
        raise ValueError(f"Invalid default pytest duration: {path}")
    if not isinstance(raw_nodes, dict):
        raise ValueError(f"Invalid pytest timing node map: {path}")
    timings: dict[str, float] = {}
    for node, seconds in raw_nodes.items():
        if not isinstance(node, str) or not isinstance(seconds, int | float) or seconds <= 0:
            raise ValueError(f"Invalid pytest timing entry: {node!r}")
        timings[node] = float(seconds)
    return timings, float(default_seconds)


def partition_test_nodes(
    nodes: Sequence[str],
    shard_count: int,
    *,
    source_sizes: dict[str, int] | None = None,
    timing_weights: dict[str, float] | None = None,
    default_timing: float = 0.25,
) -> tuple[tuple[str, ...], ...]:
    if shard_count < 1:
        raise ValueError("shard_count must be positive")
    normalized = tuple(sorted(set(nodes)))
    if len(normalized) != len(nodes):
        raise ValueError("pytest node ids must be unique")
    node_weights: dict[str, float] = {}
    if timing_weights is not None:
        if default_timing <= 0:
            raise ValueError("default_timing must be positive")
        node_weights = {
            node: max(timing_weights.get(node, default_timing), 0.001) for node in normalized
        }
    else:
        nodes_per_source = Counter(source_file_for_node(node) for node in normalized)
        sizes = source_sizes or {}
        for node in normalized:
            source = source_file_for_node(node)
            source_path = REPOSITORY_ROOT / source
            source_size = sizes.get(
                source,
                source_path.stat().st_size if source_path.is_file() else 1,
            )
            node_weights[node] = max(
                1,
                (source_size + nodes_per_source[source] - 1) // nodes_per_source[source],
            )
    bins: list[list[str]] = [[] for _ in range(shard_count)]
    weights = [0.0] * shard_count
    for node in sorted(normalized, key=lambda item: (-node_weights[item], item)):
        index = min(range(shard_count), key=lambda candidate: (weights[candidate], candidate))
        bins[index].append(node)
        weights[index] += node_weights[node]
    return tuple(tuple(sorted(group)) for group in bins)


def relative_nodes(
    shard_index: int,
    shard_count: int,
    *,
    marker: str | None = None,
    excluded_files: Sequence[str] = (),
    timings_file: Path | None = DEFAULT_TIMINGS_FILE,
) -> tuple[str, ...]:
    if not 0 <= shard_index < shard_count:
        raise ValueError(f"shard_index must be between 0 and {shard_count - 1}")
    nodes = discover_test_nodes(marker=marker, excluded_files=excluded_files)
    timing_weights: dict[str, float] | None = None
    default_timing = 0.25
    if timings_file is not None and timings_file.is_file():
        timing_weights, default_timing = load_timing_weights(timings_file)
    return partition_test_nodes(
        nodes,
        shard_count,
        timing_weights=timing_weights,
        default_timing=default_timing,
    )[shard_index]


def self_test() -> None:
    nodes = tuple(
        [f"tests/v2/test_large.py::test_case_{index}" for index in range(12)]
        + [f"tests/v2/test_small_{index}.py::test_case" for index in range(4)]
    )
    source_sizes = {
        "tests/v2/test_large.py": 12_000,
        **{f"tests/v2/test_small_{index}.py": 100 for index in range(4)},
    }
    partitions = partition_test_nodes(nodes, 4, source_sizes=source_sizes)
    flattened = [node for partition in partitions for node in partition]
    if len(flattened) != len(set(flattened)):
        raise AssertionError("A test node was assigned to more than one shard")
    if set(flattened) != set(nodes):
        raise AssertionError("The shard plan did not cover every collected test node")
    if any(not partition for partition in partitions):
        raise AssertionError("Every configured shard must receive at least one test node")
    large_counts = [
        sum(source_file_for_node(node) == "tests/v2/test_large.py" for node in partition)
        for partition in partitions
    ]
    if max(large_counts) - min(large_counts) > 1:
        raise AssertionError("Nodes from a large test file were not distributed evenly")
    if DEFAULT_TIMINGS_FILE.is_file():
        load_timing_weights(DEFAULT_TIMINGS_FILE)
    print(f"test shard self-test passed ({len(nodes)} synthetic nodes across 4 shards)")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Select a deterministic V2 pytest shard")
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=4)
    parser.add_argument("--marker")
    parser.add_argument("--exclude-file", action="append", default=[])
    parser.add_argument("--timings-file", type=Path, default=DEFAULT_TIMINGS_FILE)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    arguments, pytest_arguments = parser.parse_known_args(argv)
    if pytest_arguments[:1] == ["--"]:
        pytest_arguments = pytest_arguments[1:]
    if arguments.self_test:
        self_test()
        return 0
    selected = relative_nodes(
        arguments.shard_index,
        arguments.shard_count,
        marker=arguments.marker,
        excluded_files=arguments.exclude_file,
        timings_file=arguments.timings_file,
    )
    if not arguments.run:
        print("\n".join(selected))
        return 0
    if not selected:
        raise RuntimeError(f"No tests were assigned to shard {arguments.shard_index}")
    command = [sys.executable, "-m", "pytest", *selected, *pytest_arguments]
    print(f"running shard {arguments.shard_index + 1}/{arguments.shard_count}: {len(selected)} nodes")
    return subprocess.run(command, cwd=REPOSITORY_ROOT, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
