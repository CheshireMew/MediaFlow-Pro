from __future__ import annotations

import argparse
import importlib.metadata
import json
import wave
from pathlib import Path

import numpy as np
import sherpa_onnx

CHUNK_SECONDS = 1.5
CHUNK_STEP_SECONDS = 0.75


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--num-threads", type=int, default=4)
    parser.add_argument("--threshold", type=float, default=0.4)
    parser.add_argument("--minimum-speakers", type=int)
    parser.add_argument("--maximum-speakers", type=int)
    return parser.parse_args()


def read_wave(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as source:
        if source.getnchannels() != 1 or source.getsampwidth() != 2:
            raise ValueError("输入必须是单声道 16-bit PCM WAV")
        sample_rate = source.getframerate()
        samples = np.frombuffer(
            source.readframes(source.getnframes()),
            dtype="<i2",
        ).astype(np.float32)
    if sample_rate <= 0 or samples.size == 0:
        raise ValueError("输入 WAV 没有有效音频")
    return np.ascontiguousarray(samples / 32768.0), sample_rate


def interval_chunks(samples: np.ndarray, sample_rate: int) -> list[np.ndarray]:
    chunk_size = max(1, round(CHUNK_SECONDS * sample_rate))
    step_size = max(1, round(CHUNK_STEP_SECONDS * sample_rate))
    if samples.size < chunk_size:
        if samples.size == 0:
            raise ValueError("转写片段没有音频样本")
        return [np.ascontiguousarray(np.resize(samples, chunk_size), dtype=np.float32)]
    starts = list(range(0, samples.size - chunk_size + 1, step_size))
    final_start = samples.size - chunk_size
    if starts[-1] != final_start:
        starts.append(final_start)
    return [
        np.ascontiguousarray(samples[start : start + chunk_size], dtype=np.float32)
        for start in starts
    ]


def normalized(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    if not np.isfinite(norm) or norm <= 1e-8:
        raise RuntimeError("3D-Speaker 返回了无效音色向量")
    return vector / norm


def extract_interval_embedding(
    extractor: sherpa_onnx.SpeakerEmbeddingExtractor,
    samples: np.ndarray,
    sample_rate: int,
) -> np.ndarray:
    embeddings: list[np.ndarray] = []
    for chunk in interval_chunks(samples, sample_rate):
        stream = extractor.create_stream()
        stream.accept_waveform(sample_rate=sample_rate, waveform=chunk)
        stream.input_finished()
        if not extractor.is_ready(stream):
            raise RuntimeError("音频片段太短，无法提取音色")
        embeddings.append(normalized(np.asarray(extractor.compute(stream), dtype=np.float32)))
    return normalized(np.mean(np.stack(embeddings), axis=0))


def average_similarity(
    similarity: np.ndarray,
    left: list[int],
    right: list[int],
) -> float:
    return float(np.mean(similarity[np.ix_(left, right)]))


def cluster_embeddings(
    embeddings: np.ndarray,
    *,
    threshold: float,
    minimum_speakers: int | None,
    maximum_speakers: int | None,
) -> list[int]:
    count = embeddings.shape[0]
    minimum = min(count, minimum_speakers or 1)
    maximum = min(count, maximum_speakers or count)
    if minimum > maximum:
        raise ValueError("最小说话人数不能大于最大说话人数")
    similarity = np.clip(embeddings @ embeddings.T, -1.0, 1.0)
    clusters = [[index] for index in range(count)]
    while len(clusters) > minimum:
        best_pair: tuple[int, int] | None = None
        best_score = -2.0
        for left in range(len(clusters)):
            for right in range(left + 1, len(clusters)):
                score = average_similarity(
                    similarity,
                    clusters[left],
                    clusters[right],
                )
                if score > best_score:
                    best_score = score
                    best_pair = (left, right)
        if best_pair is None:
            break
        if len(clusters) <= maximum and best_score < threshold:
            break
        left, right = best_pair
        clusters[left] = sorted([*clusters[left], *clusters[right]])
        clusters.pop(right)
    clusters.sort(key=lambda values: min(values))
    labels = [0] * count
    for label, members in enumerate(clusters):
        for index in members:
            labels[index] = label
    return labels


def main() -> None:
    args = parse_args()
    source = Path(args.input).resolve(strict=True)
    model = Path(args.model).resolve(strict=True)
    request = json.loads(Path(args.request).read_text(encoding="utf-8"))
    if request.get("schema_version") != 1:
        raise ValueError("不支持的音色聚类请求版本")
    intervals = list(request.get("intervals") or ())
    if not intervals:
        raise ValueError("音色聚类请求没有转写片段")
    samples, sample_rate = read_wave(source)
    config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
        model=str(model),
        num_threads=args.num_threads,
        debug=False,
        provider="cpu",
    )
    if not config.validate():
        raise ValueError(f"无效的 3D-Speaker 模型配置：{model}")
    extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)
    embeddings: list[np.ndarray] = []
    for item in intervals:
        start_seconds = float(item["start_seconds"])
        end_seconds = float(item["end_seconds"])
        start_sample = max(0, round(start_seconds * sample_rate))
        end_sample = min(samples.size, round(end_seconds * sample_rate))
        if end_sample <= start_sample:
            raise ValueError(
                f"转写片段超出音频范围：{start_seconds:.3f}-{end_seconds:.3f}"
            )
        embeddings.append(
            extract_interval_embedding(
                extractor,
                samples[start_sample:end_sample],
                sample_rate,
            )
        )
    labels = cluster_embeddings(
        np.stack(embeddings),
        threshold=args.threshold,
        minimum_speakers=args.minimum_speakers,
        maximum_speakers=args.maximum_speakers,
    )
    payload = {
        "schema_version": 1,
        "engine": "3D-Speaker CAM++ via sherpa-onnx",
        "engine_version": importlib.metadata.version("sherpa-onnx"),
        "model": model.name,
        "device": "cpu",
        "exclusive": True,
        "turns": [
            {
                "speaker": f"SPEAKER_{label:02d}",
                "start_seconds": float(item["start_seconds"]),
                "end_seconds": float(item["end_seconds"]),
            }
            for item, label in zip(intervals, labels, strict=True)
        ],
    }
    Path(args.output).write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
