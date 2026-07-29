from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from mediaflow.application.sequence_service import SequenceService
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.domain.audio import AudioBus, AudioEffect
from mediaflow.domain.enums import AssetKind, AudioEffectKind, TrackKind
from mediaflow.domain.subtitles import SubtitleDocument, SubtitleSegment
from mediaflow.infrastructure.mlt import TimelineCompiler
from mediaflow.infrastructure.project_repository import ProjectRepository


def _audio_graph_signature(repository: ProjectRepository, sequence_id: str) -> tuple:
    buses = repository.audio.list_audio_buses(sequence_id)
    by_id = {bus.id: bus for bus in buses}
    bus_signature = []
    for bus in buses:
        effects = []
        for effect in repository.audio.list_audio_effects(bus.id):
            parameters = dict(effect.parameters)
            driver_bus_id = str(parameters.get("driver_bus_id", ""))
            if driver_bus_id:
                parameters["driver_bus_id"] = by_id[driver_bus_id].name
            effects.append(
                (
                    effect.kind.value,
                    effect.position,
                    effect.enabled,
                    tuple(sorted(parameters.items())),
                )
            )
        bus_signature.append(
            (
                bus.name,
                by_id[bus.parent_bus_id].name if bus.parent_bus_id else None,
                bus.position,
                bus.gain_db,
                bus.muted,
                bus.solo,
                bus.channel_layout,
                tuple(effects),
            )
        )
    state = repository.timeline.load_timeline(sequence_id)
    track_signature = tuple(
        (
            track.name,
            track.kind.value,
            by_id[track.audio_bus_id].name if track.audio_bus_id else None,
            track.enabled,
            track.muted,
            track.solo,
            track.primary_dialogue,
        )
        for track in state.tracks
    )
    return tuple(bus_signature), track_signature


def _compiled_audio_bus_names(repository: ProjectRepository, sequence_id: str) -> set[str]:
    state = repository.timeline.load_timeline(sequence_id)
    root = ET.fromstring(TimelineCompiler(repository).compile(state).xml)
    return {
        property_node.text or ""
        for tractor in root.findall("tractor")
        for property_node in tractor.findall("property")
        if property_node.get("name") == "mediaflow:audio_bus"
    }


def test_short_sequence_clones_and_replaces_the_complete_audio_graph(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "source.wav"
    source_path.write_bytes(b"audio-source")
    project_dir = tmp_path / "Audio Graph Short"
    with ProjectRepository.create(project_dir, "Audio Graph Short") as repository:
        asset = repository.catalog.import_external_asset(source_path, AssetKind.AUDIO)
        asset = repository.catalog.update_asset(
            asset.model_copy(
                update={
                    "metadata": asset.metadata.model_copy(
                        update={"duration_frames": 120, "has_audio": True}
                    )
                }
            )
        )
        project = repository.catalog.get_project()
        source_editor = TimelineEditor(repository, project.main_sequence_id)
        buses = repository.audio.list_audio_buses(project.main_sequence_id)
        master = next(bus for bus in buses if bus.parent_bus_id is None)
        dialogue = next(bus for bus in buses if bus.name == "对白")
        music = next(bus for bus in buses if bus.name == "音乐")
        repository.audio.save_audio_bus(
            master.model_copy(update={"gain_db": -1.5, "channel_layout": "5.1"})
        )
        narration = repository.audio.save_audio_bus(
            AudioBus(
                sequence_id=project.main_sequence_id,
                name="嵌套旁白",
                parent_bus_id=dialogue.id,
                position=len(buses),
                gain_db=-3.0,
                channel_layout="mono",
            )
        )
        repository.audio.save_audio_effect(
            AudioEffect(
                bus_id=narration.id,
                kind=AudioEffectKind.HIGH_PASS,
                position=0,
                parameters={"frequency_hz": 120.0},
            )
        )
        repository.audio.save_audio_effect(
            AudioEffect(
                bus_id=music.id,
                kind=AudioEffectKind.DUCKING,
                position=0,
                parameters={
                    "driver_bus_id": narration.id,
                    "threshold_db": -24.0,
                    "reduction_db": -12.0,
                    "attack_ms": 0.0,
                    "release_ms": 0.0,
                },
            )
        )
        narration_track = source_editor.add_track(
            TrackKind.AUDIO,
            "Narration",
            audio_bus_id=narration.id,
        )
        music_track = source_editor.add_track(
            TrackKind.AUDIO,
            "Music",
            audio_bus_id=music.id,
        )
        source_editor.add_clip(
            track_id=narration_track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=90,
        )
        source_editor.add_clip(
            track_id=music_track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=90,
        )

        service = SequenceService(repository)
        short = service.create_short_from_bounds(
            project.main_sequence_id,
            10,
            70,
            name="第一版",
        )
        source_signature = _audio_graph_signature(repository, project.main_sequence_id)
        assert _audio_graph_signature(repository, short.id) == source_signature
        assert _compiled_audio_bus_names(repository, short.id) == _compiled_audio_bus_names(
            repository,
            project.main_sequence_id,
        )
        short_xml = TimelineCompiler(repository).compile(
            repository.timeline.load_timeline(short.id)
        ).xml
        assert "avfilter.highpass" in short_xml
        assert "0=-12dB" in short_xml

        old_destination_bus_ids = {
            bus.id for bus in repository.audio.list_audio_buses(short.id)
        }
        TimelineEditor(repository, short.id).set_sequence_in_out(2, 30)
        repository.audio.save_audio_bus(
            narration.model_copy(update={"gain_db": -6.0, "solo": True})
        )
        service.sync_short_from_bounds(
            project.main_sequence_id,
            short.id,
            20,
            60,
            name="第二版",
        )

        updated = repository.timeline.load_timeline(short.id)
        assert updated.sequence.name == "第二版"
        assert updated.sequence.in_out is None
        assert _audio_graph_signature(repository, short.id) == _audio_graph_signature(
            repository,
            project.main_sequence_id,
        )
        updated_buses = repository.audio.list_audio_buses(short.id)
        assert len([bus for bus in updated_buses if bus.parent_bus_id is None]) == 1
        assert old_destination_bus_ids.isdisjoint({bus.id for bus in updated_buses})

    with ProjectRepository.open(project_dir) as reopened:
        assert _audio_graph_signature(reopened, short.id) == _audio_graph_signature(
            reopened,
            reopened.catalog.get_project().main_sequence_id,
        )
        assert "avfilter.highpass" in TimelineCompiler(reopened).compile(
            reopened.timeline.load_timeline(short.id)
        ).xml


def test_short_sync_preserves_manual_subtitle_timing_across_reopen(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "subtitle-source.mp4"
    source_path.write_bytes(b"video-source")
    project_dir = tmp_path / "Subtitle Override Short"
    with ProjectRepository.create(project_dir, "Subtitle Override Short") as repository:
        asset = repository.catalog.import_external_asset(source_path, AssetKind.VIDEO)
        asset = repository.catalog.update_asset(
            asset.model_copy(
                update={
                    "metadata": asset.metadata.model_copy(
                        update={"duration_frames": 100, "has_video": True}
                    )
                }
            )
        )
        source_sequence_id = repository.catalog.get_project().main_sequence_id
        editor = TimelineEditor(repository, source_sequence_id)
        video_track = editor.add_track(TrackKind.VIDEO, "Video")
        subtitle_track = editor.add_track(TrackKind.SUBTITLE, "Subtitle")
        editor.add_clip(
            track_id=video_track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=100,
        )
        document = SubtitleDocument(
            project_id=repository.catalog.get_project().id,
            asset_id=asset.id,
            language="zh-CN",
        )
        segment = SubtitleSegment(
            document_id=document.id,
            start_frame=10,
            end_frame=20,
            text="手调字幕",
        )
        repository.subtitles.create_subtitle_document(document, [segment])
        placement = repository.subtitles.place_subtitle_document(
            document.id,
            subtitle_track.id,
        )[0]
        repository.subtitles.update_subtitle_placement_range(
            placement.id,
            15,
            30,
            timing_overridden=True,
        )

        service = SequenceService(repository)
        short = service.create_short_from_bounds(
            source_sequence_id,
            0,
            60,
            name="字幕短片",
        )
        service.sync_short_from_bounds(
            source_sequence_id,
            short.id,
            5,
            55,
            name="字幕短片更新",
        )
        short_track = next(
            track
            for track in repository.timeline.load_timeline(short.id).tracks
            if track.kind == TrackKind.SUBTITLE
        )
        copied = repository.subtitles.list_subtitle_placements(short_track.id)
        assert [
            (item.start_frame, item.end_frame, item.timing_overridden)
            for item in copied
        ] == [(10, 25, True)]
        short_id = short.id

    with ProjectRepository.open(project_dir) as reopened:
        before_sync_track = next(
            track
            for track in reopened.timeline.load_timeline(short_id).tracks
            if track.kind == TrackKind.SUBTITLE
        )
        assert reopened.subtitles.list_subtitle_placements(
            before_sync_track.id
        )[0].timing_overridden is True
        SequenceService(reopened).sync_short_from_bounds(
            reopened.catalog.get_project().main_sequence_id,
            short_id,
            5,
            55,
            name="字幕短片更新",
        )
        after_sync_track = next(
            track
            for track in reopened.timeline.load_timeline(short_id).tracks
            if track.kind == TrackKind.SUBTITLE
        )
        copied = reopened.subtitles.list_subtitle_placements(after_sync_track.id)
        assert [
            (item.start_frame, item.end_frame, item.timing_overridden)
            for item in copied
        ] == [(10, 25, True)]
