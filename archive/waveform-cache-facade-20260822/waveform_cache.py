"""Archived compatibility facade; runtime imports use ``mediaflow.waveform_cache``."""

from mediaflow.waveform_cache import (  # noqa: F401
    WAVEFORM_CACHE_SUFFIX,
    WAVEFORM_CACHE_VERSION,
    WaveformCacheHeader,
    WaveformLevel,
    inspect_waveform_cache,
    read_waveform_peaks,
    waveform_cache_is_current,
    waveform_cache_size,
    write_waveform_cache,
)
