from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import pytest
from pydantic import ValidationError

import mediaflow.infrastructure.web_browser_cache_renderer as web_browser_cache_module
import mediaflow.infrastructure.web_direct_h264 as web_direct_h264_module
import mediaflow.infrastructure.web_direct_h264_codec as web_direct_h264_codec_module
import mediaflow.infrastructure.web_package_storage as web_package_module
import mediaflow.infrastructure.web_render_preflight as web_render_preflight_module
from mediaflow.application.sequence_service import SequenceService
from mediaflow.application.task_service import TaskStopped
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.application.web_media_service import (
    WebMediaServices,
)
from mediaflow.application.web_package_files import MANIFEST_FILE_NAME, web_package_root
from mediaflow.composition import EditorProject
from mediaflow.domain.enums import (
    AssetKind,
    ClipMediaKind,
    ExportFormat,
    TaskStatus,
    TrackKind,
)
from mediaflow.domain.exports import ExportPreset
from mediaflow.domain.project import ProjectProfile
from mediaflow.domain.settings import ServiceSettings
from mediaflow.domain.storage_names import (
    WINDOWS_INTEROP_PATH_UTF16_LIMIT,
    utf16_units,
)
from mediaflow.domain.task_commands import ExportSequenceCommand
from mediaflow.domain.tasks import ArtifactReference
from mediaflow.domain.web_manifest import parse_editable_media_manifest
from mediaflow.domain.web_package_paths import media_mime_type
from mediaflow.infrastructure.editable_media_contract import editable_media_contract
from mediaflow.infrastructure.fcpxml_export import FcpxmlExportService
from mediaflow.infrastructure.mlt import MltExportService, TimelineCompiler
from mediaflow.infrastructure.project_lock import ProcessFileLock
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.runtime_context import RuntimeContext
from mediaflow.infrastructure.structured_file_reader import LocalStructuredFileReader
from mediaflow.infrastructure.web_browser import BrowserWebPackageValidator
from mediaflow.infrastructure.web_capture_engine import (
    FastCaptureFallbackRequired,
    get_web_capture_engine,
    web_capture_diagnostics,
)
from mediaflow.infrastructure.web_package_storage import (
    LocalWebPackageStorage,
    editable_media_source_hash,
)
from mediaflow.infrastructure.web_render_service import WebRenderService
from tests.v2.editor_service_api import EditorServiceApi

STARTER = Path(
    os.environ.get(
        "MEDIAFLOW_EDITABLE_MEDIA_PACKAGE",
        Path(__file__).resolve().parents[2] / "fixtures" / "editable-media-v6",
    )
).resolve()
MEDIA_CASES = STARTER.parent / "editable-media-v6-cases"
EDITORIAL_TECHNOLOGY_COVER = MEDIA_CASES / "editorial-technology-diagram-cover"
REACT_REFERENCE = STARTER.parent / "editable-media-v6-react-reference"


def _browser_validator() -> BrowserWebPackageValidator:
    chromium = RuntimeContext.discover().paths.chromium
    if chromium is None:
        raise RuntimeError("Pinned Chromium is unavailable")
    return BrowserWebPackageValidator(chromium, editable_media_contract())


def _service(repository: ProjectRepository) -> tuple[TimelineEditor, WebMediaServices]:
    project = repository.projects.get_project()
    editor = TimelineEditor(repository, project.main_sequence_id)
    return editor, WebMediaServices(
        repository,
        lambda sequence_id: (
            editor if sequence_id == project.main_sequence_id else TimelineEditor(repository, sequence_id)
        ),
        _browser_validator(),
        LocalStructuredFileReader(),
        LocalWebPackageStorage(),
        editable_media_contract(),
    )


def _add_web_clip(
    repository: ProjectRepository,
    editor: TimelineEditor,
    service: WebMediaServices,
    *,
    duration: int = 3,
):
    project = repository.projects.get_project()
    asset = service.packages.import_package(STARTER)
    track = editor.add_track(TrackKind.VIDEO)
    clip = editor.add_clip(
        track_id=track.id,
        asset_id=asset.id,
        timeline_start=0,
        source_in=0,
        duration=duration,
    )
    return project, asset, clip


def test_editorial_technology_cover_real_consumer_chain(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "Editorial technology cover"
    repository = ProjectRepository.create(
        project_dir,
        "Editorial technology cover",
    )
    clip_id = ""
    sequence_id = ""
    try:
        editor, service = _service(repository)
        project = repository.projects.get_project()
        sequence_id = project.main_sequence_id
        asset = service.packages.import_package(EDITORIAL_TECHNOLOGY_COVER)
        assert asset.kind == AssetKind.WEB
        assert asset.metadata.width == 1500
        assert asset.metadata.height == 600

        track = editor.add_track(TrackKind.VIDEO)
        clip = editor.add_clip(
            track_id=track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=1,
        )
        clip_id = clip.id
        updated = service.clips.update_clip(
            sequence_id,
            clip.id,
            {
                "title": {
                    "content": "真实消费：科技图解封面",
                    "x": 96,
                }
            },
            scene_id="cover",
            expected_revision=0,
        )
        assert updated.revision == 1
        assert (
            repository.web.get_web_clip_state(clip.id).scenes["cover"].layers["title"].content
            == "真实消费：科技图解封面"
        )

        timeline = repository.timeline.load_timeline(sequence_id)
        renderer = WebRenderService(repository, RuntimeContext.discover().paths)
        cache = renderer.render_clip(timeline, clip.id)
        assert cache.is_file() and cache.stat().st_size > 0

        frame = tmp_path / "editorial-technology-cover-frame.png"
        subprocess.run(
            [
                str(RuntimeContext.discover().paths.ffmpeg),
                "-loglevel",
                "error",
                "-i",
                str(cache),
                "-frames:v",
                "1",
                str(frame),
            ],
            check=True,
        )
        assert frame.is_file() and frame.stat().st_size > 0

        compiled = TimelineCompiler(repository, RuntimeContext.discover().paths).compile(timeline)
        assert str(cache) in compiled.xml
        assert "index.html" not in compiled.xml

        output = tmp_path / "editorial-technology-cover.mp4"
        result = MltExportService(
            TimelineCompiler(repository, RuntimeContext.discover().paths),
            RuntimeContext.discover().paths,
        ).export(
            timeline,
            ExportPreset(
                name="Editorial technology cover verification",
                format=ExportFormat.H264,
                container="mp4",
                encoder_policy={"mode": "software"},
                audio_codec=None,
                pixel_format="yuv420p",
            ),
            output,
        )
        assert result.output_path.is_file()
        assert result.output_path.stat().st_size > 0
    finally:
        repository.close()

    with ProjectRepository.open(project_dir, writable=False) as reopened:
        state = reopened.timeline.load_timeline(sequence_id)
        assert state.web_states[clip_id].revision == 1
        assert state.web_states[clip_id].scenes["cover"].layers["title"].content == ("真实消费：科技图解封面")


def test_react_filmstrip_captures_only_requested_frames_without_full_cache(
    tmp_path: Path,
) -> None:
    with ProjectRepository.create(
        tmp_path / "React filmstrip",
        "React filmstrip",
    ) as repository:
        editor, service = _service(repository)
        project = repository.projects.get_project()
        asset = service.packages.import_package(REACT_REFERENCE)
        track = editor.add_track(TrackKind.VIDEO)
        clip = editor.add_clip(
            track_id=track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=90,
        )
        timeline = repository.timeline.load_timeline(project.main_sequence_id)
        renderer = WebRenderService(repository, RuntimeContext.discover().paths)
        full_target = renderer.cache.target(timeline, clip, asset)
        assert not WebRenderService._cache_is_ready(full_target)

        first = renderer.render_filmstrip_source(timeline, clip.id, 0)
        later = renderer.render_filmstrip_source(timeline, clip.id, 45)

        assert first != later
        assert not full_target.path.exists()
        for source in (first, later):
            manifest = json.loads(
                source.with_name(f"{source.name}.manifest.json").read_text(encoding="utf-8")
            )
            assert manifest["frame_count"] == 1
            assert manifest["has_audio"] is False
            assert manifest["probe"]["frame_count"] == 1
            assert manifest["capture"]["planned_mode"] == "auto"
            assert manifest["capture"]["actual_backend"] in {
                "drawelement",
                "screenshot",
            }
        first_png = tmp_path / "first.png"
        later_png = tmp_path / "later.png"
        for source, destination in ((first, first_png), (later, later_png)):
            subprocess.run(
                [
                    str(RuntimeContext.discover().paths.ffmpeg),
                    "-loglevel",
                    "error",
                    "-i",
                    str(source),
                    "-frames:v",
                    "1",
                    str(destination),
                ],
                check=True,
            )
        assert (
            hashlib.sha256(first_png.read_bytes()).digest() != hashlib.sha256(later_png.read_bytes()).digest()
        )


@pytest.mark.parametrize("dynamic_tag", ("canvas", "video"))
def test_web_render_preflight_routes_dynamic_surfaces_to_screenshot(
    tmp_path: Path,
    dynamic_tag: str,
) -> None:
    package = tmp_path / f"nested-{dynamic_tag}-package"
    shutil.copytree(STARTER, package)
    entry = package / "index.html"
    html = entry.read_text(encoding="utf-8")
    anchor = '    <div class="viewport" id="viewport">'
    assert anchor in html
    entry.write_text(
        html.replace(
            anchor,
            f'{anchor}\n      <{dynamic_tag} id="nested-{dynamic_tag}"></{dynamic_tag}>',
            1,
        ),
        encoding="utf-8",
    )

    with ProjectRepository.create(
        tmp_path / f"Nested {dynamic_tag} preflight",
        f"Nested {dynamic_tag} preflight",
    ) as repository:
        editor, service = _service(repository)
        project = repository.projects.get_project()
        asset = service.packages.import_package(package)
        track = editor.add_track(TrackKind.VIDEO)
        clip = editor.add_clip(
            track_id=track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=2,
        )
        timeline = repository.timeline.load_timeline(project.main_sequence_id)
        renderer = WebRenderService(repository, RuntimeContext.discover().paths)

        plan = renderer.inspect_clip_render(timeline, clip.id)

        assert plan.static_compatibility == "screenshot-required"
        assert plan.capture_mode == "screenshot"
        assert plan.strategy == "screenshot-only"
        assert plan.planned_backend == "frame-pipe"
        assert plan.fallback_backend is None
        assert [item.code for item in plan.findings] == [
            "dynamic-surface",
            "css-filter",
        ]
        assert plan.findings[0].path == "index.html"
        assert plan.actual_capture is None
        assert plan.cache_status == "missing"


def test_web_render_segments_keep_stable_completed_prefixes(tmp_path: Path) -> None:
    with ProjectRepository.create(
        tmp_path / "Stable web segments",
        "Stable web segments",
    ) as repository:
        editor, service = _service(repository)
        project, asset, clip = _add_web_clip(
            repository,
            editor,
            service,
            duration=450,
        )
        renderer = WebRenderService(repository, RuntimeContext.discover().paths)
        timeline = repository.timeline.load_timeline(project.main_sequence_id)
        original = renderer.cache.target(timeline, clip, asset)
        original_segments = renderer._segment_targets(original)

        editor.trim_clip(
            clip.id,
            timeline_start=clip.timeline_start,
            source_in=clip.source_in,
            duration=600,
        )
        extended_timeline = repository.timeline.load_timeline(project.main_sequence_id)
        extended_clip = next(item for item in extended_timeline.clips if item.id == clip.id)
        extended = renderer.cache.target(extended_timeline, extended_clip, asset)
        extended_segments = renderer._segment_targets(extended)

        assert original.segment_namespace == extended.segment_namespace
        assert [item.frame_count for _, item in original_segments] == [300, 150]
        assert [item.frame_count for _, item in extended_segments] == [300, 300]
        assert original_segments[0][1].key == extended_segments[0][1].key
        assert original_segments[1][1].key != extended_segments[1][1].key


def test_web_render_reuses_validated_segments_after_clip_extension(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(WebRenderService, "_SEGMENT_SECONDS", 0.1)
    with ProjectRepository.create(
        tmp_path / "Reusable web segments",
        "Reusable web segments",
    ) as repository:
        editor, service = _service(repository)
        project, asset, clip = _add_web_clip(
            repository,
            editor,
            service,
            duration=6,
        )
        renderer = WebRenderService(repository, RuntimeContext.discover().paths)
        timeline = repository.timeline.load_timeline(project.main_sequence_id)
        initial_target = renderer.cache.target(timeline, clip, asset)

        initial_cache = renderer.render_clip(timeline, clip.id)
        initial_manifest = json.loads(initial_target.manifest_path.read_text(encoding="utf-8"))

        assert initial_cache == initial_target.path
        assert initial_manifest["segmentation"]["segment_count"] == 2
        assert initial_manifest["segmentation"]["rendered_segment_count"] == 2
        assert initial_manifest["segmentation"]["reused_segment_count"] == 0

        editor.trim_clip(
            clip.id,
            timeline_start=clip.timeline_start,
            source_in=clip.source_in,
            duration=9,
        )
        extended_timeline = repository.timeline.load_timeline(project.main_sequence_id)
        extended_clip = next(item for item in extended_timeline.clips if item.id == clip.id)
        extended_target = renderer.cache.target(extended_timeline, extended_clip, asset)
        extended_cache = renderer.render_clip(extended_timeline, clip.id)
        extended_manifest = json.loads(
            extended_target.manifest_path.read_text(encoding="utf-8")
        )

        assert extended_cache == extended_target.path
        assert extended_manifest["probe"]["frame_count"] == 9
        assert extended_manifest["segmentation"]["segment_count"] == 3
        assert extended_manifest["segmentation"]["rendered_segment_count"] == 1
        assert extended_manifest["segmentation"]["reused_segment_count"] == 2
        assert renderer.inspect_clip_render(extended_timeline, clip.id).cache_status == "ready"


def _enable_short_non_4k_direct_h264(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        web_render_preflight_module,
        "DIRECT_H264_MIN_PIXEL_FRAMES",
        0,
    )
    monkeypatch.setattr(
        web_render_preflight_module,
        "_is_measured_direct_h264_profile",
        lambda _target: True,
    )
    monkeypatch.setattr(
        web_direct_h264_module,
        "_require_nvidia_gpu_headroom",
        lambda: None,
    )


def _require_windows_hardware_direct_h264(capture: dict[str, object]) -> None:
    if capture.get("actual_backend") == "webcodecs-h264":
        return
    reason = str(capture.get("fallback_reason") or "")
    unavailable_reasons = (
        "Chromium rejected the requested encoder config",
        "Chromium trace did not identify one platform encoder backend",
        "Chromium did not prove a Windows Media Foundation hardware encoder",
    )
    if any(value in reason for value in unavailable_reasons):
        pytest.skip(f"Windows runner has no usable hardware H.264 encoder: {reason}")
    pytest.fail(f"Direct H.264 unexpectedly fell back: {reason or capture}")


def test_direct_h264_selects_the_lowest_sufficient_encoder_level() -> None:
    select = web_direct_h264_codec_module.select_h264_codec

    assert select(1920, 1080, 30, 1) == "avc1.4D0028"
    assert select(1920, 1080, 60, 1) == "avc1.64002A"
    assert select(3840, 2160, 30, 1) == "avc1.640033"
    assert select(3840, 2160, 60, 1) == "avc1.640034"
    with pytest.raises(
        web_direct_h264_codec_module.DirectH264FallbackRequired,
        match="exceed H.264 Level 5.2",
    ):
        select(7680, 4320, 60, 1)


def test_direct_h264_rejects_saturated_nvidia_gpu_before_browser_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(web_direct_h264_module.shutil, "which", lambda _name: "nvidia-smi")
    monkeypatch.setattr(
        web_direct_h264_module.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=["nvidia-smi"],
            returncode=0,
            stdout="100, 7909, 8192\n",
            stderr="",
        ),
    )

    with pytest.raises(
        web_direct_h264_module.DirectH264FallbackRequired,
        match="utilization=100%.*memory=96.5%",
    ):
        web_direct_h264_module._require_nvidia_gpu_headroom()

    monkeypatch.setattr(
        web_direct_h264_module.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=["nvidia-smi"],
            returncode=0,
            stdout="20, 2048, 8192\n",
            stderr="",
        ),
    )
    admission = web_direct_h264_module._require_nvidia_gpu_headroom()
    assert admission == {
        "provider": "nvidia-smi",
        "utilization_percent": 20,
        "memory_used_mib": 2048,
        "memory_total_mib": 8192,
        "memory_percent": 25.0,
        "threshold_percent": 90,
    }


def test_direct_h264_preflight_is_limited_to_measured_uhd_4k30_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = web_render_preflight_module._is_measured_direct_h264_profile
    assert profile(
        SimpleNamespace(
            width=3840,
            height=2160,
            fps_numerator=30,
            fps_denominator=1,
        )
    )
    assert profile(
        SimpleNamespace(
            width=2160,
            height=3840,
            fps_numerator=30_000,
            fps_denominator=1001,
        )
    )
    for width, height, fps_numerator, fps_denominator in (
        (1920, 1080, 30, 1),
        (1280, 720, 30, 1),
        (3840, 2160, 60, 1),
        (3840, 2160, 24, 1),
    ):
        assert not profile(
            SimpleNamespace(
                width=width,
                height=height,
                fps_numerator=fps_numerator,
                fps_denominator=fps_denominator,
            )
        )

    with ProjectRepository.create(
        tmp_path / "Direct H264 workload preflight",
        "Direct H264 workload preflight",
        ProjectProfile(fps_numerator=60, fps_denominator=1),
    ) as repository:
        editor, service = _service(repository)
        project, _asset, clip = _add_web_clip(
            repository,
            editor,
            service,
            duration=300,
        )
        renderer = WebRenderService(repository, RuntimeContext.discover().paths)
        square_timeline = repository.timeline.load_timeline(project.main_sequence_id)

        square_plan = renderer.inspect_clip_render(square_timeline, clip.id)

        assert square_plan.planned_backend == "frame-pipe"
        assert any(
            "too short" in reason for reason in square_plan.backend_selection_reasons
        )

        service.clips.select_variant(
            project.main_sequence_id,
            clip.id,
            "landscape",
            expected_revision=0,
        )
        landscape_timeline = repository.timeline.load_timeline(project.main_sequence_id)

        landscape_plan = renderer.inspect_clip_render(landscape_timeline, clip.id)

        assert landscape_plan.planned_backend == "frame-pipe"
        assert any(
            "UHD 3840x2160" in reason
            for reason in landscape_plan.backend_selection_reasons
        )

        monkeypatch.setattr(
            web_render_preflight_module,
            "_is_measured_direct_h264_profile",
            lambda _target: True,
        )
        landscape_plan = renderer.inspect_clip_render(landscape_timeline, clip.id)

        assert landscape_plan.planned_backend == "webcodecs-h264"
        assert landscape_plan.fallback_backend == "frame-pipe"


def test_opaque_long_web_render_uses_bounded_direct_h264(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_short_non_4k_direct_h264(monkeypatch)
    with ProjectRepository.create(
        tmp_path / "Direct H264 web render",
        "Direct H264 web render",
    ) as repository:
        editor, service = _service(repository)
        project, asset, clip = _add_web_clip(
            repository,
            editor,
            service,
            duration=30,
        )
        timeline = repository.timeline.load_timeline(project.main_sequence_id)
        renderer = WebRenderService(repository, RuntimeContext.discover().paths)
        target = renderer.cache.target(timeline, clip, asset)

        plan = renderer.inspect_clip_render(timeline, clip.id)
        assert plan.planned_backend == "webcodecs-h264"
        assert plan.fallback_backend == "frame-pipe"

        cache = renderer.render_clip(timeline, clip.id)
        manifest = json.loads(target.manifest_path.read_text(encoding="utf-8"))
        capture = manifest["capture"]
        _require_windows_hardware_direct_h264(capture)
        encoder = capture["encoder"]

        assert cache == target.path and cache.is_file()
        assert manifest["schema"] == "mediaflow-web-render-cache/v5"
        assert capture["plan_digest"] == plan.plan_digest
        assert capture["planned_backend"] == "webcodecs-h264"
        assert capture["actual_backend"] == "webcodecs-h264"
        assert capture["fallback_reason"] is None
        assert encoder["requested_hardware_acceleration"] == "prefer-hardware"
        assert encoder["hardware_acceleration_verified"] is True
        assert encoder["zero_copy_verified"] is False
        assert encoder["attestation_method"] == "chromium-trace"
        assert encoder["actual_encoder_name"] == "MediaFoundationVideoEncodeAccelerator"
        assert encoder["actual_encoder_type"] == "hardware"
        assert encoder["encoder_storage_type"] == "unknown"
        assert encoder["input_copy_path"] == "gpu-readback-to-memory"
        assert 1 <= encoder["attested_frames"] <= 8
        assert encoder["trace_event_count"] > 0
        assert encoder["platform_encode_events"] >= encoder["attested_frames"]
        assert encoder["platform_output_events"] >= encoder["attested_frames"]
        assert encoder["gpu_readback_events"] >= encoder["attested_frames"]
        assert encoder["maximum_encode_queue_size"] <= 4
        assert encoder["maximum_pending_writes"] <= 4
        assert encoder["encoded_chunks"] == 30
        assert encoder["encoded_bytes"] > 0
        assert encoder["timestamps_monotonic"] is True
        assert encoder["exact_frame_time_boundaries"] is True
        assert manifest["probe"]["codec_name"] == "h264"
        assert manifest["probe"]["pixel_format"] == "yuv420p"
        assert manifest["probe"]["frame_count"] == 30
        assert manifest["probe"]["color_space"] == "bt709"
        assert manifest["probe"]["packet_pts_monotonic"] is True
        assert manifest["probe"]["packet_dts_monotonic"] is True
        assert manifest["probe"]["maximum_video_clock_error_microseconds"] <= 1_000
        assert manifest["probe"]["audio_video_end_drift_microseconds"] is None
        assert not list(cache.parent.glob(".mf-web-h264-*.h264"))
        assert not list(cache.parent.glob(".mf-web-render-*.mkv"))

        inspected = renderer.inspect_clip_render(timeline, clip.id)
        assert inspected.cache_status == "ready"
        assert inspected.actual_capture is not None
        assert inspected.actual_capture.backend == "webcodecs-h264"
        before = cache.stat().st_mtime_ns
        assert renderer.render_clip(timeline, clip.id) == cache
        assert cache.stat().st_mtime_ns == before


def test_direct_h264_uses_rational_frame_boundaries_without_long_run_drift() -> None:
    from mediaflow.infrastructure.web_direct_h264 import _round_microseconds

    fps_numerator = 30_000
    fps_denominator = 1001
    frame_count = round(10 * 60 * fps_numerator / fps_denominator)
    boundaries = [
        _round_microseconds(index, fps_numerator, fps_denominator)
        for index in range(frame_count + 1)
    ]
    durations = [
        right - left
        for left, right in zip(boundaries, boundaries[1:], strict=False)
    ]
    exact_end = Fraction(
        frame_count * 1_000_000 * fps_denominator,
        fps_numerator,
    )

    assert set(durations) == {33_366, 33_367}
    assert all(duration > 0 for duration in durations)
    assert sum(durations) == boundaries[-1]
    assert abs(Fraction(boundaries[-1]) - exact_end) <= Fraction(1, 2)


def test_direct_h264_failure_restarts_the_complete_frame_pipe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mediaflow.infrastructure.web_direct_h264 import DirectH264FallbackRequired

    _enable_short_non_4k_direct_h264(monkeypatch)

    with ProjectRepository.create(
        tmp_path / "Direct H264 fallback",
        "Direct H264 fallback",
    ) as repository:
        editor, service = _service(repository)
        project, asset, clip = _add_web_clip(
            repository,
            editor,
            service,
            duration=30,
        )
        timeline = repository.timeline.load_timeline(project.main_sequence_id)
        renderer = WebRenderService(repository, RuntimeContext.discover().paths)
        target = renderer.cache.target(timeline, clip, asset)

        def fail_direct(**_arguments):
            raise DirectH264FallbackRequired("injected direct encoder failure")

        monkeypatch.setattr(
            web_browser_cache_module,
            "render_webcodecs_h264",
            fail_direct,
        )
        cache = renderer.render_clip(timeline, clip.id)
        manifest = json.loads(target.manifest_path.read_text(encoding="utf-8"))

        assert cache.is_file()
        assert manifest["capture"]["planned_backend"] == "webcodecs-h264"
        assert manifest["capture"]["actual_backend"] in {"drawelement", "screenshot"}
        assert "injected direct encoder failure" in manifest["capture"]["fallback_reason"]
        assert manifest["capture"]["encoder"] is None
        assert manifest["probe"]["codec_name"] == "ffv1"
        assert manifest["probe"]["pixel_format"] == "bgra"
        assert not list(cache.parent.glob(".mf-web-h264-*.h264"))
        assert not list(cache.parent.glob(".mf-web-render-*.mkv"))


def _native_source_record(
    *,
    source_id: str,
    media_type: str,
    relative_path: str,
    binding: dict[str, object],
    package_root: Path,
) -> dict[str, object]:
    path = package_root / relative_path
    mime_type = media_mime_type(relative_path)
    assert mime_type is not None
    return {
        "id": source_id,
        "media_type": media_type,
        "file": relative_path,
        "binding": binding,
        "representation": {
            "kind": "source",
            "source_id": None,
            "build": None,
            "verification": None,
        },
        "acquisition": {
            "method": "project-owned",
            "source_url": "",
            "captured_at": "2026-07-30T00:00:00.000Z",
        },
        "rights": {
            "status": "confirmed",
            "license": "project-owned",
            "attribution": "MediaFlow test fixture",
            "terms_url": "",
        },
        "usage": "native editable media pipeline verification",
        "integrity": {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size,
            "mime_type": mime_type,
        },
        "generation": None,
        "speech": None,
        "provenance_runs": [
            {
                "recorded_at": "2026-07-30T00:00:00.000Z",
                "provider": "mediaflow-tests",
                "job_id": source_id,
                "capture": None,
            }
        ],
        "subject": {"x": 0.5, "y": 0.5},
        "crops": {},
        "notes": "Generated deterministically for the real native media chain test.",
    }


def _build_native_media_package(tmp_path: Path) -> Path:
    package_root = tmp_path / "editable-media-native"
    shutil.copytree(STARTER, package_root)
    assets = package_root / "assets"
    assets.mkdir()
    paths = RuntimeContext.discover().paths
    video = assets / "native-underlay.mkv"
    audio = assets / "native-audio.wav"
    subprocess.run(
        [
            str(paths.ffmpeg),
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x16a34a:s=320x180:r=30:d=0.3",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=0.3",
            "-shortest",
            "-c:v",
            "ffv1",
            "-level",
            "3",
            "-pix_fmt",
            "bgra",
            "-c:a",
            "flac",
            "-y",
            str(video),
        ],
        check=True,
    )
    subprocess.run(
        [
            str(paths.ffmpeg),
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:sample_rate=48000:duration=0.1",
            "-c:a",
            "pcm_s16le",
            "-y",
            str(audio),
        ],
        check=True,
    )

    manifest_path = package_root / "editable-media.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["data_fields"].extend(
        [
            {
                "id": "native_video",
                "name": "Native video",
                "kind": "media-source",
                "default": "native-underlay",
            },
            {
                "id": "native_audio",
                "name": "Native audio",
                "kind": "media-source",
                "default": "native-audio",
            },
        ]
    )
    for scene in manifest["scenes"]:
        scene["duration_ms"] = 200
        for step_index, step in enumerate(scene["steps"]):
            step["at_ms"] = step_index * 70
    for variant in manifest["variants"]:
        variant["canvas"]["background_mode"] = "transparent"
        variant["canvas"]["background_color"] = "#00000000"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    sources = {
        "protocol": "visual-multimedia-media-sources",
        "version": 4,
        "sources": [
            _native_source_record(
                source_id="native-underlay",
                media_type="video",
                relative_path="assets/native-underlay.mkv",
                binding={
                    "pipeline": "native-underlay",
                    "fit": "cover",
                    "playback": "hold",
                    "source_in_ms": 0,
                    "audio": "include",
                    "gain_db": -3,
                },
                package_root=package_root,
            ),
            _native_source_record(
                source_id="native-audio",
                media_type="audio",
                relative_path="assets/native-audio.wav",
                binding={
                    "pipeline": "native-audio",
                    "loop": "repeat",
                    "source_in_ms": 0,
                    "gain_db": -6,
                },
                package_root=package_root,
            ),
        ],
    }
    (package_root / "media-sources.json").write_text(
        json.dumps(sources, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    index_path = package_root / "index.html"
    index = index_path.read_text(encoding="utf-8")
    index = index.replace('data-duration="6"', 'data-duration="0.4"')
    index = index.replace(
        "</head>",
        (
            "<style>"
            "body,.media-canvas{background:transparent!important}"
            ".media-canvas::before{display:none!important}"
            "#native-test-overlay{position:absolute;left:20px;top:20px;"
            "width:120px;height:120px;background:#ff00ff;z-index:9999}"
            "</style></head>"
        ),
    )
    index = index.replace(
        "</main>",
        '<div id="native-test-overlay"></div></main>',
    )
    index_path.write_text(index, encoding="utf-8")
    return package_root


def _build_native_audio_only_package(tmp_path: Path) -> Path:
    package_root = tmp_path / "editable-media-native-audio-only"
    shutil.copytree(STARTER, package_root)
    assets = package_root / "assets"
    assets.mkdir()
    audio = assets / "native-audio.wav"
    subprocess.run(
        [
            str(RuntimeContext.discover().paths.ffmpeg),
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=660:sample_rate=48000:duration=0.25",
            "-c:a",
            "pcm_s16le",
            "-y",
            str(audio),
        ],
        check=True,
    )
    manifest_path = package_root / "editable-media.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["data_fields"].append(
        {
            "id": "native_audio",
            "name": "Native audio",
            "kind": "media-source",
            "default": "native-audio",
        }
    )
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    sources = {
        "protocol": "visual-multimedia-media-sources",
        "version": 4,
        "sources": [
            _native_source_record(
                source_id="native-audio",
                media_type="audio",
                relative_path="assets/native-audio.wav",
                binding={
                    "pipeline": "native-audio",
                    "loop": "repeat",
                    "source_in_ms": 0,
                    "gain_db": -6,
                },
                package_root=package_root,
            )
        ],
    }
    (package_root / "media-sources.json").write_text(
        json.dumps(sources, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return package_root


def test_editable_media_v6_full_chain(
    tmp_path: Path,
) -> None:
    repository = ProjectRepository.create(tmp_path / "V6 Web Project", "V6 Web Project")
    editor, service = _service(repository)
    application: EditorProject | None = None
    try:
        project, asset, clip = _add_web_clip(repository, editor, service)
        copied_root = web_package_root(
            repository.assets.resolve_asset_path(asset),
            repository.web.get_web_asset_spec(asset.id).manifest,
        )
        receipt = next((repository.project_dir / "sources" / "web" / "receipts").glob("r-*.json"))
        assert asset.kind == AssetKind.WEB
        assert copied_root.name.startswith("p-")
        assert json.loads(receipt.read_text(encoding="utf-8"))["status"] == "committed"
        assert asset.metadata.width == asset.metadata.height == 1080
        copied_files = {path.name for path in copied_root.iterdir() if path.is_file()}
        producer_files = {path.name for path in STARTER.iterdir() if path.is_file()}
        assert copied_files == producer_files

        initial_runtime = service.clips.runtime_state(project.main_sequence_id, clip.id)
        assert set(initial_runtime) == {
            "scenes",
            "theme",
            "theme_bindings",
            "parameters",
            "parameter_bindings",
            "parameter_locks",
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
        assert initial_runtime["scenes"]["opening"]["data"]["title"] == ("One source, three ways to play")

        updated = service.clips.update_clip(
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
        assert repository.web.get_web_clip_state(clip.id).scenes["opening"].layers["title"].x == 72

        browser_state = service.clips.runtime_state(project.main_sequence_id, clip.id)
        browser_state["scenes"]["opening"]["layers"]["title"]["x"] = 96
        committed = service.clips.commit_runtime_state(
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
        copied_state = service.clips.update_clip(
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
        renderer = WebRenderService(repository, RuntimeContext.discover().paths)
        competing = WebRenderService(repository, RuntimeContext.discover().paths)
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
        lock_path = cache.with_name(f"{cache.name}.lock")
        assert lock_path.is_file()
        released_lock = ProcessFileLock(lock_path)
        assert released_lock.acquire()
        released_lock.release()
        assert not list(cache.parent.glob(f"{cache.stem}.*.partial{cache.suffix}"))
        diagnostics = web_capture_diagnostics(RuntimeContext.discover().paths.chromium)
        assert diagnostics.last_metrics is not None
        assert diagnostics.last_metrics.worker_count == 1
        assert diagnostics.last_metrics.frame_count == 3
        assert diagnostics.last_metrics.captured_frames == 3
        assert diagnostics.last_metrics.fast_capture_workers == 1
        assert diagnostics.last_metrics.capture_backend == "drawelement"
        assert (
            diagnostics.last_metrics.capture_backend_reason
            == "every worker verified drawElementImage against Chrome screenshots"
        )
        assert diagnostics.last_metrics.fallback_reason is None
        browser_launches = diagnostics.browser_launches
        copied_cache = renderer.render_clip(timeline, copied.id)
        assert copied_cache.is_file() and copied_cache != cache
        reused_browser = web_capture_diagnostics(RuntimeContext.discover().paths.chromium)
        assert reused_browser.browser_launches == browser_launches

        probe = subprocess.run(
            [
                str(RuntimeContext.discover().paths.ffprobe),
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
        assert not list(tmp_path.glob(".mf-web-video-*.tmp.mp4"))
        failed_exports = list((tmp_path / "MediaFlow Pro Failed Exports").glob("mf-web-video-*.tmp.mp4"))
        assert len(failed_exports) == 1
        assert failed_exports[0].stat().st_size > 0

        rendered = renderer.ensure_sequence(timeline)
        assert len(rendered) == 2 and all(path.is_file() for path in rendered)
        assert len(set(rendered)) == 2
        document = TimelineCompiler(repository, RuntimeContext.discover().paths).compile(timeline)
        assert str(cache) in document.xml
        assert str(repository.assets.resolve_asset_path(asset)) not in document.xml

        output = tmp_path / "v6-web-final.mp4"
        result = MltExportService(
            TimelineCompiler(repository, RuntimeContext.discover().paths),
            RuntimeContext.discover().paths,
        ).export(
            timeline,
            ExportPreset(
                name="V6 web verification",
                format=ExportFormat.H264,
                container="mp4",
                encoder_policy={"mode": "software"},
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
            name="V6 Web short",
        )
        short_state = repository.timeline.load_timeline(short.id)
        assert short_state.web_states[short_state.clips[0].id].scenes["opening"].layers["title"].x == 96

        copied_state = service.clips.update_clip(
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
        handoff_state = repository.timeline.load_timeline(project.main_sequence_id)
        handoff_clips = {item.id: item for item in handoff_state.clips}
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
        unrendered_targets = {path for path in expected_targets if not path.exists()}
        assert unrendered_targets

        application = EditorProject(
            repository,
            settings=ServiceSettings(),
            paths=RuntimeContext.discover().paths,
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
        assert all(not path.exists() for path in unrendered_targets)

        handoff = application.export_fcpxml(
            project.main_sequence_id,
            tmp_path / "v6-web-handoff.fcpxml",
        )
        assert all(path.is_file() and path.stat().st_size > 0 for path in expected_targets)
        handoff_root = ET.parse(handoff).getroot()
        resource_uris = {
            media_rep.attrib["src"] for media_rep in handoff_root.findall("./resources/asset/media-rep")
        }
        assert resource_uris == {path.as_uri() for path in expected_targets}
        assert all(not uri.lower().endswith("/index.html") for uri in resource_uris)
    finally:
        if application is not None:
            application.close()
        else:
            repository.close()


def test_native_video_and_audio_use_one_web_cache_through_final_export(
    tmp_path: Path,
) -> None:
    package = _build_native_media_package(tmp_path)
    repository = ProjectRepository.create(
        tmp_path / "Native editable media",
        "Native editable media",
    )
    editor, service = _service(repository)
    try:
        project = repository.projects.get_project()
        asset = service.packages.import_package(package)
        video_track = editor.add_track(TrackKind.VIDEO)
        clip = editor.add_clip(
            track_id=video_track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=30,
        )

        assert asset.metadata.has_audio is True
        assert clip.media_kind == ClipMediaKind.LINKED_AV
        linked_track = next(
            track for track in editor.state.tracks if track.id == editor.state.tracks[0].linked_audio_track_id
        )
        assert linked_track.kind == TrackKind.AUDIO

        timeline = repository.timeline.load_timeline(project.main_sequence_id)
        renderer = WebRenderService(repository, RuntimeContext.discover().paths)
        target = renderer.cache.target(timeline, clip, asset)
        assert len(target.native_media_plan.video_segments) == 2
        assert len(target.native_media_plan.audio_segments) == 4
        assert target.native_media_plan.video_segments[-1].active_duration_ms == 200
        assert target.native_media_plan.video_segments[-1].duration_ms == 800
        assert target.has_audio is True

        filmstrip_source = renderer.render_filmstrip_source(
            timeline,
            clip.id,
            27,
        )
        assert filmstrip_source != target.path
        assert not target.path.exists()
        filmstrip_probe = subprocess.run(
            [
                str(RuntimeContext.discover().paths.ffprobe),
                "-v",
                "error",
                "-show_entries",
                "stream=codec_type",
                "-of",
                "json",
                str(filmstrip_source),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert json.loads(filmstrip_probe.stdout)["streams"] == [{"codec_type": "video"}]
        filmstrip_pixels = subprocess.run(
            [
                str(RuntimeContext.discover().paths.ffmpeg),
                "-loglevel",
                "error",
                "-i",
                str(filmstrip_source),
                "-frames:v",
                "1",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgba",
                "-",
            ],
            capture_output=True,
            check=True,
        ).stdout

        def filmstrip_pixel(x: int, y: int) -> tuple[int, int, int, int]:
            offset = (y * target.width + x) * 4
            return tuple(filmstrip_pixels[offset : offset + 4])  # type: ignore[return-value]

        assert filmstrip_pixel(40, 40)[0:3] == pytest.approx(
            (255, 0, 255),
            abs=15,
        )
        underlay = filmstrip_pixel(target.width // 2, target.height // 2)
        assert underlay[0] < 80 and underlay[1] > 120 and underlay[2] < 120

        cache = renderer.render_clip(timeline, clip.id)
        probe = subprocess.run(
            [
                str(RuntimeContext.discover().paths.ffprobe),
                "-v",
                "error",
                "-show_entries",
                "stream=codec_type,codec_name,pix_fmt,sample_rate,channels",
                "-of",
                "json",
                str(cache),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        streams = json.loads(probe.stdout)["streams"]
        video_stream = next(stream for stream in streams if stream["codec_type"] == "video")
        audio_stream = next(stream for stream in streams if stream["codec_type"] == "audio")
        assert video_stream["codec_name"] == "ffv1"
        assert video_stream["pix_fmt"] == "bgra"
        assert audio_stream == {
            "codec_name": "flac",
            "codec_type": "audio",
            "sample_rate": "48000",
            "channels": 2,
        }

        decoded = subprocess.run(
            [
                str(RuntimeContext.discover().paths.ffmpeg),
                "-loglevel",
                "error",
                "-ss",
                "0.9",
                "-i",
                str(cache),
                "-frames:v",
                "1",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgba",
                "-",
            ],
            capture_output=True,
            check=True,
        ).stdout
        assert len(decoded) == target.width * target.height * 4

        def pixel(x: int, y: int) -> tuple[int, int, int, int]:
            offset = (y * target.width + x) * 4
            return tuple(decoded[offset : offset + 4])  # type: ignore[return-value]

        overlay_pixel = pixel(40, 40)
        underlay_pixel = pixel(target.width // 2, target.height // 2)
        assert (
            overlay_pixel[0] > 240
            and overlay_pixel[1] < 20
            and overlay_pixel[2] > 240
            and overlay_pixel[3] == 255
        )
        assert (
            underlay_pixel[0] < 80
            and underlay_pixel[1] > 120
            and underlay_pixel[2] < 120
            and underlay_pixel[3] == 255
        )

        document = TimelineCompiler(repository, RuntimeContext.discover().paths).compile(timeline)
        assert document.xml.count(str(cache)) == 2
        assert str(repository.assets.resolve_asset_path(asset)) not in document.xml
        handoff = FcpxmlExportService(repository, RuntimeContext.discover().paths).export(
            timeline,
            tmp_path / "native-web.fcpxml",
        )
        handoff_root = ET.parse(handoff).getroot()
        handoff_asset = handoff_root.find("./resources/asset")
        assert handoff_asset is not None
        assert handoff_asset.attrib["hasAudio"] == "1"
        assert handoff_asset.attrib["audioChannels"] == "2"
        handoff_media = handoff_asset.find("./media-rep")
        assert handoff_media is not None
        assert handoff_media.attrib["src"] == cache.as_uri()

        direct_output = tmp_path / "native-web-direct.mp4"
        renderer.export_clip(
            timeline,
            clip.id,
            direct_output,
            "video",
        )
        direct_probe = subprocess.run(
            [
                str(RuntimeContext.discover().paths.ffprobe),
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=codec_name,sample_rate,channels",
                "-of",
                "json",
                str(direct_output),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert json.loads(direct_probe.stdout)["streams"] == [
            {
                "codec_name": "aac",
                "sample_rate": "48000",
                "channels": 2,
            }
        ]

        sequence_output = tmp_path / "native-web-sequence.mp4"
        MltExportService(
            TimelineCompiler(repository, RuntimeContext.discover().paths),
            RuntimeContext.discover().paths,
        ).export(
            timeline,
            ExportPreset(
                name="Native editable media verification",
                format=ExportFormat.H264,
                container="mp4",
                encoder_policy={"mode": "software"},
                audio_codec="aac",
                pixel_format="yuv420p",
            ),
            sequence_output,
        )
        decoded_audio = subprocess.run(
            [
                str(RuntimeContext.discover().paths.ffmpeg),
                "-loglevel",
                "error",
                "-i",
                str(sequence_output),
                "-map",
                "0:a:0",
                "-t",
                "0.5",
                "-f",
                "s16le",
                "-acodec",
                "pcm_s16le",
                "-",
            ],
            capture_output=True,
            check=True,
        ).stdout
        assert decoded_audio
        assert any(decoded_audio)
        with pytest.raises(ValueError, match="native audio"):
            service.rebind.plan_rebind_asset(asset.id, STARTER)
        assert repository.assets.get_asset(asset.id).metadata.has_audio is True

        detached_video, detached_audio = editor.detach_clip_audio(clip.id)
        detached_timeline = repository.timeline.load_timeline(project.main_sequence_id)
        assert detached_timeline.web_states[detached_audio.id].clip_id == detached_audio.id
        video_target = renderer.cache.target(
            detached_timeline,
            detached_video,
            asset,
        )
        audio_target = renderer.cache.target(
            detached_timeline,
            detached_audio,
            asset,
        )
        assert video_target.key == audio_target.key
        assert video_target.path == audio_target.path == cache
        assert (
            renderer.render_clip(
                detached_timeline,
                detached_audio.id,
            )
            == cache
        )
    finally:
        repository.close()


def test_direct_h264_muxes_the_existing_continuous_native_audio_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_short_non_4k_direct_h264(monkeypatch)
    package = _build_native_audio_only_package(tmp_path)
    with ProjectRepository.create(
        tmp_path / "Direct H264 native audio",
        "Direct H264 native audio",
    ) as repository:
        editor, service = _service(repository)
        project = repository.projects.get_project()
        asset = service.packages.import_package(package)
        track = editor.add_track(TrackKind.VIDEO)
        clip = editor.add_clip(
            track_id=track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=30,
        )
        timeline = repository.timeline.load_timeline(project.main_sequence_id)
        renderer = WebRenderService(repository, RuntimeContext.discover().paths)
        target = renderer.cache.target(timeline, clip, asset)

        assert asset.metadata.has_audio is True
        assert not target.native_media_plan.video_segments
        assert target.native_media_plan.audio_segments
        assert renderer.inspect_clip_render(timeline, clip.id).planned_backend == "webcodecs-h264"

        cache = renderer.render_clip(timeline, clip.id)
        manifest = json.loads(target.manifest_path.read_text(encoding="utf-8"))

        assert cache.is_file()
        _require_windows_hardware_direct_h264(manifest["capture"])
        assert manifest["capture"]["actual_backend"] == "webcodecs-h264", manifest[
            "capture"
        ]["fallback_reason"]
        assert manifest["probe"]["codec_name"] == "h264"
        assert manifest["probe"]["has_audio"] is True
        assert manifest["probe"]["audio_codec_name"] == "flac"
        assert manifest["probe"]["audio_sample_rate"] == 48000
        assert manifest["probe"]["audio_channels"] == 2
        assert manifest["probe"]["packet_pts_monotonic"] is True
        assert manifest["probe"]["packet_dts_monotonic"] is True
        assert manifest["probe"]["maximum_video_clock_error_microseconds"] <= 1_000
        assert 0 <= manifest["probe"]["audio_video_end_drift_microseconds"] <= 33_334


def test_native_media_cannot_be_misbound_to_a_browser_asset_slot(
    tmp_path: Path,
) -> None:
    package = _build_native_media_package(tmp_path)
    manifest_path = package / "editable-media.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["scenes"][0]["asset_slots"] = {"invalid-native-slot": {"data_field": "native_video"}}
    manifest["layout_contracts"][0]["asset_slots"].append(
        {
            "id": "invalid-native-slot",
            "required": False,
            "ratio": "16:9",
            "fit": "cover",
            "preserve_full_frame": False,
        }
    )
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    repository = ProjectRepository.create(
        tmp_path / "Invalid native slot",
        "Invalid native slot",
    )
    _editor, service = _service(repository)
    try:
        with pytest.raises(ValueError, match="browser-rendered image source"):
            service.packages.import_package(package)
        assert repository.assets.list_assets() == []
    finally:
        repository.close()


def test_web_render_restarts_ffmpeg_after_fast_capture_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = ProjectRepository.create(
        tmp_path / "Web Capture Fallback",
        "Web Capture Fallback",
    )
    editor, service = _service(repository)
    try:
        project, _asset, clip = _add_web_clip(repository, editor, service)
        real_engine = get_web_capture_engine(RuntimeContext.discover().paths.chromium)

        class FailsFirstFastAttempt:
            def __init__(self) -> None:
                self.capture_modes: list[str] = []

            def render_frames(self, **arguments):
                capture_mode = str(arguments.get("capture_mode", "auto"))
                self.capture_modes.append(capture_mode)
                if len(self.capture_modes) == 1:
                    raise FastCaptureFallbackRequired(
                        worker_index=0,
                        frame_index=1,
                        reason="injected production capture failure",
                    )
                return real_engine.render_frames(**arguments)

        retry_engine = FailsFirstFastAttempt()
        monkeypatch.setattr(
            web_browser_cache_module,
            "get_web_capture_engine",
            lambda _executable: retry_engine,
        )
        timeline = repository.timeline.load_timeline(project.main_sequence_id)
        renderer = WebRenderService(repository, RuntimeContext.discover().paths)

        cache = renderer.render_clip(timeline, clip.id)

        assert retry_engine.capture_modes == ["auto", "screenshot"]
        assert cache.is_file() and cache.stat().st_size > 0
        assert not list(cache.parent.glob(f"{cache.stem}.*.partial{cache.suffix}"))
        manifest_path = cache.with_name(f"{cache.name}.manifest.json")
        manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest_payload["schema"] == "mediaflow-web-render-cache/v5"
        assert manifest_payload["probe"]["frame_count"] == 3
        assert manifest_payload["capture"]["planned_mode"] == "auto"
        assert manifest_payload["capture"]["planned_backend"] == "frame-pipe"
        assert manifest_payload["capture"]["actual_backend"] == "screenshot"
        assert manifest_payload["capture"]["captured_frames"] == 3
        metrics = real_engine.diagnostics().last_metrics
        assert metrics is not None
        assert metrics.capture_backend == "screenshot"
        assert metrics.fallback_reason is not None
        assert "injected production capture failure" in metrics.fallback_reason
        probe = subprocess.run(
            [
                str(RuntimeContext.discover().paths.ffprobe),
                "-v",
                "error",
                "-count_frames",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=nb_read_frames",
                "-of",
                "default=noprint_wrappers=1",
                str(cache),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert "nb_read_frames=3" in probe.stdout

        render_count = real_engine.diagnostics().render_count
        cache.write_bytes(b"corrupted cache contents")
        recovered_cache = renderer.render_clip(timeline, clip.id)

        assert recovered_cache == cache
        assert recovered_cache.stat().st_size > len(b"corrupted cache contents")
        assert real_engine.diagnostics().render_count == render_count + 1
        recovered_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert recovered_manifest["probe"]["frame_count"] == 3
        assert retry_engine.capture_modes == ["auto", "screenshot", "auto"]
    finally:
        repository.close()


def test_web_render_cancellation_aborts_ffmpeg_and_never_publishes_cache(
    tmp_path: Path,
) -> None:
    repository = ProjectRepository.create(
        tmp_path / "Cancelled Web Render",
        "Cancelled Web Render",
    )
    editor, service = _service(repository)
    try:
        project, asset, clip = _add_web_clip(repository, editor, service)
        timeline = repository.timeline.load_timeline(project.main_sequence_id)
        renderer = WebRenderService(repository, RuntimeContext.discover().paths)
        target = renderer.cache.target(timeline, clip, asset)
        completed_frames = 0

        def report(progress) -> None:
            nonlocal completed_frames
            if progress.message_code == "web_rendering" and progress.completed:
                completed_frames = int(progress.completed)

        def cancel_after_first_encoded_frame() -> None:
            if completed_frames >= 1:
                raise TaskStopped(TaskStatus.CANCELLED)

        with pytest.raises(TaskStopped):
            renderer.render_clip(
                timeline,
                clip.id,
                progress=report,
                check_cancelled=cancel_after_first_encoded_frame,
            )

        assert completed_frames >= 1
        assert not target.path.exists()
        assert not target.path.with_name(f"{target.path.name}.manifest.json").exists()
        assert not list(target.path.parent.glob(f"{target.path.stem}.*.partial{target.path.suffix}"))
    finally:
        repository.close()


def test_direct_h264_cancellation_removes_stream_and_never_publishes_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_short_non_4k_direct_h264(monkeypatch)
    repository = ProjectRepository.create(
        tmp_path / "Cancelled direct H264 render",
        "Cancelled direct H264 render",
    )
    editor, service = _service(repository)
    try:
        project, asset, clip = _add_web_clip(
            repository,
            editor,
            service,
            duration=30,
        )
        timeline = repository.timeline.load_timeline(project.main_sequence_id)
        renderer = WebRenderService(repository, RuntimeContext.discover().paths)
        target = renderer.cache.target(timeline, clip, asset)
        completed_frames = 0

        def report(progress) -> None:
            nonlocal completed_frames
            if progress.message_code == "web_rendering" and progress.completed:
                completed_frames = int(progress.completed)

        def cancel_after_first_encoded_frame() -> None:
            if completed_frames >= 1:
                raise TaskStopped(TaskStatus.CANCELLED)

        with pytest.raises(TaskStopped):
            renderer.render_clip(
                timeline,
                clip.id,
                progress=report,
                check_cancelled=cancel_after_first_encoded_frame,
            )

        assert completed_frames >= 1
        assert not target.path.exists()
        assert not target.manifest_path.exists()
        assert not list(target.path.parent.glob(".mf-web-h264-*.h264"))
        assert not list(target.path.parent.glob(".mf-web-render-*.mkv"))
    finally:
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
        discovered = RuntimeContext.discover().paths
        isolated_runtime = tmp_path / "isolated-runtime"
        renderer = WebRenderService(
            repository,
            replace(discovered, runtime_dir=isolated_runtime),
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
        discovered = RuntimeContext.discover().paths
        isolated_runtime = tmp_path / "isolated-path-runtime"
        renderer = WebRenderService(
            repository,
            replace(discovered, runtime_dir=isolated_runtime),
        )
        cache_target = renderer.cache.target(timeline, clip)
        output_parent = tmp_path
        destination = output_parent / "overlay.mkv"
        while utf16_units(str(output_parent.resolve())) + 1 + 64 <= WINDOWS_INTEROP_PATH_UTF16_LIMIT:
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
        timeline = repository.timeline.load_timeline(project_record.main_sequence_id)
        paths = RuntimeContext.discover().paths
        cache_target = WebRenderService(
            repository,
            paths,
        ).cache.target(timeline, clip)
        output = tmp_path / "existing-web-sequence.mp4"
        original = b"existing user export"
        output.write_bytes(original)
        project = EditorProject(
            repository,
            settings=ServiceSettings(),
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
                encoder_policy={"mode": "software"},
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
            command.model_copy(update={"output_path": str(successful_output)})
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


def test_editable_media_v6_contract_rejections(
    tmp_path: Path,
) -> None:
    manifest = json.loads((STARTER / "editable-media.json").read_text(encoding="utf-8"))
    manifest["version"] = 1
    with pytest.raises((ValueError, ValidationError), match="version"):
        parse_editable_media_manifest(manifest, editable_media_contract())

    manifest = json.loads((STARTER / "editable-media.json").read_text(encoding="utf-8"))
    manifest["scenes"].append(dict(manifest["scenes"][0]))
    with pytest.raises((ValueError, ValidationError), match="scene identifiers"):
        parse_editable_media_manifest(manifest, editable_media_contract())

    missing_sources = tmp_path / "missing-media-sources"
    shutil.copytree(STARTER, missing_sources)
    (missing_sources / "media-sources.json").rename(missing_sources / "media-sources.unavailable")
    repository = ProjectRepository.create(tmp_path / "Invalid V6 Project", "Invalid V6")
    editor, service = _service(repository)
    try:
        with pytest.raises(FileNotFoundError, match="media-sources"):
            service.packages.import_package(missing_sources)

        remote = tmp_path / "remote-v6-package"
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
            service.packages.import_package(remote)

        non_deterministic = tmp_path / "non-deterministic-v6-package"
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
            service.packages.import_package(non_deterministic)

        corrupted_media = tmp_path / "corrupted-media-integrity"
        shutil.copytree(
            MEDIA_CASES / "warm-paper-project-list",
            corrupted_media,
        )
        avatar = corrupted_media / "assets" / "creator-avatar.png"
        avatar.write_bytes(avatar.read_bytes() + b"corruption")
        with pytest.raises(ValueError, match="source integrity"):
            service.packages.import_package(corrupted_media)

        incorrect_mime = tmp_path / "incorrect-media-mime"
        shutil.copytree(
            MEDIA_CASES / "warm-paper-project-list",
            incorrect_mime,
        )
        sources_path = incorrect_mime / "media-sources.json"
        sources = json.loads(sources_path.read_text(encoding="utf-8"))
        sources["sources"][0]["integrity"]["mime_type"] = "application/octet-stream"
        sources_path.write_text(
            json.dumps(sources, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="MIME type"):
            service.packages.import_package(incorrect_mime)
    finally:
        repository.close()


def test_web_package_deep_tree_is_rejected_before_any_project_copy(
    tmp_path: Path,
    max_project_path: Path,
) -> None:
    source = tmp_path / "deep-web-package"
    shutil.copytree(STARTER, source)
    deep_file = source / ("a" * 25) / ("b" * 25) / "unreferenced-extra.json"
    deep_file.parent.mkdir(parents=True)
    deep_file.write_text("{}", encoding="utf-8")
    repository = ProjectRepository.create(max_project_path, "Deep Web")
    _editor, service = _service(repository)
    try:
        with pytest.raises(ValueError, match="路径过深"):
            service.packages.import_package(source)

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
    placeholder_root = max_project_path / "sources" / "web" / f"p-{'0' * 24}"
    component_units = WINDOWS_INTEROP_PATH_UTF16_LIMIT - utf16_units(str(placeholder_root)) - 1
    boundary_name = f"{'x' * (component_units - len('.txt'))}.txt"
    (source / boundary_name).write_text("boundary", encoding="utf-8")
    repository = ProjectRepository.create(max_project_path, "Boundary Web")
    _editor, service = _service(repository)
    try:
        asset = service.packages.import_package(source)
        spec = repository.web.get_web_asset_spec(asset.id)
        package_root = web_package_root(
            repository.assets.resolve_asset_path(asset),
            spec.manifest,
        )
        copied_boundary = package_root / boundary_name

        assert copied_boundary.is_file()
        assert utf16_units(str(copied_boundary)) == WINDOWS_INTEROP_PATH_UTF16_LIMIT
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
    original_copy = web_package_module.copy_web_package_file

    def fail_after_one_file(
        source: str,
        destination: str,
    ) -> tuple[int, str]:
        nonlocal copied
        if copied:
            raise OSError("injected package copy failure")
        copied += 1
        return original_copy(source, destination)

    monkeypatch.setattr(
        web_package_module,
        "copy_web_package_file",
        fail_after_one_file,
    )
    try:
        with pytest.raises(OSError, match="injected package copy failure"):
            service.packages.import_package(STARTER)

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
            service.packages.import_package(STARTER)

        assert not [asset for asset in repository.assets.list_assets() if asset.kind == AssetKind.WEB]
        assert not list((repository.project_dir / "sources" / "web").glob("p-*"))
        assert not list((repository.project_dir / "staging" / "web").glob("s-*"))
        failed = list((repository.project_dir / "archive" / "web").glob("f-*"))
        failed_receipts = list((repository.project_dir / "archive" / "web").glob("r-*.json"))
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
    nested_content = nested_entry.read_text(encoding="utf-8")
    nested_content = nested_content.replace(
        'href="editable-media-editor.css"',
        'href="../editable-media-editor.css"',
    ).replace(
        'src="editable-media-editor.js"',
        'src="../editable-media-editor.js"',
    ).replace(
            '<script src="editable-media-runtime.js"></script>',
            ('<script src="../editable-media-runtime.js" data-manifest="../editable-media.json"></script>'),
    )
    nested_entry.write_text(nested_content, encoding="utf-8")
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
        asset = service.packages.import_package(source)
        spec = repository.web.get_web_asset_spec(asset.id)
        imported_entry = repository.assets.resolve_asset_path(asset)
        imported_root = web_package_root(imported_entry, spec.manifest)

        assert imported_entry.parent.name == "pages"
        assert imported_root.name.startswith("p-")
        assert (imported_root / MANIFEST_FILE_NAME).is_file()
        _browser_validator().validate(imported_root, spec.manifest)
    finally:
        repository.close()


def test_editable_media_v6_scene_features(
    tmp_path: Path,
) -> None:
    repository = ProjectRepository.create(tmp_path / "Extended V6 Web", "Extended V6 Web")
    editor, service = _service(repository)
    try:
        project, asset, clip = _add_web_clip(repository, editor, service, duration=4)
        state = service.clips.select_variant(
            project.main_sequence_id,
            clip.id,
            "portrait",
            expected_revision=0,
        )
        state = service.clips.update_clip(
            project.main_sequence_id,
            clip.id,
            {"title": {"content": "Opening title", "x": 120}},
            scene_id="opening",
            expected_revision=state.revision,
        )
        state = service.clips.update_clip(
            project.main_sequence_id,
            clip.id,
            {"title": {"content": "Delivery title", "x": 180}},
            scene_id="delivery",
            expected_revision=state.revision,
        )
        state = service.clips.set_keyframe(
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
        state = service.clips.move_keyframe(
            project.main_sequence_id,
            clip.id,
            "title",
            "opacity",
            900,
            960,
            scene_id="opening",
            expected_revision=state.revision,
        )
        state = service.clips.update_parameter(
            project.main_sequence_id,
            clip.id,
            "spring_strength",
            0.9,
            expected_revision=state.revision,
        )
        state = service.clips.update_parameter(
            project.main_sequence_id,
            clip.id,
            "stagger_interval_ms",
            220,
            scene_id="opening",
            expected_revision=state.revision,
        )
        state = service.clips.set_parameter_keyframe(
            project.main_sequence_id,
            clip.id,
            "spring_strength",
            600,
            0.82,
            scene_id="opening",
            easing={"kind": "ease_out"},
            expected_revision=state.revision,
        )
        state = service.clips.move_parameter_keyframe(
            project.main_sequence_id,
            clip.id,
            "spring_strength",
            600,
            720,
            scene_id="opening",
            expected_revision=state.revision,
        )
        state = service.clips.set_parameter_lock(
            project.main_sequence_id,
            clip.id,
            "spring_strength",
            True,
            expected_revision=state.revision,
        )
        with pytest.raises(PermissionError, match="locked"):
            service.clips.update_parameter(
                project.main_sequence_id,
                clip.id,
                "spring_strength",
                0.5,
                expected_revision=state.revision,
                actor="automation",
            )
        state = service.clips.update_theme(
            project.main_sequence_id,
            clip.id,
            {"accent": "#ff0066"},
            expected_revision=state.revision,
        )
        state = service.clips.update_data(
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
        state = service.clips.update_data_from_file(
            project.main_sequence_id,
            clip.id,
            snapshot_file,
            scene_id="delivery",
            expected_revision=state.revision,
        )
        state = service.clips.set_field_locks(
            project.main_sequence_id,
            clip.id,
            "title",
            ["content"],
            True,
            scene_id="opening",
            expected_revision=state.revision,
        )

        diff = service.clips.diff_clip_update(
            project.main_sequence_id,
            clip.id,
            {"title": {"content": "Automation replacement", "opacity": 0.7}},
            scene_id="opening",
            expected_revision=state.revision,
        )
        assert diff.locked_paths == ["scenes.opening.layers.title.content"]
        with pytest.raises(PermissionError, match="locked"):
            service.clips.update_clip(
                project.main_sequence_id,
                clip.id,
                {"title": {"content": "Automation replacement"}},
                scene_id="opening",
                actor="automation",
                expected_revision=state.revision,
            )

        runtime = service.clips.runtime_state(project.main_sequence_id, clip.id)
        assert runtime["variant"]["id"] == "portrait"
        assert runtime["scenes"]["opening"]["layers"]["title"]["x"] == 120
        assert runtime["scenes"]["delivery"]["layers"]["title"]["x"] == 180
        assert runtime["scenes"]["delivery"]["data"]["right_value"] == "From JSON"
        assert runtime["theme"]["accent"] == "#ff0066"
        assert runtime["parameters"]["spring_strength"] == 0.9
        assert runtime["parameter_locks"] == ["spring_strength"]
        assert runtime["scenes"]["opening"]["parameters"]["stagger_interval_ms"] == 220
        assert (
            runtime["scenes"]["opening"]["parameter_animations"]["spring_strength"]["keyframes"][0]["time_ms"]
            == 720
        )
        assert (
            runtime["scenes"]["opening"]["animations"]["title"]["opacity"]["keyframes"][0]["time_ms"] == 960
        )

        edit_document = service.clips.describe_clip_editing(
            project.main_sequence_id,
            clip.id,
            scene_id="opening",
        )
        fields = {item.path: item for item in edit_document.fields}
        spring = fields["parameters.spring_strength"]
        stagger = fields["scenes.opening.parameters.stagger_interval_ms"]
        enter = fields["scenes.opening.layers.title.enter_ms"]
        assert spring.value == 0.9
        assert spring.descriptor.control == "slider"
        assert spring.descriptor.constraints.minimum == 0
        assert spring.descriptor.constraints.maximum == 1
        assert spring.descriptor.unit == "ratio"
        assert spring.locked is True
        assert spring.descriptor.timeline == "keyframe"
        assert stagger.value == 220 and stagger.descriptor.kind == "integer"
        assert stagger.descriptor.unit == "ms"
        assert enter.descriptor.timeline == "interval"

        variants = service.batches.create_variants(
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
            assert variant_state.scenes["opening"].layers["title"].content == expected

        replacement = tmp_path / "replacement-v6-package"
        shutil.copytree(STARTER, replacement)
        manifest_path = replacement / "editable-media.json"
        replacement_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        replacement_manifest["component"]["name"] = "Editable card replacement"
        manifest_path.write_text(
            json.dumps(replacement_manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        report = service.rebind.plan_rebind_asset(asset.id, replacement)
        assert not report.conflicts and clip.id in report.affected_clips
        committed = service.rebind.commit_rebind_asset(
            asset.id,
            replacement,
            report.plan_digest,
            {},
        )
        rebound = repository.web.get_web_clip_state(clip.id)
        assert committed.new_source_hash != committed.old_source_hash
        assert rebound.source_hash == committed.new_source_hash
        assert rebound.scenes["delivery"].layers["title"].x == 180
        assert rebound.parameters["spring_strength"] == 0.9
        assert rebound.scenes["opening"].parameter_animations["spring_strength"].keyframes[0].time_ms == 720
    finally:
        repository.close()


def test_rebind_database_failure_keeps_the_old_package_readable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = ProjectRepository.create(tmp_path / "Rebind Failure", "Rebind Failure")
    _editor, service = _service(repository)
    asset = service.packages.import_package(STARTER)
    old_spec = repository.web.get_web_asset_spec(asset.id)
    old_root = web_package_root(
        repository.assets.resolve_asset_path(asset),
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
        plan = service.rebind.plan_rebind_asset(asset.id, replacement)
        with pytest.raises(OSError, match="injected rebind database failure"):
            service.rebind.commit_rebind_asset(
                asset.id,
                replacement,
                plan.plan_digest,
                {},
            )

        current_asset = repository.assets.get_asset(asset.id)
        current_spec = repository.web.get_web_asset_spec(asset.id)
        assert current_spec == old_spec
        assert (
            web_package_root(
                repository.assets.resolve_asset_path(current_asset),
                current_spec.manifest,
            )
            == old_root
        )
        _browser_validator().validate(old_root, old_spec.manifest)
        assert old_root.is_dir()
        assert not list((repository.project_dir / "staging" / "web").glob("s-*"))
        assert len(list((repository.project_dir / "archive" / "web").glob("f-*"))) == 1
    finally:
        repository.close()


def test_rebind_requires_exact_conflict_decisions_and_an_unchanged_plan(
    tmp_path: Path,
) -> None:
    repository = ProjectRepository.create(
        tmp_path / "Strict Rebind",
        "Strict Rebind",
    )
    editor, service = _service(repository)
    try:
        project, asset, clip = _add_web_clip(repository, editor, service)
        state = service.clips.update_parameter(
            project.main_sequence_id,
            clip.id,
            "spring_strength",
            0.9,
            expected_revision=0,
        )
        state = service.clips.set_parameter_keyframe(
            project.main_sequence_id,
            clip.id,
            "spring_strength",
            600,
            0.75,
            scene_id="opening",
            expected_revision=state.revision,
        )
        state = service.clips.set_parameter_lock(
            project.main_sequence_id,
            clip.id,
            "spring_strength",
            True,
            expected_revision=state.revision,
        )

        replacement = tmp_path / "replacement-without-spring"
        shutil.copytree(STARTER, replacement)
        manifest_path = replacement / MANIFEST_FILE_NAME
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["component"]["name"] = "Replacement without spring"
        manifest["parameters"] = [
            item for item in manifest["parameters"] if item["descriptor"]["id"] != "spring_strength"
        ]
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        plan = service.rebind.plan_rebind_asset(asset.id, replacement)
        assert {item.kind for item in plan.conflicts} == {"removed-parameter"}
        assert len(plan.conflicts) == 2
        decisions = {item.path: item.allowed_resolutions[0] for item in plan.conflicts}
        with pytest.raises(ValueError, match="one decision for every conflict"):
            service.rebind.commit_rebind_asset(
                asset.id,
                replacement,
                plan.plan_digest,
                {},
            )

        manifest["component"]["name"] = "Changed after review"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        with pytest.raises(RuntimeError, match="plan changed"):
            service.rebind.commit_rebind_asset(
                asset.id,
                replacement,
                plan.plan_digest,
                decisions,
            )

        reviewed = service.rebind.plan_rebind_asset(asset.id, replacement)
        report = service.rebind.commit_rebind_asset(
            asset.id,
            replacement,
            reviewed.plan_digest,
            {item.path: item.allowed_resolutions[0] for item in reviewed.conflicts},
        )
        migrated = repository.web.get_web_clip_state(clip.id)
        assert set(report.resolved_paths) == {item.path for item in reviewed.conflicts}
        assert "spring_strength" not in migrated.parameters
        assert "spring_strength" not in migrated.parameter_locks
        assert "spring_strength" not in (migrated.scenes["opening"].parameter_animations)
    finally:
        repository.close()


def test_named_version_restore_keeps_the_immutable_pre_rebind_web_package(
    tmp_path: Path,
) -> None:
    repository = ProjectRepository.create(tmp_path / "Web Version", "Web Version")
    _editor, service = _service(repository)
    try:
        asset = service.packages.import_package(STARTER)
        old_spec = repository.web.get_web_asset_spec(asset.id)
        old_root = web_package_root(
            repository.assets.resolve_asset_path(asset),
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

        plan = service.rebind.plan_rebind_asset(asset.id, replacement)
        report = service.rebind.commit_rebind_asset(
            asset.id,
            replacement,
            plan.plan_digest,
            {},
        )
        rebound_asset = repository.assets.get_asset(asset.id)
        rebound_spec = repository.web.get_web_asset_spec(asset.id)
        rebound_root = web_package_root(
            repository.assets.resolve_asset_path(rebound_asset),
            rebound_spec.manifest,
        )
        assert report.archive_path == str(old_root)
        assert rebound_root != old_root
        assert old_root.is_dir() and rebound_root.is_dir()
        _browser_validator().validate(rebound_root, rebound_spec.manifest)

        repository.records.restore_project_version(version.id)
        restored_asset = repository.assets.get_asset(asset.id)
        restored_spec = repository.web.get_web_asset_spec(asset.id)
        restored_root = web_package_root(
            repository.assets.resolve_asset_path(restored_asset),
            restored_spec.manifest,
        )
        assert restored_root == old_root
        assert restored_spec == old_spec
        _browser_validator().validate(restored_root, restored_spec.manifest)
        assert rebound_root.is_dir()
    finally:
        repository.close()


def test_publication_reconciliation_is_write_only_and_preserves_referenced_packages(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "Publication Recovery"
    repository = ProjectRepository.create(project_dir, "Publication Recovery")
    _editor, service = _service(repository)
    asset = service.packages.import_package(STARTER)
    spec = repository.web.get_web_asset_spec(asset.id)
    referenced_root = web_package_root(
        repository.assets.resolve_asset_path(asset),
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
        assert json.loads(referenced_receipt.read_text(encoding="utf-8"))["status"] == "pending"

    with ProjectRepository.open(project_dir, writable=True) as writable:
        _service(writable)
        assert referenced_root.is_dir()
        assert not orphan_root.exists()
        assert not staging_root.exists()
        assert json.loads(referenced_receipt.read_text(encoding="utf-8"))["status"] == "committed"
        failures = list((project_dir / "archive" / "web").glob("f-*"))
        assert len(failures) == 2
        current_asset = writable.assets.get_asset(asset.id)
        current_spec = writable.web.get_web_asset_spec(asset.id)
        assert (
            web_package_root(
                writable.assets.resolve_asset_path(current_asset),
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
    asset = service.packages.import_package(STARTER)
    spec = repository.web.get_web_asset_spec(asset.id)
    package_root = web_package_root(
        repository.assets.resolve_asset_path(asset),
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


def test_v6_cli_chain(
    tmp_path: Path,
    monkeypatch,
    editor_service_api: EditorServiceApi,
) -> None:
    monkeypatch.setenv("MEDIAFLOW_PROJECT_ROOT", str(tmp_path))

    created = editor_service_api.execute(
        "project.create",
        request_id="create-cli-v6-web-project",
        arguments={
            "name": "CLI V6 Web Project",
            "directory_name": "cli-v6-web-project",
            "profile": ProjectProfile().model_dump(mode="json", exclude_computed_fields=True),
        },
    )
    project_path = Path(created["path"])

    def request_payload(operation: str, arguments: dict | None = None) -> dict:
        return editor_service_api.request(
            operation,
            project=project_path,
            arguments=arguments,
        )

    def request(operation: str, arguments: dict | None = None) -> dict:
        return editor_service_api.execute_request(request_payload(operation, arguments))["result"]

    sequence_id = created["project"]["main_sequence_id"]
    imported = request("web.import", {"source": str(STARTER)})
    asset_id = imported["asset"]["id"]
    track_id = request(
        "timeline.track.add",
        {"sequence_id": sequence_id, "kind": "video"},
    )["track"]["id"]
    clip_id = request(
        "timeline.clip.add",
        {
            "sequence_id": sequence_id,
            "track_id": track_id,
            "asset_id": asset_id,
            "timeline_start": 0,
            "source_in": 0,
            "duration": 2,
        },
    )["clip"]["id"]

    updated = request(
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
    revision = updated["web_clip_state"]["revision"]
    selected = request(
        "web.clip.variant.select",
        {
            "sequence_id": sequence_id,
            "clip_id": clip_id,
            "variant_id": "landscape",
            "expected_revision": revision,
        },
    )
    revision = selected["web_clip_state"]["revision"]
    data_updated = request(
        "web.clip.data.update",
        {
            "sequence_id": sequence_id,
            "clip_id": clip_id,
            "scene_id": "delivery",
            "values": {"left_value": "CLI data"},
            "expected_revision": revision,
        },
    )
    revision = data_updated["web_clip_state"]["revision"]

    described = request(
        "web.clip.edit.describe",
        {
            "sequence_id": sequence_id,
            "clip_id": clip_id,
            "scene_id": "opening",
        },
    )["edit_document"]
    fields = {item["path"]: item for item in described["fields"]}
    assert fields["parameters.spring_strength"]["descriptor"]["control"] == "slider"
    assert fields["parameters.spring_strength"]["descriptor"]["unit"] == "ratio"
    assert fields["scenes.opening.parameters.stagger_interval_ms"]["descriptor"]["kind"] == "integer"

    parameter_updated = request(
        "web.clip.parameter.update",
        {
            "sequence_id": sequence_id,
            "clip_id": clip_id,
            "scene_id": "opening",
            "parameter_id": "spring_strength",
            "value": 0.88,
            "expected_revision": revision,
            "actor": "automation",
        },
    )
    revision = parameter_updated["web_clip_state"]["revision"]
    parameter_animated = request(
        "web.clip.parameter.keyframe.set",
        {
            "sequence_id": sequence_id,
            "clip_id": clip_id,
            "scene_id": "opening",
            "parameter_id": "spring_strength",
            "time_ms": 480,
            "value": 0.7,
            "easing": {"kind": "ease_out"},
            "expected_revision": revision,
            "actor": "automation",
        },
    )
    revision = parameter_animated["web_clip_state"]["revision"]
    parameter_locked = request(
        "web.clip.parameter.lock.update",
        {
            "sequence_id": sequence_id,
            "clip_id": clip_id,
            "scene_id": "opening",
            "parameter_id": "spring_strength",
            "locked": True,
            "expected_revision": revision,
        },
    )
    revision = parameter_locked["web_clip_state"]["revision"]
    version = request("project.version.create", {"name": "Before CLI render"})
    assert version["version"]["name"] == "Before CLI render"

    inspected_before = request(
        "web.clip.render.inspect",
        {"sequence_id": sequence_id, "clip_id": clip_id},
    )["render_plan"]
    assert inspected_before["schema"] == "mediaflow-web-render-plan/v1"
    assert inspected_before["cache_status"] == "missing"
    assert inspected_before["capture_mode"] == "auto"
    assert inspected_before["strategy"] == (
        "verified-drawelement-with-atomic-screenshot-fallback"
    )
    assert inspected_before["verification_frames"]

    completed = subprocess.run(
        [sys.executable, "-m", "mediaflow.cli", "execute", "--request", "-"],
        input=json.dumps(request_payload("web.clip.get", {"clip_id": clip_id})),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        cwd=Path(__file__).resolve().parents[3],
    )
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    clip_state = payload["result"]["result"]["web_clip_state"]
    assert clip_state["scenes"]["opening"]["layers"]["title"]["content"] == "CLI edit"
    assert clip_state["scenes"]["delivery"]["data_snapshot"]["values"]["left_value"] == ("CLI data")
    assert clip_state["variant"]["id"] == "landscape"
    assert clip_state["parameters"]["spring_strength"] == 0.88
    assert clip_state["parameter_locks"] == ["spring_strength"]
    assert (
        clip_state["scenes"]["opening"]["parameter_animations"]["spring_strength"]["keyframes"][0]["time_ms"]
        == 480
    )
    assert clip_state["revision"] == revision

    render = subprocess.run(
        [sys.executable, "-m", "mediaflow.cli", "execute", "--request", "-"],
        input=json.dumps(
            request_payload(
                "web.clip.render",
                {"sequence_id": sequence_id, "clip_id": clip_id, "timeout": 60},
            )
        ),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        cwd=Path(__file__).resolve().parents[3],
    )
    rendered = json.loads(render.stdout)
    assert rendered["ok"] is True
    task_receipt = rendered["result"]["result"]["task"]
    completed_render = editor_service_api.execute(
        "task.wait",
        project=project_path,
        arguments={"task_id": task_receipt["id"], "timeout": 60},
    )["task"]
    assert completed_render["status"] == "completed"
    artifact = ArtifactReference.model_validate(completed_render["artifacts"][0])
    assert artifact.resolve(project_path).is_file()

    inspected_after = request(
        "web.clip.render.inspect",
        {"sequence_id": sequence_id, "clip_id": clip_id},
    )["render_plan"]
    assert inspected_after["plan_digest"] == inspected_before["plan_digest"]
    assert inspected_after["cache_status"] == "ready"
    assert inspected_after["actual_capture"]["backend"] in {
        "drawelement",
        "screenshot",
    }
    assert inspected_after["actual_capture"]["captured_frames"] == 2

    variants = request(
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
        connection.execute("UPDATE schema_info SET version=17 WHERE component='project'")

    with pytest.raises(RuntimeError, match="历史 editable-media"):
        ProjectRepository.open(root, writable=True)
