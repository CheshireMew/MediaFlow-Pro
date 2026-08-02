from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
from playwright.sync_api import sync_playwright

from mediaflow.application.web_package_files import editable_media_source_hash
from mediaflow.domain.editable_media_contract import (
    validate_editable_media_document,
)
from mediaflow.domain.web_media import (
    WebClipState,
    parse_editable_media_manifest,
    web_runtime_state,
)
from mediaflow.infrastructure.chromium_runtime import (
    find_chromium_executable,
)
from mediaflow.infrastructure.editable_media_project_migration import (
    V5_RUNTIME_PATH,
    migrate_editable_media_v4_manifest,
)
from mediaflow.infrastructure.project_schema_definition import PROJECT_SCHEMA_VERSION
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from mediaflow.infrastructure.web_browser import WebPackagePreviewServer

FIXTURE = Path("tests/fixtures/editable-media-v4-project").resolve()
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _cli(request: dict[str, object]) -> tuple[int, dict[str, object]]:
    completed = subprocess.run(
        [sys.executable, "-m", "mediaflow.cli", "execute", "--request", "-"],
        cwd=REPOSITORY_ROOT,
        input=json.dumps(request, ensure_ascii=False),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=240,
    )
    assert completed.stderr == ""
    return completed.returncode, json.loads(completed.stdout)


def _request(
    project: Path,
    operation: str,
    arguments: dict[str, object],
    *,
    request_id: str | None = None,
) -> dict[str, object]:
    request: dict[str, object] = {
        "protocol": "mediaflow-cli",
        "version": 2,
        "operation": operation,
        "project": str(project),
        "arguments": arguments,
    }
    if request_id is not None:
        request["request_id"] = request_id
    return request


def _project_web_state(project: Path) -> dict[str, object]:
    connection = sqlite3.connect(project / "project.mfp")
    connection.row_factory = sqlite3.Row
    try:
        asset = connection.execute(
            """SELECT asset.id, asset.path, web_asset.manifest_json,
                      web_asset.source_hash
               FROM asset
               JOIN web_asset ON web_asset.asset_id=asset.id"""
        ).fetchone()
        assert asset is not None
        state = connection.execute(
            """SELECT state.clip_id, state.state_json, state.revision
               FROM web_clip_state AS state
               JOIN clip ON clip.id=state.clip_id
               WHERE clip.asset_id=?""",
            (asset["id"],),
        ).fetchone()
        assert state is not None
        version = connection.execute("SELECT version FROM schema_info WHERE component='project'").fetchone()
        assert version is not None
        raw_manifest = json.loads(str(asset["manifest_json"]))
        manifest = (
            migrate_editable_media_v4_manifest(raw_manifest)
            if raw_manifest["version"] == 4
            else parse_editable_media_manifest(raw_manifest)
        )
        clip_state = WebClipState.model_validate(
            {
                **json.loads(str(state["state_json"])),
                "clip_id": state["clip_id"],
                "revision": state["revision"],
            }
        )
        runtime_state = web_runtime_state(clip_state, manifest)
        entry = (project / Path(str(asset["path"]))).resolve()
        package = entry
        for _part in _pure_path_parts(manifest.entry):
            package = package.parent
        return {
            "asset_id": str(asset["id"]),
            "clip_id": str(state["clip_id"]),
            "schema_version": int(version[0]),
            "raw_manifest": raw_manifest,
            "manifest": manifest,
            "runtime_state": runtime_state,
            "entry": entry,
            "package": package,
            "source_hash": str(asset["source_hash"]),
        }
    finally:
        connection.close()


def _pure_path_parts(value: str) -> tuple[str, ...]:
    normalized = value.replace("\\", "/")
    return tuple(part for part in normalized.split("/") if part)


def _legacy_runtime_state(state: dict[str, object]) -> dict[str, object]:
    legacy = json.loads(json.dumps(state))
    legacy.pop("parameters", None)
    legacy.pop("parameter_bindings", None)
    legacy.pop("parameter_locks", None)
    for scene in legacy["scenes"].values():
        scene.pop("parameters", None)
        scene.pop("parameter_animations", None)
        scene.pop("parameter_locks", None)
    return legacy


def _screenshots(
    snapshot: dict[str, object],
    runtime_state: dict[str, object],
) -> list[np.ndarray]:
    manifest = snapshot["manifest"]
    canvas = manifest.default_variant.canvas
    images: list[np.ndarray] = []
    with (
        WebPackagePreviewServer(snapshot["package"]) as preview,
        sync_playwright() as playwright,
    ):
        browser = playwright.chromium.launch(
            executable_path=str(find_chromium_executable()),
            headless=True,
            args=["--disable-gpu"],
        )
        page = browser.new_page(
            viewport={"width": canvas.width, "height": canvas.height},
            device_scale_factor=1,
        )
        page.goto(preview.url_for(manifest.entry))
        page.evaluate("() => window.editableMedia.ready")
        page.evaluate(
            "state => window.editableMedia.setState(state)",
            runtime_state,
        )
        for seconds in (0.0, 0.9, 1.8, 3.2, 4.8):
            page.evaluate("time => window.__hf.seek(time)", seconds)
            encoded = np.frombuffer(
                page.screenshot(type="png", omit_background=True),
                dtype=np.uint8,
            )
            image = cv2.imdecode(encoded, cv2.IMREAD_UNCHANGED)
            assert image is not None
            images.append(image)
        browser.close()
    return images


def _only_package(project: Path) -> Path:
    packages = sorted(path for path in (project / "sources" / "web").glob("p-*") if path.is_dir())
    assert len(packages) == 1
    return packages[0]


def test_v4_manifest_migration_preserves_steps_and_declares_v5_semantics() -> None:
    source = json.loads((_only_package(FIXTURE) / "editable-media.json").read_text(encoding="utf-8"))

    migrated = migrate_editable_media_v4_manifest(source)

    assert migrated.version == 5
    assert migrated.parameters == []
    for old_scene, scene in zip(source["scenes"], migrated.scenes, strict=True):
        assert scene.parameters == {}
        assert [step.id for step in scene.steps] == [step["id"] for step in old_scene["steps"]]
        assert [step.at_ms for step in scene.steps] == [step["at_ms"] for step in old_scene["steps"]]
        assert [step.state_kind for step in scene.steps] == [
            "start",
            *("change" for _ in old_scene["steps"][1:-1]),
            "result",
        ]
        assert all(step.description == step.label for step in scene.steps)
        assert all(step.review is False for step in scene.steps)
        assert scene.motion.complexity == "simple"
        assert scene.motion.driver == "object"
        assert scene.motion.camera is None


def test_failed_v4_project_upgrade_rolls_back_database_and_archives_staging(
    tmp_path: Path,
) -> None:
    project = tmp_path / "invalid-v4-project"
    shutil.copytree(FIXTURE, project)
    package = _only_package(project)
    original_path = package.relative_to(project).as_posix()
    original_hash = editable_media_source_hash(package)
    connection = sqlite3.connect(project / "project.mfp")
    try:
        state_row = connection.execute("SELECT clip_id, state_json FROM web_clip_state").fetchone()
        assert state_row is not None
        state = json.loads(str(state_row[1]))
        state["scenes"] = []
        connection.execute(
            "UPDATE web_clip_state SET state_json=? WHERE clip_id=?",
            (json.dumps(state), state_row[0]),
        )
        connection.commit()
    finally:
        connection.close()

    code, result = _cli(
        _request(
            project,
            "project.upgrade",
            {},
            request_id="invalid-v4-project-upgrade",
        )
    )

    assert code == 1
    assert result["ok"] is False
    connection = sqlite3.connect(project / "project.mfp")
    connection.row_factory = sqlite3.Row
    try:
        assert (
            connection.execute("SELECT version FROM schema_info WHERE component='project'").fetchone()[
                "version"
            ]
            == 35
        )
        asset = connection.execute(
            """SELECT asset.path, web_asset.source_hash,
                      web_asset.manifest_json
               FROM asset
               JOIN web_asset ON web_asset.asset_id=asset.id"""
        ).fetchone()
        assert asset is not None
        assert asset["path"].replace("\\", "/").startswith(original_path)
        assert asset["source_hash"] == original_hash
        assert json.loads(asset["manifest_json"])["version"] == 4
        assert (
            connection.execute(
                """SELECT name FROM sqlite_master
               WHERE type='table' AND name='editable_media_upgrade'"""
            ).fetchone()
            is None
        )
    finally:
        connection.close()
    assert package.is_dir()
    assert editable_media_source_hash(package) == original_hash
    assert not any((project / "staging" / "web").glob("s-*"))
    failed = list((project / "archive" / "web").glob("f-*"))
    assert len(failed) == 1
    assert editable_media_source_hash(failed[0]) != original_hash


def test_real_v4_project_upgrades_once_and_reaches_visible_v5_output(
    tmp_path: Path,
) -> None:
    origin = json.loads((FIXTURE / "fixture-origin.json").read_text(encoding="utf-8"))
    assert origin["project_schema_version"] == 35
    assert origin["editable_media_version"] == 4
    assert {relative: _sha256(FIXTURE / Path(relative)) for relative in origin["files"]} == origin["files"]

    project = tmp_path / "Real v4 Project"
    shutil.copytree(FIXTURE, project)
    before = _project_web_state(project)
    assert before["schema_version"] == 35
    assert before["raw_manifest"]["version"] == 4
    before_pixels = _screenshots(
        before,
        _legacy_runtime_state(before["runtime_state"]),
    )

    code, inspection = _cli(_request(project, "project.inspect", {}))
    assert code == 1
    assert inspection["error"]["code"] == "upgrade_required"

    code, upgrade = _cli(
        _request(
            project,
            "project.upgrade",
            {},
            request_id="real-v4-project-upgrade",
        )
    )
    assert code == 0
    assert upgrade["ok"] is True

    after = _project_web_state(project)
    assert after["schema_version"] == PROJECT_SCHEMA_VERSION
    assert after["raw_manifest"]["version"] == 5
    validate_editable_media_document(after["raw_manifest"])
    assert editable_media_source_hash(after["package"]) == after["source_hash"]
    assert _sha256(after["package"] / "editable-media-runtime.js") == _sha256(V5_RUNTIME_PATH)
    after_pixels = _screenshots(after, after["runtime_state"])
    assert len(before_pixels) == len(after_pixels)
    for old_image, new_image in zip(before_pixels, after_pixels, strict=True):
        assert np.array_equal(old_image, new_image)

    connection = sqlite3.connect(project / "project.mfp")
    connection.row_factory = sqlite3.Row
    try:
        upgrades = connection.execute("SELECT * FROM editable_media_upgrade").fetchall()
        assert len(upgrades) == 1
        archived = project / Path(upgrades[0]["archive_package_path"])
        assert archived.is_dir()
        archived_manifest = json.loads((archived / "editable-media.json").read_text(encoding="utf-8"))
        assert archived_manifest["version"] == 4
    finally:
        connection.close()

    code, project_result = _cli(_request(project, "project.inspect", {}))
    assert code == 0
    assert project_result["ok"] is True
    validate_editable_media_document(project_result["result"]["web_assets"][0]["manifest"])

    code, web_result = _cli(
        _request(
            project,
            "web.inspect",
            {"asset_id": after["asset_id"]},
        )
    )
    assert code == 0
    assert web_result["result"]["web_asset"]["source_hash"] == after["source_hash"]

    output = project / "exports" / "migrated-v5-proof.mp4"
    code, export = _cli(
        _request(
            project,
            "web.clip.export",
            {
                "sequence_id": project_result["result"]["project"]["main_sequence_id"],
                "clip_id": after["clip_id"],
                "output_path": str(output),
                "format": "video",
                "timeout": 180,
            },
            request_id="real-v4-project-export",
        )
    )
    assert code == 0
    assert export["ok"] is True
    ffprobe = RuntimePaths.discover().ffprobe
    assert ffprobe is not None
    probe = subprocess.run(
        [
            str(ffprobe),
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_type,width,height",
            "-of",
            "json",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    stream = json.loads(probe.stdout)["streams"][0]
    assert stream["codec_type"] == "video"
    assert int(stream["width"]) > 0
    assert int(stream["height"]) > 0
