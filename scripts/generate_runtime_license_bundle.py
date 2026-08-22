# ruff: noqa: E402

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mediaflow.atomic_file import atomic_write_text
from mediaflow.infrastructure.runtime_contract import load_runtime_contract


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_notice(source: Path, destination: Path) -> dict[str, object]:
    if not source.is_file():
        raise FileNotFoundError(f"Required runtime license file is missing: {source}")
    shutil.copy2(source, destination)
    return {
        "path": destination.name,
        "bytes": destination.stat().st_size,
        "sha256": sha256(destination),
    }


def generate(runtime_root: Path, output_dir: Path) -> dict[str, object]:
    runtime_root = runtime_root.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    if output_dir.exists():
        raise FileExistsError(f"Runtime license output already exists: {output_dir}")
    contract = load_runtime_contract(ROOT / "runtime.lock.json")
    if contract.target.operating_system != "windows":
        raise RuntimeError("The portable runtime license bundle currently targets Windows")
    output_dir.mkdir(parents=True)

    browser = contract.playwright
    chromium_executable = contract.chromium_directory(runtime_root) / browser.executable
    if not chromium_executable.is_file():
        raise FileNotFoundError(chromium_executable)
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        launched = playwright.chromium.launch(
            executable_path=str(chromium_executable),
            headless=True,
        )
        try:
            page = launched.new_page()
            page.goto("chrome://credits")
            page.wait_for_load_state("domcontentloaded")
            credits_html = page.content()
            title = page.title()
        finally:
            launched.close()
    if title != "Credits" or len(credits_html) < 1_000_000:
        raise RuntimeError("Chromium did not expose its complete embedded credits document")
    chromium_credits = output_dir / "chromium-third-party-credits.html"
    atomic_write_text(chromium_credits, credits_html)

    shotcut_root = runtime_root / "deps" / f"shotcut-{contract.reviewed_bundle.version}"
    shotcut_payload = shotcut_root / contract.reviewed_bundle.archive_root
    records = {
        "chromium_project_license": _copy_notice(
            ROOT / "packaging" / "licenses" / "CHROMIUM-LICENSE.txt",
            output_dir / "CHROMIUM-LICENSE.txt",
        ),
        "chromium_third_party_credits": {
            "path": chromium_credits.name,
            "bytes": chromium_credits.stat().st_size,
            "sha256": sha256(chromium_credits),
        },
        "shotcut_copying": _copy_notice(
            shotcut_payload / "COPYING.txt",
            output_dir / "SHOTCUT-COPYING.txt",
        ),
        "shotcut_license": _copy_notice(
            shotcut_payload / "LICENSE",
            output_dir / "SHOTCUT-LICENSE.txt",
        ),
    }
    manifest = {
        "schema": "mediaflow-runtime-license-bundle/v1",
        "target": contract.target.key,
        "chromium_version": browser.browser_version,
        "shotcut_version": contract.reviewed_bundle.version,
        "files": records,
    }
    atomic_write_text(
        output_dir / "manifest.json",
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Extract exact browser/runtime notices for a Windows portable build"
    )
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    arguments = parser.parse_args(argv)
    manifest = generate(arguments.runtime_root, arguments.output_dir)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
