from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from mediaflow.domain.project import Asset
from mediaflow.domain.timeline import Clip, Transition
from mediaflow.infrastructure.mlt.graph import MltGraph


@dataclass(frozen=True, slots=True)
class MltPlaylistBlank:
    length: int


@dataclass(frozen=True, slots=True)
class MltPlaylistEntry:
    producer: str
    in_frame: int
    out_frame: int


MltPlaylistItem = MltPlaylistBlank | MltPlaylistEntry


def plan_playlist_layout(
    clips: Iterable[Clip],
    assets: dict[str, Asset],
    transitions: Iterable[Transition],
    *,
    producer_id: Callable[[str], str],
    transition_id: Callable[[str], str],
) -> tuple[MltPlaylistItem, ...]:
    """Plan the shared visible timing of an MLT video or audio playlist."""

    incoming = {item.right_clip_id: item for item in transitions}
    outgoing = {item.left_clip_id: item for item in transitions}
    cursor = 0
    layout: list[MltPlaylistItem] = []
    for clip in clips:
        incoming_after = MltGraph.transition_parts(incoming[clip.id])[1] if clip.id in incoming else 0
        outgoing_before = MltGraph.transition_parts(outgoing[clip.id])[0] if clip.id in outgoing else 0
        visible_start = clip.timeline_start + incoming_after
        visible_end = clip.timeline_end - outgoing_before
        if visible_start > cursor:
            layout.append(MltPlaylistBlank(visible_start - cursor))
        if visible_end > visible_start:
            source_in = MltGraph.producer_frame(
                clip,
                assets[clip.asset_id],
                incoming_after,
            )
            layout.append(
                MltPlaylistEntry(
                    producer=producer_id(clip.id),
                    in_frame=source_in,
                    out_frame=source_in + visible_end - visible_start - 1,
                )
            )
            cursor = visible_end
        if clip.id in outgoing:
            transition = outgoing[clip.id]
            layout.append(
                MltPlaylistEntry(
                    producer=transition_id(transition.id),
                    in_frame=0,
                    out_frame=transition.duration - 1,
                )
            )
            cursor += transition.duration
    return tuple(layout)


def append_playlist_layout(
    playlist: ET.Element,
    layout: Iterable[MltPlaylistItem],
) -> None:
    for item in layout:
        if isinstance(item, MltPlaylistBlank):
            ET.SubElement(playlist, "blank", {"length": str(item.length)})
            continue
        ET.SubElement(
            playlist,
            "entry",
            {
                "producer": item.producer,
                "in": str(item.in_frame),
                "out": str(item.out_frame),
            },
        )
