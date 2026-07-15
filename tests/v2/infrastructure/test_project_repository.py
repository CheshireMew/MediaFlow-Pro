import sqlite3
from pathlib import Path

import pytest

from mediaflow.application.asset_service import AssetService
from mediaflow.domain.enums import AssetKind, ColorMode, ExportFormat, SequenceKind, TrackKind
from mediaflow.domain.models import (
    Clip,
    ExportPreset,
    ProjectProfile,
    TimelineMarker,
    TimelineRange,
)
from mediaflow.infrastructure.file_fingerprint import fingerprint_matches
from mediaflow.infrastructure.project_repository import ProjectRepository


def test_create_project_builds_final_directory_and_default_graph(tmp_path: Path) -> None:
    root = tmp_path / "Demo"
    profile = ProjectProfile(
        width=3840,
        height=2160,
        fps_numerator=60_000,
        fps_denominator=1001,
        color_mode=ColorMode.HDR10_BT2020_PQ,
        bit_depth=10,
    )
    with ProjectRepository.create(root, "Demo", profile) as repository:
        project = repository.get_project()
        sequences = repository.list_sequences()
        timeline = repository.load_timeline(project.main_sequence_id)
        buses = repository.list_audio_buses(project.main_sequence_id)

        assert project.name == "Demo"
        assert sequences[0].kind == SequenceKind.MAIN
        assert sequences[0].profile == profile
        assert [track.kind for track in timeline.tracks] == [
            TrackKind.VIDEO,
            TrackKind.AUDIO,
            TrackKind.SUBTITLE,
        ]
        assert [bus.name for bus in buses] == ["主总线", "对白", "音乐", "效果"]

    assert (root / "project.mfp").is_file()
    for directory in ("downloads", "generated", "proxies", "cache", "exports"):
        assert (root / directory).is_dir()


def test_second_writer_falls_back_to_read_only(tmp_path: Path) -> None:
    root = tmp_path / "Locked"
    first = ProjectRepository.create(root, "Locked")
    try:
        second = ProjectRepository.open(root, writable=True)
        try:
            assert second.read_only is True
            with pytest.raises(PermissionError, match="read-only"):
                second.create_short_sequence("Short")
        finally:
            second.close()
    finally:
        first.close()


def test_version_one_project_is_migrated_to_persisted_workflows(tmp_path: Path) -> None:
    root = tmp_path / "Migrated"
    with ProjectRepository.create(root, "Migrated"):
        pass
    database = root / "project.mfp"
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE workflow_run")
        connection.execute("UPDATE schema_info SET version=1")
        connection.execute("UPDATE project SET workflow_auto_continue=0")

    with ProjectRepository.open(root, writable=True) as repository:
        assert repository.get_project().workflow_auto_continue is None
        assert repository.list_workflow_runs() == []
        version = repository._fetchone("SELECT version FROM schema_info")
        assert version["version"] == 5


def test_version_three_project_gains_timeline_annotations_and_export_settings(tmp_path: Path) -> None:
    root = tmp_path / "MigratedV3"
    with ProjectRepository.create(root, "MigratedV3"):
        pass
    database = root / "project.mfp"
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE sequence_export_setting")
        connection.execute("DROP TABLE timeline_range")
        connection.execute("DROP TABLE timeline_marker")
        connection.execute("UPDATE schema_info SET version=3")

    with ProjectRepository.open(root, writable=True) as repository:
        project = repository.get_project()
        assert repository.list_timeline_markers(project.main_sequence_id) == []
        assert repository.list_timeline_ranges(project.main_sequence_id) == []
        assert repository._fetchone("SELECT version FROM schema_info")["version"] == 5


def test_timeline_annotations_and_sequence_export_preset_round_trip(tmp_path: Path) -> None:
    root = tmp_path / "Annotations"
    with ProjectRepository.create(root, "Annotations") as repository:
        sequence_id = repository.get_project().main_sequence_id
        state = repository.load_timeline(sequence_id)
        state.markers.append(TimelineMarker(sequence_id=sequence_id, frame=42, name="重点"))
        state.ranges.append(
            TimelineRange(sequence_id=sequence_id, start_frame=30, end_frame=90, name="候选")
        )
        repository.save_timeline(state)
        preset = ExportPreset(
            name="社交平台",
            format=ExportFormat.H264,
            container="mp4",
            video_codec="libx264",
            audio_codec="aac",
            pixel_format="yuv420p",
        )
        repository.save_sequence_export_preset(sequence_id, preset)

    with ProjectRepository.open(root) as reopened:
        sequence = reopened.get_sequence(reopened.get_project().main_sequence_id)
        state = reopened.load_timeline(sequence.id)
        assert [(item.frame, item.name) for item in state.markers] == [(42, "重点")]
        assert [(item.start_frame, item.end_frame) for item in state.ranges] == [(30, 90)]
        assert sequence.export_preset == preset


def test_external_asset_keeps_absolute_path_and_fingerprint(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"real-media-fixture")
    with ProjectRepository.create(tmp_path / "Project", "Project") as repository:
        asset = repository.import_external_asset(source, AssetKind.VIDEO)
        reloaded = repository.get_asset(asset.id)

        assert Path(reloaded.path).is_absolute()
        assert repository.resolve_asset_path(reloaded) == source.resolve()
        assert reloaded.fingerprint is not None
        assert fingerprint_matches(source, reloaded.fingerprint)


def test_timeline_round_trip_persists_actual_asset_reference(tmp_path: Path) -> None:
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"fixture")
    root = tmp_path / "Project"
    with ProjectRepository.create(root, "Project") as repository:
        asset = repository.import_external_asset(source, AssetKind.VIDEO)
        project = repository.get_project()
        state = repository.load_timeline(project.main_sequence_id)
        video_track = next(track for track in state.tracks if track.kind == TrackKind.VIDEO)
        state.clips.append(
            Clip(
                track_id=video_track.id,
                asset_id=asset.id,
                timeline_start=0,
                source_in=0,
                duration=120,
            )
        )
        repository.save_timeline(state)

    with ProjectRepository.open(root) as reopened:
        state = reopened.load_timeline(reopened.get_project().main_sequence_id)
        assert len(state.clips) == 1
        assert state.clips[0].asset_id == reopened.list_assets()[0].id


def test_managed_asset_cannot_escape_project(tmp_path: Path) -> None:
    from mediaflow.domain.enums import AssetOrigin
    from mediaflow.domain.models import Asset

    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"fixture")
    with ProjectRepository.create(tmp_path / "Project", "Project") as repository:
        project = repository.get_project()
        with pytest.raises(ValueError, match="inside the project"):
            repository.add_asset(
                Asset(
                    project_id=project.id,
                    name=outside.name,
                    kind=AssetKind.VIDEO,
                    origin=AssetOrigin.DOWNLOAD,
                    path=str(outside),
                    managed=True,
                )
            )


def test_changed_source_invalidates_derived_media(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"version-one")
    with ProjectRepository.create(tmp_path / "Project", "Project") as repository:
        asset = repository.import_external_asset(source, AssetKind.VIDEO)
        proxy = repository.project_dir / "proxies" / "proxy.mp4"
        sdr_proxy = repository.project_dir / "proxies" / "proxy-sdr.mp4"
        waveform = repository.project_dir / "cache" / "waveform.json"
        proxy.write_bytes(b"proxy")
        sdr_proxy.write_bytes(b"sdr-proxy")
        waveform.write_text("{}", encoding="utf-8")
        asset = repository.update_asset(
            asset.model_copy(
                update={
                    "proxy_path": str(proxy),
                    "sdr_preview_proxy_path": str(sdr_proxy),
                    "waveform_path": str(waveform),
                }
            )
        )

        source.write_bytes(b"version-two-is-different")
        refreshed = repository.refresh_asset_status(asset.id)

        assert refreshed.proxy_path is None
        assert refreshed.sdr_preview_proxy_path is None
        assert refreshed.waveform_path is None
        assert refreshed.fingerprint is not None
        assert refreshed.fingerprint.edge_sha256 != asset.fingerprint.edge_sha256


def test_relink_requires_matching_content_or_explicit_confirmation(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    replacement = tmp_path / "replacement.mp4"
    source.write_bytes(b"original")
    replacement.write_bytes(b"different")
    with ProjectRepository.create(tmp_path / "Project", "Project") as repository:
        asset = repository.import_external_asset(source, AssetKind.VIDEO)
        source.unlink()
        assert repository.refresh_asset_status(asset.id).status.value == "offline"

        with pytest.raises(ValueError, match="does not match"):
            repository.relink_asset(asset.id, replacement)
        relinked = repository.relink_asset(asset.id, replacement, allow_different_content=True)
        assert repository.resolve_asset_path(relinked) == replacement.resolve()


def test_batch_relink_only_uses_exact_fingerprint_matches(tmp_path: Path) -> None:
    source_a = tmp_path / "source-a.mp4"
    source_b = tmp_path / "source-b.mp4"
    source_a.write_bytes(b"same-content")
    source_b.write_bytes(b"other-content")
    search_root = tmp_path / "relocated"
    search_root.mkdir()
    exact_match = search_root / "nested" / "source-a.mp4"
    exact_match.parent.mkdir()
    hidden_elsewhere = tmp_path / "outside-search.mp4"

    with ProjectRepository.create(tmp_path / "Project", "Project") as repository:
        asset_a = repository.import_external_asset(source_a, AssetKind.VIDEO)
        asset_b = repository.import_external_asset(source_b, AssetKind.VIDEO)
        source_a.replace(exact_match)
        source_b.replace(hidden_elsewhere)
        repository.refresh_asset_status(asset_a.id)
        repository.refresh_asset_status(asset_b.id)

        relinked, unresolved = AssetService(repository, probe=None).relink_offline_from_directory(search_root)
        assert [asset.id for asset in relinked] == [asset_a.id]
        assert [asset.id for asset in unresolved] == [asset_b.id]
        assert repository.resolve_asset_path(relinked[0]) == exact_match.resolve()
