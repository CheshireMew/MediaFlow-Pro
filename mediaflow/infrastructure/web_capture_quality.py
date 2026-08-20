from __future__ import annotations

import math
import struct

from mediaflow.infrastructure.web_capture_models import _FastCaptureComparison

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _decode_png_bgra(payload: bytes):
    import cv2
    import numpy as np

    image = cv2.imdecode(
        np.frombuffer(payload, dtype=np.uint8),
        cv2.IMREAD_UNCHANGED,
    )
    if image is None:
        return None
    if image.ndim == 2:
        return cv2.cvtColor(image, cv2.COLOR_GRAY2BGRA)
    if image.shape[2] == 3:
        return cv2.cvtColor(image, cv2.COLOR_BGR2BGRA)
    return image


def _compare_fast_capture(left: bytes, right: bytes) -> _FastCaptureComparison:
    import cv2
    import numpy as np

    left_image = _decode_png_bgra(left)
    right_image = _decode_png_bgra(right)
    if left_image is None or right_image is None:
        return _FastCaptureComparison(
            psnr_db=0.0,
            blurred_psnr_db=0.0,
            mean_absolute_error=float("inf"),
            blurred_channel_error=255,
            alpha_equal=False,
        )
    if left_image.shape != right_image.shape:
        return _FastCaptureComparison(
            psnr_db=0.0,
            blurred_psnr_db=0.0,
            mean_absolute_error=float("inf"),
            blurred_channel_error=255,
            alpha_equal=False,
        )
    difference = cv2.absdiff(left_image, right_image)
    blurred_left = cv2.GaussianBlur(left_image, (5, 5), 1.2)
    blurred_right = cv2.GaussianBlur(right_image, (5, 5), 1.2)
    blurred_difference = cv2.absdiff(blurred_left, blurred_right)
    return _FastCaptureComparison(
        psnr_db=float(cv2.PSNR(left_image, right_image)),
        blurred_psnr_db=float(cv2.PSNR(blurred_left, blurred_right)),
        mean_absolute_error=float(np.mean(difference[:, :, :3])),
        blurred_channel_error=int(np.max(blurred_difference[:, :, :3])),
        alpha_equal=bool(np.array_equal(left_image[:, :, 3], right_image[:, :, 3])),
    )


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1))
    return ordered[index]


def _validate_png(payload: bytes, width: int, height: int) -> None:
    if len(payload) < 24 or payload[:8] != _PNG_SIGNATURE or payload[12:16] != b"IHDR":
        raise RuntimeError("Capture backend returned an invalid PNG frame")
    actual_width, actual_height = struct.unpack(">II", payload[16:24])
    if (actual_width, actual_height) != (width, height):
        raise RuntimeError(
            "Capture backend returned the wrong frame size: "
            f"{actual_width}x{actual_height}, expected {width}x{height}"
        )


def _fast_capture_sample_indices(
    *,
    frame_count: int,
    worker_count: int,
) -> tuple[int, ...]:
    if frame_count <= 0 or worker_count <= 0:
        raise ValueError("Editable media capture needs positive frame and worker counts")
    samples: set[int] = set()
    for worker_index in range(min(frame_count, worker_count)):
        start = frame_count * worker_index // worker_count
        end = frame_count * (worker_index + 1) // worker_count
        samples.add(start)
        samples.add(start + round(max(0, end - start - 1) * 0.95))
    target_count = min(frame_count, 4 + 2 * max(0, worker_count - 1))
    for fraction in (0.25, 0.5, 0.75, 0.95, 1.0):
        if len(samples) >= target_count:
            break
        samples.add(round((frame_count - 1) * fraction))
    if len(samples) < target_count:
        for frame_index in range(frame_count):
            samples.add(frame_index)
            if len(samples) >= target_count:
                break
    return tuple(sorted(samples))
