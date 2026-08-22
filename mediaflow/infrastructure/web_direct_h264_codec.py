from __future__ import annotations

import base64
import threading
from fractions import Fraction

from .web_direct_h264_models import DirectH264FallbackRequired, EncodedChunk
from .web_render_target import WebRenderTarget

MAX_ENCODE_QUEUE_SIZE = 4
MAX_PENDING_WRITES = 4

_H264_LEVELS: tuple[tuple[int, int, str], ...] = (
    # maximum macroblocks per frame, maximum macroblocks per second, codec
    (8_192, 245_760, "avc1.4D0028"),  # Main Profile, Level 4.0 (1080p30)
    (8_704, 522_240, "avc1.64002A"),  # High Profile, Level 4.2 (1080p60)
    (22_080, 589_824, "avc1.640032"),  # High Profile, Level 5.0
    (36_864, 983_040, "avc1.640033"),  # High Profile, Level 5.1 (4K30)
    (36_864, 2_073_600, "avc1.640034"),  # High Profile, Level 5.2 (4K60)
)


def select_h264_codec(
    width: int,
    height: int,
    fps_numerator: int,
    fps_denominator: int,
) -> str:
    """Choose the lowest H.264 level that can carry the requested frame clock."""

    if min(width, height, fps_numerator, fps_denominator) <= 0:
        raise ValueError("H.264 dimensions and frame rate must be positive")
    macroblocks_per_frame = ((width + 15) // 16) * ((height + 15) // 16)
    macroblocks_per_second = Fraction(
        macroblocks_per_frame * fps_numerator,
        fps_denominator,
    )
    for maximum_frame, maximum_rate, codec in _H264_LEVELS:
        if (
            macroblocks_per_frame <= maximum_frame
            and macroblocks_per_second <= maximum_rate
        ):
            return codec
    raise DirectH264FallbackRequired(
        "The requested frame size and rate exceed H.264 Level 5.2"
    )


class BoundedChunkSink:
    def __init__(self, stream) -> None:
        self._stream = stream
        self._lock = threading.Lock()
        self.chunks: list[EncodedChunk] = []
        self.bytes_written = 0

    def write(self, payload: object) -> None:
        if not isinstance(payload, dict):
            raise ValueError("WebCodecs returned an invalid encoded chunk envelope")
        try:
            index = int(payload["index"])
            timestamp = int(payload["timestamp"])
            duration = int(payload["duration"])
            kind = str(payload["type"])
            encoded = payload["data"]
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("WebCodecs returned incomplete encoded chunk metadata") from error
        if index < 0 or timestamp < 0 or duration <= 0 or kind not in {"key", "delta"}:
            raise ValueError("WebCodecs returned invalid encoded chunk metadata")
        if not isinstance(encoded, str):
            raise ValueError("WebCodecs returned an invalid encoded chunk payload")
        data = base64.b64decode(encoded, validate=True)
        if not data:
            raise ValueError("WebCodecs returned an empty encoded chunk")
        with self._lock:
            if index != len(self.chunks):
                raise ValueError("WebCodecs encoded chunk order is not deterministic")
            self._stream.write(data)
            self.bytes_written += len(data)
            self.chunks.append(
                EncodedChunk(
                    timestamp=timestamp,
                    duration=duration,
                    kind=kind,
                    size=len(data),
                )
            )


INITIALIZE_ENCODER = """
async ({config, maximumEncodeQueueSize, maximumPendingWrites}) => {
    if (typeof VideoEncoder !== "function" || typeof VideoFrame !== "function") {
        return {supported: false, reason: "WebCodecs video encoding is unavailable"};
    }
    const support = await VideoEncoder.isConfigSupported(config);
    if (!support.supported) {
        return {
            supported: false,
            reason: "Chromium rejected the requested encoder config",
            support,
        };
    }
    const state = {
        errors: [],
        chunkCount: 0,
        encodedBytes: 0,
        pendingWrites: 0,
        maximumEncodeQueueSize,
        maximumPendingWrites,
        maximumObservedEncodeQueueSize: 0,
        maximumObservedPendingWrites: 0,
        writeChain: Promise.resolve(),
    };
    const toBase64 = bytes => {
        let binary = "";
        const step = 0x8000;
        for (let offset = 0; offset < bytes.length; offset += step) {
            binary += String.fromCharCode(...bytes.subarray(offset, offset + step));
        }
        return btoa(binary);
    };
    const encoder = new VideoEncoder({
        output: chunk => {
            const bytes = new Uint8Array(chunk.byteLength);
            chunk.copyTo(bytes);
            const packet = {
                index: state.chunkCount,
                timestamp: chunk.timestamp,
                duration: chunk.duration,
                type: chunk.type,
                data: toBase64(bytes),
            };
            state.chunkCount += 1;
            state.encodedBytes += bytes.length;
            state.pendingWrites += 1;
            state.maximumObservedPendingWrites = Math.max(
                state.maximumObservedPendingWrites,
                state.pendingWrites,
            );
            state.writeChain = state.writeChain
                .then(() => window.__mediaflowWriteEncodedChunk(packet))
                .catch(error => state.errors.push(`encoded chunk write failed: ${String(error)}`))
                .finally(() => { state.pendingWrites -= 1; });
        },
        error: error => state.errors.push(`VideoEncoder failed: ${String(error)}`),
    });
    encoder.configure(support.config);
    state.encoder = encoder;
    state.config = support.config;
    window.__mediaflowDirectH264 = state;
    return {supported: true, config: support.config};
}
"""

ENCODE_CURRENT_CANVAS = """
async ({timestamp, duration, keyFrame, width, height}) => {
    const state = window.__mediaflowDirectH264;
    const canvas = document.getElementById("__mediaflow_capture_canvas");
    const root = document.querySelector("[data-composition-id]");
    const context = canvas?.getContext("2d");
    if (!state?.encoder || !canvas || !root || !context) {
        throw new Error("WebCodecs direct H.264 capture canvas is not initialized");
    }
    await new Promise((resolve, reject) => {
        let settled = false;
        const draw = () => {
            if (settled) return;
            settled = true;
            try {
                context.clearRect(0, 0, width, height);
                let background = "";
                for (let element = root.parentElement; element; element = element.parentElement) {
                    if (element === canvas) continue;
                    const color = getComputedStyle(element).backgroundColor;
                    if (color && color !== "transparent" && color !== "rgba(0, 0, 0, 0)") {
                        background = color;
                        break;
                    }
                }
                if (background) {
                    context.fillStyle = background;
                    context.fillRect(0, 0, width, height);
                }
                context.drawElementImage(root, 0, 0);
                resolve();
            } catch (error) {
                reject(error);
            }
        };
        const onPaint = () => {
            canvas.removeEventListener("paint", onPaint);
            draw();
        };
        canvas.addEventListener("paint", onPaint);
        window.__mediaflowInvalidateCapture?.();
        // drawElementImage is synchronous after __hf seek has forced style and
        // layout. Avoid headless setTimeout(0), which can be clamped to 250 ms.
        canvas.removeEventListener("paint", onPaint);
        draw();
    });
    const frame = new VideoFrame(canvas, {timestamp, duration});
    try {
        state.encoder.encode(frame, {keyFrame});
    } finally {
        frame.close();
    }
    state.maximumObservedEncodeQueueSize = Math.max(
        state.maximumObservedEncodeQueueSize,
        state.encoder.encodeQueueSize,
    );
    if (state.encoder.encodeQueueSize >= state.maximumEncodeQueueSize) {
        await state.encoder.flush();
    }
    if (state.pendingWrites >= state.maximumPendingWrites) {
        await state.writeChain;
    }
    if (state.errors.length) throw new Error(state.errors.join("; "));
    return {
        encodeQueueSize: state.encoder.encodeQueueSize,
        pendingWrites: state.pendingWrites,
        maximumObservedEncodeQueueSize: state.maximumObservedEncodeQueueSize,
        maximumObservedPendingWrites: state.maximumObservedPendingWrites,
    };
}
"""

FINISH_ENCODER = """
async () => {
    const state = window.__mediaflowDirectH264;
    if (!state?.encoder) throw new Error("WebCodecs direct H.264 encoder is missing");
    await state.encoder.flush();
    await state.writeChain;
    state.encoder.close();
    if (state.errors.length) throw new Error(state.errors.join("; "));
    return {
        chunkCount: state.chunkCount,
        encodedBytes: state.encodedBytes,
        config: state.config,
        maximumObservedEncodeQueueSize: state.maximumObservedEncodeQueueSize,
        maximumObservedPendingWrites: state.maximumObservedPendingWrites,
    };
}
"""


def round_microseconds(frame_index: int, fps_numerator: int, fps_denominator: int) -> int:
    value = Fraction(frame_index * 1_000_000 * fps_denominator, fps_numerator)
    return (2 * value.numerator + value.denominator) // (2 * value.denominator)


def encoder_bitrate(target: WebRenderTarget) -> int:
    pixel_rate = Fraction(
        target.width * target.height * target.fps_numerator,
        target.fps_denominator,
    )
    return max(4_000_000, min(80_000_000, round(float(pixel_rate) * 0.25)))


def validate_encoded_chunks(sink: BoundedChunkSink, target: WebRenderTarget) -> None:
    if len(sink.chunks) != target.frame_count:
        raise DirectH264FallbackRequired(
            "WebCodecs output frame count mismatch: "
            f"expected={target.frame_count}, actual={len(sink.chunks)}"
        )
    for index, chunk in enumerate(sink.chunks):
        timestamp = round_microseconds(index, target.fps_numerator, target.fps_denominator)
        next_timestamp = round_microseconds(
            index + 1,
            target.fps_numerator,
            target.fps_denominator,
        )
        if chunk.timestamp != timestamp or chunk.duration != next_timestamp - timestamp:
            raise DirectH264FallbackRequired(
                "WebCodecs changed the exact frame clock: "
                f"frame={index}, timestamp={chunk.timestamp}, duration={chunk.duration}"
            )
    if sink.chunks[0].kind != "key":
        raise DirectH264FallbackRequired("WebCodecs did not start the stream with a key frame")
