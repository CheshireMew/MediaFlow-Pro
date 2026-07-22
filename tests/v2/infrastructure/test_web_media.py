from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from pydantic import ValidationError
from PySide6.QtGui import QImage

from mediaflow.application.sequence_service import SequenceService
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.application.web_media_service import WebMediaService
from mediaflow.automation.dispatcher import execute_request
from mediaflow.domain.enums import AssetKind, ExportFormat, TrackKind
from mediaflow.domain.exports import ExportPreset
from mediaflow.domain.project import ProjectProfile
from mediaflow.domain.web_media import EditableMediaManifest
from mediaflow.infrastructure.mlt import MltExportService, TimelineCompiler
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from mediaflow.infrastructure.web_browser import BrowserWebPackageValidator
from mediaflow.infrastructure.web_component_library import WebComponentLibrary
from mediaflow.infrastructure.web_render_service import WebRenderService

STARTER = Path(
    "E:/Work/BaiduSyncdisk/Code/Cheshire-skill/visual-multimedia/assets/web-media-starter"
)


def test_real_starter_import_edit_history_copy_render_and_mlt_consumption(tmp_path: Path) -> None:
    repository = ProjectRepository.create(tmp_path / "Editable Web Project", "Editable Web Project")
    project = repository.get_project()
    editor = TimelineEditor(repository, project.main_sequence_id)
    web = WebMediaService(repository, lambda _sequence_id: editor, BrowserWebPackageValidator())
    try:
        asset = web.import_package(STARTER)
        assert asset.kind == AssetKind.WEB
        copied_root = repository.resolve_asset_path(asset).parent
        assert copied_root == repository.project_dir / "sources" / "web" / asset.id
        assert (copied_root / "editable-media.json").is_file()

        video_track = next(track for track in editor.state.tracks if track.kind == TrackKind.VIDEO)
        clip = editor.add_clip(
            track_id=video_track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=3,
        )
        updated = web.update_clip(
            project.main_sequence_id,
            clip.id,
            {"title": {"content": "Edited in MediaFlow", "x": 72}},
            expected_revision=0,
        )
        assert updated.revision == 1
        assert repository.get_web_clip_state(clip.id).layers["title"].content == "Edited in MediaFlow"

        editor.undo()
        assert repository.get_web_clip_state(clip.id).layers == {}
        editor.redo()
        assert repository.get_web_clip_state(clip.id).layers["title"].x == 72

        copied = editor.copy_clip(clip.id, timeline_start=3)
        assert repository.get_web_clip_state(copied.id).layers["title"].content == "Edited in MediaFlow"
        assert repository.get_web_clip_state(copied.id).revision == 0

        state = repository.load_timeline(project.main_sequence_id)
        renderer = WebRenderService(repository, RuntimePaths.discover())
        competing_renderer = WebRenderService(repository, RuntimePaths.discover())
        with ThreadPoolExecutor(max_workers=2) as pool:
            concurrent_paths = [
                future.result()
                for future in (
                    pool.submit(renderer.render_clip, state, clip.id),
                    pool.submit(competing_renderer.render_clip, state, clip.id),
                )
            ]
        assert concurrent_paths[0] == concurrent_paths[1]
        cache = concurrent_paths[0]
        assert not list(cache.parent.glob(f"{cache.stem}.*.partial{cache.suffix}"))
        assert not cache.with_name(f"{cache.name}.lock").exists()
        assert cache.is_file() and cache.suffix == ".mkv" and cache.stat().st_size > 0
        probe = subprocess.run(
            [
                str(RuntimePaths.discover().ffprobe),
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_name,pix_fmt,nb_frames",
                "-of",
                "default=noprint_wrappers=1",
                str(cache),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert "codec_name=ffv1" in probe.stdout
        assert "pix_fmt=bgra" in probe.stdout
        animation_frames = tmp_path / "animation-frame-%02d.png"
        subprocess.run(
            [
                str(RuntimePaths.discover().ffmpeg),
                "-v",
                "error",
                "-i",
                str(cache),
                "-vf",
                "select=eq(n\\,0)+eq(n\\,2)",
                "-fps_mode",
                "passthrough",
                "-y",
                str(animation_frames),
            ],
            check=True,
        )
        first_animation_frame = tmp_path / "animation-frame-01.png"
        third_animation_frame = tmp_path / "animation-frame-02.png"
        assert first_animation_frame.is_file() and third_animation_frame.is_file()
        assert first_animation_frame.read_bytes() != third_animation_frame.read_bytes()

        before_source_change = renderer.cache.target(state, state.clips[0], asset)
        entry = repository.resolve_asset_path(asset)
        entry.write_text(entry.read_text(encoding="utf-8") + "\n<!-- source revision -->\n", encoding="utf-8")
        after_source_change = renderer.cache.target(state, state.clips[0], asset)
        assert after_source_change.key != before_source_change.key
        assert not after_source_change.path.exists()
        cache = renderer.render_clip(state, clip.id)
        assert cache == after_source_change.path

        rendered = renderer.ensure_sequence(state)
        assert len(rendered) == 2 and all(path.is_file() for path in rendered)
        document = TimelineCompiler(repository).compile(state)
        assert str(cache) in document.xml
        assert str(repository.resolve_asset_path(asset)) not in document.xml

        output = tmp_path / "editable-web-export.mp4"
        result = MltExportService(TimelineCompiler(repository), RuntimePaths.discover()).export(
            state,
            ExportPreset(
                name="Web verification",
                format=ExportFormat.H264,
                container="mp4",
                video_codec="libx264",
                audio_codec=None,
                pixel_format="yuv420p",
            ),
            output,
        )
        assert result.output_path.is_file() and result.output_path.stat().st_size > 0
        frame = tmp_path / "editable-web-export-frame.png"
        subprocess.run(
            [
                str(RuntimePaths.discover().ffmpeg),
                "-v",
                "error",
                "-i",
                str(output),
                "-frames:v",
                "1",
                "-y",
                str(frame),
            ],
            check=True,
        )
        assert frame.is_file() and frame.stat().st_size > 0

        short = SequenceService(repository).create_short_from_bounds(
            project.main_sequence_id,
            0,
            3,
            name="Web short",
        )
        short_state = repository.load_timeline(short.id)
        assert len(short_state.clips) == 1
        short_web_state = short_state.web_states[short_state.clips[0].id]
        assert short_web_state.layers["title"].content == "Edited in MediaFlow"

        _left, right = editor.split_clip(copied.id, 4)
        assert repository.get_web_clip_state(right.id).layers["title"].content == "Edited in MediaFlow"
        editor.delete_clip(right.id)
        with pytest.raises(KeyError):
            repository.get_web_clip_state(right.id)
    finally:
        repository.close()


def test_transparent_static_web_state_reaches_browser_cache_and_final_export(tmp_path: Path) -> None:
    package = tmp_path / "transparent-overlay"
    package.mkdir()
    manifest = {
        "protocol": "editable-media",
        "version": 1,
        "entry": "index.html",
        "canvas": {
            "width": 64,
            "height": 64,
            "background_mode": "transparent",
            "background_color": "#000000",
        },
        "timeline": {"duration_ms": 0, "fps": 25, "loop": "none"},
        "layers": [
            {
                "id": "badge",
                "name": "Badge",
                "kind": "text",
                "selector": "[data-editable-id='badge']",
                "default_bounds": {"x": 0, "y": 0, "width": 16, "height": 16},
                "editable": ["content", "x", "y", "width", "height"],
            }
        ],
        "resources": ["editable-media-runtime.js"],
    }
    (package / "editable-media.json").write_text(json.dumps(manifest), encoding="utf-8")
    (package / "index.html").write_text(
        """<!doctype html><html><head><style>
        html,body { margin:0; width:64px; height:64px; overflow:hidden; background:transparent; }
        [data-editable-id='badge'] { position:absolute; left:0; top:0; width:16px; height:16px;
          overflow:visible; white-space:nowrap; background:#ff0000; color:#ffffff;
          font:10px sans-serif; line-height:16px; }
        </style></head><body><div data-editable-id="badge">A</div>
        <script src="editable-media-runtime.js"></script></body></html>""",
        encoding="utf-8",
    )
    (package / "editable-media-runtime.js").write_text(
        (STARTER / "editable-media-runtime.js").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    repository = ProjectRepository.create(
        tmp_path / "Transparent Web Project",
        "Transparent Web Project",
        ProjectProfile(width=64, height=64, fps_numerator=25, fps_denominator=1),
    )
    project = repository.get_project()
    editor = TimelineEditor(repository, project.main_sequence_id)
    web = WebMediaService(repository, lambda _sequence_id: editor, BrowserWebPackageValidator())
    try:
        asset = web.import_package(package)
        track = next(item for item in editor.state.tracks if item.kind == TrackKind.VIDEO)
        clip = editor.add_clip(
            track_id=track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=1,
        )
        moved = web.update_clip(
            project.main_sequence_id,
            clip.id,
            {"badge": {"x": 32, "y": 24}},
            expected_revision=0,
        )
        renderer = WebRenderService(repository, RuntimePaths.discover())
        moved_cache = renderer.render_clip(repository.load_timeline(project.main_sequence_id), clip.id)
        moved_image = QImage(str(moved_cache))
        assert not moved_image.isNull()
        assert moved_image.pixelColor(1, 1).alpha() == 0
        moved_pixel = moved_image.pixelColor(33, 25)
        assert moved_pixel.alpha() > 240 and moved_pixel.red() > 220

        web.update_clip(
            project.main_sequence_id,
            clip.id,
            {"badge": {"content": "WWW"}},
            expected_revision=moved.revision,
        )
        content_cache = renderer.render_clip(
            repository.load_timeline(project.main_sequence_id),
            clip.id,
        )
        assert content_cache.read_bytes() != moved_cache.read_bytes()

        export_cases = {
            "png": tmp_path / "web-instance.png",
            "gif": tmp_path / "web-instance.gif",
            "alpha_video": tmp_path / "web-instance-alpha.mkv",
            "video": tmp_path / "web-instance.mp4",
            "overlay": tmp_path / "web-instance-overlay.png",
        }
        for format_name, destination in export_cases.items():
            result = renderer.export_clip(
                repository.load_timeline(project.main_sequence_id),
                clip.id,
                destination,
                format_name,
            )
            assert Path(result.output_path) == destination.resolve()
            assert destination.is_file() and destination.stat().st_size > 0

        output = tmp_path / "transparent-overlay-export.mp4"
        MltExportService(TimelineCompiler(repository), RuntimePaths.discover()).export(
            repository.load_timeline(project.main_sequence_id),
            ExportPreset(
                name="Transparent overlay verification",
                format=ExportFormat.H264,
                container="mp4",
                video_codec="libx264",
                audio_codec=None,
                pixel_format="yuv420p",
            ),
            output,
        )
        exported_frame = tmp_path / "transparent-overlay-export.png"
        subprocess.run(
            [
                str(RuntimePaths.discover().ffmpeg),
                "-v",
                "error",
                "-i",
                str(output),
                "-frames:v",
                "1",
                "-y",
                str(exported_frame),
            ],
            check=True,
        )
        exported_image = QImage(str(exported_frame))
        exported_pixel = exported_image.pixelColor(33, 25)
        assert exported_pixel.red() > 150 and exported_pixel.green() < 120
    finally:
        repository.close()


def test_editable_media_contract_rejects_unsupported_versions_duplicate_layers_and_missing_files(
    tmp_path: Path,
) -> None:
    manifest = json.loads((STARTER / "editable-media.json").read_text(encoding="utf-8"))
    manifest["version"] = 2
    with pytest.raises(ValidationError, match="version"):
        EditableMediaManifest.model_validate(manifest)

    manifest["version"] = 1
    manifest["layers"].append(dict(manifest["layers"][0]))
    with pytest.raises(ValidationError, match="unique"):
        EditableMediaManifest.model_validate(manifest)

    package = tmp_path / "missing-resource-package"
    package.mkdir()
    valid = json.loads((STARTER / "editable-media.json").read_text(encoding="utf-8"))
    (package / "editable-media.json").write_text(json.dumps(valid), encoding="utf-8")
    repository = ProjectRepository.create(tmp_path / "Missing Resource Project", "Missing Resource")
    editor = TimelineEditor(repository, repository.get_project().main_sequence_id)
    try:
        with pytest.raises(FileNotFoundError):
            WebMediaService(
                repository,
                lambda _sequence_id: editor,
                BrowserWebPackageValidator(),
            ).import_package(package)
    finally:
        repository.close()


def test_import_rejects_missing_runtime_interface_and_remote_dependencies(tmp_path: Path) -> None:
    base_manifest = {
        "protocol": "editable-media",
        "version": 1,
        "entry": "index.html",
        "canvas": {"width": 64, "height": 64, "background_mode": "transparent"},
        "timeline": {"duration_ms": 0, "fps": 30, "loop": "none"},
        "layers": [
            {
                "id": "title",
                "name": "Title",
                "kind": "text",
                "selector": "[data-editable-id='title']",
                "default_bounds": {"x": 0, "y": 0, "width": 64, "height": 20},
                "editable": ["content"],
            }
        ],
        "resources": [],
    }
    repository = ProjectRepository.create(tmp_path / "Invalid Runtime Project", "Invalid Runtime")
    editor = TimelineEditor(repository, repository.get_project().main_sequence_id)
    service = WebMediaService(
        repository,
        lambda _sequence_id: editor,
        BrowserWebPackageValidator(),
    )
    try:
        missing_interface = tmp_path / "missing-interface"
        missing_interface.mkdir()
        (missing_interface / "editable-media.json").write_text(
            json.dumps(base_manifest), encoding="utf-8"
        )
        (missing_interface / "index.html").write_text(
            "<div data-editable-id='title'>No runtime</div>", encoding="utf-8"
        )
        with pytest.raises(ValueError, match="runtime validation failed"):
            service.import_package(missing_interface)

        remote = tmp_path / "remote-dependency"
        remote.mkdir()
        (remote / "editable-media.json").write_text(json.dumps(base_manifest), encoding="utf-8")
        (remote / "index.html").write_text(
            """<img data-editable-id='title' src='https://example.com/image.png'>
            <script>
            const state = {layers: {}, revision: 0};
            window.editableMedia = {
              ready: Promise.resolve(true),
              getState: () => state,
              setState: value => value,
              setTime: value => value,
              getBounds: () => ({title: {x: 0, y: 0, width: 64, height: 20}})
            };
            </script>""",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="remote resources"):
            service.import_package(remote)
    finally:
        repository.close()


def test_extended_web_state_variants_component_library_and_rebind_share_one_state(
    tmp_path: Path,
) -> None:
    repository = ProjectRepository.create(tmp_path / "Extended Web Project", "Extended Web")
    project = repository.get_project()
    editor = TimelineEditor(repository, project.main_sequence_id)
    validator = BrowserWebPackageValidator()
    service = WebMediaService(repository, lambda sequence_id: (
        editor if sequence_id == project.main_sequence_id else TimelineEditor(repository, sequence_id)
    ), validator)
    try:
        asset = service.import_package(STARTER)
        track = next(item for item in editor.state.tracks if item.kind == TrackKind.VIDEO)
        clip = editor.add_clip(
            track_id=track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=4,
        )
        state = service.select_layout(project.main_sequence_id, clip.id, "portrait")
        state = service.update_clip(
            project.main_sequence_id,
            clip.id,
            {"title": {"content": "One state", "x": 120}},
            layout_id="portrait",
            expected_revision=state.revision,
        )
        assert state.layers["title"].content == "One state"
        assert state.layers["title"].x is None
        assert state.layout_overrides["portrait"]["title"].x == 120

        state = service.set_keyframe(
            project.main_sequence_id,
            clip.id,
            "title",
            "opacity",
            0,
            0.2,
            easing={"kind": "ease_in_out"},
            expected_revision=state.revision,
        )
        state = service.set_keyframe(
            project.main_sequence_id,
            clip.id,
            "title",
            "opacity",
            1000,
            1.0,
            easing={"kind": "ease_out"},
            expected_revision=state.revision,
        )
        state = service.update_theme(
            project.main_sequence_id,
            clip.id,
            {"accent": "#ff0066"},
            expected_revision=state.revision,
        )
        state = service.update_data(
            project.main_sequence_id,
            clip.id,
            {
                "left_value": "Snapshot ready",
                "chart_data": [{"label": "X", "value": 80}],
            },
            expected_revision=state.revision,
        )
        snapshot_file = tmp_path / "snapshot.json"
        snapshot_file.write_text(json.dumps({"right_value": "From JSON"}), encoding="utf-8")
        state = service.update_data_from_file(
            project.main_sequence_id,
            clip.id,
            snapshot_file,
            expected_revision=state.revision,
        )
        assert state.data_snapshot.source_kind == "file"
        assert state.data_snapshot.values["right_value"] == "From JSON"

        state = service.set_field_locks(
            project.main_sequence_id,
            clip.id,
            "title",
            ["content"],
            True,
            expected_revision=state.revision,
        )
        diff = service.diff_clip_update(
            project.main_sequence_id,
            clip.id,
            {"title": {"content": "AI replacement", "opacity": 0.7}},
            expected_revision=state.revision,
        )
        assert diff.locked_paths == ["layers.title.content"]
        with pytest.raises(PermissionError, match="locked"):
            service.update_clip(
                project.main_sequence_id,
                clip.id,
                {"title": {"content": "AI replacement"}},
                actor="automation",
                expected_revision=state.revision,
            )

        runtime = service.runtime_state(project.main_sequence_id, clip.id)
        assert runtime["layout"]["id"] == "portrait"
        assert runtime["layers"]["title"]["x"] == 120
        assert runtime["theme"]["accent"] == "#ff0066"
        assert runtime["data"]["chart_data"] == [{"label": "X", "value": 80}]

        variants = service.create_variants(
            project.main_sequence_id,
            clip.id,
            [
                {"name": "Alice", "accent": "#1255ff"},
                {"name": "Bob", "accent": "#ff8811"},
            ],
            {"name": "layers.title.content", "accent": "theme.accent"},
            name_template="Card {name}",
            actor="human",
        )
        assert [item.name for item in variants] == ["Card Alice", "Card Bob"]
        for result, expected in zip(variants, ["Alice", "Bob"], strict=True):
            variant_state = repository.get_web_clip_state(result.clip_id)
            assert variant_state.variant_name == f"Card {expected}"
            assert variant_state.layers["title"].content == expected

        library = WebComponentLibrary(tmp_path / "components", validator)
        installed = library.install(STARTER)
        assert installed.component_id == "editable-card"
        assert library.get("editable-card").version_hash == installed.version_hash
        assert library.list() == [installed]

        replacement = tmp_path / "replacement-package"
        shutil.copytree(STARTER, replacement)
        manifest_path = replacement / "editable-media.json"
        replacement_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        replacement_manifest["component"]["name"] = "Editable card v2"
        manifest_path.write_text(
            json.dumps(replacement_manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        old_root = repository.resolve_asset_path(asset).parent
        report = service.rebind_asset(asset.id, replacement, dry_run=True)
        assert not report.conflicts and clip.id in report.affected_clips
        committed = service.rebind_asset(asset.id, replacement, dry_run=False)
        assert committed.new_source_hash != committed.old_source_hash
        assert old_root.is_dir()
        assert repository.resolve_asset_path(repository.get_asset(asset.id)).parent != old_root
        assert repository.get_web_clip_state(clip.id).source_hash == committed.new_source_hash
    finally:
        repository.close()


def test_version_seventeen_web_state_migrates_whole_layer_lock_to_field_locks(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Web V17 Migration"
    repository = ProjectRepository.create(root, "Web V17 Migration")
    project = repository.get_project()
    editor = TimelineEditor(repository, project.main_sequence_id)
    service = WebMediaService(repository, lambda _sequence_id: editor, BrowserWebPackageValidator())
    asset = service.import_package(STARTER)
    track = next(item for item in editor.state.tracks if item.kind == TrackKind.VIDEO)
    clip = editor.add_clip(
        track_id=track.id,
        asset_id=asset.id,
        timeline_start=0,
        source_in=0,
        duration=1,
    )
    repository.close()

    with sqlite3.connect(root / "project.mfp") as connection:
        manifest = json.loads(
            connection.execute(
                "SELECT manifest_json FROM web_asset WHERE asset_id=?",
                (asset.id,),
            ).fetchone()[0]
        )
        title = next(item for item in manifest["layers"] if item["id"] == "title")
        title["editable"].append("locked")
        connection.execute(
            "UPDATE web_asset SET manifest_json=? WHERE asset_id=?",
            (json.dumps(manifest), asset.id),
        )
        connection.execute(
            "UPDATE web_clip_state SET state_json=? WHERE clip_id=?",
            (json.dumps({"title": {"content": "Legacy", "locked": True}}), clip.id),
        )
        connection.execute("UPDATE schema_info SET version=17 WHERE component='project'")

    with ProjectRepository.open(root, writable=True) as migrated:
        state = migrated.get_web_clip_state(clip.id)
        manifest = migrated.get_web_asset_spec(asset.id).manifest
        title = next(item for item in manifest.layers if item.id == "title")
        assert state.layers["title"].content == "Legacy"
        assert "locked" not in title.editable
        assert state.locks["title"] == title.editable


def test_versioned_cli_subprocess_reads_updates_and_renders_the_same_web_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MEDIAFLOW_RUNTIME_DIR", str(tmp_path / "runtime"))
    project_path = tmp_path / "CLI Web Project"

    def request(operation: str, arguments: dict | None = None) -> dict:
        return {
            "protocol": "mediaflow-cli",
            "version": 1,
            "operation": operation,
            "project": str(project_path),
            "arguments": arguments or {},
        }

    created = execute_request(request("project.create", {"name": "CLI Web Project"}))
    sequence_id = created["project"]["main_sequence_id"]
    installed = execute_request(request("web.component.install", {"source": str(STARTER)}))
    assert installed["component"]["component_id"] == "editable-card"
    listed = execute_request(request("web.component.list"))
    assert [item["component_id"] for item in listed["components"]] == ["editable-card"]
    component_asset = execute_request(
        request("web.component.import", {"component_id": "editable-card"})
    )
    assert component_asset["asset"]["kind"] == "web"
    imported = execute_request(request("web.import", {"source": str(STARTER)}))
    asset_id = imported["asset"]["id"]
    timeline = execute_request(request("timeline.get", {"sequence_id": sequence_id}))["timeline"]
    video_track_id = next(item["id"] for item in timeline["tracks"] if item["kind"] == "video")
    clip = execute_request(
        request(
            "timeline.clip.add",
            {
                "sequence_id": sequence_id,
                "track_id": video_track_id,
                "asset_id": asset_id,
                "timeline_start": 0,
                "source_in": 0,
                "duration": 2,
            },
        )
    )["clip"]
    clip_id = clip["id"]
    updated = execute_request(
        request(
            "web.clip.update",
            {
                "sequence_id": sequence_id,
                "clip_id": clip_id,
                "updates": {"title": {"content": "CLI edit"}},
                "expected_revision": 0,
                "actor": "automation",
            },
        )
    )
    revision = updated["web_clip_state"]["revision"]
    keyed = execute_request(
        request(
            "web.clip.keyframe.set",
            {
                "sequence_id": sequence_id,
                "clip_id": clip_id,
                "layer_id": "title",
                "field": "opacity",
                "time_ms": 0,
                "value": 0.4,
                "easing": {"kind": "ease_in_out"},
                "expected_revision": revision,
            },
        )
    )
    revision = keyed["web_clip_state"]["revision"]
    themed = execute_request(
        request(
            "web.clip.theme.update",
            {
                "sequence_id": sequence_id,
                "clip_id": clip_id,
                "changes": {"accent": "#fa0066"},
                "expected_revision": revision,
            },
        )
    )
    revision = themed["web_clip_state"]["revision"]
    data_updated = execute_request(
        request(
            "web.clip.data.update",
            {
                "sequence_id": sequence_id,
                "clip_id": clip_id,
                "values": {"left_value": "CLI data"},
                "expected_revision": revision,
            },
        )
    )
    revision = data_updated["web_clip_state"]["revision"]
    locked = execute_request(
        request(
            "web.clip.lock.update",
            {
                "sequence_id": sequence_id,
                "clip_id": clip_id,
                "layer_id": "title",
                "fields": ["x"],
                "locked": True,
                "expected_revision": revision,
            },
        )
    )
    revision = locked["web_clip_state"]["revision"]
    diff = execute_request(
        request(
            "web.clip.diff",
            {
                "sequence_id": sequence_id,
                "clip_id": clip_id,
                "updates": {"title": {"x": 300}},
                "expected_revision": revision,
                "actor": "automation",
            },
        )
    )
    assert diff["diff"]["locked_paths"] == ["layers.title.x"]
    laid_out = execute_request(
        request(
            "web.clip.layout.select",
            {
                "sequence_id": sequence_id,
                "clip_id": clip_id,
                "layout_id": "square",
                "expected_revision": revision,
            },
        )
    )
    revision = laid_out["web_clip_state"]["revision"]

    subprocess_request = request("web.clip.get", {"clip_id": clip_id})
    completed = subprocess.run(
        [sys.executable, "-m", "mediaflow.cli", "execute", "--request", "-"],
        input=json.dumps(subprocess_request),
        capture_output=True,
        text=True,
        check=True,
        cwd=Path(__file__).resolve().parents[3],
    )
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["result"]["web_clip_state"]["layers"]["title"]["content"] == "CLI edit"
    assert payload["result"]["web_clip_state"]["revision"] == revision
    assert payload["result"]["web_clip_state"]["theme"]["accent"] == "#fa0066"
    assert payload["result"]["web_clip_state"]["data_snapshot"]["values"]["left_value"] == (
        "CLI data"
    )

    render = subprocess.run(
        [sys.executable, "-m", "mediaflow.cli", "execute", "--request", "-"],
        input=json.dumps(
            request(
                "web.clip.render",
                {"sequence_id": sequence_id, "clip_id": clip_id, "timeout": 60},
            )
        ),
        capture_output=True,
        text=True,
        check=True,
        cwd=Path(__file__).resolve().parents[3],
    )
    rendered = json.loads(render.stdout)
    assert rendered["ok"] is True
    assert rendered["result"]["task"]["status"] == "completed"
    assert Path(rendered["result"]["task"]["artifacts"][0]).is_file()

    overlay = tmp_path / "cli-overlay.mkv"
    exported = execute_request(
        request(
            "web.clip.export",
            {
                "sequence_id": sequence_id,
                "clip_id": clip_id,
                "output_path": str(overlay),
                "format": "overlay",
            },
        )
    )
    assert Path(exported["export"]["output_path"]).is_file()

    task_overlay = tmp_path / "cli-task-overlay.mkv"
    task_export = execute_request(
        request(
            "task.start",
            {
                "sequence_id": sequence_id,
                "task_command": {
                    "command_type": "export_web_clip",
                    "sequence_id": sequence_id,
                    "clip_id": clip_id,
                    "output_path": str(task_overlay),
                    "format": "overlay",
                },
                "timeout": 60,
            },
        )
    )
    assert task_export["task"]["status"] == "completed"
    assert task_overlay.is_file()

    variant_csv = tmp_path / "variants.csv"
    variant_csv.write_text("person\nAda\nLin\n", encoding="utf-8")
    variants = execute_request(
        request(
            "web.batch.create",
            {
                "source_sequence_id": sequence_id,
                "clip_id": clip_id,
                "source": str(variant_csv),
                "bindings": {"person": "layers.title.content"},
                "name_template": "CLI {person}",
                "actor": "automation",
            },
        )
    )
    assert [item["name"] for item in variants["variants"]] == ["CLI Ada", "CLI Lin"]
