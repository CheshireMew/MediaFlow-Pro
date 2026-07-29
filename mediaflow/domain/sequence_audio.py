from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction

from mediaflow.domain.asr import (
    AsrResult,
    TranscriptionPlan,
    TranscriptionRegionPlan,
    TranscriptionSourcePlan,
)
from mediaflow.domain.audio import AudioBus
from mediaflow.domain.enums import ClipMediaKind, TrackKind
from mediaflow.domain.project import Asset, ProjectProfile
from mediaflow.domain.settings import AsrSettings
from mediaflow.domain.timebase import seconds_to_frames
from mediaflow.domain.timeline import Clip, TimelineState


@dataclass(frozen=True, slots=True)
class SequenceAudioSelection:
    track_ids: tuple[str, ...]
    asset_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DialogueTranscriptionSelection:
    track_id: str
    clips: tuple[Clip, ...]
    asset_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProjectedDialogueWord:
    start_frame: int
    end_frame: int
    text: str
    confidence: float | None


@dataclass(frozen=True, slots=True)
class ProjectedDialogueSegment:
    start_frame: int
    end_frame: int
    text: str
    confidence: float | None
    words: tuple[ProjectedDialogueWord, ...]


TRANSCRIPTION_CONTEXT_SECONDS = 0.5


def audio_clips_for_track(state: TimelineState, audio_track_id: str) -> list[Clip]:
    """Return actual and still-linked audio clips routed through one audio track."""
    source_video_track_ids = {
        track.id
        for track in state.tracks
        if track.linked_audio_track_id == audio_track_id
    }
    return sorted(
        (
            clip
            for clip in state.clips
            if (
                clip.media_kind == ClipMediaKind.AUDIO_ONLY
                and clip.track_id == audio_track_id
            )
            or (
                clip.media_kind == ClipMediaKind.LINKED_AV
                and clip.track_id in source_video_track_ids
            )
        ),
        key=lambda clip: (clip.timeline_start, clip.id),
    )


def output_audio_clips_for_track(
    state: TimelineState,
    audio_track_id: str,
) -> list[Clip]:
    """Return clips whose audio remains visible in the effective output graph.

    Independent audio follows its audio track. Linked A/V additionally follows
    the source video track's enabled, solo and mute state, so hidden picture
    never leaks audio while the video producer itself remains available.
    """

    audible_video_track_ids = {
        track.id
        for track in state.effective_tracks(TrackKind.VIDEO)
        if not track.muted
    }
    return [
        clip
        for clip in audio_clips_for_track(state, audio_track_id)
        if clip.media_kind == ClipMediaKind.AUDIO_ONLY
        or clip.track_id in audible_video_track_ids
    ]


def select_dialogue_transcription_sources(
    state: TimelineState,
    assets: dict[str, Asset],
    *,
    start_frame: int = 0,
    end_frame: int | None = None,
) -> DialogueTranscriptionSelection:
    """Select source clips from the sequence's one designated dialogue track."""
    primary_tracks = [track for track in state.tracks if track.primary_dialogue]
    if len(primary_tracks) != 1:
        raise ValueError("请先指定一条主要对白轨")
    track = primary_tracks[0]
    if track.kind != TrackKind.AUDIO:
        raise ValueError("主要对白轨必须是音频轨")

    start = max(0, int(start_frame))
    end = state.duration_frames if end_frame is None else min(
        state.duration_frames,
        int(end_frame),
    )
    clips = tuple(
        clip
        for clip in audio_clips_for_track(state, track.id)
        if clip.timeline_start < end
        and clip.timeline_end > start
        and clip.asset_id in assets
        and assets[clip.asset_id].metadata.has_audio
    )
    asset_ids = tuple(dict.fromkeys(clip.asset_id for clip in clips))
    return DialogueTranscriptionSelection(track.id, clips, asset_ids)


def build_dialogue_transcription_plan(
    state: TimelineState,
    assets: dict[str, Asset],
    asr: AsrSettings,
    *,
    project_profile: ProjectProfile,
    start_frame: int = 0,
    end_frame: int | None = None,
) -> TranscriptionPlan:
    assets = {
        asset_id: asset.in_frame_clock(project_profile, state.sequence.profile)
        for asset_id, asset in assets.items()
    }
    selection = select_dialogue_transcription_sources(
        state,
        assets,
        start_frame=start_frame,
        end_frame=end_frame,
    )
    if any(clip.speed_numerator <= 0 for clip in selection.clips):
        raise ValueError(
            "主要对白轨包含倒放片段；倒放语音不能用源素材字幕映射"
        )
    start = max(0, int(start_frame))
    end = state.duration_frames if end_frame is None else min(
        state.duration_frames,
        int(end_frame),
    )
    fps_numerator = state.sequence.profile.fps_numerator
    fps_denominator = state.sequence.profile.fps_denominator
    padding_frames = max(
        1,
        round(TRANSCRIPTION_CONTEXT_SECONDS * fps_numerator / fps_denominator),
    )
    source_plans: list[TranscriptionSourcePlan] = []
    for asset_id in selection.asset_ids:
        asset = assets[asset_id]
        if asset.fingerprint is None:
            raise ValueError(f"素材缺少文件指纹，无法创建稳定的转录任务：{asset.name}")
        raw_regions = [
            _clip_source_region(
                clip,
                start_frame=start,
                end_frame=end,
            )
            for clip in selection.clips
            if clip.asset_id == asset_id
        ]
        padded_regions = [
            (
                max(0, region_start - padding_frames),
                min(asset.metadata.duration_frames, region_end + padding_frames),
            )
            for region_start, region_end in raw_regions
            if region_end > region_start
        ]
        merged = _merge_frame_regions(padded_regions)
        if not merged:
            continue
        source_plans.append(
            TranscriptionSourcePlan(
                asset_id=asset.id,
                asset_name=asset.name,
                fingerprint=asset.fingerprint,
                regions=[
                    TranscriptionRegionPlan(
                        start_frame=region_start,
                        end_frame=region_end,
                    )
                    for region_start, region_end in merged
                ],
            )
        )
    return TranscriptionPlan(
        sequence_id=state.sequence.id,
        timeline_signature=_transcription_timeline_signature(
            state,
            selection,
            start_frame=start,
            end_frame=end,
        ),
        dialogue_track_id=selection.track_id,
        timeline_start_frame=start,
        timeline_end_frame=end,
        fps_numerator=fps_numerator,
        fps_denominator=fps_denominator,
        sources=source_plans,
        asr=asr.model_copy(deep=True),
    )


def _transcription_timeline_signature(
    state: TimelineState,
    selection: DialogueTranscriptionSelection,
    *,
    start_frame: int,
    end_frame: int,
) -> str:
    payload = {
        "sequence_id": state.sequence.id,
        "fps_numerator": state.sequence.profile.fps_numerator,
        "fps_denominator": state.sequence.profile.fps_denominator,
        "start_frame": start_frame,
        "end_frame": end_frame,
        "dialogue_track_id": selection.track_id,
        "clips": [
            {
                "id": clip.id,
                "asset_id": clip.asset_id,
                "timeline_start": clip.timeline_start,
                "source_in": clip.source_in,
                "duration": clip.duration,
                "speed_numerator": clip.speed_numerator,
                "speed_denominator": clip.speed_denominator,
            }
            for clip in selection.clips
        ],
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _clip_source_region(
    clip: Clip,
    *,
    start_frame: int,
    end_frame: int,
) -> tuple[int, int]:
    visible_start = max(start_frame, clip.timeline_start)
    visible_end = min(end_frame, clip.timeline_end)
    if visible_end <= visible_start:
        return 0, 0
    speed = Fraction(clip.speed_numerator, clip.speed_denominator)
    source_start = Fraction(clip.source_in) + (
        visible_start - clip.timeline_start
    ) * speed
    source_end = Fraction(clip.source_in) + (
        visible_end - clip.timeline_start
    ) * speed
    return _floor_fraction(source_start), _ceil_fraction(source_end)


def _merge_frame_regions(regions: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(regions):
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def project_dialogue_transcript(
    state: TimelineState,
    clips: tuple[Clip, ...],
    transcripts: dict[str, AsrResult],
    *,
    start_frame: int,
    end_frame: int,
) -> tuple[ProjectedDialogueSegment, ...]:
    """Map source-relative ASR timing through dialogue clips onto the sequence."""
    start = max(0, int(start_frame))
    end = min(state.duration_frames, int(end_frame))
    if end <= start:
        return ()
    projected: list[ProjectedDialogueSegment] = []
    fps_numerator = state.sequence.profile.fps_numerator
    fps_denominator = state.sequence.profile.fps_denominator
    for clip in clips:
        transcript = transcripts.get(clip.asset_id)
        if transcript is None:
            raise ValueError(f"素材缺少转录结果：{clip.asset_id}")
        for recognized in transcript.segments:
            source_start = seconds_to_frames(
                recognized.start_seconds,
                fps_numerator,
                fps_denominator,
            )
            source_end = max(
                source_start + 1,
                seconds_to_frames(
                    recognized.end_seconds,
                    fps_numerator,
                    fps_denominator,
                ),
            )
            if recognized.words:
                mapped_words: list[ProjectedDialogueWord] = []
                selected_source_words = []
                for word in recognized.words:
                    word_start = seconds_to_frames(
                        word.start_seconds,
                        fps_numerator,
                        fps_denominator,
                    )
                    word_end = max(
                        word_start + 1,
                        seconds_to_frames(
                            word.end_seconds,
                            fps_numerator,
                            fps_denominator,
                        ),
                    )
                    mapped = _map_source_range_to_clip(
                        word_start,
                        word_end,
                        clip,
                        start_frame=start,
                        end_frame=end,
                    )
                    text = word.text.strip()
                    if mapped is None or not text:
                        continue
                    selected_source_words.append(word)
                    mapped_words.append(
                        ProjectedDialogueWord(
                            start_frame=mapped[0],
                            end_frame=mapped[1],
                            text=text,
                            confidence=word.confidence,
                        )
                    )
                if not mapped_words:
                    continue
                segment_start = min(word.start_frame for word in mapped_words)
                segment_end = max(word.end_frame for word in mapped_words)
                text = _joined_word_text(selected_source_words)
                if not text:
                    text = recognized.text.strip()
                projected.append(
                    ProjectedDialogueSegment(
                        start_frame=segment_start,
                        end_frame=segment_end,
                        text=text,
                        confidence=recognized.confidence,
                        words=tuple(mapped_words),
                    )
                )
                continue
            mapped = _map_source_range_to_clip(
                source_start,
                source_end,
                clip,
                start_frame=start,
                end_frame=end,
            )
            text = recognized.text.strip()
            if mapped is not None and text:
                projected.append(
                    ProjectedDialogueSegment(
                        start_frame=mapped[0],
                        end_frame=mapped[1],
                        text=text,
                        confidence=recognized.confidence,
                        words=(),
                    )
                )
    return tuple(
        sorted(
            projected,
            key=lambda item: (item.start_frame, item.end_frame, item.text),
        )
    )


def _map_source_range_to_clip(
    source_start: int,
    source_end: int,
    clip: Clip,
    *,
    start_frame: int,
    end_frame: int,
) -> tuple[int, int] | None:
    speed = Fraction(clip.speed_numerator, clip.speed_denominator)
    clip_source_start = Fraction(clip.source_in)
    clip_source_end = clip_source_start + Fraction(clip.duration) * speed
    start = max(Fraction(source_start), clip_source_start)
    end = min(Fraction(source_end), clip_source_end)
    if end <= start:
        return None
    timeline_start = clip.timeline_start + _floor_fraction(
        (start - clip_source_start) / speed
    )
    timeline_end = clip.timeline_start + _ceil_fraction(
        (end - clip_source_start) / speed
    )
    timeline_start = max(start_frame, clip.timeline_start, timeline_start)
    timeline_end = min(end_frame, clip.timeline_end, timeline_end)
    if timeline_end <= timeline_start:
        return None
    return timeline_start, timeline_end


def _floor_fraction(value: Fraction) -> int:
    return value.numerator // value.denominator


def _ceil_fraction(value: Fraction) -> int:
    return -(-value.numerator // value.denominator)


def _joined_word_text(words: Sequence[object]) -> str:
    raw_values = [str(getattr(word, "text", "")) for word in words]
    joined = "".join(raw_values).strip()
    if any(re.search(r"\s", value) for value in raw_values):
        return joined
    values = [value.strip() for value in raw_values if value.strip()]
    if values and all(re.fullmatch(r"[\w'’-]+[.,!?;:]?", value, re.ASCII) for value in values):
        return " ".join(values)
    return "".join(values)


def select_audible_sequence_audio(
    state: TimelineState,
    assets: dict[str, Asset],
    buses: list[AudioBus],
    *,
    start_frame: int = 0,
    end_frame: int | None = None,
) -> SequenceAudioSelection:
    """Return the clips that can reach the sequence's audible master output."""
    end = state.duration_frames if end_frame is None else min(state.duration_frames, end_frame)
    start = max(0, start_frame)
    if end <= start or not buses:
        return SequenceAudioSelection((), ())

    by_id = {bus.id: bus for bus in buses}
    roots = [bus for bus in buses if bus.parent_bus_id is None]
    if len(roots) != 1:
        return SequenceAudioSelection((), ())
    master = roots[0]
    solo_bus_ids = {bus.id for bus in buses if bus.solo}
    allowed_bus_ids = set(by_id)
    if solo_bus_ids:
        allowed_bus_ids = set(solo_bus_ids)
        for bus_id in tuple(solo_bus_ids):
            cursor = by_id[bus_id]
            while cursor.parent_bus_id:
                allowed_bus_ids.add(cursor.parent_bus_id)
                cursor = by_id[cursor.parent_bus_id]

    def bus_reaches_master(bus_id: str) -> bool:
        seen: set[str] = set()
        cursor = by_id.get(bus_id)
        while cursor is not None:
            if cursor.id in seen or cursor.id not in allowed_bus_ids or cursor.muted:
                return False
            if cursor.id == master.id:
                return True
            seen.add(cursor.id)
            cursor = by_id.get(cursor.parent_bus_id or "")
        return False

    track_ids: list[str] = []
    asset_ids: list[str] = []
    for track in state.effective_tracks(TrackKind.AUDIO):
        if track.muted:
            continue
        if not bus_reaches_master(track.audio_bus_id or master.id):
            continue
        audible_assets = [
            clip.asset_id
            for clip in output_audio_clips_for_track(state, track.id)
            if clip.timeline_start < end
            and clip.timeline_end > start
            and clip.asset_id in assets
            and assets[clip.asset_id].metadata.has_audio
        ]
        if not audible_assets:
            continue
        track_ids.append(track.id)
        for asset_id in audible_assets:
            if asset_id not in asset_ids:
                asset_ids.append(asset_id)
    return SequenceAudioSelection(tuple(track_ids), tuple(asset_ids))
