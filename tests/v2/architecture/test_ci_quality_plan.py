from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts.ci.install_maintenance_tools import locked_requirement_block
from scripts.ci.prepare_python_environment import (
    STATE_FILE,
    cached_environment_is_current,
    environment_python,
    expected_state,
)
from scripts.ci.quality_plan import forced_plan, normalize_path, plan_for_paths
from scripts.ci.run_quality import (
    build_quality_stages,
    local_changed_paths,
    quality_expected_bytes,
    quality_run_roots,
    recommended_runtime_workers,
)
from scripts.ci.test_resources import requires_reviewed_runtime, select_resource_profile
from scripts.ci.test_shard import (
    node_matches_prefix,
    normalize_collected_node_id,
    partition_test_nodes,
    source_file_for_node,
)

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github" / "workflows" / "quality.yml"


@pytest.mark.parametrize(
    ("paths", "scope", "portable_build", "interchange"),
    (
        (("README.md",), "maintenance", False, False),
        (("README.en.md", "README.ja.md", "CONTRIBUTING.md"), "maintenance", False, False),
        (("LICENSE", "docs/readme-preview.png"), "maintenance", False, False),
        ((".github/workflows/star-history.yml",), "maintenance", False, False),
        (("AGENTS.md", "ARCHITECTURE.md"), "maintenance", False, False),
        (("mediaflow/domain/tasks.py",), "core", False, False),
        (("mediaflow/application/task_service.py",), "core", False, False),
        (("mediaflow/composition.py",), "full", False, False),
        (("mediaflow/domain/project.py",), "full", False, True),
        (("mediaflow/infrastructure/project_repository.py",), "full", False, True),
        (("mediaflow/infrastructure/runtime_paths.py",), "full", True, False),
        (("mediaflow/desktop/native/MltRuntime.cpp",), "full", True, False),
        (("tests/v2/integration/test_native_preview.py",), "full", True, False),
        ((".github/workflows/quality.yml",), "full", True, True),
        (("scripts/ci/quality_plan.py",), "full", True, True),
        ((".env.example",), "full", True, False),
        (("requirements.lock",), "full", True, True),
        (("runtime.lock.json",), "full", True, True),
        (("scripts/verify_project_interchange.py",), "full", False, True),
        (("unclassified/release-input.bin",), "full", False, False),
    ),
)
def test_quality_scope_fails_closed_without_over_testing_maintenance_changes(
    paths: tuple[str, ...],
    scope: str,
    portable_build: bool,
    interchange: bool,
) -> None:
    plan = plan_for_paths(paths)

    assert plan.scope == scope
    assert plan.run_core is (scope != "maintenance")
    assert plan.run_full is (scope == "full")
    assert plan.run_portable_build is portable_build
    assert plan.run_interchange is interchange


def test_mixed_changes_keep_the_strongest_required_scope() -> None:
    plan = plan_for_paths(("README.md", "mediaflow/application/task_service.py"))

    assert plan.scope == "core"
    assert plan.changed_count == 2


def test_empty_or_manual_full_plans_cannot_silently_skip_release_boundaries() -> None:
    assert plan_for_paths(()).scope == "full"
    forced = forced_plan("full", reason="test")
    assert forced.run_portable_build is True
    assert forced.run_interchange is True


def test_windows_paths_are_normalized_before_classification() -> None:
    assert normalize_path(r"mediaflow\desktop\native\MltRuntime.cpp") == (
        "mediaflow/desktop/native/mltruntime.cpp"
    )


def test_test_node_shards_are_deterministic_disjoint_complete_and_split_large_files() -> None:
    nodes = tuple(
        [f"tests/v2/test_large.py::test_case_{index}" for index in range(12)]
        + [f"tests/v2/test_small_{index}.py::test_case" for index in range(4)]
    )
    source_sizes = {
        "tests/v2/test_large.py": 12_000,
        **{f"tests/v2/test_small_{index}.py": 100 for index in range(4)},
    }
    first = partition_test_nodes(nodes, 4, source_sizes=source_sizes)
    second = partition_test_nodes(tuple(reversed(nodes)), 4, source_sizes=source_sizes)
    flattened = [node for partition in first for node in partition]

    assert first == second
    assert len(flattened) == len(set(flattened))
    assert set(flattened) == set(nodes)
    assert all(partition for partition in first)
    large_counts = [
        sum(source_file_for_node(node) == "tests/v2/test_large.py" for node in partition)
        for partition in first
    ]
    assert max(large_counts) - min(large_counts) <= 1
    raw_node = r"tests\v2\test_large.py::test_case[param-\u4e2d]"
    assert normalize_collected_node_id(raw_node) == (r"tests/v2/test_large.py::test_case[param-\u4e2d]")
    assert node_matches_prefix(
        "tests/v2/test_large.py::test_case[param-1]",
        "tests/v2/test_large.py::test_case",
    )
    assert not node_matches_prefix(
        "tests/v2/test_large.py::test_case_other[param-1]",
        "tests/v2/test_large.py::test_case",
    )
    timed = partition_test_nodes(
        nodes,
        4,
        timing_weights={nodes[0]: 100.0},
        default_timing=0.25,
    )
    assert sum(nodes[0] in partition for partition in timed) == 1


def test_resource_profiles_are_one_disjoint_boundary_without_marker_churn() -> None:
    nodes = (
        "tests/v2/domain/test_models.py::test_model",
        "tests/v2/application/test_task_service.py::test_task",
        "tests/v2/infrastructure/test_mlt_export.py::test_export",
        "tests/v2/infrastructure/test_editable_media_v6_runtime.py::test_protocol",
        "tests/v2/application/test_portable_timeline_import.py::test_import",
        "tests/v2/desktop/test_qml_smoke.py::test_window",
        "tests/v2/application/test_editor_api.py::test_preview_frame_render_returns_real_revision_bound_png_evidence",
    )

    lightweight = select_resource_profile(nodes, "lightweight")
    runtime = select_resource_profile(nodes, "runtime")

    assert set(lightweight).isdisjoint(runtime)
    assert set(lightweight) | set(runtime) == set(nodes)
    assert requires_reviewed_runtime(nodes[2]) is True
    assert requires_reviewed_runtime(nodes[3]) is True
    assert requires_reviewed_runtime(nodes[4]) is True
    assert requires_reviewed_runtime(nodes[5]) is True
    assert requires_reviewed_runtime(nodes[-1]) is True
    assert requires_reviewed_runtime(nodes[0]) is False


def test_local_changed_paths_never_selects_ignored_quality_evidence() -> None:
    assert not any(path.startswith(".test-runs/") for path in local_changed_paths("HEAD"))


def test_quality_work_root_uses_an_owned_short_namespace(tmp_path: Path) -> None:
    now = datetime(2026, 8, 16, tzinfo=UTC)

    run_root, work_root = quality_run_roots(tmp_path, now, 1234)

    assert run_root.is_relative_to(tmp_path.resolve())
    assert work_root.is_relative_to(tmp_path.resolve())
    assert not work_root.is_relative_to(run_root)
    assert run_root.parent == tmp_path.resolve() / "q"
    assert work_root.parent == tmp_path.resolve() / "qw"
    assert len(work_root.name) == 8
    assert len(run_root.name) <= 28


def test_quality_peak_estimates_are_explicit_and_bounded() -> None:
    maintenance = quality_expected_bytes(forced_plan("maintenance", reason="test"))
    core = quality_expected_bytes(forced_plan("core", reason="test"))
    full = quality_expected_bytes(forced_plan("full", reason="test"))

    assert 0 < maintenance < core < full
    assert full == 16 * 1024**3


@pytest.mark.parametrize(
    ("cpu_count", "physical_gib", "commit_gib", "expected"),
    (
        (16, 30, 30, 4),
        (16, 30, 20, 2),
        (16, 10, 30, 1),
        (8, 16, 16, 2),
        (4, 64, 64, 1),
    ),
)
def test_runtime_parallelism_respects_physical_and_commit_headroom(
    cpu_count: int,
    physical_gib: int,
    commit_gib: int,
    expected: int,
) -> None:
    gib = 1024**3
    assert (
        recommended_runtime_workers(
            cpu_count=cpu_count,
            available_memory=physical_gib * gib,
            available_commit_memory=commit_gib * gib,
        )
        == expected
    )


def test_local_quality_runner_is_change_scoped_parallel_and_never_monolithic(
    tmp_path: Path,
) -> None:
    maintenance = build_quality_stages(
        forced_plan("maintenance", reason="test"),
        run_root=tmp_path,
        max_runtime_workers=2,
    )
    core = build_quality_stages(
        forced_plan("core", reason="test"),
        run_root=tmp_path,
        max_runtime_workers=2,
    )
    full = build_quality_stages(
        forced_plan("full", reason="test"),
        run_root=tmp_path,
        max_runtime_workers=2,
    )

    assert [stage.name for stage in maintenance] == ["maintenance"]
    assert [stage.name for stage in core] == [
        "maintenance",
        "preflight",
        "core",
        "core-serial",
    ]
    assert [stage.name for stage in full] == [
        "maintenance",
        "preflight",
        "core",
        "core-serial",
        "runtime",
        "interactive-verifiers",
        "interactive-qml",
        "interactive-chains",
        "offline-preflight",
        "offline-parallel",
        "offline-final",
    ]
    assert next(stage for stage in core if stage.name == "core").max_workers == 2
    runtime = next(stage for stage in full if stage.name == "runtime")
    assert runtime.max_workers == 2
    assert len(runtime.commands) == 4
    interactive_qml = next(stage for stage in full if stage.name == "interactive-qml")
    assert interactive_qml.max_workers == 1
    assert [command.name for command in interactive_qml.commands] == ["qml-project-chain"]
    assert next(stage for stage in full if stage.name == "interactive-chains").max_workers == 2
    assert next(stage for stage in full if stage.name == "offline-parallel").max_workers == 2
    command_lines = [command.arguments for stage in full for command in stage.commands]
    assert not any(
        arguments[:4]
        == (
            arguments[0],
            "-m",
            "pytest",
            "tests/v2",
        )
        for arguments in command_lines
        if len(arguments) >= 4
    )
    assert len({command.name for stage in full for command in stage.commands}) == sum(
        len(stage.commands) for stage in full
    )


def test_complete_python_environment_state_rejects_a_different_lock(tmp_path: Path) -> None:
    requirements = tmp_path / "requirements.lock"
    requirements.write_text("example==1 --hash=sha256:abc\n", encoding="utf-8")
    environment = tmp_path / "environment"
    python = environment_python(environment)
    python.parent.mkdir(parents=True)
    python.touch()
    (environment / STATE_FILE).write_text(
        json.dumps(expected_state(requirements)),
        encoding="utf-8",
    )

    assert cached_environment_is_current(environment, requirements) is True

    requirements.write_text("example==2 --hash=sha256:def\n", encoding="utf-8")
    assert cached_environment_is_current(environment, requirements) is False


def test_maintenance_yaml_dependency_is_derived_from_the_reviewed_lock() -> None:
    requirement = locked_requirement_block("pyyaml")

    assert requirement.startswith("pyyaml==6.0.3 \\")
    assert "--hash=sha256:" in requirement


def test_workflow_consumes_one_plan_and_keeps_expensive_boundaries_conditional() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "python scripts/ci/quality_plan.py" in workflow
    assert "needs.plan.outputs.run_core == 'true'" in workflow
    assert "needs.plan.outputs.run_full == 'true'" in workflow
    assert "needs.plan.outputs.run_portable_build == 'true'" in workflow
    assert "needs.plan.outputs.run_interchange == 'true'" in workflow
    assert "needs.plan.outputs.run_portable == 'true'" not in workflow
    assert "python -m scripts.ci.test_shard" in workflow
    assert '--marker "not integration and not slow"' in workflow
    assert "--resource-profile lightweight" in workflow
    assert "--resource-profile runtime" in workflow
    assert "--timings-file scripts/ci/test_timings.windows.json" in workflow
    assert "--exclude-file tests/v2/architecture/test_ci_quality_plan.py" in workflow
    assert workflow.count("python scripts/ci/install_maintenance_tools.py") == 2
    assert "python -m pytest tests/v2 -m" not in workflow
    assert "cancel-in-progress:" in workflow
    assert "V2 quality gate" in workflow


def test_complete_python_cache_is_reused_and_lightweight_core_has_no_media_sdk_setup() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    core = workflow.split("  core-tests:", 1)[1].split("  runtime-tests:", 1)[0]

    assert workflow.count("key: mediaflow-python-v1-") == 7
    assert workflow.count("python scripts/ci/prepare_python_environment.py") == 7
    assert workflow.count("steps.python-environment.outputs.cache-hit == 'true'") == 7
    assert "--resource-profile lightweight" in core
    assert "prepare_runtime.py" not in core
    assert "prepare_ci_qt.py" not in core
    assert "build_native.py" not in core
    assert "mediaflow-runtime\\deps" not in core
    assert "mediaflow-runtime\\qt" not in core
    assert "mediaflow-runtime\\native" not in core
    assert workflow.count("id: native-preview") == 3
    assert workflow.count("if: steps.native-preview.outputs.cache-hit != 'true'") == 3


def test_portable_native_preview_smoke_precedes_expensive_contracts() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    portable = workflow.split("  portable-core:", 1)[1].split("  interchange-produce:", 1)[0]

    smoke = portable.index("Run native preview smoke before portable contracts")
    contracts = portable.index("Verify portable contracts and service core")
    type_check = portable.index("Type-check on the actual target platform")
    cli = portable.index("Run the real CLI and Chromium chains")
    assert smoke < contracts < type_check < cli
    assert portable.count("tests/v2/integration/test_native_preview.py") == 1
    assert "needs.plan.outputs.run_portable_build == 'true'" in portable


def test_project_interchange_has_an_independent_trigger_and_no_browser_setup() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    interchange = workflow.split("  interchange-produce:", 1)[1].split("  quality-gate:", 1)[0]

    assert interchange.count("needs.plan.outputs.run_interchange == 'true'") == 2
    assert "playwright install-deps chromium" not in interchange
    assert workflow.count("playwright install-deps chromium") == 1


def test_browser_scenarios_and_failure_diagnostics_have_process_boundaries() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert workflow.count("test_unified_import_opens_the_v6_package_through_local_preview_server") == 1
    assert workflow.count("test_real_dom_drag_crosses_webchannel_persists_and_is_read_back_by_page") == 1
    assert "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a" in workflow
    assert "if-no-files-found: ignore" in workflow


def test_runtime_caches_follow_the_artifact_that_actually_invalidates() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "mediaflow-deps-v2-" in workflow
    assert "mediaflow-qt-v2-" in workflow
    assert "mediaflow-native-v2-" in workflow
    assert "hashFiles('runtime.lock.json')" in workflow
    assert "hashFiles('runtime.lock.json', 'requirements.lock', 'scripts/build_native.py'" in workflow
