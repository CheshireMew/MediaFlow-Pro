from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path

import cv2
import numpy as np
from playwright.sync_api import sync_playwright

from mediaflow.application.web_package_files import (
    publication_receipt_json,
)
from mediaflow.automation.operation_registry import OPERATIONS
from mediaflow.domain.web_manifest import (
    editable_media_manifest_document,
    parse_editable_media_manifest,
)
from mediaflow.domain.web_state import (
    WebClipState,
    web_runtime_state,
)
from mediaflow.infrastructure.editable_media_contract import (
    editable_media_contract,
    validate_editable_media_document,
)
from mediaflow.infrastructure.editable_media_project_migration import (
    V6_RUNTIME_PATH,
    migrate_editable_media_manifest_to_v6,
)
from mediaflow.infrastructure.project_schema_definition import PROJECT_SCHEMA_VERSION
from mediaflow.infrastructure.runtime_context import RuntimeContext
from mediaflow.infrastructure.web_browser import WebPackagePreviewServer
from mediaflow.infrastructure.web_package_storage import editable_media_source_hash

FIXTURE = Path("tests/fixtures/editable-media-v4-project").resolve()
EDITABLE_MEDIA_CONTRACT = editable_media_contract()
V6_STARTER = Path("tests/fixtures/editable-media-v6/editable-media.json").resolve()
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
    payload = json.loads(completed.stdout)
    if payload.get("ok") is True and isinstance(payload.get("result"), dict):
        operation_response = payload["result"]
        if isinstance(operation_response.get("result"), dict):
            payload = {
                **payload,
                "collaboration": operation_response,
                "result": operation_response["result"],
            }
    return completed.returncode, payload


def _request(
    project: Path,
    operation: str,
    arguments: dict[str, object],
    *,
    request_id: str | None = None,
) -> dict[str, object]:
    request: dict[str, object] = {
        "protocol": "mediaflow-editor",
        "version": 4,
        "operation": operation,
        "project": str(project),
        "arguments": arguments,
        "actor": {"kind": "agent", "id": "project-migration-test"},
        "client_id": "pytest-project-migration",
    }
    definition = OPERATIONS[operation]
    if definition.project_access == "write":
        with sqlite3.connect(project / "project.mfp") as connection:
            row = connection.execute("SELECT content_revision FROM project LIMIT 1").fetchone()
        if row is None:
            raise RuntimeError("Project has no content revision")
        request["base_revision"] = int(row[0])
        request["request_id"] = request_id or f"{operation}-{uuid.uuid4().hex}"
    elif request_id is not None:
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
            migrate_editable_media_manifest_to_v6(raw_manifest)
            if raw_manifest["version"] == 4
            else parse_editable_media_manifest(raw_manifest, EDITABLE_MEDIA_CONTRACT)
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
            executable_path=str(RuntimeContext.discover().paths.chromium),
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


def test_v4_manifest_migration_preserves_steps_and_declares_v6_semantics() -> None:
    source = json.loads((_only_package(FIXTURE) / "editable-media.json").read_text(encoding="utf-8"))

    migrated = migrate_editable_media_manifest_to_v6(source)

    assert migrated.version == 6
    assert migrated.frame_readiness.retry_limit == 1
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


def test_v5_manifest_migration_restores_required_nullable_v6_fields() -> None:
    source = json.loads(V6_STARTER.read_text(encoding="utf-8"))
    source["version"] = 5
    source.pop("frame_readiness")
    source["parameters"] = [
        {
            "id": parameter["descriptor"]["id"],
            "name": parameter["descriptor"]["label"],
            "description": parameter["descriptor"]["description"],
            "group": parameter["descriptor"]["group"],
            "kind": parameter["descriptor"]["kind"],
            "scope": parameter["binding"]["scope"],
            "default": parameter["descriptor"]["default"],
            "animatable": parameter["descriptor"]["timeline"] == "keyframe",
            "control": parameter["descriptor"]["control"],
            "unit": parameter["descriptor"].get("unit"),
            "css_variable": parameter["binding"].get("css_variable"),
            "constraints": {
                **{
                    key: value
                    for key, value in parameter["descriptor"]["constraints"].items()
                    if key != "choices"
                },
                "choices": [
                    choice["value"]
                    for choice in parameter["descriptor"]["constraints"].get(
                        "choices",
                        [],
                    )
                ],
            },
        }
        for parameter in source["parameters"]
    ]

    migrated = migrate_editable_media_manifest_to_v6(source)
    document = editable_media_manifest_document(migrated)

    assert migrated.version == 6
    assert all("camera" in scene["motion"] for scene in document["scenes"])


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
    assert not list((project / "archive" / "web").glob("f-*"))


def _replace_project_package_hash(
    project: Path,
    *,
    asset_id: str,
    clip_id: str,
    source_hash: str,
    receipt: Path,
) -> None:
    connection = sqlite3.connect(project / "project.mfp")
    try:
        connection.execute(
            "UPDATE web_asset SET source_hash=? WHERE asset_id=?",
            (source_hash, asset_id),
        )
        state_row = connection.execute(
            "SELECT state_json FROM web_clip_state WHERE clip_id=?",
            (clip_id,),
        ).fetchone()
        assert state_row is not None
        state = json.loads(str(state_row[0]))
        state["source_hash"] = source_hash
        connection.execute(
            "UPDATE web_clip_state SET state_json=? WHERE clip_id=?",
            (json.dumps(state, ensure_ascii=False), clip_id),
        )
        connection.commit()
    finally:
        connection.close()
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    receipt.write_text(
        publication_receipt_json(
            asset_id=asset_id,
            source_hash=source_hash,
            token=str(payload["token"]),
            status="committed",
        ),
        encoding="utf-8",
    )


def test_third_party_v4_runtime_requires_republication_without_project_writes(
    tmp_path: Path,
) -> None:
    project = tmp_path / "third-party-v4-runtime"
    shutil.copytree(FIXTURE, project)
    before = _project_web_state(project)
    package = before["package"]
    assert isinstance(package, Path)
    runtime = package / "editable-media-runtime.js"
    runtime.write_bytes(runtime.read_bytes() + b"\n// third-party runtime\n")
    source_hash = editable_media_source_hash(package)
    receipt = next((project / "sources" / "web" / "receipts").glob("r-*.json"))
    _replace_project_package_hash(
        project,
        asset_id=str(before["asset_id"]),
        clip_id=str(before["clip_id"]),
        source_hash=source_hash,
        receipt=receipt,
    )

    code, result = _cli(
        _request(
            project,
            "project.upgrade",
            {},
            request_id="third-party-v4-runtime-upgrade",
        )
    )

    assert code == 1
    assert result["ok"] is False
    assert "Third-party editable-media v4 runtime" in json.dumps(result, ensure_ascii=False)
    assert "republish" in json.dumps(result, ensure_ascii=False)
    with sqlite3.connect(project / "project.mfp") as connection:
        assert connection.execute("SELECT version FROM schema_info WHERE component='project'").fetchone() == (
            35,
        )
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='editable_media_upgrade'"
            ).fetchone()
            is None
        )
    assert editable_media_source_hash(package) == source_hash
    assert not list((project / "staging" / "web").glob("s-*"))
    assert not list((project / "archive" / "web").glob("f-*"))


def test_all_legacy_web_assets_are_preflighted_before_the_first_publication(
    tmp_path: Path,
) -> None:
    project = tmp_path / "two-v4-assets"
    shutil.copytree(FIXTURE, project)
    first = _project_web_state(project)
    first_package = first["package"]
    assert isinstance(first_package, Path)
    second_asset_id = "ffffffff-ffff-4fff-8fff-ffffffffffff"
    second_clip_id = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
    second_token = "ffffffffffffffffffffffff"
    second_package = project / "sources" / "web" / f"p-{second_token}"
    shutil.copytree(first_package, second_package)
    runtime = second_package / "editable-media-runtime.js"
    runtime.write_bytes(runtime.read_bytes() + b"\n// unproven second runtime\n")
    second_hash = editable_media_source_hash(second_package)
    second_receipt = project / "sources" / "web" / "receipts" / f"r-{second_token}.json"
    second_receipt.write_text(
        publication_receipt_json(
            asset_id=second_asset_id,
            source_hash=second_hash,
            token=second_token,
            status="committed",
        ),
        encoding="utf-8",
    )
    connection = sqlite3.connect(project / "project.mfp")
    try:
        connection.execute(
            """INSERT INTO asset
               SELECT ?, project_id, name || ' 2', kind, origin, ?, managed,
                      proxy_path, sdr_preview_proxy_path, waveform_path, status,
                      fingerprint_json, metadata_json, created_at
               FROM asset WHERE id=?""",
            (
                second_asset_id,
                f"sources/web/p-{second_token}/index.html",
                first["asset_id"],
            ),
        )
        connection.execute(
            """INSERT INTO web_asset(asset_id, manifest_json, source_hash)
               SELECT ?, manifest_json, ? FROM web_asset WHERE asset_id=?""",
            (second_asset_id, second_hash, first["asset_id"]),
        )
        connection.execute(
            """INSERT INTO clip
               SELECT ?, track_id, ?, timeline_start + duration, source_in,
                      duration, media_kind, speed_numerator, speed_denominator,
                      pitch_compensation, transform_json,
                      transform_keyframes_json, audio_json
               FROM clip WHERE id=?""",
            (second_clip_id, second_asset_id, first["clip_id"]),
        )
        state_row = connection.execute(
            "SELECT state_json, revision FROM web_clip_state WHERE clip_id=?",
            (first["clip_id"],),
        ).fetchone()
        assert state_row is not None
        state = json.loads(str(state_row[0]))
        state["source_hash"] = second_hash
        connection.execute(
            "INSERT INTO web_clip_state(clip_id, state_json, revision) VALUES (?, ?, ?)",
            (
                second_clip_id,
                json.dumps(state, ensure_ascii=False),
                int(state_row[1]),
            ),
        )
        connection.commit()
    finally:
        connection.close()

    code, result = _cli(
        _request(
            project,
            "project.upgrade",
            {},
            request_id="two-v4-assets-preflight",
        )
    )

    assert code == 1
    assert "Third-party editable-media v4 runtime" in json.dumps(result, ensure_ascii=False)
    with sqlite3.connect(project / "project.mfp") as connection:
        assert connection.execute("SELECT version FROM schema_info WHERE component='project'").fetchone() == (
            35,
        )
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='editable_media_upgrade'"
            ).fetchone()
            is None
        )
        assert [
            json.loads(row[0])["version"]
            for row in connection.execute("SELECT manifest_json FROM web_asset ORDER BY asset_id")
        ] == [4, 4]
    assert len(list((project / "sources" / "web").glob("p-*"))) == 2
    assert not list((project / "staging" / "web").glob("s-*"))
    assert not list((project / "archive" / "web").glob("f-*"))


def test_real_v4_project_upgrades_once_and_reaches_visible_v6_output(
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
    assert code == 0, json.dumps(upgrade, ensure_ascii=False, indent=2)
    assert upgrade["ok"] is True
    event = upgrade["collaboration"]["event"]
    assert event["operation"] == "project.upgrade"
    assert event["write_set"]
    assert all(change["action"] in {"create", "update", "delete"} for change in event["changes"])
    assert any(path.startswith("/assets/") for path in event["write_set"])
    assert any(path.startswith("/web/clips/") for path in event["write_set"])

    after = _project_web_state(project)
    assert after["schema_version"] == PROJECT_SCHEMA_VERSION
    assert after["raw_manifest"]["version"] == 6
    validate_editable_media_document(after["raw_manifest"])
    assert editable_media_source_hash(after["package"]) == after["source_hash"]
    assert _sha256(after["package"] / "editable-media-runtime.js") == _sha256(V6_RUNTIME_PATH)
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

    output = project / "exports" / "migrated-v6-proof.mp4"
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
    task_receipt = export["result"]["task"]
    code, waited = _cli(
        _request(
            project,
            "task.wait",
            {"task_id": task_receipt["id"], "timeout": 180},
        )
    )
    assert code == 0
    assert waited["result"]["task"]["status"] == "completed"
    ffprobe = RuntimeContext.discover().paths.ffprobe
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
