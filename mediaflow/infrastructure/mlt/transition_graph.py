from __future__ import annotations

import xml.etree.ElementTree as ET

from mediaflow.domain.enums import TransitionKind
from mediaflow.domain.project import Asset
from mediaflow.domain.timeline import Clip, Transition
from mediaflow.infrastructure.mlt.graph import MltGraph


class MltTransitionGraph:
    TRANSITION_SERVICES = {
        TransitionKind.FADE: "luma",
        TransitionKind.DISSOLVE: "luma",
        TransitionKind.FADE_BLACK: "luma",
        TransitionKind.WIPE_LEFT: "luma",
        TransitionKind.WIPE_RIGHT: "luma",
        TransitionKind.SLIDE_LEFT: "affine",
        TransitionKind.SLIDE_RIGHT: "affine",
        TransitionKind.ZOOM: "affine",
    }

    def append_transition_tractor(
        self,
        root: ET.Element,
        item: Transition,
        clips: dict[str, Clip],
        assets: dict[str, Asset],
    ) -> None:
        left = clips[item.left_clip_id]
        right = clips[item.right_clip_id]
        before, after = MltGraph.transition_parts(item)
        left_start = MltGraph.producer_frame(left, assets[left.asset_id], left.duration - before)
        right_start = max(0, MltGraph.producer_frame(right, assets[right.asset_id], -before))
        if before >= left.duration or after >= right.duration:
            raise ValueError("Transition duration leaves no visible frames in an adjacent clip")

        tractor = ET.SubElement(
            root,
            "tractor",
            {
                "id": MltGraph.transition_id(item.id),
                "in": "0",
                "out": str(item.duration - 1),
            },
        )
        ET.SubElement(
            tractor,
            "track",
            {
                "producer": MltGraph.producer_id(left.id),
                "in": str(left_start),
                "out": str(left_start + item.duration - 1),
            },
        )
        ET.SubElement(
            tractor,
            "track",
            {
                "producer": MltGraph.producer_id(right.id),
                "in": str(right_start),
                "out": str(right_start + item.duration - 1),
            },
        )
        video = ET.SubElement(
            tractor,
            "transition",
            {"id": f"video_transition_{item.id}", "in": "0", "out": str(item.duration - 1)},
        )
        MltGraph.property(video, "a_track", "0")
        MltGraph.property(video, "b_track", "1")
        MltGraph.property(video, "mlt_service", self.TRANSITION_SERVICES[item.kind])
        MltGraph.property(video, "mediaflow:kind", item.kind.value)
        if item.kind == TransitionKind.WIPE_LEFT:
            MltGraph.property(video, "geometry", "0=0%/0%:100%x100%; 1=0%/0%:0%x100%")
        elif item.kind == TransitionKind.WIPE_RIGHT:
            MltGraph.property(video, "reverse", "1")
        elif item.kind in {TransitionKind.SLIDE_LEFT, TransitionKind.SLIDE_RIGHT}:
            direction = -100 if item.kind == TransitionKind.SLIDE_LEFT else 100
            MltGraph.property(video, "geometry", f"0={direction}%/0%:100%x100%; 1=0%/0%:100%x100%")
        elif item.kind == TransitionKind.ZOOM:
            MltGraph.property(video, "geometry", "0=25%/25%:50%x50%; 1=0%/0%:100%x100%")
        for name, value in item.parameters.items():
            MltGraph.property(video, str(name), str(value))

        audio = ET.SubElement(
            tractor,
            "transition",
            {"id": f"audio_transition_{item.id}", "in": "0", "out": str(item.duration - 1)},
        )
        MltGraph.property(audio, "a_track", "0")
        MltGraph.property(audio, "b_track", "1")
        MltGraph.property(audio, "start", "-1")
        MltGraph.property(audio, "accepts_blanks", "1")
        MltGraph.property(audio, "mlt_service", "mix")
