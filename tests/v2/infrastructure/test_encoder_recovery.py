from __future__ import annotations

from mediaflow.infrastructure.mlt.export_service import MltExportService


def test_only_hardware_encoder_failures_are_recoverable() -> None:
    assert MltExportService._hardware_failure_reason(
        "h264_nvenc",
        "No NVENC capable devices found",
    )
    assert MltExportService._hardware_failure_reason(
        "h264_qsv",
        "Error initializing an MFX session: MFX_ERR_NOT_FOUND",
    )
    assert MltExportService._hardware_failure_reason(
        "h264_amf",
        "AMF_NOT_SUPPORTED while initializing the encoder",
    )
    assert (
        MltExportService._hardware_failure_reason(
            "h264_nvenc",
            "No space left on device",
        )
        is None
    )
    assert (
        MltExportService._hardware_failure_reason(
            "libx264",
            "Unknown encoder",
        )
        is None
    )
