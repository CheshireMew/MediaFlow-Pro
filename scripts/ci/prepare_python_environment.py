from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import venv
from pathlib import Path

ENVIRONMENT_SCHEMA = "mediaflow-ci-python-environment/v1"
STATE_FILE = ".mediaflow-ci-environment.json"


def environment_python(environment: Path) -> Path:
    relative = Path("Scripts/python.exe") if os.name == "nt" else Path("bin/python")
    return environment / relative


def requirements_digest(requirements: Path) -> str:
    return hashlib.sha256(requirements.read_bytes()).hexdigest()


def expected_state(requirements: Path) -> dict[str, str]:
    return {
        "schema": ENVIRONMENT_SCHEMA,
        "platform": platform.system(),
        "architecture": platform.machine(),
        "python": platform.python_version(),
        "requirements_sha256": requirements_digest(requirements),
    }


def load_state(environment: Path) -> dict[str, object] | None:
    path = environment / STATE_FILE
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def cached_environment_is_current(environment: Path, requirements: Path) -> bool:
    return environment_python(environment).is_file() and load_state(environment) == expected_state(
        requirements
    )


def _run(*arguments: str | Path) -> None:
    subprocess.run([str(argument) for argument in arguments], check=True)


def prepare_environment(
    environment: Path,
    requirements: Path,
    repository: Path,
    *,
    cache_hit: bool,
) -> Path:
    environment = environment.expanduser().resolve()
    requirements = requirements.expanduser().resolve()
    repository = repository.expanduser().resolve()
    python = environment_python(environment)

    if cache_hit:
        if not cached_environment_is_current(environment, requirements):
            raise RuntimeError(
                "The restored Python environment does not match the exact interpreter and "
                "requirements lock"
            )
    else:
        if not python.is_file():
            environment.parent.mkdir(parents=True, exist_ok=True)
            venv.EnvBuilder(with_pip=True).create(environment)
        _run(python, "-m", "pip", "install", "--require-hashes", "-r", requirements)
        _run(python, "-m", "pip", "check")
        (environment / STATE_FILE).write_text(
            json.dumps(expected_state(requirements), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    _run(
        python,
        "-m",
        "pip",
        "install",
        "--no-deps",
        "--no-build-isolation",
        "-e",
        repository,
    )
    _run(python, "-m", "pip", "check")
    return python


def publish_github_environment(python: Path) -> None:
    github_path = os.environ.get("GITHUB_PATH")
    if github_path:
        with Path(github_path).open("a", encoding="utf-8") as output:
            output.write(f"{python.parent}\n")
    github_environment = os.environ.get("GITHUB_ENV")
    if github_environment:
        with Path(github_environment).open("a", encoding="utf-8") as output:
            output.write(f"MEDIAFLOW_CI_PYTHON={python}\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare or validate the checksum-keyed complete CI Python environment"
    )
    parser.add_argument("--environment", type=Path, required=True)
    parser.add_argument("--requirements", type=Path, default=Path("requirements.lock"))
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--cache-hit", default="false")
    arguments = parser.parse_args()
    cache_hit_value = arguments.cache_hit.strip().lower()
    if cache_hit_value not in {"true", "false"}:
        parser.error("--cache-hit must be true or false")
    cache_hit = cache_hit_value == "true"
    python = prepare_environment(
        arguments.environment,
        arguments.requirements,
        arguments.repository,
        cache_hit=cache_hit,
    )
    publish_github_environment(python)
    print(python)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
