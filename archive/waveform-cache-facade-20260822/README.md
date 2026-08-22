# Waveform cache facade archive

This directory preserves the removed `mediaflow.infrastructure.waveform_cache`
compatibility facade because project policy does not permit deleting files without
explicit approval. The facade had no consumers; production code, tests and
verification scripts import the single implementation at `mediaflow.waveform_cache`.
