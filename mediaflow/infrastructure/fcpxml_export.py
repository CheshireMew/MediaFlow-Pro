from __future__ import annotations

from fractions import Fraction
from pathlib import Path
from xml.etree import ElementTree as ET

from mediaflow.application.ports import TaskHandlerDocuments
from mediaflow.domain.enums import AssetKind, TrackKind
from mediaflow.domain.timeline import Clip, TimelineState


class FcpxmlExportService:
    """Serialize the canonical project timeline into an interoperable FCPXML document."""

    def __init__(self, documents: TaskHandlerDocuments) -> None:
        self.documents = documents

    def export(self, state: TimelineState, destination: str | Path) -> Path:
        output = Path(destination).expanduser().resolve()
        if output.suffix.lower() != ".fcpxml":
            output = output.with_suffix(".fcpxml")
        output.parent.mkdir(parents=True, exist_ok=True)

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
                "1-1-1 (Rec. 709)"
                if profile.color_mode.value == "sdr_bt709"
                else "9-16-9 (Rec. 2020 HLG)"
            ),
        )
        assets = {asset.id: asset for asset in self.documents.list_assets()}
        resource_ids: dict[str, str] = {}
        referenced_asset_ids = {clip.asset_id for clip in state.clips}
        referenced_assets = [
            asset for asset in assets.values() if asset.id in referenced_asset_ids
        ]
        for index, asset in enumerate(referenced_assets, start=2):
            resource_id = f"r{index}"
            resource_ids[asset.id] = resource_id
            attributes = {
                "id": resource_id,
                "name": asset.name,
                "src": self.documents.resolve_asset_path(asset).resolve().as_uri(),
                "start": "0s",
                "duration": self._time(
                    max(1, asset.metadata.duration_frames),
                    profile.fps_numerator,
                    profile.fps_denominator,
                ),
            }
            if asset.kind in {AssetKind.VIDEO, AssetKind.IMAGE, AssetKind.WEB}:
                attributes.update({"hasVideo": "1", "format": format_id})
            if asset.metadata.has_audio or asset.kind == AssetKind.AUDIO:
                attributes.update(
                    {
                        "hasAudio": "1",
                        "audioSources": "1",
                        "audioChannels": str(profile.audio_channels),
                        "audioRate": str(profile.audio_sample_rate),
                    }
                )
            ET.SubElement(resources, "asset", **attributes)

        library = ET.SubElement(root, "library", location=self.documents.project_dir.resolve().as_uri())
        event = ET.SubElement(library, "event", name="MediaFlow Pro")
        project = ET.SubElement(event, "project", name=state.sequence.name)
        duration = max(1, state.duration_frames)
        sequence = ET.SubElement(
            project,
            "sequence",
            format=format_id,
            duration=self._time(duration, profile.fps_numerator, profile.fps_denominator),
            tcStart="0s",
            tcFormat="NDF",
            audioLayout="stereo" if profile.audio_channels == 2 else "surround",
            audioRate=str(profile.audio_sample_rate),
        )
        spine = ET.SubElement(sequence, "spine")
        tracks = {track.id: track for track in state.tracks}
        primary_track = next(
            (
                track
                for track in sorted(state.tracks, key=lambda item: item.position)
                if track.kind == TrackKind.VIDEO and track.enabled
            ),
            None,
        )
        primary_clips = (
            state.clips_for_track(primary_track.id) if primary_track is not None else []
        )
        cursor = 0
        for clip in primary_clips:
            if clip.timeline_start > cursor:
                ET.SubElement(
                    spine,
                    "gap",
                    name="Gap",
                    offset=self._time(cursor, profile.fps_numerator, profile.fps_denominator),
                    start="0s",
                    duration=self._time(
                        clip.timeline_start - cursor,
                        profile.fps_numerator,
                        profile.fps_denominator,
                    ),
                )
            self._append_clip(spine, clip, assets, resource_ids, state, lane=None)
            cursor = max(cursor, clip.timeline_end)
        if cursor < duration:
            ET.SubElement(
                spine,
                "gap",
                name="Gap",
                offset=self._time(cursor, profile.fps_numerator, profile.fps_denominator),
                start="0s",
                duration=self._time(
                    duration - cursor,
                    profile.fps_numerator,
                    profile.fps_denominator,
                ),
            )
        for clip in sorted(state.clips, key=lambda item: (item.timeline_start, item.id)):
            if primary_track is not None and clip.track_id == primary_track.id:
                continue
            track = tracks[clip.track_id]
            lane = track.position + 1 if track.kind == TrackKind.VIDEO else -(track.position + 1)
            self._append_clip(spine, clip, assets, resource_ids, state, lane=lane)
        self._append_captions(spine, state)

        ET.indent(root, space="  ")
        temporary = output.with_suffix(output.suffix + ".partial")
        ET.ElementTree(root).write(temporary, encoding="utf-8", xml_declaration=True)
        temporary.replace(output)
        return output

    def _append_clip(
        self,
        parent: ET.Element,
        clip: Clip,
        assets: dict,
        resource_ids: dict[str, str],
        state: TimelineState,
        *,
        lane: int | None,
    ) -> None:
        profile = state.sequence.profile
        attributes = {
            "name": assets[clip.asset_id].name,
            "ref": resource_ids[clip.asset_id],
            "offset": self._time(
                clip.timeline_start,
                profile.fps_numerator,
                profile.fps_denominator,
            ),
            "start": self._time(
                clip.source_in,
                profile.fps_numerator,
                profile.fps_denominator,
            ),
            "duration": self._time(
                clip.duration,
                profile.fps_numerator,
                profile.fps_denominator,
            ),
        }
        if lane is not None:
            attributes["lane"] = str(lane)
        clip_element = ET.SubElement(parent, "asset-clip", **attributes)
        for marker in state.markers:
            if clip.timeline_start <= marker.frame < clip.timeline_end:
                ET.SubElement(
                    clip_element,
                    "marker",
                    start=self._time(
                        clip.source_in + marker.frame - clip.timeline_start,
                        profile.fps_numerator,
                        profile.fps_denominator,
                    ),
                    value=marker.name or "Marker",
                    completed="0",
                )

    def _append_captions(self, spine: ET.Element, state: TimelineState) -> None:
        profile = state.sequence.profile
        segments = {
            segment.id: segment
            for document in self.documents.list_subtitle_documents()
            for segment in self.documents.list_subtitle_segments(document.id)
        }
        subtitle_tracks = [track for track in state.tracks if track.kind == TrackKind.SUBTITLE]
        for track in subtitle_tracks:
            for placement in self.documents.list_subtitle_placements(track.id):
                segment = segments.get(placement.segment_id)
                if segment is None:
                    continue
                caption = ET.SubElement(
                    spine,
                    "caption",
                    lane=str(-(track.position + 1)),
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
                    role="iTT?captionFormat=ITT.en",
                )
                text = ET.SubElement(caption, "text")
                ET.SubElement(text, "text-style").text = placement.text_override or segment.text

    @staticmethod
    def _time(frames: int, numerator: int, denominator: int) -> str:
        value = Fraction(frames * denominator, numerator)
        if value.denominator == 1:
            return f"{value.numerator}s"
        return f"{value.numerator}/{value.denominator}s"
