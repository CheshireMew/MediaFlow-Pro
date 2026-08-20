# ruff: noqa: E402

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.documentation_screenshot_contract import (
    DOCUMENTATION_SCREENSHOTS,
    MANIFEST_PATH,
    documentation_ui_digest,
    file_sha256,
    png_dimensions,
)

MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\((<[^>]+>|[^)\s]+)(?:\s+[^)]*)?\)")
HTML_MEDIA = re.compile(r"<(?:img|source)\b[^>]*\b(?:src|srcset)=[\"']([^\"']+)", re.IGNORECASE)
FENCED_CODE = re.compile(r"```.*?```|~~~.*?~~~", re.DOTALL)
EXTERNAL_SCHEMES = {"data", "http", "https", "mailto"}


def tracked_markdown() -> tuple[Path, ...]:
    completed = subprocess.run(
        ["git", "ls-files", "*.md"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return tuple(
        REPOSITORY_ROOT / line
        for line in completed.stdout.splitlines()
        if line and not line.replace("\\", "/").startswith("archive/")
    )


def link_destinations(document: Path) -> tuple[str, ...]:
    source = FENCED_CODE.sub("", document.read_text(encoding="utf-8"))
    markdown = [match.group(1).strip("<>") for match in MARKDOWN_LINK.finditer(source)]
    html = [match.group(1).split(",", 1)[0].strip().split(" ", 1)[0] for match in HTML_MEDIA.finditer(source)]
    return tuple((*markdown, *html))


def local_target(document: Path, destination: str) -> Path | None:
    clean = destination.strip()
    if not clean or clean.startswith("#") or "${{" in clean:
        return None
    parsed = urlsplit(clean)
    if parsed.scheme.lower() in EXTERNAL_SCHEMES or parsed.netloc:
        return None
    path = unquote(parsed.path)
    if not path:
        return None
    if path.startswith("/"):
        return REPOSITORY_ROOT / path.lstrip("/")
    return document.parent / path


def verify_links() -> list[str]:
    failures: list[str] = []
    for document in tracked_markdown():
        for destination in link_destinations(document):
            target = local_target(document, destination)
            if target is not None and not target.exists():
                relative = document.relative_to(REPOSITORY_ROOT).as_posix()
                failures.append(f"{relative}: missing local target {destination}")
    return failures


def verify_repository_contract() -> list[str]:
    failures: list[str] = []
    readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
    license_text = (REPOSITORY_ROOT / "LICENSE").read_text(encoding="utf-8")
    if "GNU GENERAL PUBLIC LICENSE" not in license_text or "Version 3" not in license_text:
        failures.append("LICENSE is not the complete GNU GPL v3 text")
    if "[GNU GPL v3](LICENSE)" not in readme:
        failures.append("README does not link to the project license")
    if "[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)" not in readme:
        failures.append("README does not link to third-party notices")
    local_media = [
        target
        for destination in link_destinations(REPOSITORY_ROOT / "README.md")
        if (target := local_target(REPOSITORY_ROOT / "README.md", destination)) is not None
        and target.suffix.lower() in {".gif", ".jpeg", ".jpg", ".png", ".svg", ".webp"}
    ]
    if not local_media:
        failures.append("README does not contain a repository-owned product image")
    return failures


def verify_documentation_screenshots() -> list[str]:
    if not MANIFEST_PATH.is_file():
        return ["documentation screenshot manifest is missing"]
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        return ["documentation screenshot manifest is invalid"]
    failures: list[str] = []
    if manifest.get("schema") != "mediaflow-documentation-screenshots/v1":
        failures.append("documentation screenshot manifest schema is unsupported")
    if manifest.get("generator") != "scripts/update_documentation_screenshots.py":
        failures.append("documentation screenshots do not name their maintained generator")
    if manifest.get("ui_source_digest") != documentation_ui_digest():
        failures.append("documentation screenshots are stale; run the maintained generator")
    records = manifest.get("images")
    if not isinstance(records, list):
        return [*failures, "documentation screenshot manifest has no image records"]
    indexed = {
        str(record.get("path") or ""): record
        for record in records
        if isinstance(record, dict)
    }
    if set(indexed) != set(DOCUMENTATION_SCREENSHOTS):
        failures.append("documentation screenshot manifest does not own the expected images")
    for relative_path, scenario in DOCUMENTATION_SCREENSHOTS.items():
        image = REPOSITORY_ROOT / relative_path
        record = indexed.get(relative_path)
        if record is None or not image.is_file():
            failures.append(f"missing maintained documentation screenshot: {relative_path}")
            continue
        try:
            width, height = png_dimensions(image)
        except ValueError as error:
            failures.append(str(error))
            continue
        if width < 1280 or height < 720:
            failures.append(f"documentation screenshot is too small: {relative_path}")
        if record.get("scenario") != scenario:
            failures.append(f"documentation screenshot scenario drifted: {relative_path}")
        if record.get("sha256") != file_sha256(image):
            failures.append(f"documentation screenshot hash drifted: {relative_path}")
        if [record.get("width"), record.get("height")] != [width, height]:
            failures.append(f"documentation screenshot dimensions drifted: {relative_path}")
        if record.get("local_paths_exposed") is not False:
            failures.append(f"documentation screenshot path-hygiene proof is missing: {relative_path}")
    return failures


def main() -> int:
    failures = [
        *verify_repository_contract(),
        *verify_documentation_screenshots(),
        *verify_links(),
    ]
    if failures:
        raise RuntimeError("Repository documentation verification failed:\n- " + "\n- ".join(failures))
    print(f"repository documentation verified ({len(tracked_markdown())} Markdown files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
