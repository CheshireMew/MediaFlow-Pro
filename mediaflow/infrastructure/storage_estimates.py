from __future__ import annotations

from mediaflow.infrastructure.storage_policy import (
    PROXY_AUDIO_BITRATE,
    PROXY_VIDEO_MAX_BITRATE,
)


def estimate_video_cache_bytes(
    width: int,
    height: int,
    frame_count: int,
    *,
    concurrent_copies: int = 2,
) -> int:
    values = (width, height, frame_count, concurrent_copies)
    if any(type(value) is not int or value <= 0 for value in values):
        raise ValueError("Video cache estimates require positive integer dimensions and counts")
    # Four BGRA bytes per pixel plus an equal allowance for lossless coding,
    # container/index data and partial/final coexistence. Do not assume that
    # compressed video will be smaller than its decoded pixels.
    return max(64 * 1024**2, width * height * frame_count * 8 * concurrent_copies)


def estimate_proxy_peak_bytes(
    duration_seconds: float,
    *,
    output_count: int = 1,
) -> int | None:
    if duration_seconds <= 0 or output_count <= 0:
        return None
    encoded = duration_seconds * (PROXY_VIDEO_MAX_BITRATE + PROXY_AUDIO_BITRATE) / 8
    container_headroom = encoded * 1.15
    return max(64 * 1024**2, int(container_headroom * output_count))


def estimate_download_peak_bytes(
    duration_seconds: float,
    *,
    media_kind: str,
    resolution: str,
) -> int | None:
    if duration_seconds <= 0:
        return None
    if media_kind == "audio" or resolution == "audio":
        payload_bitrate = 512_000
    else:
        normalized = resolution.strip().lower()
        height_rates = {
            2160: 120_000_000,
            1440: 60_000_000,
            1080: 30_000_000,
            720: 15_000_000,
            480: 8_000_000,
            360: 5_000_000,
        }
        if normalized == "best":
            payload_bitrate = height_rates[2160]
        else:
            aliases = {"4k": 2160, "2k": 1440}
            try:
                height = aliases[normalized] if normalized in aliases else int(normalized.removesuffix("p"))
            except ValueError:
                return None
            selected_height = min(height_rates, key=lambda item: abs(item - height))
            payload_bitrate = height_rates[selected_height]
        payload_bitrate += 512_000
    final_payload = duration_seconds * payload_bitrate / 8
    # Separate streams, merged output, subtitles and failure archival can coexist.
    return max(64 * 1024**2, int(final_payload * 4 + 64 * 1024**2))


def estimate_pcm_audio_peak_bytes(
    duration_seconds: float,
    *,
    sample_rate: int,
    channels: int = 1,
    concurrent_copies: int = 2,
) -> int | None:
    if duration_seconds <= 0 or sample_rate <= 0 or channels <= 0 or concurrent_copies <= 0:
        return None
    return max(
        16 * 1024**2,
        int(duration_seconds * sample_rate * channels * 2 * concurrent_copies),
    )
