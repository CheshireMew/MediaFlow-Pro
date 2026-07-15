from __future__ import annotations

from dataclasses import dataclass

from mediaflow.domain.enums import ColorMode, TransitionKind


@dataclass(frozen=True, slots=True)
class TransitionCapability:
    kind: TransitionKind
    minimum_bit_depth: int
    hdr10_verified: bool


TRANSITION_CAPABILITIES = {
    kind: TransitionCapability(
        kind=kind,
        minimum_bit_depth=10 if kind in {
            TransitionKind.FADE,
            TransitionKind.DISSOLVE,
            TransitionKind.FADE_BLACK,
        } else 8,
        hdr10_verified=kind in {
            TransitionKind.FADE,
            TransitionKind.DISSOLVE,
            TransitionKind.FADE_BLACK,
        },
    )
    for kind in TransitionKind
}


def transition_is_available(kind: TransitionKind, color_mode: ColorMode) -> bool:
    capability = TRANSITION_CAPABILITIES[kind]
    return color_mode != ColorMode.HDR10_BT2020_PQ or capability.hdr10_verified
