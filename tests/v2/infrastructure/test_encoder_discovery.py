from __future__ import annotations

from mediaflow.infrastructure.encoder_discovery import EncoderDiscoveryService


def test_actual_ffmpeg_encoder_catalog_drives_export_options() -> None:
    options = EncoderDiscoveryService().video_options()
    available = {item["value"] for item in options}

    assert {"libx264", "libx265", "libsvtav1", "prores_ks"} <= available
    assert all(item["formats"] for item in options)
    assert all(item["labelKey"] for item in options)
