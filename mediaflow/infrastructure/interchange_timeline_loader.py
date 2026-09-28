from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlparse
from xml.etree import ElementTree as ET

from mediaflow.domain.enums import ClipMediaKind, ColorMode
from mediaflow.domain.interchange import (
    InterchangeClipAdjustment,
    InterchangeNativeClipSpec,
    InterchangeRawKeyframe,
    InterchangeTimelineSummary,
    InterchangeTransitionSpec,
    LoadedInterchangeTimeline,
)
from mediaflow.domain.portable_timeline import (
    LoadedPortableTimeline,
    PortableCaptionClip,
    PortableClip,
    PortableMediaClip,
    PortableSubtitleStyle,
    PortableTimelineDocument,
    PortableTimelineMarker,
    PortableTimelineProfile,
    PortableTimelineSource,
    PortableTimelineTrack,
)
from mediaflow.domain.project import ProjectProfile
from mediaflow.domain.timebase import round_fraction
from mediaflow.domain.timeline import ClipAudio
from mediaflow.file_digest import sha256_file

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


class InterchangeTimelineLoader:
    """Read FCPXML and CMX 3600 into one validated, native-import plan."""

    def load(
        self,
        path: str | Path,
        *,
        default_profile: ProjectProfile,
        frame_rate: float | None = None,
        media_mappings: dict[str, str] | None = None,
    ) -> LoadedInterchangeTimeline:
        source = Path(path).expanduser().resolve(strict=True)
        suffix = source.suffix.casefold()
        if suffix in {".fcpxml", ".xml"}:
            if frame_rate is not None:
                raise ValueError("FCPXML 已包含精确帧率，不能再指定 EDL 帧率")
            return self._load_fcpxml(source, media_mappings or {})
        if suffix == ".edl":
            return self._load_edl(
                source,
                default_profile=default_profile,
                frame_rate=frame_rate,
                media_mappings=media_mappings or {},
            )
        raise ValueError("只支持 .fcpxml、.xml 和 CMX 3600 .edl 时间线")

    def _load_fcpxml(
        self,
        path: Path,
        media_mappings: dict[str, str],
    ) -> LoadedInterchangeTimeline:
        digest = sha256_file(path)
        root = ET.parse(path).getroot()
        if root.tag != "fcpxml":
            raise ValueError("XML 根节点不是 FCPXML")
        sequence = root.find("./library/event/project/sequence")
        if sequence is None:
            sequence = root.find(".//sequence")
        if sequence is None:
            raise ValueError("FCPXML 中没有可导入的 sequence")
        profile = self._fcpxml_profile(root, sequence)
        fps = Fraction(profile.fps_numerator, profile.fps_denominator)
        project = root.find("./library/event/project")
        sequence_name = (
            project.attrib.get("name", "") if project is not None else ""
        ).strip() or path.stem
        declared_duration = self._frames(sequence.attrib.get("duration", "0s"), fps)

        portable_sources, source_paths, source_labels, missing = self._fcpxml_sources(
            root,
            path,
            media_mappings,
        )
        source_by_resource = {item.id.removeprefix("fcpxml-source-"): item for item in portable_sources}
        source_ids = {item.id for item in portable_sources}
        tracks: list[PortableTimelineTrack] = []
        native_clips: list[InterchangeNativeClipSpec] = []
        transitions: list[InterchangeTransitionSpec] = []
        warnings: list[str] = []

        timeline_container, stories = self._fcpxml_stories(sequence)
        for story_index, story in enumerate(stories):
            portable_clips: list[PortableMediaClip] = []
            story_native: list[InterchangeNativeClipSpec] = []
            story_transitions: list[tuple[int, int]] = []
            cursor = 0
            for child_index, child in enumerate(list(story)):
                if child.tag == "gap":
                    cursor = max(
                        cursor,
                        self._frames(child.attrib.get("offset", "0s"), fps)
                        + self._frames(child.attrib.get("duration", "0s"), fps),
                    )
                    continue
                if child.tag == "transition":
                    story_transitions.append(
                        (
                            self._frames(child.attrib.get("offset", "0s"), fps),
                            max(1, self._frames(child.attrib.get("duration", "0s"), fps)),
                        )
                    )
                    continue
                parsed = self._fcpxml_clip(
                    child,
                    fps=fps,
                    profile=profile,
                    source_by_resource=source_by_resource,
                    source_ids=source_ids,
                    clip_id=f"fcpxml-clip-{story_index + 1}-{child_index + 1}",
                    cursor=cursor,
                )
                if parsed is None:
                    continue
                portable_clip, native = parsed
                portable_clips.append(portable_clip)
                story_native.append(native)
                cursor = max(cursor, native.timeline_start + native.duration)
            if not portable_clips:
                continue
            track_kind: Literal["audio", "video"] = (
                "audio"
                if all(item.media_kind == ClipMediaKind.AUDIO_ONLY for item in story_native)
                else "video"
            )
            track_id = f"fcpxml-track-{story_index + 1}"
            ordered = sorted(portable_clips, key=lambda item: (item.timeline_start_seconds, item.id))
            self._require_non_overlapping(ordered, track_id)
            track_clips: list[PortableClip] = [*ordered]
            tracks.append(
                PortableTimelineTrack(
                    id=track_id,
                    kind=track_kind,
                    name=(
                        story.attrib.get("name")
                        or f"{'音频' if track_kind == 'audio' else '视频'} {len(tracks) + 1}"
                    ),
                    muted=story.attrib.get("enabled", "1") == "0",
                    clips=track_clips,
                )
            )
            native_clips.extend(story_native)
            ordered_native = sorted(story_native, key=lambda item: (item.timeline_start, item.clip_id))
            for offset, duration in story_transitions:
                center = offset + duration // 2
                pair = next(
                    (
                        (left, right)
                        for left, right in zip(ordered_native, ordered_native[1:], strict=False)
                        if left.timeline_start + left.duration == right.timeline_start
                        and abs((left.timeline_start + left.duration) - center) <= max(1, duration)
                    ),
                    None,
                )
                if pair is None:
                    warnings.append(f"无法把位于第 {offset} 帧的 FCPXML 转场绑定到相邻片段")
                    continue
                transitions.append(
                    InterchangeTransitionSpec(
                        left_clip_id=pair[0].clip_id,
                        right_clip_id=pair[1].clip_id,
                        duration=min(duration, pair[0].duration, pair[1].duration),
                    )
                )

        caption_tracks, caption_count = self._fcpxml_captions(timeline_container, fps)
        tracks.extend(caption_tracks)
        markers = self._fcpxml_markers(timeline_container, fps)
        if not tracks:
            raise ValueError("FCPXML 时间线没有可导入的媒体或字幕轨道")
        duration = max(
            declared_duration,
            max((item.timeline_start + item.duration for item in native_clips), default=0),
            max(
                (
                    self._seconds_to_frames(clip.timeline_start_seconds + clip.duration_seconds, fps)
                    for track in caption_tracks
                    for clip in track.clips
                ),
                default=0,
            ),
            1,
        )
        document = PortableTimelineDocument(
            protocol="visual-multimedia-timeline",
            version=1,
            project_id=f"fcpxml-{digest[:16]}",
            profile=PortableTimelineProfile(
                width=profile.width,
                height=profile.height,
                frame_rate=float(fps),
                sample_rate=48_000,
                channel_layout="mono" if profile.audio_channels == 1 else "stereo",
                background="#000000",
                duration_seconds=float(Fraction(duration, 1) / fps),
            ),
            sources=portable_sources,
            tracks=tracks,
            subtitle_styles=[
                PortableSubtitleStyle(
                    id=f"fcpxml-caption-style-{index + 1}",
                    font_family="Arial",
                    font_size=48,
                    primary_color="#ffffff",
                    outline_color="#000000",
                    outline_width=2,
                    margin_vertical=54,
                    alignment=2,
                )
                for index in range(len(caption_tracks))
            ],
            markers=markers,
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
            format="fcpxml",
            name=sequence_name,
            profile=profile,
            duration_frames=duration,
            source_count=len(portable_sources),
            track_count=len(tracks),
            clip_count=len(native_clips),
            caption_count=caption_count,
            marker_count=len(markers),
            transition_count=len(transitions),
            missing_sources=missing,
            warnings=warnings,
        )
        return LoadedInterchangeTimeline(
            summary=summary,
            portable=loaded_portable,
            native_clips=tuple(native_clips),
            transitions=tuple(transitions),
            source_labels=source_labels,
            path=path,
        )

    def _fcpxml_profile(self, root: ET.Element, sequence: ET.Element) -> ProjectProfile:
        format_id = sequence.attrib.get("format", "")
        format_element = next(
            (
                item
                for item in root.findall("./resources/format")
                if not format_id or item.attrib.get("id") == format_id
            ),
            None,
        )
        if format_element is None:
            raise ValueError("FCPXML 缺少 sequence 使用的 format 资源")
        frame_duration = self._time(format_element.attrib.get("frameDuration", ""))
        if frame_duration <= 0:
            raise ValueError("FCPXML format 的 frameDuration 无效")
        fps = 1 / frame_duration
        width = int(format_element.attrib.get("width", "1920"))
        height = int(format_element.attrib.get("height", "1080"))
        color_space = format_element.attrib.get("colorSpace", "").casefold()
        hdr = "2020" in color_space or "pq" in color_space or "hlg" in color_space
        layout = sequence.attrib.get("audioLayout", "stereo").casefold()
        channels = 1 if layout == "mono" else 6 if layout in {"surround", "5.1"} else 2
        audio_rate = sequence.attrib.get("audioRate", "48k").casefold()
        if audio_rate not in {"48k", "48000", "48khz"}:
            raise ValueError("MediaFlow Pro 只使用 48 kHz 音频时钟")
        return ProjectProfile(
            width=width,
            height=height,
            fps_numerator=fps.numerator,
            fps_denominator=fps.denominator,
            color_mode=ColorMode.HDR10_BT2020_PQ if hdr else ColorMode.SDR_BT709,
            bit_depth=10 if hdr else 8,
            audio_sample_rate=48_000,
            audio_channels=channels,
        )

    def _fcpxml_sources(
        self,
        root: ET.Element,
        timeline_path: Path,
        media_mappings: dict[str, str],
    ) -> tuple[list[PortableTimelineSource], dict[str, Path], dict[str, str], list[str]]:
        sources: list[PortableTimelineSource] = []
        paths: dict[str, Path] = {}
        labels: dict[str, str] = {}
        missing: list[str] = []
        image_suffixes = {".bmp", ".gif", ".heic", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}
        for resource in root.findall("./resources/asset"):
            resource_id = resource.attrib.get("id", "").strip()
            if not resource_id:
                continue
            source_id = f"fcpxml-source-{resource_id}"
            media_rep = resource.find("media-rep")
            raw = (
                media_mappings.get(resource_id)
                or media_mappings.get(resource.attrib.get("name", ""))
                or (media_rep.attrib.get("src", "") if media_rep is not None else "")
            )
            resolved = self._resolve_media_path(raw, timeline_path.parent)
            label = resource.attrib.get("name", "").strip() or resource_id
            labels[source_id] = label
            has_video = resource.attrib.get("hasVideo", "0") == "1"
            has_audio = resource.attrib.get("hasAudio", "0") == "1"
            suffix = resolved.suffix.casefold() if resolved is not None else Path(raw).suffix.casefold()
            kind: Literal["image", "video", "audio"] = (
                "image" if suffix in image_suffixes else "video" if has_video else "audio"
            )
            duration_value = resource.attrib.get("duration")
            duration_seconds = float(self._time(duration_value)) if duration_value else None
            digest = "0" * 64
            if resolved is not None and resolved.is_file():
                digest = sha256_file(resolved)
                paths[source_id] = resolved
            else:
                missing.append(f"{resource_id} · {label}")
            sources.append(
                PortableTimelineSource(
                    id=source_id,
                    kind=kind,
                    file=(resolved.name if resolved is not None else label),
                    sha256=digest,
                    duration_seconds=duration_seconds,
                )
            )
            if not has_video and not has_audio:
                raise ValueError(f"FCPXML 资源 {resource_id} 没有音频或视频声明")
        return sources, paths, labels, missing

    @staticmethod
    def _fcpxml_stories(sequence: ET.Element) -> tuple[ET.Element, list[ET.Element]]:
        root_spine = sequence.find("spine")
        if root_spine is None:
            raise ValueError("FCPXML sequence 缺少 spine")
        wrapper = next(
            (
                child
                for child in list(root_spine)
                if child.tag == "clip" and child.find("spine") is not None and "ref" not in child.attrib
            ),
            None,
        )
        if wrapper is not None:
            return wrapper, wrapper.findall("spine")
        return root_spine, [root_spine]

    def _fcpxml_clip(
        self,
        element: ET.Element,
        *,
        fps: Fraction,
        profile: ProjectProfile,
        source_by_resource: dict[str, PortableTimelineSource],
        source_ids: set[str],
        clip_id: str,
        cursor: int,
    ) -> tuple[PortableMediaClip, InterchangeNativeClipSpec] | None:
        if element.tag not in {"asset-clip", "clip"}:
            return None
        component: ET.Element | None = None
        resource_id = element.attrib.get("ref", "")
        component_kind = ""
        if not resource_id:
            for name in ("video", "audio"):
                candidate = element.find(name)
                if candidate is not None and candidate.attrib.get("ref"):
                    component = candidate
                    component_kind = name
                    resource_id = candidate.attrib["ref"]
                    break
        source_id = f"fcpxml-source-{resource_id}"
        if not resource_id or source_id not in source_ids:
            raise ValueError(f"FCPXML 片段引用了未知资源：{resource_id or '(empty)'}")
        source = source_by_resource[resource_id]
        duration = max(1, self._frames(element.attrib.get("duration", "0s"), fps))
        timeline_start = (
            self._frames(element.attrib["offset"], fps) if "offset" in element.attrib else cursor
        )
        timing_element = component or element
        source_in = self._frames(
            timing_element.attrib.get("start", element.attrib.get("start", "0s")),
            fps,
        )
        speed = Fraction(1)
        pitch_compensation = True
        time_map = timing_element.find("timeMap") or element.find("timeMap")
        if time_map is not None:
            points = time_map.findall("timept")
            if len(points) != 2:
                raise ValueError("FCPXML 可变速率 timeMap 不能无损映射为原生恒定速度片段")
            timeline_a = self._time(points[0].attrib.get("time", "0s"))
            timeline_b = self._time(points[1].attrib.get("time", "0s"))
            source_a = self._time(points[0].attrib.get("value", "0s"))
            source_b = self._time(points[1].attrib.get("value", "0s"))
            if timeline_b == timeline_a:
                raise ValueError("FCPXML timeMap 的时间点不能重合")
            speed = (source_b - source_a) / (timeline_b - timeline_a)
            source_at_zero = source_a - speed * timeline_a
            source_in = round_fraction(source_at_zero * fps)
            pitch_compensation = time_map.attrib.get("preservesPitch", "1") != "0"
        if speed == 0:
            raise ValueError("FCPXML timeMap 不能产生零速片段")
        src_enable = element.attrib.get("srcEnable", "").casefold()
        if component_kind == "audio" or src_enable == "audio":
            media_kind = ClipMediaKind.AUDIO_ONLY
            track_kind = "audio"
        elif component_kind == "video" or src_enable == "video" or source.kind == "image":
            media_kind = ClipMediaKind.VIDEO_ONLY
            track_kind = "video"
        else:
            media_kind = ClipMediaKind.LINKED_AV
            track_kind = "video"
        portable_source_in = source_in
        portable_speed = abs(speed)
        if speed < 0:
            portable_source_in = max(0, source_in - round_fraction(Fraction(duration) * abs(speed)))
        audio = self._fcpxml_audio_adjustment(element, component, fps)
        adjustment = self._fcpxml_video_adjustment(element, component, fps)
        native = InterchangeNativeClipSpec(
            clip_id=clip_id,
            source_id=source_id,
            timeline_start=timeline_start,
            source_in=source_in,
            duration=duration,
            media_kind=media_kind,
            speed_numerator=speed.numerator,
            speed_denominator=speed.denominator,
            pitch_compensation=pitch_compensation,
            audio=audio,
            adjustment=adjustment,
        )
        portable = PortableMediaClip(
            id=clip_id,
            type="media",
            source_id=source_id,
            timeline_start_seconds=float(Fraction(timeline_start, 1) / fps),
            source_in_seconds=float(Fraction(portable_source_in, 1) / fps),
            duration_seconds=float(Fraction(duration, 1) / fps),
            speed=float(portable_speed),
            audio_enabled=media_kind == ClipMediaKind.LINKED_AV,
            gain_db=audio.gain_db,
        )
        if track_kind == "audio" and media_kind != ClipMediaKind.AUDIO_ONLY:
            raise RuntimeError("Internal FCPXML track classification mismatch")
        return portable, native

    def _fcpxml_video_adjustment(
        self,
        outer: ET.Element,
        component: ET.Element | None,
        fps: Fraction,
    ) -> InterchangeClipAdjustment:
        roots = [outer] + ([component] if component is not None else [])
        transform = self._first(roots, "adjust-transform")
        crop = self._first(roots, "adjust-crop")
        crop_rect = crop.find("trim-rect") if crop is not None else None
        blend = self._first(roots, "adjust-blend")
        animations: dict[str, list[InterchangeRawKeyframe]] = {}
        for container, names in (
            (transform, {"position": "position", "scale": "scale", "rotation": "rotation"}),
            (
                crop_rect,
                {
                    "left": "crop_left",
                    "top": "crop_top",
                    "right": "crop_right",
                    "bottom": "crop_bottom",
                },
            ),
            (blend, {"amount": "opacity"}),
        ):
            if container is None:
                continue
            for parameter in container.findall("param"):
                target = names.get(parameter.attrib.get("name", ""))
                if target is None:
                    continue
                values: list[InterchangeRawKeyframe] = []
                for keyframe in parameter.findall("./keyframeAnimation/keyframe"):
                    if keyframe.attrib.get("interp", "linear") != "linear":
                        raise ValueError("FCPXML 只支持线性 transform/crop/opacity 关键帧导入")
                    values.append(
                        InterchangeRawKeyframe(
                            frame=max(0, self._frames(keyframe.attrib.get("time", "0s"), fps)),
                            value=keyframe.attrib.get("value", "0"),
                        )
                    )
                if values:
                    animations[target] = sorted(values, key=lambda item: item.frame)
        return InterchangeClipAdjustment(
            position=transform.attrib.get("position") if transform is not None else None,
            scale=transform.attrib.get("scale") if transform is not None else None,
            rotation=transform.attrib.get("rotation") if transform is not None else None,
            opacity=blend.attrib.get("amount") if blend is not None else None,
            crop_left=crop_rect.attrib.get("left") if crop_rect is not None else None,
            crop_top=crop_rect.attrib.get("top") if crop_rect is not None else None,
            crop_right=crop_rect.attrib.get("right") if crop_rect is not None else None,
            crop_bottom=crop_rect.attrib.get("bottom") if crop_rect is not None else None,
            animations=animations,
        )

    def _fcpxml_audio_adjustment(
        self,
        outer: ET.Element,
        component: ET.Element | None,
        fps: Fraction,
    ) -> ClipAudio:
        roots = [outer] + ([component] if component is not None else [])
        volume = self._first(roots, "adjust-volume")
        panner = self._first(roots, "adjust-panner")
        gain = 0.0
        fade_in = 0
        fade_out = 0
        if volume is not None:
            amount = volume.attrib.get("amount", "0dB").strip()
            if not amount.casefold().endswith("db"):
                raise ValueError("FCPXML 音量必须使用 dB 表示")
            gain = float(amount[:-2] or 0)
            fade_in_element = volume.find("./param/fadeIn")
            fade_out_element = volume.find("./param/fadeOut")
            if fade_in_element is not None:
                fade_in = max(0, self._frames(fade_in_element.attrib.get("duration", "0s"), fps))
            if fade_out_element is not None:
                fade_out = max(0, self._frames(fade_out_element.attrib.get("duration", "0s"), fps))
        pan = float(panner.attrib.get("amount", "0")) if panner is not None else 0.0
        return ClipAudio(gain_db=gain, pan=pan, fade_in_frames=fade_in, fade_out_frames=fade_out)

    def _fcpxml_captions(
        self,
        container: ET.Element,
        fps: Fraction,
    ) -> tuple[list[PortableTimelineTrack], int]:
        grouped: dict[str, list[PortableCaptionClip]] = {}
        for index, caption in enumerate(container.findall("caption")):
            text = "".join(caption.itertext()).strip()
            if not text:
                continue
            lane = caption.attrib.get("lane", "-1")
            role = caption.attrib.get("role", "")
            language = role.rsplit(".", 1)[-1] if "." in role else None
            start = self._frames(caption.attrib.get("offset", "0s"), fps)
            duration = max(1, self._frames(caption.attrib.get("duration", "0s"), fps))
            grouped.setdefault(lane, []).append(
                PortableCaptionClip(
                    id=f"fcpxml-caption-{index + 1}",
                    type="caption",
                    timeline_start_seconds=float(Fraction(start, 1) / fps),
                    duration_seconds=float(Fraction(duration, 1) / fps),
                    text=text,
                    style_id="pending",
                    language=language,
                )
            )
        tracks: list[PortableTimelineTrack] = []
        for index, (lane, clips) in enumerate(sorted(grouped.items())):
            style_id = f"fcpxml-caption-style-{index + 1}"
            styled = [item.model_copy(update={"style_id": style_id}) for item in clips]
            self._require_non_overlapping(styled, f"caption-lane-{lane}")
            styled_clips: list[PortableClip] = [*styled]
            tracks.append(
                PortableTimelineTrack(
                    id=f"fcpxml-caption-track-{index + 1}",
                    kind="subtitle",
                    name=f"字幕 {index + 1}",
                    muted=False,
                    clips=styled_clips,
                )
            )
        return tracks, sum(len(item.clips) for item in tracks)

    def _fcpxml_markers(self, container: ET.Element, fps: Fraction) -> list[PortableTimelineMarker]:
        markers: list[PortableTimelineMarker] = []
        for index, marker in enumerate(container.findall("marker")):
            frame = max(0, self._frames(marker.attrib.get("start", "0s"), fps))
            markers.append(
                PortableTimelineMarker(
                    id=f"fcpxml-marker-{index + 1}",
                    time_seconds=float(Fraction(frame, 1) / fps),
                    label=marker.attrib.get("value", "").strip() or "Marker",
                )
            )
        return markers

    def _load_edl(
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
        title = next(
            (line.partition(":")[2].strip() for line in lines if line.upper().startswith("TITLE:")),
            path.stem,
        ) or path.stem
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
        video_keys = {
            self._edl_source_key(item)
            for item in events
            if item.track.upper().startswith("V")
        }
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
                resolved = self._resolve_media_path(raw, path.parent)
                label = event.source_label or event.reel
                source_labels[source_id] = label
                source_kind: Literal["video", "audio"] = (
                    "video" if key in video_keys else "audio"
                )
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
            media_kind = (
                ClipMediaKind.VIDEO_ONLY if track_kind == "video" else ClipMediaKind.AUDIO_ONLY
            )
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
            self._require_non_overlapping(portable_clips, track_code)
            kind: Literal["video", "audio"] = (
                "video" if track_code.startswith("V") else "audio"
            )
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
            warnings=[
                "CMX 3600 不携带画面尺寸、色彩空间和完整音频布局；已采用当前序列配置。"
            ],
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
            dropped = (4 if nominal_fps == 60 else 2) * (
                hour * 54 + minute - minute // 10
            )
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

    @staticmethod
    def _first(roots: Sequence[ET.Element | None], name: str) -> ET.Element | None:
        for root in roots:
            if root is None:
                continue
            item = root.find(name)
            if item is not None:
                return item
        return None

    @staticmethod
    def _resolve_media_path(raw: str, root: Path) -> Path | None:
        value = raw.strip()
        if not value:
            return None
        if re.match(r"^[A-Za-z]:[\\/]", value):
            return Path(value).resolve()
        parsed = urlparse(value)
        if parsed.scheme.casefold() == "file":
            decoded = unquote(parsed.path)
            if re.match(r"^/[A-Za-z]:/", decoded):
                decoded = decoded[1:]
            candidate = Path(decoded)
        elif parsed.scheme:
            return None
        else:
            candidate = Path(value)
            if not candidate.is_absolute():
                candidate = root / candidate
        try:
            return candidate.resolve()
        except OSError:
            return candidate.absolute()

    @staticmethod
    def _time(value: str | None) -> Fraction:
        text = (value or "").strip()
        if not text.endswith("s"):
            raise ValueError(f"FCPXML 时间值必须以 s 结尾：{text or '(empty)'}")
        raw = text[:-1]
        if "/" in raw:
            numerator, denominator = raw.split("/", 1)
            return Fraction(int(numerator), int(denominator))
        return Fraction(raw)

    def _frames(self, value: str, fps: Fraction) -> int:
        return round_fraction(self._time(value) * fps)

    @staticmethod
    def _seconds_to_frames(value: float, fps: Fraction) -> int:
        return round_fraction(Fraction(str(value)) * fps)

    @staticmethod
    def _require_non_overlapping(
        clips: Sequence[PortableMediaClip | PortableCaptionClip],
        track_id: str,
    ) -> None:
        previous_end = 0.0
        for clip in sorted(clips, key=lambda item: (item.timeline_start_seconds, item.id)):
            if clip.timeline_start_seconds < previous_end - 1e-9:
                raise ValueError(f"交换时间线轨道 {track_id} 包含重叠片段，无法映射为单一原生轨道")
            previous_end = clip.timeline_start_seconds + clip.duration_seconds
