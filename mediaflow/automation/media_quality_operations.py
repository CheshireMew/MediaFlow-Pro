from __future__ import annotations

from mediaflow.automation.operation_context import OperationContext
from mediaflow.domain.reference_comparison import (
    ReferenceComparisonAcceptance,
    ReferenceComparisonResult,
)


def compare_reference(context: OperationContext) -> ReferenceComparisonResult:
    acceptance_document = context.arguments.get("acceptance")
    acceptance = (
        ReferenceComparisonAcceptance.model_validate(acceptance_document)
        if acceptance_document is not None
        else None
    )
    return context.application.reference_comparison.compare(
        reference_path=context.required("reference_path"),
        candidate_path=context.required("candidate_path"),
        output_dir=context.required("output_dir"),
        reference_start_frame=int(
            context.arguments.get("reference_start_frame", 0)
        ),
        candidate_start_frame=int(
            context.arguments.get("candidate_start_frame", 0)
        ),
        frame_count=context.arguments.get("frame_count"),
        temporal_search_radius_frames=int(
            context.arguments.get("temporal_search_radius_frames", 0)
        ),
        boundary_frame_count=int(
            context.arguments.get("boundary_frame_count", 3)
        ),
        contact_sheet_rows=int(context.arguments.get("contact_sheet_rows", 8)),
        acceptance=acceptance,
        overwrite=bool(context.arguments.get("overwrite", False)),
    )
