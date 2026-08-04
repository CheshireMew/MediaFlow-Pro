from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
REQUIREMENTS_LOCK = REPOSITORY_ROOT / "requirements.lock"


def locked_requirement_block(package: str, lock_file: Path = REQUIREMENTS_LOCK) -> str:
    prefix = f"{package.lower()}=="
    lines = lock_file.read_text(encoding="utf-8").splitlines()
    start = next(
        (index for index, line in enumerate(lines) if line.lower().startswith(prefix)),
        None,
    )
    if start is None:
        raise RuntimeError(f"{package} is missing from {lock_file}")
    selected = [lines[start]]
    while selected[-1].rstrip().endswith("\\"):
        start += 1
        if start >= len(lines):
            raise RuntimeError(f"Incomplete locked requirement for {package}")
        selected.append(lines[start])
    block = "\n".join(selected) + "\n"
    if "--hash=sha256:" not in block:
        raise RuntimeError(f"{package} does not have a reviewed hash in {lock_file}")
    return block


def install(package: str) -> None:
    requirement = locked_requirement_block(package)
    with tempfile.TemporaryDirectory(prefix="mediaflow-maintenance-") as temporary:
        selected_lock = Path(temporary) / "requirements.lock"
        selected_lock.write_text(requirement, encoding="utf-8")
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--require-hashes",
                "--only-binary=:all:",
                "-r",
                str(selected_lock),
            ],
            check=True,
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install one hashed CI maintenance tool")
    parser.add_argument("package", nargs="?", default="pyyaml")
    parser.add_argument("--print-lock", action="store_true")
    arguments = parser.parse_args(argv)
    if arguments.print_lock:
        print(locked_requirement_block(arguments.package), end="")
        return 0
    install(arguments.package)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
