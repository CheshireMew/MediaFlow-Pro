from __future__ import annotations

import re
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Literal

from mediaflow.domain.enums import ClipMediaKind
from mediaflow.domain.interchange import (
    InterchangeNativeClipSpec,
    InterchangeTimelineSummary,
    InterchangeTransitionSpec,
    LoadedInterchangeTimeline,
)
from mediaflow.domain.portable_timeline import (
    LoadedPortableTimeline,
    PortableClip,
    PortableMediaClip,
    PortableTimelineDocument,
    PortableTimelineProfile,
    PortableTimelineSource,
    PortableTimelineTrack,
)
from mediaflow.domain.project import ProjectProfile
from mediaflow.file_digest import sha256_file
from mediaflow.infrastructure.interchange_reader_support import require_non_overlapping, resolve_media_path

_EDL_EVENT = re.compile(
    r"^\s*(?P<event>\d+)\s+(?P<reel>\S+)\s+(?P<track>\S+)\s+"
    r"(?P<edit>[A-Z])(?:\s+(?P<transition>\d+))?\s+"
    r"(?P<src_in>\d{2}:\d{2}:\d{2}[:;]\d{2})\s+"
    r"(?P<src_out>\d{2}:\d{2}:\d{2}[:;]\d{2})\s+"
    r"(?P<rec_in>\d{2}:\d{2}:\d{2}[:;]\d{2})\s+"
    r"(?P<rec_out>\d{2}:\d{2}:\d{2}[:;]\d{2})\s*$"
)

_EDL_SOURCE_COMMENT = re.compile(
    r"^\*\s*(?:FROM\s+CLIP\s+NAME|SOURCE\s+FILE|SOURCE\s+CLIP)\s*:\s*(.+?)\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class _EdlEvent:
    number: str
    reel: str
    track: str
    edit: str
    transition: int
    src_in: int
    src_out: int
    rec_in: int
    rec_out: int
    source_label: str


class EdlTimelineReader:
    def load(
        self,
        path: Path,
        *,
        default_profile: ProjectProfile,
        frame_rate: float | None,
        media_mappings: dict[str, str],
    ) -> LoadedInterchangeTimeline:
        digest = sha256_file(path)
        text = path.read_text(encoding="utf-8-sig")
        lines = text.splitlines()
        title = (
            next(
                (line.partition(":")[2].strip() for line in lines if line.upper().startswith("TITLE:")),
                path.stem,
            )
            or path.stem
        )
        drop = any("DROP FRAME" in line.upper() and "NON-DROP" not in line.upper() for line in lines)
        fps = self._edl_fps(default_profile, frame_rate, drop=drop)
        nominal_fps = 60 if fps > 50 else 30 if fps > 25 else round(float(fps))
        events: list[_EdlEvent] = []
        pending: dict[str, str] | None = None
        for line in lines:
            match = _EDL_EVENT.match(line)
            if match:
                if pending is not None:
                    events.append(self._edl_event_from_fields(pending, nominal_fps, drop, ""))
                pending = match.groupdict(default="")
                continue
            source_match = _EDL_SOURCE_COMMENT.match(line)
            if pending is not None and source_match:
                events.append(
                    self._edl_event_from_fields(
                        pending,
                        nominal_fps,
                        drop,
                        source_match.group(1).strip().strip('"'),
                    )
                )
                pending = None
        if pending is not None:
            events.append(self._edl_event_from_fields(pending, nominal_fps, drop, ""))
        if not events:
            raise ValueError("EDL 中没有可识别的 CMX 3600 编辑事件")
        record_origin = min(item.rec_in for item in events)
        sources_by_key: dict[str, PortableTimelineSource] = {}
        source_paths: dict[str, Path] = {}
        source_labels: dict[str, str] = {}
        missing: list[str] = []
        event_source_ids: dict[int, str] = {}
        video_keys = {self._edl_source_key(item) for item in events if item.track.upper().startswith("V")}
        for index, event in enumerate(events):
            key = self._edl_source_key(event)
            source_id = (
                f"edl-source-{len(sources_by_key) + 1}"
                if key not in sources_by_key
                else sources_by_key[key].id
            )
            if key not in sources_by_key:
                raw = (
                    media_mappings.get(event.number)
                    or media_mappings.get(event.reel)
                    or media_mappings.get(event.source_label)
                    or event.source_label
                )
                resolved = resolve_media_path(raw, path.parent)
                label = event.source_label or event.reel
                source_labels[source_id] = label
                source_kind: Literal["video", "audio"] = "video" if key in video_keys else "audio"
                source_digest = "0" * 64
                if resolved is not None and resolved.is_file():
                    source_paths[source_id] = resolved
                    source_digest = sha256_file(resolved)
                else:
                    missing.append(f"{event.reel} · {label}")
                sources_by_key[key] = PortableTimelineSource(
                    id=source_id,
                    kind=source_kind,
                    file=resolved.name if resolved is not None else label,
                    sha256=source_digest,
                )
            event_source_ids[index] = source_id

        grouped: dict[str, list[tuple[PortableMediaClip, InterchangeNativeClipSpec, _EdlEvent]]] = {}
        for index, event in enumerate(events):
            duration = self._positive_interval(event.rec_in, event.rec_out, nominal_fps)
            source_duration = self._positive_interval(event.src_in, event.src_out, nominal_fps)
            if source_duration != duration:
                raise ValueError(
                    f"EDL 事件 {event.number} 的源区间与记录区间长度不同；请先在来源软件烘焙变速"
                )
            timeline_start = event.rec_in - record_origin
            source_id = event_source_ids[index]
            track_kind = "video" if event.track.upper().startswith("V") else "audio"
            media_kind = ClipMediaKind.VIDEO_ONLY if track_kind == "video" else ClipMediaKind.AUDIO_ONLY
            clip_id = f"edl-clip-{index + 1}"
            native = InterchangeNativeClipSpec(
                clip_id=clip_id,
                source_id=source_id,
                timeline_start=timeline_start,
                source_in=event.src_in,
                duration=duration,
                media_kind=media_kind,
            )
            portable = PortableMediaClip(
                id=clip_id,
                type="media",
                source_id=source_id,
                timeline_start_seconds=float(Fraction(timeline_start, 1) / fps),
                source_in_seconds=float(Fraction(event.src_in, 1) / fps),
                duration_seconds=float(Fraction(duration, 1) / fps),
                audio_enabled=False,
            )
            grouped.setdefault(event.track.upper(), []).append((portable, native, event))

        tracks: list[PortableTimelineTrack] = []
        native_clips: list[InterchangeNativeClipSpec] = []
        transitions: list[InterchangeTransitionSpec] = []
        for track_index, (track_code, items) in enumerate(sorted(grouped.items())):
            ordered = sorted(items, key=lambda item: (item[1].timeline_start, item[1].clip_id))
            portable_clips = [item[0] for item in ordered]
            require_non_overlapping(portable_clips, track_code)
            kind: Literal["video", "audio"] = "video" if track_code.startswith("V") else "audio"
            track_clips: list[PortableClip] = [*portable_clips]
            tracks.append(
                PortableTimelineTrack(
                    id=f"edl-track-{track_index + 1}",
                    kind=kind,
                    name=track_code,
                    muted=False,
                    clips=track_clips,
                )
            )
            native_clips.extend(item[1] for item in ordered)
            if kind == "video":
                for previous, current in zip(ordered, ordered[1:], strict=False):
                    if current[2].edit != "D" or current[2].transition <= 0:
                        continue
                    if previous[1].timeline_start + previous[1].duration != current[1].timeline_start:
                        raise ValueError("EDL 溶解转场两侧片段必须在记录时间线上首尾相接")
                    transitions.append(
                        InterchangeTransitionSpec(
                            left_clip_id=previous[1].clip_id,
                            right_clip_id=current[1].clip_id,
                            duration=min(
                                current[2].transition,
                                previous[1].duration,
                                current[1].duration,
                            ),
                        )
                    )
        duration = max(item.timeline_start + item.duration for item in native_clips)
        profile = default_profile.model_copy(
            update={"fps_numerator": fps.numerator, "fps_denominator": fps.denominator}
        )
        document = PortableTimelineDocument(
            protocol="visual-multimedia-timeline",
            version=1,
            project_id=f"cmx3600-{digest[:16]}",
            profile=PortableTimelineProfile(
                width=profile.width,
                height=profile.height,
                frame_rate=float(fps),
                sample_rate=48_000,
                channel_layout="mono" if profile.audio_channels == 1 else "stereo",
                background="#000000",
                duration_seconds=float(Fraction(duration, 1) / fps),
            ),
            sources=list(sources_by_key.values()),
            tracks=tracks,
            subtitle_styles=[],
            markers=[],
        )
        loaded_portable = LoadedPortableTimeline(
            path=path,
            root=path.parent,
            sha256=digest,
            document=document,
            sources=source_paths,
        )
        summary = InterchangeTimelineSummary(
            timeline_path=str(path),
            timeline_sha256=digest,
            format="cmx3600",
            name=title,
            profile=profile,
            duration_frames=duration,
            source_count=len(sources_by_key),
            track_count=len(tracks),
            clip_count=len(native_clips),
            caption_count=0,
            marker_count=0,
            transition_count=len(transitions),
            missing_sources=sorted(set(missing)),
            warnings=["CMX 3600 不携带画面尺寸、色彩空间和完整音频布局；已采用当前序列配置。"],
        )
        return LoadedInterchangeTimeline(
            summary=summary,
            portable=loaded_portable,
            native_clips=tuple(native_clips),
            transitions=tuple(transitions),
            source_labels=source_labels,
            path=path,
        )

    def _edl_event_from_fields(
        self,
        fields: dict[str, str],
        nominal_fps: int,
        drop: bool,
        source_label: str,
    ) -> _EdlEvent:
        return _EdlEvent(
            number=fields["event"],
            reel=fields["reel"],
            track=fields["track"],
            edit=fields["edit"],
            transition=int(fields.get("transition") or 0),
            src_in=self._timecode_frames(fields["src_in"], nominal_fps, drop),
            src_out=self._timecode_frames(fields["src_out"], nominal_fps, drop),
            rec_in=self._timecode_frames(fields["rec_in"], nominal_fps, drop),
            rec_out=self._timecode_frames(fields["rec_out"], nominal_fps, drop),
            source_label=source_label,
        )

    @staticmethod
    def _edl_fps(
        default_profile: ProjectProfile,
        frame_rate: float | None,
        *,
        drop: bool,
    ) -> Fraction:
        if frame_rate is not None:
            fps = Fraction(str(frame_rate)).limit_denominator(100_000)
        elif drop:
            fps = Fraction(30_000, 1001)
        else:
            fps = Fraction(default_profile.fps_numerator, default_profile.fps_denominator)
        if drop and fps not in {Fraction(30_000, 1001), Fraction(60_000, 1001)}:
            raise ValueError("Drop-frame EDL 只支持 29.97 或 59.94 fps")
        nominal = 60 if fps > 50 else 30 if fps > 25 else round(float(fps))
        if nominal not in {24, 25, 30, 50, 60}:
            raise ValueError("EDL 帧率必须对应 24、25、29.97/30、50 或 59.94/60 fps")
        return fps

    @staticmethod
    def _timecode_frames(value: str, nominal_fps: int, drop: bool) -> int:
        hour, minute, second, frame = (int(part) for part in re.split("[:;]", value))
        if frame >= nominal_fps:
            raise ValueError(f"EDL 时间码帧号超出帧率：{value}")
        total = ((hour * 60 + minute) * 60 + second) * nominal_fps + frame
        if drop:
            dropped = (4 if nominal_fps == 60 else 2) * (hour * 54 + minute - minute // 10)
            total -= dropped
        return total

    @staticmethod
    def _positive_interval(start: int, end: int, nominal_fps: int) -> int:
        if end <= start:
            end += 24 * 60 * 60 * nominal_fps
        return end - start

    @staticmethod
    def _edl_source_key(event: _EdlEvent) -> str:
        return (event.source_label or event.reel).casefold()
