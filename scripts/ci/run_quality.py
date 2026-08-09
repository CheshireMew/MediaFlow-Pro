from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import psutil

from scripts.ci.quality_plan import QualityPlan, forced_plan, plan_for_paths

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EXCLUDED_TESTS = (
    "tests/v2/architecture/test_ci_quality_plan.py",
    "tests/v2/architecture/test_ci_runtime.py",
    "tests/v2/infrastructure/test_environment.py",
    "tests/v2/application/test_cli_process.py",
)
LOCAL_SERIAL_TESTS = (
    "tests/v2/application/test_task_handler_commit_boundaries.py::"
    "test_visual_analysis_cancelled_at_saving_publishes_no_file_or_timeline_edit",
)
RUFF_TARGETS = (
    "mediaflow",
    "tests/v2",
    "scripts/ci",
    "scripts/update_qm_translations.py",
    "scripts/prepare_ci_qt.py",
    "scripts/verify_display_capabilities.py",
    "scripts/verify_development_runtime.py",
    "scripts/verify_desktop_cli_requests.py",
    "scripts/verify_license_inventory.py",
    "scripts/verify_performance.py",
    "scripts/verify_preview_performance.py",
    "scripts/verify_reference_comparison_chain.py",
    "scripts/verify_real_user_chain.py",
    "scripts/verify_release_runtime.py",
    "scripts/verify_ui_matrix.py",
    "scripts/verify_web_render_performance.py",
)


@dataclass(frozen=True, slots=True)
class QualityCommand:
    name: str
    arguments: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class QualityStage:
    name: str
    commands: tuple[QualityCommand, ...]
    max_workers: int = 1


@dataclass(frozen=True, slots=True)
class CommandResult:
    name: str
    status: str
    exit_code: int
    seconds: float
    log: str


class StageFailure(RuntimeError):
    def __init__(self, stage: str, results: tuple[CommandResult, ...]):
        self.results = results
        failures = ", ".join(result.name for result in results if result.exit_code != 0)
        super().__init__(f"Quality stage {stage} failed: {failures}")


def _python(*arguments: str) -> tuple[str, ...]:
    return (sys.executable, *arguments)


def _git(*arguments: str, check: bool = True) -> str:
    completed = subprocess.run(
        ("git", *arguments),
        cwd=REPOSITORY_ROOT,
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return completed.stdout.strip()


def default_base() -> str:
    completed = subprocess.run(
        ("git", "rev-parse", "--verify", "origin/main"),
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
    )
    return "origin/main" if completed.returncode == 0 else "HEAD"


def local_changed_paths(base: str) -> tuple[str, ...]:
    tracked = _git(
        "diff",
        "--name-only",
        "--no-renames",
        "--diff-filter=ACMRD",
        base,
    ).splitlines()
    untracked = _git("ls-files", "--others", "--exclude-standard").splitlines()
    return tuple(dict.fromkeys(path for path in (*tracked, *untracked) if path.strip()))


def _shard_command(profile: str, shard_index: int, shard_count: int) -> QualityCommand:
    arguments = [
        sys.executable,
        "-m",
        "scripts.ci.test_shard",
        "--shard-index",
        str(shard_index),
        "--shard-count",
        str(shard_count),
        "--resource-profile",
        profile,
        "--marker",
        "not integration and not slow",
        "--timings-file",
        "scripts/ci/test_timings.windows.json",
    ]
    for excluded in DEFAULT_EXCLUDED_TESTS:
        arguments.extend(("--exclude-file", excluded))
    if profile == "lightweight":
        for node in LOCAL_SERIAL_TESTS:
            arguments.extend(("--exclude-node", node))
    arguments.extend(("--run", "--", "-q", "--tb=short", "--durations=25"))
    return QualityCommand(f"{profile}-{shard_index + 1}-of-{shard_count}", tuple(arguments))


def maintenance_commands() -> tuple[QualityCommand, ...]:
    workflow_parser = (
        "from pathlib import Path; import yaml; "
        "[yaml.safe_load(path.read_text(encoding='utf-8')) "
        "for path in Path('.github/workflows').glob('*.y*ml')]"
    )
    return (
        QualityCommand(
            "quality-plan-self-test",
            _python("scripts/ci/quality_plan.py", "--self-test"),
        ),
        QualityCommand("workflow-yaml", _python("-c", workflow_parser)),
        QualityCommand(
            "repository-docs",
            _python("scripts/ci/verify_repository_docs.py"),
        ),
        QualityCommand(
            "shard-self-test",
            _python("-m", "scripts.ci.test_shard", "--self-test"),
        ),
    )


def preflight_commands() -> tuple[QualityCommand, ...]:
    contract_tests = (
        "tests/v2/architecture/test_ci_quality_plan.py",
        "tests/v2/architecture/test_ci_runtime.py",
        "tests/v2/infrastructure/test_environment.py",
        "tests/v2/application/test_cli_process.py",
    )
    return (
        QualityCommand(
            "compileall",
            _python("-m", "compileall", "-q", "mediaflow", "scripts", "tests/v2"),
        ),
        QualityCommand(
            "ruff",
            _python("-m", "ruff", "check", *RUFF_TARGETS),
        ),
        QualityCommand("mypy", _python("-m", "mypy")),
        QualityCommand(
            "preflight-contracts",
            _python("-m", "pytest", *contract_tests, "-q", "--tb=short", "--durations=25"),
        ),
    )


def interactive_verifier_commands() -> tuple[QualityCommand, ...]:
    return (
        QualityCommand(
            "development-runtime",
            _python("scripts/verify_development_runtime.py", "--profile", "core"),
        ),
        QualityCommand(
            "display-capabilities",
            _python("scripts/verify_display_capabilities.py"),
        ),
    )


def interactive_qml_commands() -> tuple[QualityCommand, ...]:
    return (
        QualityCommand(
            "qml-project-chain",
            _python(
                "-m",
                "pytest",
                "tests/v2/desktop/test_qml_smoke.py::test_qml_real_project_chain_is_visible_in_models",
                "-q",
                "--tb=short",
            ),
        ),
    )


def interactive_chain_commands() -> tuple[QualityCommand, ...]:
    # Longest independent chains go first so the two workers finish together.
    # The order follows the latest Windows full-run timings.
    return (
        QualityCommand(
            "desktop-cli-requests",
            _python("-m", "scripts.verify_desktop_cli_requests"),
        ),
        QualityCommand(
            "web-editor-import",
            _python(
                "-m",
                "pytest",
                "tests/v2/desktop/test_web_editor.py::test_unified_import_opens_the_v6_package_through_local_preview_server",
                "-q",
                "--tb=short",
            ),
        ),
        QualityCommand(
            "drag-import-scrub",
            _python(
                "-m",
                "pytest",
                "tests/v2/desktop/test_qml_smoke.py::test_drag_import_placement_snap_tracks_and_first_video_profile",
                "-q",
                "--tb=short",
            ),
        ),
        QualityCommand(
            "web-editor-drag",
            _python(
                "-m",
                "pytest",
                "tests/v2/desktop/test_web_editor.py::test_real_dom_drag_crosses_webchannel_persists_and_is_read_back_by_page",
                "-q",
                "--tb=short",
            ),
        ),
        QualityCommand(
            "chromium-native-integration",
            _python(
                "-m",
                "pytest",
                "tests/v2/infrastructure/test_web_capture_engine.py::test_real_react_retryable_frame_replaces_page_and_preserves_order",
                "tests/v2/infrastructure/test_web_capture_engine.py::test_real_draw_element_failure_requires_clean_screenshot_retry",
                "tests/v2/integration/test_native_preview.py",
                "-q",
                "--tb=short",
            ),
        ),
    )


def local_serial_commands() -> tuple[QualityCommand, ...]:
    return (
        QualityCommand(
            "local-timing-sensitive-tests",
            _python(
                "-m",
                "pytest",
                *LOCAL_SERIAL_TESTS,
                "-q",
                "--tb=short",
                "--durations=10",
            ),
        ),
    )


def offline_preflight_commands(run_root: Path) -> tuple[QualityCommand, ...]:
    return (
        QualityCommand(
            "license-inventory",
            _python(
                "scripts/verify_license_inventory.py",
                "--output-dir",
                str(run_root / "license"),
            ),
        ),
        QualityCommand("performance", _python("-m", "scripts.verify_performance")),
        QualityCommand(
            "preview-performance",
            _python(
                "-m",
                "scripts.verify_preview_performance",
                "--duration-seconds",
                "20",
                "--playback-check-seconds",
                "5",
            ),
        ),
    )


def offline_parallel_commands() -> tuple[QualityCommand, ...]:
    return (
        QualityCommand("ui-matrix", _python("-m", "scripts.verify_ui_matrix")),
        QualityCommand(
            "reference-comparison",
            _python(
                "-m",
                "scripts.verify_reference_comparison_chain",
                "--package",
                "tests/fixtures/editable-media-v6",
            ),
        ),
    )


def offline_final_commands() -> tuple[QualityCommand, ...]:
    return (
        QualityCommand(
            "web-render-performance",
            _python("-m", "scripts.verify_web_render_performance"),
        ),
    )


def recommended_runtime_workers() -> int:
    cpu_count = os.cpu_count() or 1
    available_memory = psutil.virtual_memory().available
    if cpu_count >= 12 and available_memory >= 12 * 1024**3:
        return 4
    if cpu_count >= 6 and available_memory >= 8 * 1024**3:
        return 2
    return 1


def build_quality_stages(
    plan: QualityPlan,
    *,
    run_root: Path,
    max_runtime_workers: int,
) -> tuple[QualityStage, ...]:
    stages = [QualityStage("maintenance", maintenance_commands())]
    if plan.run_core:
        stages.append(QualityStage("preflight", preflight_commands()))
        stages.append(
            QualityStage(
                "core",
                tuple(_shard_command("lightweight", index, 2) for index in range(2)),
                max_workers=2,
            )
        )
        stages.append(QualityStage("core-serial", local_serial_commands()))
    if plan.run_full:
        stages.append(
            QualityStage(
                "runtime",
                tuple(_shard_command("runtime", index, 4) for index in range(4)),
                max_workers=max_runtime_workers,
            )
        )
        stages.append(
            QualityStage("interactive-verifiers", interactive_verifier_commands())
        )
        stages.append(QualityStage("interactive-qml", interactive_qml_commands()))
        stages.append(
            QualityStage(
                "interactive-chains",
                interactive_chain_commands(),
                max_workers=2,
            )
        )
        stages.append(
            QualityStage("offline-preflight", offline_preflight_commands(run_root))
        )
        stages.append(
            QualityStage(
                "offline-parallel",
                offline_parallel_commands(),
                max_workers=2,
            )
        )
        stages.append(QualityStage("offline-final", offline_final_commands()))
    return tuple(stages)


def _command_root(work_root: Path, command: QualityCommand) -> Path:
    identity = hashlib.sha256(command.name.encode("utf-8")).hexdigest()[:8]
    root = work_root / identity
    for directory in ("t", "s", "p", "m"):
        (root / directory).mkdir(parents=True, exist_ok=True)
    return root


def _run_command(
    command: QualityCommand,
    run_root: Path,
    work_root: Path,
) -> CommandResult:
    root = _command_root(work_root, command)
    log_path = run_root / "logs" / f"{command.name}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment.update(
        {
            "MEDIAFLOW_TEST_ROOT": str(root / "t"),
            "MEDIAFLOW_SERVICE_STATE_DIR": str(root / "s"),
            "MEDIAFLOW_PROJECT_ROOT": str(root / "p"),
            "MEDIAFLOW_MEDIA_ROOT": str(root / "m"),
            "PYTHONFAULTHANDLER": "1",
            "PYTHONUTF8": "1",
            "QT_QPA_PLATFORM": environment.get("QT_QPA_PLATFORM", "offscreen"),
            "QTWEBENGINE_CHROMIUM_FLAGS": environment.get(
                "QTWEBENGINE_CHROMIUM_FLAGS",
                "--disable-gpu",
            ),
        }
    )
    print(f"\n[{command.name}] {' '.join(command.arguments)}", flush=True)
    started = time.perf_counter()
    with log_path.open("w", encoding="utf-8", errors="replace") as log:
        process = subprocess.Popen(
            command.arguments,
            cwd=REPOSITORY_ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            log.write(line)
            log.flush()
            print(f"[{command.name}] {line}", end="", flush=True)
        exit_code = process.wait()
    seconds = round(time.perf_counter() - started, 3)
    status = "passed" if exit_code == 0 else "failed"
    print(f"[{command.name}] {status} in {seconds:.1f}s", flush=True)
    return CommandResult(command.name, status, exit_code, seconds, str(log_path))


def _run_stage(
    stage: QualityStage,
    run_root: Path,
    work_root: Path,
) -> tuple[CommandResult, ...]:
    print(
        f"\n=== {stage.name}: {len(stage.commands)} command(s), "
        f"max_workers={stage.max_workers} ===",
        flush=True,
    )
    if stage.max_workers == 1:
        results = tuple(
            _run_command(command, run_root, work_root) for command in stage.commands
        )
    else:
        with ThreadPoolExecutor(max_workers=stage.max_workers) as executor:
            futures = [
                executor.submit(_run_command, command, run_root, work_root)
                for command in stage.commands
            ]
            results = tuple(future.result() for future in futures)
    if any(result.exit_code != 0 for result in results):
        raise StageFailure(stage.name, results)
    return results


def _write_report(
    path: Path,
    *,
    plan: QualityPlan,
    changed_paths: Sequence[str],
    work_root: Path,
    status: str,
    results: Sequence[CommandResult],
    started_at: str,
    error: str | None = None,
) -> None:
    payload = {
        "schema": "mediaflow-local-quality/v1",
        "status": status,
        "started_at": started_at,
        "finished_at": datetime.now(UTC).isoformat(),
        "plan": asdict(plan),
        "changed_paths": list(changed_paths),
        "work_root": str(work_root),
        "commands": [asdict(result) for result in results],
        "error": error,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _print_dry_run(stages: Sequence[QualityStage], plan: QualityPlan) -> None:
    print(json.dumps(asdict(plan), ensure_ascii=False, indent=2))
    for stage in stages:
        print(f"\n[{stage.name}] max_workers={stage.max_workers}")
        for command in stage.commands:
            print("  " + " ".join(command.arguments))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the one authoritative local MediaFlow quality plan",
    )
    parser.add_argument("--base", default=default_base())
    parser.add_argument(
        "--scope",
        choices=("auto", "maintenance", "core", "full"),
        default="auto",
    )
    parser.add_argument("--max-runtime-workers", type=int, choices=range(1, 5))
    parser.add_argument("--dry-run", action="store_true")
    arguments = parser.parse_args(argv)

    paths = local_changed_paths(arguments.base)
    plan = (
        plan_for_paths(paths)
        if arguments.scope == "auto"
        else forced_plan(arguments.scope, reason=f"local forced {arguments.scope} regression")
    )
    now = datetime.now(UTC)
    stamp = now.strftime("q-%Y%m%dT%H%M%S.%fZ")
    work_stamp = now.strftime("%H%M") + f"-{os.getpid()}"
    configured_test_root = Path(
        os.environ.get("MEDIAFLOW_TEST_ROOT", REPOSITORY_ROOT / ".test-runs")
    ).expanduser()
    run_root = (configured_test_root / "quality" / stamp).resolve()
    work_parent = (
        configured_test_root.parents[1]
        if len(configured_test_root.parents) > 1
        else configured_test_root.parent
    )
    work_root = (work_parent / "q" / work_stamp).resolve()
    runtime_workers = arguments.max_runtime_workers or recommended_runtime_workers()
    stages = build_quality_stages(
        plan,
        run_root=run_root,
        max_runtime_workers=runtime_workers,
    )
    if arguments.dry_run:
        _print_dry_run(stages, plan)
        return 0

    run_root.mkdir(parents=True, exist_ok=False)
    work_root.mkdir(parents=True, exist_ok=False)
    started_at = datetime.now(UTC).isoformat()
    print(f"Quality plan: {plan.scope} ({plan.reason})")
    print(f"Changed paths: {len(paths)}")
    print(f"Evidence root: {run_root}")
    print(f"Short work root: {work_root}")
    if plan.run_full:
        print(f"Runtime shard workers: {runtime_workers}")
    if plan.run_portable_build or plan.run_interchange:
        print(
            "Cross-platform source builds and project interchange remain CI boundaries; "
            "this local run verifies the selected Windows boundary."
        )
    results: list[CommandResult] = []
    try:
        for stage in stages:
            results.extend(_run_stage(stage, run_root, work_root))
    except StageFailure as error:
        results.extend(error.results)
        _write_report(
            run_root / "quality-report.json",
            plan=plan,
            changed_paths=paths,
            work_root=work_root,
            status="failed",
            results=results,
            started_at=started_at,
            error=str(error),
        )
        print(f"Quality run failed: {error}", file=sys.stderr)
        return 1
    except BaseException as error:
        _write_report(
            run_root / "quality-report.json",
            plan=plan,
            changed_paths=paths,
            work_root=work_root,
            status="failed",
            results=results,
            started_at=started_at,
            error=str(error),
        )
        print(f"Quality run failed: {error}", file=sys.stderr)
        return 1
    _write_report(
        run_root / "quality-report.json",
        plan=plan,
        changed_paths=paths,
        work_root=work_root,
        status="passed",
        results=results,
        started_at=started_at,
    )
    total_seconds = sum(result.seconds for result in results)
    print(
        f"Quality run passed: {len(results)} commands, "
        f"{total_seconds:.1f}s cumulative command time"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
