import re
import subprocess
from pathlib import Path

from mediaflow.application.asset_service import AssetService
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.domain.enums import AudioEffectKind, ColorMode, ExportFormat, TrackKind, TransitionKind
from mediaflow.domain.models import (
    AudioEffect,
    ExportPreset,
    ProjectProfile,
    SubtitleDocument,
    SubtitleSegment,
)
from mediaflow.infrastructure.encoder_discovery import EncoderDiscoveryService
from mediaflow.infrastructure.media_probe import MediaProbe
from mediaflow.infrastructure.mlt import MltExportService, TimelineCompiler
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from tests.v2.infrastructure.test_media_pipeline import generate_real_media


def test_canonical_timeline_compiles_and_real_mlt_export_is_consumable(tmp_path: Path) -> None:
    paths = RuntimePaths.discover()
    assert paths.melt is not None
    source = tmp_path / "source.mp4"
    generate_real_media(source, paths, width=320, height=180)
    root = tmp_path / "Project"

    with ProjectRepository.create(root, "Project") as repository:
        asset_service = AssetService(repository, MediaProbe(paths))
        asset = asset_service.import_external(source)
        asset = asset_service.adopt_main_profile_from_video(asset.id)
        project = repository.get_project()
        editor = TimelineEditor(repository, project.main_sequence_id)
        video_track = next(track for track in editor.state.tracks if track.kind == TrackKind.VIDEO)
        editor.add_clip(
            track_id=video_track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=25,
        )
        master_bus = next(
            bus for bus in repository.list_audio_buses(project.main_sequence_id) if bus.parent_bus_id is None
        )
        repository.save_audio_bus(master_bus.model_copy(update={"gain_db": -1.0}))
        repository.save_audio_effect(
            AudioEffect(
                bus_id=master_bus.id,
                kind=AudioEffectKind.LIMITER,
                position=0,
                parameters={"ceiling_db": -1.0},
            )
        )
        state = editor.state
        compiler = TimelineCompiler(repository)
        document = compiler.compile(state)
        assert str(source.resolve()) in document.xml
        assert "tractor0" in document.xml
        assert "avfilter.alimiter" in document.xml
        assert "audio_bus_" in document.xml

        preset = ExportPreset(
            name="H.264 Test",
            format=ExportFormat.H264,
            container="mp4",
            video_codec="libx264",
            audio_codec="aac",
            pixel_format="yuv420p",
            quality_value=23,
            preset="veryfast",
            gop_frames=25,
        )
        result = MltExportService(compiler, paths).export(
            state,
            preset,
            root / "exports" / "real-mlt-export.mp4",
        )

        assert result.output_path.is_file()
        video = next(stream for stream in result.probe["streams"] if stream["codec_type"] == "video")
        audio = next(stream for stream in result.probe["streams"] if stream["codec_type"] == "audio")
        assert video["codec_name"] == "h264"
        assert audio["codec_name"] == "aac"
        assert (video["width"], video["height"]) == (320, 180)

        hardware = {
            item["value"] for item in EncoderDiscoveryService(paths).video_options()
        }
        if "h264_nvenc" in hardware:
            hardware_result = MltExportService(compiler, paths).export(
                state,
                preset.model_copy(
                    update={
                        "name": "H.264 NVENC Test",
                        "video_codec": "h264_nvenc",
                        "preset": "p4",
                    }
                ),
                root / "exports" / "real-nvenc-export.mp4",
            )
            hardware_video = next(
                stream
                for stream in hardware_result.probe["streams"]
                if stream["codec_type"] == "video"
            )
            assert hardware_video["codec_name"] == "h264"


def _generate_color_media(path: Path, paths: RuntimePaths, color: str) -> None:
    result = subprocess.run(
        [
            str(paths.ffmpeg),
            "-y",
            "-hide_banner",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s=160x90:r=25:d=3",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")


def _generate_temporal_color_media(path: Path, paths: RuntimePaths) -> None:
    command = [str(paths.ffmpeg), "-y", "-hide_banner", "-v", "error"]
    for color in ("red", "green", "blue"):
        command.extend(["-f", "lavfi", "-i", f"color=c={color}:s=160x90:r=25:d=1"])
    command.extend(
        [
            "-filter_complex",
            "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
            "-map",
            "[v]",
            "-c:v",
            "libx264",
            "-g",
            "1",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ]
    )
    result = subprocess.run(command, capture_output=True, timeout=60, check=False)
    assert result.returncode == 0, result.stderr.decode(errors="replace")


def _generate_sine_audio(
    path: Path,
    paths: RuntimePaths,
    *,
    frequency: int,
    duration: int,
) -> None:
    result = subprocess.run(
        [
            str(paths.ffmpeg),
            "-y",
            "-hide_banner",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:sample_rate=48000:duration={duration}",
            "-af",
            "volume=0.35",
            "-c:a",
            "pcm_s16le",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def _band_mean_volume(
    path: Path,
    paths: RuntimePaths,
    *,
    start: float,
    duration: float,
    frequency: int,
) -> float:
    result = subprocess.run(
        [
            str(paths.ffmpeg),
            "-hide_banner",
            "-ss",
            str(start),
            "-t",
            str(duration),
            "-i",
            str(path),
            "-af",
            f"bandpass=f={frequency}:w=30,volumedetect",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    match = re.search(r"mean_volume:\s*(-?[0-9.]+) dB", result.stderr)
    assert match, result.stderr
    return float(match.group(1))


def _frame_rgb_means(path: Path, paths: RuntimePaths, frames: list[int]) -> list[tuple[float, float, float]]:
    expression = "+".join(f"eq(n\\,{frame})" for frame in frames)
    result = subprocess.run(
        [
            str(paths.ffmpeg),
            "-hide_banner",
            "-v",
            "error",
            "-i",
            str(path),
            "-vf",
            f"select='{expression}'",
            "-vsync",
            "0",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    frame_size = 160 * 90 * 3
    assert len(result.stdout) == frame_size * len(frames)
    means = []
    for index in range(len(frames)):
        payload = result.stdout[index * frame_size : (index + 1) * frame_size]
        means.append(tuple(sum(payload[channel::3]) / (frame_size // 3) for channel in range(3)))
    return means


def test_mlt_transition_uses_two_real_sources_and_preserves_timeline_duration(tmp_path: Path) -> None:
    paths = RuntimePaths.discover()
    red_source = tmp_path / "red.mp4"
    blue_source = tmp_path / "blue.mp4"
    _generate_color_media(red_source, paths, "red")
    _generate_color_media(blue_source, paths, "blue")

    with ProjectRepository.create(tmp_path / "Transition Project", "Transition Project") as repository:
        assets = AssetService(repository, MediaProbe(paths))
        red = assets.import_external(red_source)
        blue = assets.import_external(blue_source)
        red = assets.adopt_main_profile_from_video(red.id)
        editor = TimelineEditor(repository, repository.get_project().main_sequence_id)
        video_track = next(track for track in editor.state.tracks if track.kind == TrackKind.VIDEO)
        left = editor.add_clip(
            track_id=video_track.id,
            asset_id=red.id,
            timeline_start=0,
            source_in=0,
            duration=75,
        )
        right = editor.add_clip(
            track_id=video_track.id,
            asset_id=blue.id,
            timeline_start=75,
            source_in=0,
            duration=75,
        )
        editor.create_transition(left.id, right.id, TransitionKind.DISSOLVE, 10)
        state = editor.state
        compiler = TimelineCompiler(repository)
        document = compiler.compile(state)
        assert 'mlt_service">luma<' in document.xml
        assert 'mlt_service">mix<' in document.xml
        assert "transition_hold_" in document.xml
        assert document.duration_frames == 150

        preset = ExportPreset(
            name="Transition Test",
            format=ExportFormat.H264,
            container="mp4",
            video_codec="libx264",
            audio_codec=None,
            pixel_format="yuv420p",
            quality_value=18,
            preset="veryfast",
            gop_frames=25,
        )
        result = MltExportService(compiler, paths).export(
            state,
            preset,
            repository.project_dir / "exports" / "transition.mp4",
        )
        stream = next(item for item in result.probe["streams"] if item["codec_type"] == "video")
        assert int(stream["nb_frames"]) == 150
        before, middle, after = _frame_rgb_means(result.output_path, paths, [10, 75, 140])
        assert before[0] > 180 and before[2] < 80
        assert middle[0] > 55 and middle[2] > 55
        assert after[2] > 180 and after[0] < 80


def test_real_mlt_timewarp_reads_correct_forward_and_reverse_source_frames(tmp_path: Path) -> None:
    paths = RuntimePaths.discover()
    source = tmp_path / "temporal.mp4"
    _generate_temporal_color_media(source, paths)

    with ProjectRepository.create(tmp_path / "Timewarp Project", "Timewarp Project") as repository:
        assets = AssetService(repository, MediaProbe(paths))
        asset = assets.import_external(source)
        asset = assets.adopt_main_profile_from_video(asset.id)
        editor = TimelineEditor(repository, repository.get_project().main_sequence_id)
        video_track = next(track for track in editor.state.tracks if track.kind == TrackKind.VIDEO)
        fast = editor.add_clip(
            track_id=video_track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=37,
            speed_numerator=2,
        )
        editor.add_clip(
            track_id=video_track.id,
            asset_id=asset.id,
            timeline_start=37,
            source_in=74,
            duration=75,
            speed_numerator=-1,
        )
        assert fast.duration == 37
        preset = ExportPreset(
            name="Timewarp Test",
            format=ExportFormat.H264,
            container="mp4",
            video_codec="libx264",
            audio_codec=None,
            pixel_format="yuv420p",
            quality_value=18,
            preset="veryfast",
            gop_frames=25,
        )
        result = MltExportService(TimelineCompiler(repository), paths).export(
            editor.state,
            preset,
            repository.project_dir / "exports" / "timewarp.mp4",
        )
        colors = _frame_rgb_means(result.output_path, paths, [3, 18, 33, 42, 72, 102])
        fast_red, fast_green, fast_blue, reverse_blue, reverse_green, reverse_red = colors
        assert fast_red[0] > 150 and fast_red[2] < 80
        assert fast_green[1] > fast_green[0] and fast_green[1] > fast_green[2]
        assert fast_blue[2] > 150 and fast_blue[0] < 80
        assert reverse_blue[2] > 150 and reverse_blue[0] < 80
        assert reverse_green[1] > reverse_green[0] and reverse_green[1] > reverse_green[2]
        assert reverse_red[0] > 150 and reverse_red[2] < 80


def test_real_mlt_transition_accepts_speed_adjusted_adjacent_clips(tmp_path: Path) -> None:
    paths = RuntimePaths.discover()
    red_source = tmp_path / "red-fast.mp4"
    blue_source = tmp_path / "blue-fast.mp4"
    _generate_color_media(red_source, paths, "red")
    _generate_color_media(blue_source, paths, "blue")

    with ProjectRepository.create(tmp_path / "Fast Transition", "Fast Transition") as repository:
        assets = AssetService(repository, MediaProbe(paths))
        red = assets.import_external(red_source)
        blue = assets.import_external(blue_source)
        red = assets.adopt_main_profile_from_video(red.id)
        editor = TimelineEditor(repository, repository.get_project().main_sequence_id)
        video_track = next(track for track in editor.state.tracks if track.kind == TrackKind.VIDEO)
        left = editor.add_clip(
            track_id=video_track.id,
            asset_id=red.id,
            timeline_start=0,
            source_in=0,
            duration=37,
            speed_numerator=2,
        )
        right = editor.add_clip(
            track_id=video_track.id,
            asset_id=blue.id,
            timeline_start=37,
            source_in=0,
            duration=37,
            speed_numerator=2,
        )
        editor.create_transition(left.id, right.id, TransitionKind.DISSOLVE, 10)
        preset = ExportPreset(
            name="Fast Transition Test",
            format=ExportFormat.H264,
            container="mp4",
            video_codec="libx264",
            audio_codec=None,
            pixel_format="yuv420p",
            quality_value=18,
            preset="veryfast",
            gop_frames=25,
        )
        result = MltExportService(TimelineCompiler(repository), paths).export(
            editor.state,
            preset,
            repository.project_dir / "exports" / "fast-transition.mp4",
        )
        stream = next(item for item in result.probe["streams"] if item["codec_type"] == "video")
        assert int(stream["nb_frames"]) == 74
        before, middle, after = _frame_rgb_means(result.output_path, paths, [5, 37, 68])
        assert before[0] > 150 and before[2] < 80
        assert middle[0] > 45 and middle[2] > 45
        assert after[2] > 150 and after[0] < 80


def test_subtitle_placement_is_burned_and_exported_as_external_srt(tmp_path: Path) -> None:
    paths = RuntimePaths.discover()
    source = tmp_path / "black.mp4"
    _generate_color_media(source, paths, "black")

    with ProjectRepository.create(tmp_path / "Subtitle Project", "Subtitle Project") as repository:
        asset_service = AssetService(repository, MediaProbe(paths))
        asset = asset_service.import_external(source)
        asset = asset_service.adopt_main_profile_from_video(asset.id)
        project = repository.get_project()
        editor = TimelineEditor(repository, project.main_sequence_id)
        video_track = next(track for track in editor.state.tracks if track.kind == TrackKind.VIDEO)
        subtitle_track = next(track for track in editor.state.tracks if track.kind == TrackKind.SUBTITLE)
        editor.add_clip(
            track_id=video_track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=25,
        )
        document = SubtitleDocument(
            project_id=project.id,
            asset_id=asset.id,
            language="en",
        )
        segment = SubtitleSegment(
            document_id=document.id,
            start_frame=5,
            end_frame=20,
            text="HELLO MEDIAFLOW",
        )
        repository.create_subtitle_document(document, [segment])
        repository.place_subtitle_document(document.id, subtitle_track.id)
        state = editor.state
        preset = ExportPreset(
            name="Subtitle Test",
            format=ExportFormat.H264,
            container="mp4",
            video_codec="libx264",
            audio_codec=None,
            pixel_format="yuv420p",
            quality_value=18,
            preset="veryfast",
            gop_frames=25,
            burn_subtitle_track_id=subtitle_track.id,
        )
        result = MltExportService(TimelineCompiler(repository), paths).export(
            state,
            preset,
            repository.project_dir / "exports" / "subtitle.mp4",
        )
        assert len(result.subtitle_files) == 1
        assert "HELLO MEDIAFLOW" in result.subtitle_files[0].read_text(encoding="utf-8-sig")
        without_text, with_text = _frame_rgb_means(result.output_path, paths, [2, 10])
        assert sum(with_text) > sum(without_text) + 2.0


def test_real_hevc_hdr10_export_is_ten_bit_and_carries_mastering_metadata(tmp_path: Path) -> None:
    paths = RuntimePaths.discover()
    source = tmp_path / "sdr-source.mp4"
    _generate_color_media(source, paths, "white")
    profile = ProjectProfile(
        width=160,
        height=90,
        fps_numerator=25,
        fps_denominator=1,
        color_mode=ColorMode.HDR10_BT2020_PQ,
        bit_depth=10,
    )
    with ProjectRepository.create(tmp_path / "HDR Project", "HDR Project", profile) as repository:
        asset = AssetService(repository, MediaProbe(paths)).import_external(source)
        editor = TimelineEditor(repository, repository.get_project().main_sequence_id)
        video_track = next(track for track in editor.state.tracks if track.kind == TrackKind.VIDEO)
        first = editor.add_clip(
            track_id=video_track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=25,
        )
        second = editor.add_clip(
            track_id=video_track.id,
            asset_id=asset.id,
            timeline_start=25,
            source_in=0,
            duration=25,
        )
        editor.create_transition(first.id, second.id, TransitionKind.DISSOLVE, duration=8)
        preset = ExportPreset(
            name="HDR10 Test",
            format=ExportFormat.HEVC,
            container="mp4",
            video_codec="libx265",
            audio_codec=None,
            pixel_format="yuv420p10le",
            quality_value=28,
            preset="ultrafast",
            gop_frames=25,
        )
        result = MltExportService(TimelineCompiler(repository), paths).export(
            editor.state,
            preset,
            repository.project_dir / "exports" / "hdr10.mp4",
        )
        video = next(item for item in result.probe["streams"] if item["codec_type"] == "video")
        assert video["codec_name"] == "hevc"
        assert "10" in video["pix_fmt"]
        assert video["color_primaries"] == "bt2020"
        assert video["color_transfer"] == "smpte2084"
        side_data = list(video.get("side_data_list") or [])
        side_data.extend(result.probe["frames"][0].get("side_data_list") or [])
        side_types = {item.get("side_data_type") for item in side_data}
        assert "Mastering display metadata" in side_types
        assert "Content light level metadata" in side_types


def test_real_av1_prores_and_audio_exports_are_consumable(tmp_path: Path) -> None:
    paths = RuntimePaths.discover()
    source = tmp_path / "source.mp4"
    generate_real_media(source, paths, width=320, height=180)
    with ProjectRepository.create(tmp_path / "Formats Project", "Formats Project") as repository:
        asset_service = AssetService(repository, MediaProbe(paths))
        asset = asset_service.import_external(source)
        asset = asset_service.adopt_main_profile_from_video(asset.id)
        editor = TimelineEditor(repository, repository.get_project().main_sequence_id)
        video_track = next(track for track in editor.state.tracks if track.kind == TrackKind.VIDEO)
        editor.add_clip(
            track_id=video_track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=25,
        )
        service = MltExportService(TimelineCompiler(repository), paths)

        av1 = service.export(
            editor.state,
            ExportPreset(
                name="AV1 Test",
                format=ExportFormat.AV1,
                container="mkv",
                video_codec="libsvtav1",
                audio_codec="libopus",
                pixel_format="yuv420p",
                quality_value=35,
                preset="12",
                gop_frames=25,
            ),
            repository.project_dir / "exports" / "video-av1.mkv",
        )
        assert (
            next(item for item in av1.probe["streams"] if item["codec_type"] == "video")["codec_name"]
            == "av1"
        )

        prores = service.export(
            editor.state,
            ExportPreset(
                name="ProRes Test",
                format=ExportFormat.PRORES,
                container="mov",
                video_codec="prores_ks",
                audio_codec="pcm_s16le",
                pixel_format="yuv422p10le",
                quality_value=0,
                preset="medium",
                gop_frames=25,
                advanced={"profile": 3},
            ),
            repository.project_dir / "exports" / "video-prores.mov",
        )
        assert (
            next(item for item in prores.probe["streams"] if item["codec_type"] == "video")["codec_name"]
            == "prores"
        )

        audio = service.export(
            editor.state,
            ExportPreset(
                name="FLAC Test",
                format=ExportFormat.AUDIO,
                container="flac",
                video_codec=None,
                audio_codec="flac",
                pixel_format=None,
                quality_value=0,
                preset="medium",
                gop_frames=25,
            ),
            repository.project_dir / "exports" / "audio.flac",
        )
        assert not any(item["codec_type"] == "video" for item in audio.probe["streams"])
        assert (
            next(item for item in audio.probe["streams"] if item["codec_type"] == "audio")["codec_name"]
            == "flac"
        )

        for name, container, codec, suffix, expected_codec in (
            ("AAC Test", "ipod", "aac", "m4a", "aac"),
            ("Opus Test", "ogg", "libopus", "ogg", "opus"),
            ("PCM Test", "wav", "pcm_s24le", "wav", "pcm_s24le"),
        ):
            result = service.export(
                editor.state,
                ExportPreset(
                    name=name,
                    format=ExportFormat.AUDIO,
                    container=container,
                    video_codec=None,
                    audio_codec=codec,
                    pixel_format=None,
                    quality_value=0,
                    preset="medium",
                    gop_frames=25,
                ),
                repository.project_dir / "exports" / f"audio-{codec}.{suffix}",
            )
            assert not any(item["codec_type"] == "video" for item in result.probe["streams"])
            assert (
                next(item for item in result.probe["streams"] if item["codec_type"] == "audio")[
                    "codec_name"
                ]
                == expected_codec
            )

        surround = service.export(
            editor.state,
            ExportPreset(
                name="5.1 PCM Test",
                format=ExportFormat.AUDIO,
                container="wav",
                video_codec=None,
                audio_codec="pcm_s24le",
                pixel_format=None,
                quality_value=0,
                preset="medium",
                gop_frames=25,
                advanced={"audio_sample_rate": 48_000, "audio_channels": 6},
            ),
            repository.project_dir / "exports" / "audio-5.1.wav",
        )
        surround_audio = next(
            item for item in surround.probe["streams"] if item["codec_type"] == "audio"
        )
        assert surround_audio["channels"] == 6
        assert surround_audio["sample_rate"] == "48000"


def test_fixed_audio_effect_catalog_renders_real_audio(tmp_path: Path) -> None:
    paths = RuntimePaths.discover()
    source = tmp_path / "dialogue.wav"
    _generate_sine_audio(source, paths, frequency=700, duration=2)
    with ProjectRepository.create(tmp_path / "Effect Catalog", "Effect Catalog") as repository:
        asset = AssetService(repository, MediaProbe(paths)).import_external(source)
        project = repository.get_project()
        editor = TimelineEditor(repository, project.main_sequence_id)
        audio_track = next(
            item for item in editor.state.tracks if item.kind == TrackKind.AUDIO
        )
        editor.add_clip(
            track_id=audio_track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=60,
        )
        master = next(
            item
            for item in repository.list_audio_buses(project.main_sequence_id)
            if item.parent_bus_id is None
        )
        kinds = [
            AudioEffectKind.PARAMETRIC_EQ,
            AudioEffectKind.HIGH_PASS,
            AudioEffectKind.LOW_PASS,
            AudioEffectKind.COMPRESSOR,
            AudioEffectKind.LIMITER,
            AudioEffectKind.NOISE_GATE,
            AudioEffectKind.RNNOISE,
            AudioEffectKind.CHANNEL_MAP,
            AudioEffectKind.LOUDNESS_NORMALIZE,
        ]
        for position, kind in enumerate(kinds):
            repository.save_audio_effect(
                AudioEffect(bus_id=master.id, kind=kind, position=position)
            )

        result = MltExportService(TimelineCompiler(repository), paths).export(
            editor.state,
            ExportPreset(
                name="Effect Catalog Test",
                format=ExportFormat.AUDIO,
                container="flac",
                video_codec=None,
                audio_codec="flac",
                pixel_format=None,
                quality_value=0,
                preset="medium",
                gop_frames=30,
                advanced={"audio_sample_rate": 48_000, "audio_channels": 2},
            ),
            repository.project_dir / "exports" / "effect-catalog.flac",
        )
        audio = next(
            item for item in result.probe["streams"] if item["codec_type"] == "audio"
        )
        assert audio["codec_name"] == "flac"
        assert audio["sample_rate"] == "48000"
        assert audio["channels"] == 2
        assert result.output_path.stat().st_size > 1000


def test_dialogue_bus_really_ducks_music_in_exported_audio(tmp_path: Path) -> None:
    paths = RuntimePaths.discover()
    music_source = tmp_path / "music.wav"
    dialogue_source = tmp_path / "dialogue.wav"
    _generate_sine_audio(music_source, paths, frequency=440, duration=3)
    _generate_sine_audio(dialogue_source, paths, frequency=1000, duration=1)
    with ProjectRepository.create(tmp_path / "Ducking Project", "Ducking Project") as repository:
        assets = AssetService(repository, MediaProbe(paths))
        music_asset = assets.import_external(music_source)
        dialogue_asset = assets.import_external(dialogue_source)
        project = repository.get_project()
        editor = TimelineEditor(repository, project.main_sequence_id)
        buses = repository.list_audio_buses(project.main_sequence_id)
        dialogue_bus = next(item for item in buses if item.name == "对白")
        music_bus = next(item for item in buses if item.name == "音乐")
        dialogue_track = next(item for item in editor.state.tracks if item.kind == TrackKind.AUDIO)
        music_track = editor.add_track(
            TrackKind.AUDIO,
            "Music",
            audio_bus_id=music_bus.id,
        )
        editor.add_clip(
            track_id=music_track.id,
            asset_id=music_asset.id,
            timeline_start=0,
            source_in=0,
            duration=90,
        )
        editor.add_clip(
            track_id=dialogue_track.id,
            asset_id=dialogue_asset.id,
            timeline_start=30,
            source_in=0,
            duration=30,
        )
        repository.save_audio_effect(
            AudioEffect(
                bus_id=music_bus.id,
                kind=AudioEffectKind.DUCKING,
                position=0,
                parameters={
                    "driver_bus_id": dialogue_bus.id,
                    "reduction_db": -20.0,
                    "attack_ms": 0.0,
                    "release_ms": 0.0,
                },
            )
        )
        output = (
            MltExportService(TimelineCompiler(repository), paths)
            .export(
                editor.state,
                ExportPreset(
                    name="Ducking Test",
                    format=ExportFormat.AUDIO,
                    container="flac",
                    video_codec=None,
                    audio_codec="flac",
                    pixel_format=None,
                    quality_value=0,
                    preset="medium",
                    gop_frames=60,
                ),
                repository.project_dir / "exports" / "ducking.flac",
            )
            .output_path
        )
        music_before = _band_mean_volume(output, paths, start=0.2, duration=0.5, frequency=440)
        music_during = _band_mean_volume(output, paths, start=1.2, duration=0.5, frequency=440)
        assert music_during < music_before - 12.0
