from __future__ import annotations

from dataclasses import dataclass

from mediaflow.domain.enums import ColorMode, TransitionKind


@dataclass(frozen=True, slots=True)
class TransitionCapability:
    kind: TransitionKind
    category: str
    label_key: str
    description_key: str
    preview_direction: str
    default_duration_frames: int
    minimum_bit_depth: int
    hdr10_verified: bool


TRANSITION_CAPABILITIES: dict[TransitionKind, TransitionCapability] = {
    TransitionKind.DISSOLVE: TransitionCapability(
        kind=TransitionKind.DISSOLVE,
        category="blend",
        label_key="交叉溶解",
        description_key="前后画面平滑叠化，适合大多数连续镜头。",
        preview_direction="blend",
        default_duration_frames=15,
        minimum_bit_depth=10,
        hdr10_verified=True,
    ),
    TransitionKind.FADE: TransitionCapability(
        kind=TransitionKind.FADE,
        category="blend",
        label_key="淡化",
        description_key="前一个镜头逐渐让位于后一个镜头。",
        preview_direction="fade",
        default_duration_frames=15,
        minimum_bit_depth=10,
        hdr10_verified=True,
    ),
    TransitionKind.FADE_BLACK: TransitionCapability(
        kind=TransitionKind.FADE_BLACK,
        category="blend",
        label_key="淡黑",
        description_key="经过黑场连接两个镜头，适合段落分隔。",
        preview_direction="black",
        default_duration_frames=20,
        minimum_bit_depth=10,
        hdr10_verified=True,
    ),
    TransitionKind.WIPE_LEFT: TransitionCapability(
        kind=TransitionKind.WIPE_LEFT,
        category="wipe",
        label_key="左擦除",
        description_key="新画面从右向左擦入。",
        preview_direction="left",
        default_duration_frames=15,
        minimum_bit_depth=8,
        hdr10_verified=False,
    ),
    TransitionKind.WIPE_RIGHT: TransitionCapability(
        kind=TransitionKind.WIPE_RIGHT,
        category="wipe",
        label_key="右擦除",
        description_key="新画面从左向右擦入。",
        preview_direction="right",
        default_duration_frames=15,
        minimum_bit_depth=8,
        hdr10_verified=False,
    ),
    TransitionKind.SLIDE_LEFT: TransitionCapability(
        kind=TransitionKind.SLIDE_LEFT,
        category="motion",
        label_key="左滑动",
        description_key="两个镜头保持空间关系并一起向左移动。",
        preview_direction="left",
        default_duration_frames=15,
        minimum_bit_depth=8,
        hdr10_verified=False,
    ),
    TransitionKind.SLIDE_RIGHT: TransitionCapability(
        kind=TransitionKind.SLIDE_RIGHT,
        category="motion",
        label_key="右滑动",
        description_key="两个镜头保持空间关系并一起向右移动。",
        preview_direction="right",
        default_duration_frames=15,
        minimum_bit_depth=8,
        hdr10_verified=False,
    ),
    TransitionKind.ZOOM: TransitionCapability(
        kind=TransitionKind.ZOOM,
        category="motion",
        label_key="缩放",
        description_key="通过中心缩放连接两个镜头。",
        preview_direction="zoom",
        default_duration_frames=15,
        minimum_bit_depth=8,
        hdr10_verified=False,
    ),
}


def transition_is_available(kind: TransitionKind, color_mode: ColorMode) -> bool:
    capability = TRANSITION_CAPABILITIES[kind]
    return color_mode != ColorMode.HDR10_BT2020_PQ or capability.hdr10_verified
