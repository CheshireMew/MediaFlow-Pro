from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class WorkflowUpdate:
    selected_asset_ids: list[str] = field(default_factory=list)
    status_source: str = ""
    status_arguments: tuple[str, ...] = ()

    def merge(self, other: WorkflowUpdate) -> WorkflowUpdate:
        return WorkflowUpdate(
            selected_asset_ids=other.selected_asset_ids or self.selected_asset_ids,
            status_source=other.status_source or self.status_source,
            status_arguments=(
                other.status_arguments if other.status_source else self.status_arguments
            ),
        )
