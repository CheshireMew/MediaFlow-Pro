from __future__ import annotations

import argparse
import json
import os
import subprocess
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

MAINTENANCE_ROOT_FILES = {
    ".editorconfig",
    ".gitattributes",
    ".gitignore",
    "agents.md",
    "architecture.md",
    "code_of_conduct.md",
    "contributing.md",
    "license",
    "license.md",
    "licensing.md",
    "notice",
    "notice.md",
    "readme.md",
    "security.md",
    "third_party_notices.md",
}
MAINTENANCE_MEDIA_SUFFIXES = {".gif", ".jpeg", ".jpg", ".png", ".svg", ".webp"}
MAINTENANCE_TEXT_SUFFIXES = {".md", ".rst", ".txt"}


@dataclass(frozen=True)
class QualityPlan:
    scope: str
    run_core: bool
    run_full: bool
    run_portable_build: bool
    run_interchange: bool
    changed_count: int
    reason: str

    def github_outputs(self) -> dict[str, str]:
        values = asdict(self)
        return {
            key: (str(value).lower() if isinstance(value, bool) else str(value))
            .replace("\r", " ")
            .replace("\n", " ")
            for key, value in values.items()
        }


def normalize_path(value: str | Path) -> str:
    normalized = PurePosixPath(str(value).replace("\\", "/")).as_posix()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.lower()


def is_maintenance_path(value: str | Path) -> bool:
    path = normalize_path(value)
    item = PurePosixPath(path)
    suffix = item.suffix.lower()
    if path in MAINTENANCE_ROOT_FILES:
        return True
    if len(item.parts) == 1 and suffix in MAINTENANCE_TEXT_SUFFIXES:
        return True
    if path == ".github/workflows/star-history.yml":
        return True
    if path.startswith("archive/"):
        return True
    if path.startswith("docs/"):
        return suffix in MAINTENANCE_TEXT_SUFFIXES | MAINTENANCE_MEDIA_SUFFIXES
    if path.startswith(".github/") and not path.startswith(".github/workflows/"):
        return suffix in MAINTENANCE_TEXT_SUFFIXES | MAINTENANCE_MEDIA_SUFFIXES | {".yml", ".yaml"}
    return False


def plan_for_paths(paths: Iterable[str | Path]) -> QualityPlan:
    changed = tuple(dict.fromkeys(normalize_path(path) for path in paths if str(path).strip()))
    if not changed:
        return QualityPlan("maintenance", False, False, False, False, 0, "no changed paths")
    non_maintenance = tuple(path for path in changed if not is_maintenance_path(path))
    if not non_maintenance:
        return QualityPlan(
            "maintenance",
            False,
            False,
            False,
            False,
            len(changed),
            "all changed paths are repository documentation or metadata",
        )
    return QualityPlan(
        "core",
        True,
        False,
        False,
        False,
        len(changed),
        "changed paths use core checks; full regression requires an explicit request",
    )


def forced_plan(scope: str, *, reason: str) -> QualityPlan:
    if scope == "maintenance":
        return QualityPlan(scope, False, False, False, False, 0, reason)
    if scope == "core":
        return QualityPlan(scope, True, False, False, False, 0, reason)
    if scope == "full":
        return QualityPlan(scope, True, True, True, True, 0, reason)
    raise ValueError(f"Unsupported quality scope: {scope}")


def _git(*arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return completed.stdout.strip()


def _empty_tree() -> str:
    completed = subprocess.run(
        ["git", "hash-object", "-t", "tree", "--stdin"],
        cwd=REPOSITORY_ROOT,
        check=True,
        input="",
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def changed_paths(base: str, head: str) -> tuple[str, ...]:
    selected_base = _empty_tree() if not base or set(base) == {"0"} else base
    output = _git(
        "diff",
        "--name-only",
        "--no-renames",
        "--diff-filter=ACMRD",
        selected_base,
        head,
    )
    return tuple(line for line in output.splitlines() if line.strip())


def determine_plan(
    *,
    event: str,
    base: str,
    head: str,
    manual_scope: str,
) -> tuple[QualityPlan, tuple[str, ...]]:
    if event == "workflow_dispatch":
        return forced_plan(manual_scope, reason=f"manual {manual_scope} regression"), ()
    paths = changed_paths(base, head)
    return plan_for_paths(paths), paths


def _write_github_outputs(path: Path, plan: QualityPlan) -> None:
    with path.open("a", encoding="utf-8") as output:
        for key, value in plan.github_outputs().items():
            output.write(f"{key}={value}\n")


def _write_summary(plan: QualityPlan, paths: Sequence[str]) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    preview = "\n".join(f"- `{path}`" for path in paths[:30]) or "- No commit diff (forced scope)."
    if len(paths) > 30:
        preview += f"\n- … and {len(paths) - 30} more"
    with Path(summary_path).open("a", encoding="utf-8") as summary:
        summary.write(
            "## Quality plan\n\n"
            f"- Scope: `{plan.scope}`\n"
            f"- Reason: {plan.reason}\n"
            "- Core / full / portable build / interchange: "
            f"`{plan.run_core}` / `{plan.run_full}` / "
            f"`{plan.run_portable_build}` / `{plan.run_interchange}`\n\n"
            f"Changed paths:\n\n{preview}\n"
        )


def _self_test() -> None:
    cases = {
        ("README.md",): ("maintenance", False, False, False),
        ("README.en.md", "README.ja.md", "CONTRIBUTING.md"): (
            "maintenance",
            False,
            False,
            False,
        ),
        ("LICENSE", "docs/demo.png"): ("maintenance", False, False, False),
        (".github/workflows/star-history.yml",): ("maintenance", False, False, False),
        ("AGENTS.md", "ARCHITECTURE.md"): ("maintenance", False, False, False),
        ("mediaflow/domain/project.py",): ("core", True, False, False),
        ("mediaflow/domain/task.py",): ("core", True, False, False),
        ("mediaflow/composition.py",): ("core", True, False, False),
        ("mediaflow/desktop/controllers/timeline_clip_controller.py",): ("core", True, False, False),
        ("mediaflow/desktop/qml/Main.qml",): ("core", True, False, False),
        ("mediaflow/infrastructure/runtime_paths.py",): ("core", True, False, False),
        ("mediaflow/desktop/native/MltRuntime.cpp",): ("core", True, False, False),
        ("tests/v2/integration/test_native_preview.py",): ("core", True, False, False),
        (".github/workflows/quality.yml",): ("core", True, False, False),
        ("packaging/windows/mediaflow_portable.spec",): ("core", True, False, False),
        ("scripts/ci/quality_plan.py",): ("core", True, False, False),
        (".env.example",): ("core", True, False, False),
        ("requirements.lock",): ("core", True, False, False),
        ("runtime.lock.json",): ("core", True, False, False),
        ("scripts/verify_project_interchange.py",): ("core", True, False, False),
        ("README.md", "mediaflow/application/task_service.py"): (
            "core",
            True,
            False,
            False,
        ),
        ("unexpected/build-input.bin",): ("core", True, False, False),
    }
    for paths, expected in cases.items():
        plan = plan_for_paths(paths)
        actual = (
            plan.scope,
            plan.run_core,
            plan.run_portable_build,
            plan.run_interchange,
        )
        if actual != expected:
            raise AssertionError(f"{paths}: expected {expected}, received {actual}")
        if plan.run_full:
            raise AssertionError(f"Changed paths must not request full regression: {paths}")
    if plan_for_paths(()).scope != "maintenance":
        raise AssertionError("An empty change set must not request full regression")
    forced_full = forced_plan("full", reason="test")
    if not forced_full.run_portable_build or not forced_full.run_interchange:
        raise AssertionError("A forced full plan must include every cross-platform boundary")
    print("quality plan self-test passed")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Classify the MediaFlow Pro CI scope")
    parser.add_argument("--event", default=os.environ.get("GITHUB_EVENT_NAME", "workflow_dispatch"))
    parser.add_argument("--base", default="")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument(
        "--manual-scope",
        choices=("maintenance", "core", "full"),
        default="core",
    )
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    arguments = parser.parse_args(argv)
    if arguments.self_test:
        _self_test()
        return 0
    plan, paths = determine_plan(
        event=arguments.event,
        base=arguments.base,
        head=arguments.head,
        manual_scope=arguments.manual_scope,
    )
    if arguments.github_output is not None:
        _write_github_outputs(arguments.github_output, plan)
    _write_summary(plan, paths)
    print(json.dumps({**asdict(plan), "paths": paths}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
