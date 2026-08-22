from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RecentProjectSnapshot:
    items: list[dict]
    totals: dict[str, int]
