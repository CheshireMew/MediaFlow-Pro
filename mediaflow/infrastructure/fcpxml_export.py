from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
from functools import partial
from pathlib import Path
from xml.etree import ElementTree as ET

from mediaflow.application.ports import InterchangeExportDocuments
from mediaflow.application.timeline_clock import assets_in_timeline_clock
from mediaflow.domain.clip_transform_projection import project_clip_transform_points
from mediaflow.domain.enums import (
    AssetKind,
    ClipMediaKind,
    TrackKind,
    TransitionKind,
)
from mediaflow.domain.product_identity import PRODUCT_NAME
from mediaflow.domain.project import Asset
from mediaflow.domain.sequence_audio import select_audible_sequence_audio
from mediaflow.domain.storage_names import python_io_path
from mediaflow.domain.timeline import (
    Clip,
    ClipTransform,
    TimelineState,
    Transition,
)
from mediaflow.infrastructure.output_reservation import (
    archive_failed_output,
    publish_python_output,
    require_python_output_transaction_path,
    reserve_python_output,
    temporary_output_path,
)
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from mediaflow.infrastructure.web_render_target import WebRenderCache


@dataclass(frozen=True, slots=True)
class _ClipPresentation:
    clip: Clip
    track_id: str
    media_kind: ClipMediaKind
    audio_enabled: bool = True


class FcpxmlExportService:
    """Serialize the canonical project timeline into an interoperable FCPXML document."""

    def __init__(
        self,
        documents: InterchangeExportDocuments,
        paths: RuntimePaths,
    ) -> None:
        self.documents = documents
        self.paths = paths

    def export(
        self,
        state: TimelineState,
        destination: str | Path,
        *,
        overwrite: bool = False,
    ) -> Path:
        output = self.preflight(
            state,
            destination,
            overwrite=overwrite,
        )
        python_io_path(output.parent).mkdir(parents=True, exist_ok=True)
        with reserve_python_output(output):
            return self._export_reserved(state, output, overwrite=overwrite)

    def preflight(
        self,
        state: TimelineState,
        destination: str | Path,
        *,
        overwrite: bool = False,
    ) -> Path:
        """Validate semantics and obvious destination conflicts without writing."""

        output = Path(destination).expanduser().resolve()
        if output.suffix.lower() != ".fcpxml":
            output = output.with_suffix(".fcpxml")
        output = require_python_output_transaction_path(output)
        self.validate(state)
        if python_io_path(output).exists() and not overwrite:
            raise FileExistsError(output)
        return output

    def validate(self, state: TimelineState) -> None:
        """Reject editing decisions that FCPXML cannot preserve faithfully."""

        frozen = [clip.id for clip in state.clips if clip.freeze_source_frame is not None]
        if frozen:
            raise ValueError(
                "FCPXML 交接暂时无法保证定格帧语义不变；请先把定格片段渲染为普通素材，或改为导出成片。"
            )

        assets = assets_in_timeline_clock(
            self.documents.projects,
            self.documents.sequences,
            self.documents.assets,
            state.sequence,
        )
        presentations = self._clip_presentations(state, assets)
        presented = {item.clip.id: item for item in presentations}
        for transition in state.transitions:
            left = presented.get(transition.left_clip_id)
            right = presented.get(transition.right_clip_id)
            if left is None or right is None:
                continue
            if left.track_id != right.track_id:
                raise ValueError(f"FCPXML 无法保留跨导出轨道的转场：{transition.kind.value}")
            if transition.kind != TransitionKind.DISSOLVE or transition.parameters:
                raise ValueError(
                    "FCPXML 只能可靠交接标准交叉溶解转场；"
                    f"当前转场 {transition.kind.value} 没有可确认的稳定内置效果映射"
                )

        unsupported_audio: list[str] = []
        for bus in self.documents.audio.list_audio_buses(state.sequence.id):
            if bus.gain_db != 0.0:
                unsupported_audio.append(f"{bus.name} 增益 {bus.gain_db:g} dB")
            enabled_effects = [
                effect.kind.value
                for effect in self.documents.audio.list_audio_effects(bus.id)
                if effect.enabled
            ]
            if enabled_effects:
                unsupported_audio.append(f"{bus.name} 效果 {', '.join(enabled_effects)}")
        if unsupported_audio:
            raise ValueError(
                f"FCPXML 没有可可靠重建 {PRODUCT_NAME} 音频总线处理的等价结构："
                + "；".join(unsupported_audio)
                + "。如需保持最终声音，请改为导出成片。"
            )

    def _export_reserved(
        self,
        state: TimelineState,
        output: Path,
        *,
        overwrite: bool,
    ) -> Path:
        if python_io_path(output).exists() and not overwrite:
            raise FileExistsError(output)
        root = ET.Element("fcpxml", version="1.11")
        resources = ET.SubElement(root, "resources")
        profile = state.sequence.profile
        format_id = "r1"
        ET.SubElement(
            resources,
            "format",
            id=format_id,
            name=f"FFVideoFormat{profile.width}x{profile.height}",
            frameDuration=self._time(1, profile.fps_numerator, profile.fps_denominator),
            width=str(profile.width),
            height=str(profile.height),
            colorSpace=(
                "1-1-1 (Rec. 709)" if profile.color_mode.value == "sdr_bt709" else "9-16-9 (Rec. 2020 PQ)"
            ),
        )
        assets = assets_in_timeline_clock(
            self.documents.projects,
            self.documents.sequences,
            self.documents.assets,
            state.sequence,
        )
        presentations = self._clip_presentations(state, assets)
        resource_ids = self._append_resources(
            resources,
            state,
            assets,
            presentations,
            format_id,
        )

        library = ET.SubElement(
            root,
            "library",
            location=self.documents.project_dir.resolve().as_uri(),
        )
        event = ET.SubElement(library, "event", name=PRODUCT_NAME)
        project = ET.SubElement(event, "project", name=state.sequence.name)
        duration = max(1, state.duration_frames)
        sequence = ET.SubElement(
            project,
            "sequence",
            format=format_id,
            duration=self._time(duration, profile.fps_numerator, profile.fps_denominator),
            tcStart="0s",
            tcFormat="NDF",
            audioLayout={1: "mono", 2: "stereo", 6: "surround"}[profile.audio_channels],
            audioRate=f"{profile.audio_sample_rate // 1000}k",
        )
        sequence_spine = ET.SubElement(sequence, "spine")
        timeline_container = ET.SubElement(
            sequence_spine,
            "clip",
            name=state.sequence.name,
            offset="0s",
            start="0s",
            duration=self._time(
                duration,
                profile.fps_numerator,
                profile.fps_denominator,
            ),
            format=format_id,
        )
        primary_spine = ET.SubElement(timeline_container, "spine")
        video_tracks = state.effective_tracks(TrackKind.VIDEO)
        audio_track_ids = set(
            select_audible_sequence_audio(
                state,
                assets,
                self.documents.audio.list_audio_buses(state.sequence.id),
            ).track_ids
        )
        audio_tracks = [
            track for track in state.effective_tracks(TrackKind.AUDIO) if track.id in audio_track_ids
        ]
        subtitle_tracks = state.effective_tracks(TrackKind.SUBTITLE)
        presentations_by_track: dict[str, list[_ClipPresentation]] = {}
        for presentation in presentations:
            presentations_by_track.setdefault(
                presentation.track_id,
                [],
            ).append(presentation)

        primary_track = next(iter(video_tracks), None)
        self._append_track_story(
            primary_spine,
            (presentations_by_track.get(primary_track.id, []) if primary_track is not None else []),
            assets,
            resource_ids,
            state,
            fill_to_frame=duration,
        )
        for lane, track in enumerate(video_tracks[1:], start=1):
            track_items = presentations_by_track.get(track.id, [])
            if not track_items:
                continue
            track_spine = ET.SubElement(
                timeline_container,
                "spine",
                name=track.name,
                lane=str(lane),
                offset="0s",
            )
            self._append_track_story(
                track_spine,
                track_items,
                assets,
                resource_ids,
                state,
            )
        for lane, track in enumerate(audio_tracks, start=1):
            track_items = presentations_by_track.get(track.id, [])
            if not track_items:
                continue
            track_spine = ET.SubElement(
                timeline_container,
                "spine",
                name=track.name,
                lane=str(-lane),
                offset="0s",
            )
            self._append_track_story(
                track_spine,
                track_items,
                assets,
                resource_ids,
                state,
            )
        caption_lanes = {
            track.id: -(len(audio_tracks) + index)
            for index, track in enumerate(
                subtitle_tracks,
                start=1,
            )
        }
        self._append_captions(
            timeline_container,
            state,
            caption_lanes,
        )
        self._append_markers(
            timeline_container,
            state,
        )

        ET.indent(root, space="  ")
        temporary = temporary_output_path(output, "fcpxml")
        try:
            ET.ElementTree(root).write(temporary, encoding="utf-8", xml_declaration=True)
            publish_python_output(temporary, output)
        except Exception:
            archive_failed_output(temporary, output)
            raise
        return output

    def _clip_presentations(
        self,
        state: TimelineState,
        assets: dict[str, Asset],
    ) -> list[_ClipPresentation]:
        tracks = {track.id: track for track in state.tracks}
        video_track_ids = {track.id for track in state.effective_tracks(TrackKind.VIDEO)}
        audio_track_ids = set(
            select_audible_sequence_audio(
                state,
                assets,
                self.documents.audio.list_audio_buses(state.sequence.id),
            ).track_ids
        )
        presentations: list[_ClipPresentation] = []
        for clip in state.clips:
            track = tracks.get(clip.track_id)
            asset = assets.get(clip.asset_id)
            if track is None:
                raise ValueError(f"Timeline references unknown track: {clip.track_id}")
            if asset is None:
                raise ValueError(f"Timeline references unknown asset: {clip.asset_id}")
            clip.validate_source_range(
                asset.kind,
                asset.metadata.duration_frames,
            )
            if clip.media_kind == ClipMediaKind.LINKED_AV:
                linked_audio_id = track.linked_audio_track_id
                video_enabled = track.id in video_track_ids
                audio_enabled = linked_audio_id is not None and linked_audio_id in audio_track_ids
                if video_enabled:
                    presentations.append(
                        _ClipPresentation(
                            clip=clip,
                            track_id=track.id,
                            media_kind=ClipMediaKind.LINKED_AV,
                            audio_enabled=audio_enabled,
                        )
                    )
                elif audio_enabled and linked_audio_id is not None:
                    presentations.append(
                        _ClipPresentation(
                            clip=clip,
                            track_id=linked_audio_id,
                            media_kind=ClipMediaKind.AUDIO_ONLY,
                        )
                    )
            elif clip.media_kind == ClipMediaKind.VIDEO_ONLY and track.id in video_track_ids:
                presentations.append(
                    _ClipPresentation(
                        clip=clip,
                        track_id=track.id,
                        media_kind=ClipMediaKind.VIDEO_ONLY,
                    )
                )
            elif clip.media_kind == ClipMediaKind.AUDIO_ONLY and track.id in audio_track_ids:
                presentations.append(
                    _ClipPresentation(
                        clip=clip,
                        track_id=track.id,
                        media_kind=ClipMediaKind.AUDIO_ONLY,
                    )
                )
        return presentations

    def _append_resources(
        self,
        resources: ET.Element,
        state: TimelineState,
        assets: dict[str, Asset],
        presentations: list[_ClipPresentation],
        format_id: str,
    ) -> dict[str, str]:
        profile = state.sequence.profile
        resource_by_source: dict[tuple[str, str], str] = {}
        resource_ids: dict[str, str] = {}
        next_resource_index = 2
        cache = WebRenderCache(self.documents, self.paths)
        for presentation in presentations:
            clip = presentation.clip
            asset = assets[clip.asset_id]
            source_key = ("web-clip", clip.id) if asset.kind == AssetKind.WEB else ("asset", asset.id)
            resource_id = resource_by_source.get(source_key)
            if resource_id is None:
                resource_id = f"r{next_resource_index}"
                next_resource_index += 1
                resource_by_source[source_key] = resource_id
                if asset.kind == AssetKind.WEB:
                    target = cache.target(state, clip, asset)
                    source = target.path.resolve()
                    if not source.is_file() or source.stat().st_size <= 0:
                        raise FileNotFoundError(f"FCPXML 需要已生成的网页媒体缓存：{source}")
                    duration_frames = target.frame_count
                    resource_name = f"{asset.name} · {clip.id[:8]}"
                else:
                    source = self.documents.assets.resolve_asset_path(asset).resolve()
                    duration_frames = max(
                        1,
                        asset.metadata.duration_frames,
                    )
                    resource_name = asset.name
                attributes = {
                    "id": resource_id,
                    "name": resource_name,
                    "start": "0s",
                    "duration": self._time(
                        max(1, duration_frames),
                        profile.fps_numerator,
                        profile.fps_denominator,
                    ),
                }
                if asset.kind in {
                    AssetKind.VIDEO,
                    AssetKind.IMAGE,
                    AssetKind.WEB,
                }:
                    attributes.update(
                        {
                            "hasVideo": "1",
                            "videoSources": "1",
                            "format": format_id,
                        }
                    )
                if asset.metadata.has_audio or asset.kind == AssetKind.AUDIO:
                    attributes.update(
                        {
                            "hasAudio": "1",
                            "audioSources": "1",
                            "audioChannels": str(profile.audio_channels),
                            "audioRate": str(profile.audio_sample_rate),
                        }
                    )
                resource = ET.SubElement(
                    resources,
                    "asset",
                    attributes,
                )
                ET.SubElement(
                    resource,
                    "media-rep",
                    kind="original-media",
                    src=source.as_uri(),
                    suggestedFilename=source.name,
                )
            resource_ids[clip.id] = resource_id
        return resource_ids

    def _append_track_story(
        self,
        parent: ET.Element,
        presentations: list[_ClipPresentation],
        assets: dict[str, Asset],
        resource_ids: dict[str, str],
        state: TimelineState,
        *,
        fill_to_frame: int | None = None,
    ) -> None:
        ordered = sorted(
            presentations,
            key=lambda item: (
                item.clip.timeline_start,
                item.clip.id,
            ),
        )
        presented_ids = {item.clip.id for item in ordered}
        outgoing = {
            transition.left_clip_id: transition
            for transition in state.transitions
            if transition.left_clip_id in presented_ids and transition.right_clip_id in presented_ids
        }
        cursor = 0
        for presentation in ordered:
            clip = presentation.clip
            if clip.timeline_start > cursor:
                self._append_gap(
                    parent,
                    cursor,
                    clip.timeline_start - cursor,
                    state,
                )
            self._append_clip(
                parent,
                presentation,
                assets,
                resource_ids,
                state,
            )
            transition = outgoing.get(clip.id)
            if transition is not None:
                self._append_transition(
                    parent,
                    transition,
                    clip,
                    state,
                )
            cursor = max(cursor, clip.timeline_end)
        if fill_to_frame is not None and cursor < fill_to_frame:
            self._append_gap(
                parent,
                cursor,
                fill_to_frame - cursor,
                state,
            )

    def _append_gap(
        self,
        parent: ET.Element,
        start_frame: int,
        duration_frames: int,
        state: TimelineState,
    ) -> None:
        if duration_frames <= 0:
            return
        profile = state.sequence.profile
        ET.SubElement(
            parent,
            "gap",
            name="Gap",
            offset=self._time(
                start_frame,
                profile.fps_numerator,
                profile.fps_denominator,
            ),
            start="0s",
            duration=self._time(
                duration_frames,
                profile.fps_numerator,
                profile.fps_denominator,
            ),
        )

    def _append_transition(
        self,
        parent: ET.Element,
        transition: Transition,
        left: Clip,
        state: TimelineState,
    ) -> None:
        profile = state.sequence.profile
        before = transition.duration // 2
        ET.SubElement(
            parent,
            "transition",
            name="Cross Dissolve",
            offset=self._time(
                left.timeline_end - before,
                profile.fps_numerator,
                profile.fps_denominator,
            ),
            duration=self._time(
                transition.duration,
                profile.fps_numerator,
                profile.fps_denominator,
            ),
        )

    def _append_clip(
        self,
        parent: ET.Element,
        presentation: _ClipPresentation,
        assets: dict[str, Asset],
        resource_ids: dict[str, str],
        state: TimelineState,
    ) -> None:
        clip = presentation.clip
        asset = assets[clip.asset_id]
        profile = state.sequence.profile
        source_start = self._time(
            clip.source_in,
            profile.fps_numerator,
            profile.fps_denominator,
        )
        duration = self._time(
            clip.duration,
            profile.fps_numerator,
            profile.fps_denominator,
        )
        attributes = {
            "name": asset.name,
            "offset": self._time(
                clip.timeline_start,
                profile.fps_numerator,
                profile.fps_denominator,
            ),
            "start": source_start,
            "duration": duration,
        }
        if presentation.media_kind == ClipMediaKind.LINKED_AV:
            linked_attributes = {
                **attributes,
                "ref": resource_ids[clip.id],
            }
            if not presentation.audio_enabled:
                linked_attributes["srcEnable"] = "video"
            clip_element = ET.SubElement(
                parent,
                "asset-clip",
                linked_attributes,
            )
            self._append_time_map(clip_element, clip, state)
            self._append_video_adjustments(
                clip_element,
                clip,
                asset,
                state,
            )
            self._append_audio_adjustments(
                clip_element,
                clip,
                state,
            )
            return

        clip_element = ET.SubElement(
            parent,
            "clip",
            attributes,
        )
        if presentation.media_kind == ClipMediaKind.VIDEO_ONLY:
            self._append_video_adjustments(
                clip_element,
                clip,
                asset,
                state,
            )
            component_name = "video"
        else:
            self._append_audio_adjustments(
                clip_element,
                clip,
                state,
            )
            component_name = "audio"
        component = ET.SubElement(
            clip_element,
            component_name,
            ref=resource_ids[clip.id],
            offset=source_start,
            start=source_start,
            duration=duration,
        )
        self._append_time_map(component, clip, state)

    def _append_time_map(
        self,
        parent: ET.Element,
        clip: Clip,
        state: TimelineState,
    ) -> None:
        if clip.speed_numerator == clip.speed_denominator:
            return
        profile = state.sequence.profile
        time_map = ET.SubElement(
            parent,
            "timeMap",
            frameSampling="floor",
            preservesPitch=("1" if clip.pitch_compensation else "0"),
        )
        source_start = Fraction(clip.source_in)
        source_end = source_start + Fraction(
            clip.duration * clip.speed_numerator,
            clip.speed_denominator,
        )
        ET.SubElement(
            time_map,
            "timept",
            time="0s",
            value=self._fraction_time(
                source_start,
                profile.fps_numerator,
                profile.fps_denominator,
            ),
            interp="linear",
        )
        ET.SubElement(
            time_map,
            "timept",
            time=self._time(
                clip.duration,
                profile.fps_numerator,
                profile.fps_denominator,
            ),
            value=self._fraction_time(
                source_end,
                profile.fps_numerator,
                profile.fps_denominator,
            ),
            interp="linear",
        )

    def _append_video_adjustments(
        self,
        parent: ET.Element,
        clip: Clip,
        asset: Asset,
        state: TimelineState,
    ) -> None:
        projection = project_clip_transform_points(clip)
        points = list(projection.points)
        has_keyframes = projection.has_keyframes
        transforms = [value for _frame, value in points]
        if any(
            value.crop_left or value.crop_top or value.crop_right or value.crop_bottom for value in transforms
        ):
            crop = ET.SubElement(
                parent,
                "adjust-crop",
                mode="trim",
            )
            crop_rect = ET.SubElement(
                crop,
                "trim-rect",
                self._crop_attributes(
                    clip.transform,
                    asset,
                    state,
                ),
            )
            if has_keyframes:
                for name in (
                    "left",
                    "top",
                    "right",
                    "bottom",
                ):
                    self._append_parameter_animation(
                        crop_rect,
                        name,
                        points,
                        partial(
                            self._crop_attribute_value,
                            asset=asset,
                            state=state,
                            field=name,
                        ),
                        state,
                    )
        if any(
            value.x or value.y or value.scale_x != 1.0 or value.scale_y != 1.0 or value.rotation
            for value in transforms
        ):
            transform = ET.SubElement(
                parent,
                "adjust-transform",
                self._transform_attributes(
                    clip.transform,
                    state,
                ),
            )
            if has_keyframes:
                self._append_parameter_animation(
                    transform,
                    "position",
                    points,
                    lambda value: self._transform_attributes(
                        value,
                        state,
                    )["position"],
                    state,
                )
                self._append_parameter_animation(
                    transform,
                    "scale",
                    points,
                    lambda value: self._transform_attributes(
                        value,
                        state,
                    )["scale"],
                    state,
                )
                self._append_parameter_animation(
                    transform,
                    "rotation",
                    points,
                    lambda value: self._transform_attributes(
                        value,
                        state,
                    )["rotation"],
                    state,
                )
        if any(value.opacity != 1.0 for value in transforms):
            blend = ET.SubElement(
                parent,
                "adjust-blend",
                amount=f"{clip.transform.opacity:g}",
            )
            if has_keyframes:
                self._append_parameter_animation(
                    blend,
                    "amount",
                    points,
                    lambda value: f"{value.opacity:g}",
                    state,
                )

    def _append_audio_adjustments(
        self,
        parent: ET.Element,
        clip: Clip,
        state: TimelineState,
    ) -> None:
        profile = state.sequence.profile
        audio = clip.audio
        if audio.gain_db != 0.0 or audio.fade_in_frames or audio.fade_out_frames:
            amount = f"{audio.gain_db:g}dB"
            volume = ET.SubElement(
                parent,
                "adjust-volume",
                amount=amount,
            )
            if audio.fade_in_frames or audio.fade_out_frames:
                parameter = ET.SubElement(
                    volume,
                    "param",
                    name="amount",
                    value=amount,
                )
                if audio.fade_in_frames:
                    ET.SubElement(
                        parameter,
                        "fadeIn",
                        type="linear",
                        duration=self._time(
                            audio.fade_in_frames,
                            profile.fps_numerator,
                            profile.fps_denominator,
                        ),
                    )
                if audio.fade_out_frames:
                    ET.SubElement(
                        parameter,
                        "fadeOut",
                        type="linear",
                        duration=self._time(
                            audio.fade_out_frames,
                            profile.fps_numerator,
                            profile.fps_denominator,
                        ),
                    )
        if audio.pan != 0.0:
            ET.SubElement(
                parent,
                "adjust-panner",
                mode="1",
                amount=f"{audio.pan:g}",
            )

    def _append_parameter_animation(
        self,
        parent: ET.Element,
        name: str,
        points: list[tuple[int, ClipTransform]],
        value: Callable[[ClipTransform], str],
        state: TimelineState,
    ) -> None:
        profile = state.sequence.profile
        parameter = ET.SubElement(parent, "param", name=name)
        animation = ET.SubElement(
            parameter,
            "keyframeAnimation",
        )
        for frame, transform in points:
            ET.SubElement(
                animation,
                "keyframe",
                time=self._time(
                    frame,
                    profile.fps_numerator,
                    profile.fps_denominator,
                ),
                value=value(transform),
                interp="linear",
                curve="linear",
            )

    @staticmethod
    def _transform_attributes(
        transform: ClipTransform,
        state: TimelineState,
    ) -> dict[str, str]:
        profile = state.sequence.profile
        horizontal = transform.x * profile.width / profile.height
        return {
            "position": f"{horizontal:g} {-transform.y:g}",
            "scale": (f"{transform.scale_x:g} {transform.scale_y:g}"),
            "rotation": f"{-transform.rotation:g}",
        }

    @staticmethod
    def _crop_attributes(
        transform: ClipTransform,
        asset: Asset,
        state: TimelineState,
    ) -> dict[str, str]:
        profile = state.sequence.profile
        source_width = asset.metadata.width or profile.width
        source_height = asset.metadata.height or profile.height
        horizontal_scale = 100.0 * source_width / source_height
        return {
            "left": f"{transform.crop_left * horizontal_scale:g}",
            "top": f"{transform.crop_top * 100.0:g}",
            "right": f"{transform.crop_right * horizontal_scale:g}",
            "bottom": f"{transform.crop_bottom * 100.0:g}",
        }

    @staticmethod
    def _crop_attribute_value(
        transform: ClipTransform,
        *,
        asset: Asset,
        state: TimelineState,
        field: str,
    ) -> str:
        return FcpxmlExportService._crop_attributes(
            transform,
            asset,
            state,
        )[field]

    def _append_markers(
        self,
        parent: ET.Element,
        state: TimelineState,
    ) -> None:
        profile = state.sequence.profile
        for marker in sorted(
            state.markers,
            key=lambda item: (item.frame, item.id),
        ):
            ET.SubElement(
                parent,
                "marker",
                start=self._time(
                    marker.frame,
                    profile.fps_numerator,
                    profile.fps_denominator,
                ),
                value=marker.name or "Marker",
                completed="0",
            )

    def _append_captions(
        self,
        parent: ET.Element,
        state: TimelineState,
        lanes: dict[str, int],
    ) -> None:
        profile = state.sequence.profile
        segments = {
            segment.id: (
                segment,
                document.language.replace("_", "-"),
            )
            for document in self.documents.subtitles.list_subtitle_documents()
            for segment in self.documents.subtitles.list_subtitle_segments(document.id)
        }
        subtitle_tracks = state.effective_tracks(TrackKind.SUBTITLE)
        for track in subtitle_tracks:
            for placement in self.documents.subtitles.list_subtitle_placements(track.id):
                segment_entry = segments.get(placement.segment_id)
                if segment_entry is None:
                    continue
                segment, language = segment_entry
                caption = ET.SubElement(
                    parent,
                    "caption",
                    lane=str(lanes[track.id]),
                    offset=self._time(
                        placement.start_frame,
                        profile.fps_numerator,
                        profile.fps_denominator,
                    ),
                    duration=self._time(
                        placement.end_frame - placement.start_frame,
                        profile.fps_numerator,
                        profile.fps_denominator,
                    ),
                    role=(f"ITT Subtitles?captionFormat=ITT.{language or 'und'}"),
                )
                text = ET.SubElement(caption, "text")
                ET.SubElement(text, "text-style").text = placement.text_override or segment.text

    @staticmethod
    def _time(frames: int, numerator: int, denominator: int) -> str:
        value = Fraction(frames * denominator, numerator)
        if value.denominator == 1:
            return f"{value.numerator}s"
        return f"{value.numerator}/{value.denominator}s"

    @staticmethod
    def _fraction_time(frames: Fraction, numerator: int, denominator: int) -> str:
        value = frames * denominator / numerator
        if value.denominator == 1:
            return f"{value.numerator}s"
        return f"{value.numerator}/{value.denominator}s"
