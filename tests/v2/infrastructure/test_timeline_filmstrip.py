from __future__ import annotations

from concurrent.futures import CancelledError
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import pytest

from mediaflow.domain.enums import AssetKind, ClipMediaKind, TrackKind
from mediaflow.domain.project import MediaMetadata
from mediaflow.domain.timeline import Clip, Track
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.runtime_context import RuntimeContext
from mediaflow.infrastructure.timeline_filmstrip import (
    TimelineFilmstripService,
    _FilmstripMemoryLru,
    _FilmstripRequestCoordinator,
)
from mediaflow.infrastructure.web_native_media import (
    WebNativeMediaPlan,
    WebNativeVideoSegment,
    slice_web_native_media_plan_for_frame,
)


def test_filmstrip_samples_real_forward_reverse_and_freeze_source_frames(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    tile = tmp_path / "tile.jpg"
    tile.write_bytes(b"tile")
    with ProjectRepository.create(tmp_path / "project", "Filmstrip") as repository:
        project = repository.projects.get_project()
        asset = repository.assets.import_external_asset(source, AssetKind.VIDEO)
        asset = repository.assets.update_asset(
            asset.model_copy(
                update={
                    "metadata": MediaMetadata(
                        duration_frames=1000,
                        width=1920,
                        height=1080,
                        has_video=True,
                    )
                }
            )
        )
        state = repository.timeline.load_timeline(project.main_sequence_id)
        track = Track(
            sequence_id=project.main_sequence_id,
            name="Video",
            kind=TrackKind.VIDEO,
            position=0,
        )
        forward = Clip(
            track_id=track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=100,
            duration=40,
            media_kind=ClipMediaKind.VIDEO_ONLY,
            speed_numerator=2,
            speed_denominator=1,
        )
        reverse = Clip(
            track_id=track.id,
            asset_id=asset.id,
            timeline_start=50,
            source_in=400,
            duration=40,
            media_kind=ClipMediaKind.VIDEO_ONLY,
            speed_numerator=-1,
            speed_denominator=2,
        )
        freeze = Clip(
            track_id=track.id,
            asset_id=asset.id,
            timeline_start=100,
            source_in=600,
            duration=40,
            media_kind=ClipMediaKind.VIDEO_ONLY,
            freeze_source_frame=617,
        )
        state.tracks = [track]
        state.clips = [forward, reverse, freeze]
        repository.timeline.save_timeline(state)
        service = TimelineFilmstripService(repository, RuntimeContext.discover().paths)
        monkeypatch.setattr(
            service,
            "_render_tiles",
            lambda *_args, source_frames, **_kwargs: {
                frame: tile for frame in source_frames
            },
        )

        rows = service.render_visible(
            project.main_sequence_id,
            visible_start_frame=0,
            visible_end_frame=140,
            pixels_per_frame=10,
            height=44,
        )

    grouped = {
        clip.id: [row for row in rows if row["clipId"] == clip.id] for clip in (forward, reverse, freeze)
    }
    assert len({row["sourceFrame"] for row in grouped[forward.id]}) > 1
    assert grouped[forward.id][0]["sourceFrame"] == 100
    assert grouped[forward.id][1]["sourceFrame"] == 116
    assert grouped[reverse.id][0]["sourceFrame"] == 400
    assert grouped[reverse.id][1]["sourceFrame"] == 396
    assert {row["sourceFrame"] for row in grouped[freeze.id]} == {617}


def test_filmstrip_prefers_sdr_then_regular_proxy_then_original(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    with ProjectRepository.create(tmp_path / "project", "Proxy order") as repository:
        asset = repository.assets.import_external_asset(source, AssetKind.VIDEO)
        proxy = repository.project_dir / "proxies" / "proxy.mp4"
        sdr = repository.project_dir / "proxies" / "sdr.mp4"
        proxy.write_bytes(b"proxy")
        sdr.write_bytes(b"sdr")
        asset = repository.assets.update_asset(
            asset.model_copy(
                update={
                    "proxy_path": str(proxy),
                    "sdr_preview_proxy_path": str(sdr),
                }
            )
        )
        service = TimelineFilmstripService(repository, RuntimeContext.discover().paths)

        assert service._visual_source(asset) == sdr


def test_filmstrip_memory_lru_evicts_old_identity_without_deleting_disk_cache(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.jpg"
    second = tmp_path / "second.jpg"
    first.write_bytes(b"1234")
    second.write_bytes(b"5678")
    cache = _FilmstripMemoryLru(maximum_bytes=4)

    cache.put("first", first)
    cache.put("second", second)

    assert cache.get("first") is None
    assert cache.get("second") == second
    assert first.is_file() and second.is_file()


def test_filmstrip_batch_falls_back_only_for_frames_ffmpeg_did_not_create(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    fallback_tile = tmp_path / "fallback.jpg"
    fallback_tile.write_bytes(b"fallback")
    with ProjectRepository.create(tmp_path / "project", "Batch fallback") as repository:
        service = TimelineFilmstripService(repository, RuntimeContext.discover().paths)
        fallback_frames: list[int] = []
        monkeypatch.setattr(
            service.ffmpeg,
            "run",
            lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stderr="fixture failure"),
        )

        def render_fallback(_source: Path, *, source_frame: int, **_kwargs) -> Path:
            fallback_frames.append(source_frame)
            return fallback_tile

        monkeypatch.setattr(service, "_render_tile", render_fallback)

        paths = service._render_tiles(
            source,
            source_identity="batch-fallback",
            source_frames=[10, 20],
            fps_numerator=30,
            fps_denominator=1,
            width=160,
            height=90,
        )

    assert fallback_frames == [10, 20]
    assert paths == {10: fallback_tile, 20: fallback_tile}


def test_filmstrip_batch_does_not_mislabel_partial_ffmpeg_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    fallback_tile = tmp_path / "fallback.jpg"
    fallback_tile.write_bytes(b"fallback")
    with ProjectRepository.create(tmp_path / "project", "Partial batch") as repository:
        service = TimelineFilmstripService(repository, RuntimeContext.discover().paths)
        fallback_frames: list[int] = []

        def partial_batch(arguments, **_kwargs):
            pattern = Path(arguments[-1])
            partial = pattern.parent / "000001.jpg"
            partial.write_bytes(b"unknown-frame")
            return SimpleNamespace(returncode=0, stderr="")

        monkeypatch.setattr(service.ffmpeg, "run", partial_batch)

        def render_fallback(_source: Path, *, source_frame: int, **_kwargs) -> Path:
            fallback_frames.append(source_frame)
            return fallback_tile

        monkeypatch.setattr(service, "_render_tile", render_fallback)
        paths = service._render_tiles(
            source,
            source_identity="partial-batch",
            source_frames=[10, 20],
            fps_numerator=30,
            fps_denominator=1,
            width=160,
            height=90,
        )

    assert fallback_frames == [10, 20]
    assert paths == {10: fallback_tile, 20: fallback_tile}


def test_web_native_plan_slice_uses_requested_frame_and_frozen_tail(tmp_path: Path) -> None:
    source = tmp_path / "underlay.mp4"
    source.write_bytes(b"source")
    plan = WebNativeMediaPlan(
        video_segments=(
            WebNativeVideoSegment(
                source_id="underlay",
                path=source,
                start_ms=Fraction(100),
                duration_ms=Fraction(500),
                active_duration_ms=Fraction(300),
                source_in_ms=1000,
                fit="cover",
                playback="hold",
            ),
        ),
        audio_segments=(),
    )

    active = slice_web_native_media_plan_for_frame(
        plan,
        source_frame=6,
        fps_numerator=20,
        fps_denominator=1,
    )
    frozen = slice_web_native_media_plan_for_frame(
        plan,
        source_frame=11,
        fps_numerator=20,
        fps_denominator=1,
    )

    assert active.audio_segments == ()
    assert active.video_segments[0].source_in_ms == 1200
    assert active.video_segments[0].start_ms == 0
    assert active.video_segments[0].duration_ms == 50
    assert frozen.video_segments[0].source_in_ms == 1250


def test_new_filmstrip_generation_cancels_unclaimed_work() -> None:
    coordinator = _FilmstripRequestCoordinator()
    key = ("project", "desktop-actor")

    with coordinator.request(key, 1) as first:
        first()
        with coordinator.request(key, 2) as second:
            with pytest.raises(CancelledError, match="superseded"):
                first()
            second()


def test_owner_cancellation_rejects_running_and_late_filmstrip_work() -> None:
    coordinator = _FilmstripRequestCoordinator()
    key = ("project", "desktop-actor")

    with coordinator.request(key, 4) as running:
        coordinator.cancel(key, 5)
        with pytest.raises(CancelledError, match="superseded"):
            running()
    with coordinator.request(key, 4) as late:
        with pytest.raises(CancelledError, match="superseded"):
            late()
    with coordinator.request(key, 6) as reopened:
        reopened()
