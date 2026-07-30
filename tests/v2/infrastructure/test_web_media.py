from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
from pydantic import ValidationError

import mediaflow.application.web_media_service as web_media_module
from mediaflow.application.sequence_service import SequenceService
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.application.web_media_service import (
    MANIFEST_FILE_NAME,
    WebMediaService,
    editable_media_source_hash,
    web_package_root,
)
from mediaflow.automation.dispatcher import execute_request
from mediaflow.composition import EditorProject
from mediaflow.domain.enums import AssetKind, ExportFormat, TrackKind
from mediaflow.domain.exports import ExportPreset
from mediaflow.domain.settings import GlobalSettings
from mediaflow.domain.storage_names import (
    WINDOWS_INTEROP_PATH_UTF16_LIMIT,
    utf16_units,
)
from mediaflow.domain.task_commands import ExportSequenceCommand
from mediaflow.domain.tasks import ArtifactReference
from mediaflow.domain.web_media import parse_editable_media_manifest
from mediaflow.infrastructure.chromium_runtime import find_chromium_executable
from mediaflow.infrastructure.mlt import MltExportService, TimelineCompiler
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from mediaflow.infrastructure.web_browser import BrowserWebPackageValidator
from mediaflow.infrastructure.web_capture_engine import web_capture_diagnostics
from mediaflow.infrastructure.web_render_service import WebRenderService

STARTER = Path(
    os.environ.get(
        "MEDIAFLOW_EDITABLE_MEDIA_PACKAGE",
        Path(__file__).resolve().parents[2] / "fixtures" / "editable-media-v3",
    )
).resolve()


def _service(repository: ProjectRepository) -> tuple[TimelineEditor, WebMediaService]:
    project = repository.catalog.get_project()
    editor = TimelineEditor(repository, project.main_sequence_id)
    return editor, WebMediaService(
        repository,
        lambda sequence_id: (
            editor
            if sequence_id == project.main_sequence_id
            else TimelineEditor(repository, sequence_id)
        ),
        BrowserWebPackageValidator(),
    )


def _add_web_clip(
    repository: ProjectRepository,
    editor: TimelineEditor,
    service: WebMediaService,
    *,
    duration: int = 3,
):
    project = repository.catalog.get_project()
    asset = service.import_package(STARTER)
    track = editor.add_track(TrackKind.VIDEO)
    clip = editor.add_clip(
        track_id=track.id,
        asset_id=asset.id,
        timeline_start=0,
        source_in=0,
        duration=duration,
    )
    return project, asset, clip


def test_editable_media_v3_full_chain(
    tmp_path: Path,
) -> None:
    repository = ProjectRepository.create(tmp_path / "V3 Web Project", "V3 Web Project")
    editor, service = _service(repository)
    application: EditorProject | None = None
    try:
        project, asset, clip = _add_web_clip(repository, editor, service)
        copied_root = web_package_root(
            repository.catalog.resolve_asset_path(asset),
            repository.web.get_web_asset_spec(asset.id).manifest,
        )
        receipt = next(
            (
                repository.project_dir
                / "sources"
                / "web"
                / "receipts"
            ).glob("r-*.json")
        )
        assert asset.kind == AssetKind.WEB
        assert copied_root.name.startswith("p-")
        assert json.loads(receipt.read_text(encoding="utf-8"))["status"] == "committed"
        assert asset.metadata.width == asset.metadata.height == 1080
        copied_files = {
            path.name for path in copied_root.iterdir() if path.is_file()
        }
        expected_files = {
            "editable-media.json",
            "editable-media-runtime.js",
            "index.html",
            "media-sources.json",
        }
        assert expected_files <= copied_files
        assert copied_files <= expected_files | {"fixture-origin.json"}

        initial_runtime = service.runtime_state(project.main_sequence_id, clip.id)
        assert set(initial_runtime) == {
            "scenes",
            "theme",
            "theme_bindings",
            "variant",
            "scene_id",
            "playback",
            "revision",
        }
        assert set(initial_runtime["scenes"]) == {"opening", "delivery"}
        assert initial_runtime["variant"] == {
            "id": "square",
            "width": 1080,
            "height": 1080,
        }
        assert initial_runtime["scenes"]["opening"]["data"]["title"] == (
            "One source, three ways to play"
        )

        updated = service.update_clip(
            project.main_sequence_id,
            clip.id,
            {"title": {"content": "Edited in MediaFlow", "x": 72}},
            scene_id="opening",
            expected_revision=0,
        )
        assert updated.revision == 1
        persisted = repository.web.get_web_clip_state(clip.id)
        assert persisted.scenes["opening"].layers["title"].content == "Edited in MediaFlow"
        assert "delivery" not in persisted.scenes

        editor.undo()
        assert repository.web.get_web_clip_state(clip.id).scenes == {}
        editor.redo()
        assert (
            repository.web.get_web_clip_state(clip.id)
            .scenes["opening"]
            .layers["title"]
            .x
            == 72
        )

        browser_state = service.runtime_state(project.main_sequence_id, clip.id)
        browser_state["scenes"]["opening"]["layers"]["title"]["x"] = 96
        committed = service.commit_runtime_state(
            project.main_sequence_id,
            clip.id,
            browser_state,
            expected_revision=1,
        )
        assert committed.revision == 2
        assert committed.scenes["opening"].layers["title"].x == 96

        copied = editor.copy_clip(clip.id, timeline_start=3)
        copied_state = repository.web.get_web_clip_state(copied.id)
        assert copied_state.scenes["opening"].layers["title"].content == "Edited in MediaFlow"
        assert copied_state.revision == 0
        copied_state = service.update_clip(
            project.main_sequence_id,
            copied.id,
            {
                "title": {
                    "content": "A distinct copied clip state",
                }
            },
            scene_id="opening",
            expected_revision=0,
        )
        assert copied_state.revision == 1

        timeline = repository.timeline.load_timeline(project.main_sequence_id)
        renderer = WebRenderService(repository, RuntimePaths.discover())
        competing = WebRenderService(repository, RuntimePaths.discover())
        with ThreadPoolExecutor(max_workers=2) as pool:
            paths = [
                future.result()
                for future in (
                    pool.submit(renderer.render_clip, timeline, clip.id),
                    pool.submit(competing.render_clip, timeline, clip.id),
                )
            ]
        assert paths[0] == paths[1]
        cache = paths[0]
        assert cache.is_file() and cache.suffix == ".mkv" and cache.stat().st_size > 0
        assert not cache.with_name(f"{cache.name}.lock").exists()
        assert not list(cache.parent.glob(f"{cache.stem}.*.partial{cache.suffix}"))
        diagnostics = web_capture_diagnostics(find_chromium_executable())
        assert diagnostics.last_metrics is not None
        assert diagnostics.last_metrics.worker_count == 1
        assert diagnostics.last_metrics.frame_count == 3
        assert diagnostics.last_metrics.captured_frames == 3
        assert diagnostics.last_metrics.fast_capture_workers == 0
        browser_launches = diagnostics.browser_launches
        copied_cache = renderer.render_clip(timeline, copied.id)
        assert copied_cache.is_file() and copied_cache != cache
        reused_browser = web_capture_diagnostics(find_chromium_executable())
        assert reused_browser.browser_launches == browser_launches

        probe = subprocess.run(
            [
                str(RuntimePaths.discover().ffprobe),
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_name,pix_fmt",
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

        protected_overlay = tmp_path / "protected-overlay.mkv"
        protected_overlay_bytes = b"existing overlay must survive"
        protected_overlay.write_bytes(protected_overlay_bytes)
        with pytest.raises(FileExistsError):
            renderer.export_clip(
                timeline,
                clip.id,
                protected_overlay,
                "overlay",
            )
        assert protected_overlay.read_bytes() == protected_overlay_bytes

        protected_video = tmp_path / "protected-video.mp4"
        protected_video_bytes = b"existing video must survive"
        protected_video.write_bytes(protected_video_bytes)
        with (
            protected_video.open("rb"),
            pytest.raises(PermissionError),
        ):
            renderer.export_clip(
                timeline,
                clip.id,
                protected_video,
                "video",
                overwrite=True,
            )
        assert protected_video.read_bytes() == protected_video_bytes
        assert not list(
            tmp_path.glob(".mf-web-video-*.tmp.mp4")
        )
        failed_exports = list(
            (tmp_path / "MediaFlow Failed Exports").glob(
                "mf-web-video-*.tmp.mp4"
            )
        )
        assert len(failed_exports) == 1
        assert failed_exports[0].stat().st_size > 0

        rendered = renderer.ensure_sequence(timeline)
        assert len(rendered) == 2 and all(path.is_file() for path in rendered)
        assert len(set(rendered)) == 2
        document = TimelineCompiler(repository).compile(timeline)
        assert str(cache) in document.xml
        assert str(repository.catalog.resolve_asset_path(asset)) not in document.xml

        output = tmp_path / "v3-web-final.mp4"
        result = MltExportService(
            TimelineCompiler(repository),
            RuntimePaths.discover(),
        ).export(
            timeline,
            ExportPreset(
                name="V3 web verification",
                format=ExportFormat.H264,
                container="mp4",
                video_codec="libx264",
                audio_codec=None,
                pixel_format="yuv420p",
            ),
            output,
        )
        assert result.output_path.is_file() and result.output_path.stat().st_size > 0

        short = SequenceService(repository).create_short_from_bounds(
            project.main_sequence_id,
            0,
            3,
            name="V3 Web short",
        )
        short_state = repository.timeline.load_timeline(short.id)
        assert (
            short_state.web_states[short_state.clips[0].id]
            .scenes["opening"]
            .layers["title"]
            .x
            == 96
        )

        copied_state = service.update_clip(
            project.main_sequence_id,
            copied.id,
            {
                "title": {
                    "content": "Rendered by EditorProject handoff",
                }
            },
            scene_id="opening",
            expected_revision=copied_state.revision,
        )
        handoff_state = repository.timeline.load_timeline(
            project.main_sequence_id
        )
        handoff_clips = {
            item.id: item for item in handoff_state.clips
        }
        expected_targets = {
            renderer.cache.target(
                handoff_state,
                handoff_clips[clip.id],
                asset,
            ).path.resolve(),
            renderer.cache.target(
                handoff_state,
                handoff_clips[copied.id],
                asset,
            ).path.resolve(),
        }
        assert len(expected_targets) == 2
        unrendered_targets = {
            path for path in expected_targets if not path.exists()
        }
        assert unrendered_targets

        application = EditorProject(
            repository,
            settings=GlobalSettings(),
            paths=RuntimePaths.discover(),
        )
        protected_handoff = tmp_path / "protected-web-handoff.fcpxml"
        protected_bytes = b"existing web handoff"
        protected_handoff.write_bytes(protected_bytes)
        with pytest.raises(FileExistsError):
            application.export_fcpxml(
                project.main_sequence_id,
                protected_handoff,
            )
        assert protected_handoff.read_bytes() == protected_bytes
        assert all(
            not path.exists() for path in unrendered_targets
        )

        handoff = application.export_fcpxml(
            project.main_sequence_id,
            tmp_path / "v3-web-handoff.fcpxml",
        )
        assert all(
            path.is_file() and path.stat().st_size > 0
            for path in expected_targets
        )
        handoff_root = ET.parse(handoff).getroot()
        resource_uris = {
            media_rep.attrib["src"]
            for media_rep in handoff_root.findall(
                "./resources/asset/media-rep"
            )
        }
        assert resource_uris == {
            path.as_uri() for path in expected_targets
        }
        assert all(
            not uri.lower().endswith("/index.html")
            for uri in resource_uris
        )
    finally:
        if application is not None:
            application.close()
        else:
            repository.close()


def test_invalid_export_suffix_is_rejected_before_render(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "Invalid export suffix"
    repository = ProjectRepository.create(project_root, "Invalid export suffix")
    editor, service = _service(repository)
    try:
        project, _, clip = _add_web_clip(repository, editor, service)
        timeline = repository.timeline.load_timeline(project.main_sequence_id)
        discovered = RuntimePaths.discover()
        isolated_runtime = tmp_path / "isolated-runtime"
        renderer = WebRenderService(
            repository,
            RuntimePaths(
                runtime_dir=isolated_runtime,
                ffmpeg=discovered.ffmpeg,
                ffprobe=discovered.ffprobe,
                melt=discovered.melt,
                native_qml=discovered.native_qml,
            ),
        )
        cache_target = renderer.cache.target(timeline, clip)
        destination = tmp_path / "uncreated-output" / "overlay.mp4"
        progress_events = []

        assert cache_target.path.suffix == ".mkv"
        assert not cache_target.path.exists()
        assert not destination.parent.exists()
        with pytest.raises(ValueError, match="overlay"):
            renderer.export_clip(
                timeline,
                clip.id,
                destination,
                "overlay",
                progress=progress_events.append,
            )

        assert progress_events == []
        assert not cache_target.path.exists()
        assert not destination.parent.exists()
        assert not (isolated_runtime / "cache" / "output-locks").exists()
    finally:
        repository.close()


def test_web_export_rejects_an_unusable_temporary_sibling_before_render(
    tmp_path: Path,
) -> None:
    repository = ProjectRepository.create(
        tmp_path / "Web export path preflight",
        "Web export path preflight",
    )
    editor, service = _service(repository)
    try:
        project, _, clip = _add_web_clip(repository, editor, service)
        timeline = repository.timeline.load_timeline(project.main_sequence_id)
        discovered = RuntimePaths.discover()
        isolated_runtime = tmp_path / "isolated-path-runtime"
        renderer = WebRenderService(
            repository,
            RuntimePaths(
                runtime_dir=isolated_runtime,
                ffmpeg=discovered.ffmpeg,
                ffprobe=discovered.ffprobe,
                melt=discovered.melt,
                native_qml=discovered.native_qml,
            ),
        )
        cache_target = renderer.cache.target(timeline, clip)
        output_parent = tmp_path
        destination = output_parent / "overlay.mkv"
        while (
            utf16_units(str(output_parent.resolve())) + 1 + 64
            <= WINDOWS_INTEROP_PATH_UTF16_LIMIT
        ):
            output_parent /= "deep-web-export-directory"
            destination = output_parent / "overlay.mkv"

        assert utf16_units(str(destination.resolve())) <= WINDOWS_INTEROP_PATH_UTF16_LIMIT
        assert not output_parent.exists()
        assert not cache_target.path.exists()
        with pytest.raises(ValueError, match="目录过深"):
            renderer.export_clip(
                timeline,
                clip.id,
                destination,
                "overlay",
            )
        assert not output_parent.exists()
        assert not cache_target.path.exists()
        assert not (isolated_runtime / "cache" / "output-locks").exists()
    finally:
        repository.close()


def test_sequence_export_task_rejects_a_conflict_before_rendering_web_media(
    tmp_path: Path,
) -> None:
    repository = ProjectRepository.create(
        tmp_path / "Web sequence export preflight",
        "Web sequence export preflight",
    )
    editor, service = _service(repository)
    project: EditorProject | None = None
    try:
        project_record, _, clip = _add_web_clip(repository, editor, service)
        timeline = repository.timeline.load_timeline(
            project_record.main_sequence_id
        )
        paths = RuntimePaths.discover()
        cache_target = WebRenderService(
            repository,
            paths,
        ).cache.target(timeline, clip)
        output = tmp_path / "existing-web-sequence.mp4"
        original = b"existing user export"
        output.write_bytes(original)
        project = EditorProject(
            repository,
            settings=GlobalSettings(),
            paths=paths,
        )

        command = ExportSequenceCommand(
            sequence_id=project_record.main_sequence_id,
            output_path=str(output),
            format=ExportFormat.H264,
            preset=ExportPreset(
                name="Web preflight",
                format=ExportFormat.H264,
                container="mp4",
                video_codec="libx264",
                audio_codec="aac",
                pixel_format="yuv420p",
            ),
        )
        task = project.start_task(command)
        completed = project.wait_for_task(task.id, timeout=30)

        assert completed.status.value == "failed"
        assert "already exists" in completed.error
        assert output.read_bytes() == original
        assert not cache_target.path.exists()

        successful_output = tmp_path / "new-web-sequence.mp4"
        successful_task = project.start_task(
            command.model_copy(
                update={"output_path": str(successful_output)}
            )
        )
        successful = project.wait_for_task(
            successful_task.id,
            timeout=90,
        )

        assert successful.status.value == "completed", successful.error
        assert successful_output.is_file()
        assert successful_output.stat().st_size > 0
        assert cache_target.path.is_file()
        assert cache_target.path.stat().st_size > 0
    finally:
        if project is not None:
            project.close()
        else:
            repository.close()


def test_editable_media_v3_contract_rejections(
    tmp_path: Path,
) -> None:
    manifest = json.loads((STARTER / "editable-media.json").read_text(encoding="utf-8"))
    manifest["version"] = 1
    with pytest.raises((ValueError, ValidationError), match="version"):
        parse_editable_media_manifest(manifest)

    manifest = json.loads((STARTER / "editable-media.json").read_text(encoding="utf-8"))
    manifest["scenes"].append(dict(manifest["scenes"][0]))
    with pytest.raises((ValueError, ValidationError), match="scene identifiers"):
        parse_editable_media_manifest(manifest)

    missing_sources = tmp_path / "missing-media-sources"
    shutil.copytree(STARTER, missing_sources)
    (missing_sources / "media-sources.json").rename(
        missing_sources / "media-sources.unavailable"
    )
    repository = ProjectRepository.create(tmp_path / "Invalid V3 Project", "Invalid V3")
    editor, service = _service(repository)
    try:
        with pytest.raises(FileNotFoundError, match="media-sources"):
            service.import_package(missing_sources)

        remote = tmp_path / "remote-v3-package"
        shutil.copytree(STARTER, remote)
        entry = remote / "index.html"
        entry.write_text(
            entry.read_text(encoding="utf-8").replace(
                "</body>",
                '<img src="https://example.com/undeclared.png"></body>',
            ),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="remote resources"):
            service.import_package(remote)

        non_deterministic = tmp_path / "non-deterministic-v3-package"
        shutil.copytree(STARTER, non_deterministic)
        entry = non_deterministic / "index.html"
        entry.write_text(
            entry.read_text(encoding="utf-8").replace(
                "</body>",
                """
<script>
let nonDeterministicSeekCount = 0;
window.addEventListener("editablemediatime", () => {
  nonDeterministicSeekCount += 1;
  const title = document.querySelector("[data-editable-id='title']");
  title.textContent = `Non-deterministic seek ${nonDeterministicSeekCount}`;
});
</script>
</body>
""",
            ),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="non-monotonic frame seeks"):
            service.import_package(non_deterministic)
    finally:
        repository.close()


def test_web_package_deep_tree_is_rejected_before_any_project_copy(
    tmp_path: Path,
    max_project_path: Path,
) -> None:
    source = tmp_path / "deep-web-package"
    shutil.copytree(STARTER, source)
    deep_file = (
        source
        / ("a" * 25)
        / ("b" * 25)
        / "unreferenced-extra.json"
    )
    deep_file.parent.mkdir(parents=True)
    deep_file.write_text("{}", encoding="utf-8")
    repository = ProjectRepository.create(max_project_path, "Deep Web")
    _editor, service = _service(repository)
    try:
        with pytest.raises(ValueError, match="路径过深"):
            service.import_package(source)

        assert not list((repository.project_dir / "sources" / "web").glob("p-*"))
        assert not list((repository.project_dir / "staging" / "web").glob("s-*"))
        assert not list((repository.project_dir / "archive" / "web").glob("f-*"))
        assert deep_file.is_file()
    finally:
        repository.close()


def test_web_package_accepts_the_deepest_complete_tree_that_fits_all_roots(
    tmp_path: Path,
    max_project_path: Path,
) -> None:
    source = tmp_path / "boundary-web-package"
    shutil.copytree(STARTER, source)
    placeholder_root = (
        max_project_path
        / "sources"
        / "web"
        / f"p-{'0' * 24}"
    )
    component_units = (
        WINDOWS_INTEROP_PATH_UTF16_LIMIT
        - utf16_units(str(placeholder_root))
        - 1
    )
    boundary_name = f"{'x' * (component_units - len('.txt'))}.txt"
    (source / boundary_name).write_text("boundary", encoding="utf-8")
    repository = ProjectRepository.create(max_project_path, "Boundary Web")
    _editor, service = _service(repository)
    try:
        asset = service.import_package(source)
        spec = repository.web.get_web_asset_spec(asset.id)
        package_root = web_package_root(
            repository.catalog.resolve_asset_path(asset),
            spec.manifest,
        )
        copied_boundary = package_root / boundary_name

        assert copied_boundary.is_file()
        assert (
            utf16_units(str(copied_boundary))
            == WINDOWS_INTEROP_PATH_UTF16_LIMIT
        )
        assert not list((repository.project_dir / "staging" / "web").glob("s-*"))
        assert not list((repository.project_dir / "archive" / "web").glob("f-*"))
    finally:
        repository.close()


def test_web_package_copy_failure_leaves_only_a_failure_archive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = ProjectRepository.create(tmp_path / "Copy Failure", "Copy Failure")
    _editor, service = _service(repository)
    copied = 0
    original_copy = web_media_module._copy_web_package_file

    def fail_after_one_file(source: str, destination: str) -> str:
        nonlocal copied
        if copied:
            raise OSError("injected package copy failure")
        copied += 1
        return original_copy(source, destination)

    monkeypatch.setattr(
        web_media_module,
        "_copy_web_package_file",
        fail_after_one_file,
    )
    try:
        with pytest.raises(OSError, match="injected package copy failure"):
            service.import_package(STARTER)

        assert copied == 1
        assert not list((repository.project_dir / "sources" / "web").glob("p-*"))
        assert not list((repository.project_dir / "staging" / "web").glob("s-*"))
        failed = list((repository.project_dir / "archive" / "web").glob("f-*"))
        assert len(failed) == 1
        assert any(path.is_file() for path in failed[0].rglob("*"))
        assert (STARTER / MANIFEST_FILE_NAME).is_file()
    finally:
        repository.close()


def test_web_package_database_failure_rolls_back_records_and_archives_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = ProjectRepository.create(tmp_path / "DB Failure", "DB Failure")
    _editor, service = _service(repository)
    source_hash = editable_media_source_hash(STARTER)

    def fail_spec_save(_spec) -> None:
        raise OSError("injected web spec failure")

    monkeypatch.setattr(
        repository.web,
        "save_web_asset_spec",
        fail_spec_save,
    )
    try:
        with pytest.raises(OSError, match="injected web spec failure"):
            service.import_package(STARTER)

        assert not [
            asset
            for asset in repository.catalog.list_assets()
            if asset.kind == AssetKind.WEB
        ]
        assert not list((repository.project_dir / "sources" / "web").glob("p-*"))
        assert not list((repository.project_dir / "staging" / "web").glob("s-*"))
        failed = list((repository.project_dir / "archive" / "web").glob("f-*"))
        failed_receipts = list(
            (repository.project_dir / "archive" / "web").glob("r-*.json")
        )
        assert len(failed) == len(failed_receipts) == 1
        assert editable_media_source_hash(failed[0]) == source_hash
        assert editable_media_source_hash(STARTER) == source_hash
    finally:
        repository.close()


def test_nested_web_entry_resolves_back_to_the_single_package_root(
    tmp_path: Path,
) -> None:
    source = tmp_path / "nested-entry"
    shutil.copytree(STARTER, source)
    pages = source / "pages"
    pages.mkdir()
    entry = source / "index.html"
    nested_entry = pages / "index.html"
    entry.replace(nested_entry)
    nested_entry.write_text(
        nested_entry.read_text(encoding="utf-8").replace(
            '<script src="editable-media-runtime.js"></script>',
            (
                '<script src="../editable-media-runtime.js" '
                'data-manifest="../editable-media.json"></script>'
            ),
        ),
        encoding="utf-8",
    )
    manifest_path = source / MANIFEST_FILE_NAME
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["entry"] = "pages/index.html"
    manifest_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    repository = ProjectRepository.create(tmp_path / "Nested Entry", "Nested Entry")
    _editor, service = _service(repository)
    try:
        asset = service.import_package(source)
        spec = repository.web.get_web_asset_spec(asset.id)
        imported_entry = repository.catalog.resolve_asset_path(asset)
        imported_root = web_package_root(imported_entry, spec.manifest)

        assert imported_entry.parent.name == "pages"
        assert imported_root.name.startswith("p-")
        assert (imported_root / MANIFEST_FILE_NAME).is_file()
        BrowserWebPackageValidator().validate(imported_root, spec.manifest)
    finally:
        repository.close()


def test_editable_media_v3_scene_features(
    tmp_path: Path,
) -> None:
    repository = ProjectRepository.create(tmp_path / "Extended V3 Web", "Extended V3 Web")
    editor, service = _service(repository)
    try:
        project, asset, clip = _add_web_clip(repository, editor, service, duration=4)
        state = service.select_variant(
            project.main_sequence_id,
            clip.id,
            "portrait",
            expected_revision=0,
        )
        state = service.update_clip(
            project.main_sequence_id,
            clip.id,
            {"title": {"content": "Opening title", "x": 120}},
            scene_id="opening",
            expected_revision=state.revision,
        )
        state = service.update_clip(
            project.main_sequence_id,
            clip.id,
            {"title": {"content": "Delivery title", "x": 180}},
            scene_id="delivery",
            expected_revision=state.revision,
        )
        state = service.set_keyframe(
            project.main_sequence_id,
            clip.id,
            "title",
            "opacity",
            900,
            0.5,
            scene_id="opening",
            easing={"kind": "ease_in_out"},
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
            {"left_value": "Delivery data"},
            scene_id="delivery",
            expected_revision=state.revision,
        )
        snapshot_file = tmp_path / "snapshot.json"
        snapshot_file.write_text(
            json.dumps({"right_value": "From JSON"}),
            encoding="utf-8",
        )
        state = service.update_data_from_file(
            project.main_sequence_id,
            clip.id,
            snapshot_file,
            scene_id="delivery",
            expected_revision=state.revision,
        )
        state = service.set_field_locks(
            project.main_sequence_id,
            clip.id,
            "title",
            ["content"],
            True,
            scene_id="opening",
            expected_revision=state.revision,
        )

        diff = service.diff_clip_update(
            project.main_sequence_id,
            clip.id,
            {"title": {"content": "Automation replacement", "opacity": 0.7}},
            scene_id="opening",
            expected_revision=state.revision,
        )
        assert diff.locked_paths == ["scenes.opening.layers.title.content"]
        with pytest.raises(PermissionError, match="locked"):
            service.update_clip(
                project.main_sequence_id,
                clip.id,
                {"title": {"content": "Automation replacement"}},
                scene_id="opening",
                actor="automation",
                expected_revision=state.revision,
            )

        runtime = service.runtime_state(project.main_sequence_id, clip.id)
        assert runtime["variant"]["id"] == "portrait"
        assert runtime["scenes"]["opening"]["layers"]["title"]["x"] == 120
        assert runtime["scenes"]["delivery"]["layers"]["title"]["x"] == 180
        assert runtime["scenes"]["delivery"]["data"]["right_value"] == "From JSON"
        assert runtime["theme"]["accent"] == "#ff0066"

        variants = service.create_variants(
            project.main_sequence_id,
            clip.id,
            [
                {"name": "Alice", "accent": "#1255ff"},
                {"name": "Bob", "accent": "#ff8811"},
            ],
            {
                "name": "scenes.opening.layers.title.content",
                "accent": "theme.accent",
            },
            name_template="Card {name}",
            actor="human",
        )
        assert [item.name for item in variants] == ["Card Alice", "Card Bob"]
        for result, expected in zip(variants, ["Alice", "Bob"], strict=True):
            variant_state = repository.web.get_web_clip_state(result.clip_id)
            assert variant_state.batch_name == f"Card {expected}"
            assert (
                variant_state.scenes["opening"].layers["title"].content
                == expected
            )

        replacement = tmp_path / "replacement-v3-package"
        shutil.copytree(STARTER, replacement)
        manifest_path = replacement / "editable-media.json"
        replacement_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        replacement_manifest["component"]["name"] = "Editable card replacement"
        manifest_path.write_text(
            json.dumps(replacement_manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        report = service.rebind_asset(asset.id, replacement, dry_run=True)
        assert not report.conflicts and clip.id in report.affected_clips
        committed = service.rebind_asset(asset.id, replacement, dry_run=False)
        rebound = repository.web.get_web_clip_state(clip.id)
        assert committed.new_source_hash != committed.old_source_hash
        assert rebound.source_hash == committed.new_source_hash
        assert rebound.scenes["delivery"].layers["title"].x == 180
    finally:
        repository.close()


def test_rebind_database_failure_keeps_the_old_package_readable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = ProjectRepository.create(tmp_path / "Rebind Failure", "Rebind Failure")
    _editor, service = _service(repository)
    asset = service.import_package(STARTER)
    old_spec = repository.web.get_web_asset_spec(asset.id)
    old_root = web_package_root(
        repository.catalog.resolve_asset_path(asset),
        old_spec.manifest,
    )
    replacement = tmp_path / "replacement"
    shutil.copytree(STARTER, replacement)
    manifest_path = replacement / MANIFEST_FILE_NAME
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["component"]["name"] = "Replacement that must roll back"
    manifest_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    original_save = repository.web.save_web_asset_spec

    def fail_new_spec(spec) -> None:
        if spec.source_hash != old_spec.source_hash:
            raise OSError("injected rebind database failure")
        original_save(spec)

    monkeypatch.setattr(repository.web, "save_web_asset_spec", fail_new_spec)
    try:
        with pytest.raises(OSError, match="injected rebind database failure"):
            service.rebind_asset(asset.id, replacement, dry_run=False)

        current_asset = repository.catalog.get_asset(asset.id)
        current_spec = repository.web.get_web_asset_spec(asset.id)
        assert current_spec == old_spec
        assert (
            web_package_root(
                repository.catalog.resolve_asset_path(current_asset),
                current_spec.manifest,
            )
            == old_root
        )
        BrowserWebPackageValidator().validate(old_root, old_spec.manifest)
        assert old_root.is_dir()
        assert not list((repository.project_dir / "staging" / "web").glob("s-*"))
        assert len(list((repository.project_dir / "archive" / "web").glob("f-*"))) == 1
    finally:
        repository.close()


def test_named_version_restore_keeps_the_immutable_pre_rebind_web_package(
    tmp_path: Path,
) -> None:
    repository = ProjectRepository.create(tmp_path / "Web Version", "Web Version")
    _editor, service = _service(repository)
    try:
        asset = service.import_package(STARTER)
        old_spec = repository.web.get_web_asset_spec(asset.id)
        old_root = web_package_root(
            repository.catalog.resolve_asset_path(asset),
            old_spec.manifest,
        )
        version = repository.records.create_project_version("Before web rebind")
        replacement = tmp_path / "replacement-version"
        shutil.copytree(STARTER, replacement)
        manifest_path = replacement / MANIFEST_FILE_NAME
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload["component"]["name"] = "Replacement kept beside history"
        manifest_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        report = service.rebind_asset(asset.id, replacement, dry_run=False)
        rebound_asset = repository.catalog.get_asset(asset.id)
        rebound_spec = repository.web.get_web_asset_spec(asset.id)
        rebound_root = web_package_root(
            repository.catalog.resolve_asset_path(rebound_asset),
            rebound_spec.manifest,
        )
        assert report.archive_path == str(old_root)
        assert rebound_root != old_root
        assert old_root.is_dir() and rebound_root.is_dir()
        BrowserWebPackageValidator().validate(rebound_root, rebound_spec.manifest)

        repository.records.restore_project_version(version.id)
        restored_asset = repository.catalog.get_asset(asset.id)
        restored_spec = repository.web.get_web_asset_spec(asset.id)
        restored_root = web_package_root(
            repository.catalog.resolve_asset_path(restored_asset),
            restored_spec.manifest,
        )
        assert restored_root == old_root
        assert restored_spec == old_spec
        BrowserWebPackageValidator().validate(restored_root, restored_spec.manifest)
        assert rebound_root.is_dir()
    finally:
        repository.close()


def test_publication_reconciliation_is_write_only_and_preserves_referenced_packages(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "Publication Recovery"
    repository = ProjectRepository.create(project_dir, "Publication Recovery")
    _editor, service = _service(repository)
    asset = service.import_package(STARTER)
    spec = repository.web.get_web_asset_spec(asset.id)
    referenced_root = web_package_root(
        repository.catalog.resolve_asset_path(asset),
        spec.manifest,
    )
    receipt_root = project_dir / "sources" / "web" / "receipts"
    referenced_receipt = next(receipt_root.glob("r-*.json"))
    referenced_payload = json.loads(referenced_receipt.read_text(encoding="utf-8"))
    referenced_payload["status"] = "pending"
    referenced_receipt.write_text(
        json.dumps(referenced_payload, separators=(",", ":")),
        encoding="utf-8",
    )
    orphan_token = "b" * 24
    orphan_root = project_dir / "sources" / "web" / f"p-{orphan_token}"
    shutil.copytree(STARTER, orphan_root)
    (receipt_root / f"r-{orphan_token}.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "asset_id": "uncommitted-asset",
                "source_hash": editable_media_source_hash(orphan_root),
                "token": orphan_token,
                "directory": f"p-{orphan_token}",
                "status": "pending",
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    staging_token = "c" * 24
    staging_root = project_dir / "staging" / "web" / f"s-{staging_token}"
    staging_root.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(STARTER, staging_root)
    repository.close()

    with ProjectRepository.open(project_dir, writable=False) as readonly:
        _service(readonly)
        assert referenced_root.is_dir()
        assert orphan_root.is_dir()
        assert staging_root.is_dir()
        assert json.loads(
            referenced_receipt.read_text(encoding="utf-8")
        )["status"] == "pending"

    with ProjectRepository.open(project_dir, writable=True) as writable:
        _service(writable)
        assert referenced_root.is_dir()
        assert not orphan_root.exists()
        assert not staging_root.exists()
        assert json.loads(
            referenced_receipt.read_text(encoding="utf-8")
        )["status"] == "committed"
        failures = list((project_dir / "archive" / "web").glob("f-*"))
        assert len(failures) == 2
        current_asset = writable.catalog.get_asset(asset.id)
        current_spec = writable.web.get_web_asset_spec(asset.id)
        assert (
            web_package_root(
                writable.catalog.resolve_asset_path(current_asset),
                current_spec.manifest,
            )
            == referenced_root
        )


def test_web_package_hash_includes_empty_directories(tmp_path: Path) -> None:
    package_root = tmp_path / "hash-package"
    shutil.copytree(STARTER, package_root)
    original_hash = editable_media_source_hash(package_root)

    empty_directory = package_root / "empty-directory"
    empty_directory.mkdir()
    hash_with_empty_directory = editable_media_source_hash(package_root)

    assert hash_with_empty_directory != original_hash
    empty_directory.rmdir()
    assert editable_media_source_hash(package_root) == original_hash


@pytest.mark.parametrize("receipt_status", ["pending", "committed"])
def test_reopening_rejects_an_empty_directory_added_to_a_published_web_package(
    tmp_path: Path,
    receipt_status: str,
) -> None:
    project_dir = tmp_path / f"Tampered {receipt_status} Publication"
    repository = ProjectRepository.create(project_dir, "Tampered Publication")
    _editor, service = _service(repository)
    asset = service.import_package(STARTER)
    spec = repository.web.get_web_asset_spec(asset.id)
    package_root = web_package_root(
        repository.catalog.resolve_asset_path(asset),
        spec.manifest,
    )
    receipt = next((project_dir / "sources" / "web" / "receipts").glob("r-*.json"))
    if receipt_status == "pending":
        payload = json.loads(receipt.read_text(encoding="utf-8"))
        payload["status"] = "pending"
        receipt.write_text(
            json.dumps(payload, separators=(",", ":")),
            encoding="utf-8",
        )
    (package_root / "tampered-empty-directory").mkdir()
    repository.close()

    with ProjectRepository.open(project_dir, writable=True) as reopened:
        with pytest.raises(RuntimeError, match="changed"):
            _service(reopened)


def test_v3_cli_chain(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MEDIAFLOW_RUNTIME_DIR", str(tmp_path / "runtime"))
    project_path = tmp_path / "CLI V3 Web Project"

    def request(operation: str, arguments: dict | None = None) -> dict:
        return {
            "protocol": "mediaflow-cli",
            "version": 1,
            "operation": operation,
            "project": str(project_path),
            "arguments": arguments or {},
        }

    created = execute_request(request("project.create", {"name": "CLI V3 Web Project"}))
    sequence_id = created["project"]["main_sequence_id"]
    imported = execute_request(request("web.import", {"source": str(STARTER)}))
    asset_id = imported["asset"]["id"]
    track_id = execute_request(
        request(
            "timeline.track.add",
            {"sequence_id": sequence_id, "kind": "video"},
        )
    )["track"]["id"]
    clip_id = execute_request(
        request(
            "timeline.clip.add",
            {
                "sequence_id": sequence_id,
                "track_id": track_id,
                "asset_id": asset_id,
                "timeline_start": 0,
                "source_in": 0,
                "duration": 2,
            },
        )
    )["clip"]["id"]

    updated = execute_request(
        request(
            "web.clip.update",
            {
                "sequence_id": sequence_id,
                "clip_id": clip_id,
                "scene_id": "opening",
                "updates": {"title": {"content": "CLI edit"}},
                "expected_revision": 0,
                "actor": "automation",
            },
        )
    )
    revision = updated["web_clip_state"]["revision"]
    selected = execute_request(
        request(
            "web.clip.variant.select",
            {
                "sequence_id": sequence_id,
                "clip_id": clip_id,
                "variant_id": "landscape",
                "expected_revision": revision,
            },
        )
    )
    revision = selected["web_clip_state"]["revision"]
    data_updated = execute_request(
        request(
            "web.clip.data.update",
            {
                "sequence_id": sequence_id,
                "clip_id": clip_id,
                "scene_id": "delivery",
                "values": {"left_value": "CLI data"},
                "expected_revision": revision,
            },
        )
    )
    revision = data_updated["web_clip_state"]["revision"]

    completed = subprocess.run(
        [sys.executable, "-m", "mediaflow.cli", "execute", "--request", "-"],
        input=json.dumps(request("web.clip.get", {"clip_id": clip_id})),
        capture_output=True,
        text=True,
        check=True,
        cwd=Path(__file__).resolve().parents[3],
    )
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    clip_state = payload["result"]["web_clip_state"]
    assert clip_state["scenes"]["opening"]["layers"]["title"]["content"] == "CLI edit"
    assert clip_state["scenes"]["delivery"]["data_snapshot"]["values"]["left_value"] == (
        "CLI data"
    )
    assert clip_state["variant"]["id"] == "landscape"
    assert clip_state["revision"] == revision

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
    artifact = ArtifactReference.model_validate(
        rendered["result"]["task"]["artifacts"][0]
    )
    assert artifact.resolve(project_path).is_file()

    variants = execute_request(
        request(
            "web.batch.create",
            {
                "source_sequence_id": sequence_id,
                "clip_id": clip_id,
                "records": [{"person": "Ada"}, {"person": "Lin"}],
                "bindings": {
                    "person": "scenes.opening.layers.title.content",
                },
                "name_template": "CLI {person}",
                "actor": "automation",
            },
        )
    )
    assert [item["name"] for item in variants["variants"]] == ["CLI Ada", "CLI Lin"]


@pytest.mark.parametrize("invalid_part", ["manifest", "state"])
def test_v17_rejects_v1(
    tmp_path: Path,
    invalid_part: str,
) -> None:
    root = tmp_path / f"Rejected V1 {invalid_part}"
    repository = ProjectRepository.create(root, f"Rejected V1 {invalid_part}")
    editor, service = _service(repository)
    project, asset, clip = _add_web_clip(repository, editor, service, duration=1)
    del project
    repository.close()

    with closing(sqlite3.connect(root / "project.mfp")) as connection, connection:
        if invalid_part == "manifest":
            manifest = json.loads(
                connection.execute(
                    "SELECT manifest_json FROM web_asset WHERE asset_id=?",
                    (asset.id,),
                ).fetchone()[0]
            )
            manifest["version"] = 1
            connection.execute(
                "UPDATE web_asset SET manifest_json=? WHERE asset_id=?",
                (json.dumps(manifest), asset.id),
            )
        else:
            connection.execute(
                "UPDATE web_clip_state SET state_json=? WHERE clip_id=?",
                (
                    json.dumps(
                        {
                            "title": {
                                "content": "Legacy",
                                "locked": True,
                            }
                        }
                    ),
                    clip.id,
                ),
            )
        connection.execute(
            "UPDATE schema_info SET version=17 WHERE component='project'"
        )

    with pytest.raises(RuntimeError, match="旧版 editable-media"):
        ProjectRepository.open(root, writable=True)
