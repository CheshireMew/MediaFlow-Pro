from __future__ import annotations

import math
import shlex


def validate_cube_lut(content: str) -> int:
    """Validate the deterministic subset of the Adobe/Iridas .cube format.

    MediaFlow currently adopts 3D LUTs only. The parser accepts comments, TITLE,
    DOMAIN_MIN/MAX and one LUT_3D_SIZE declaration, then proves that the table
    contains exactly size³ finite RGB triplets.
    """

    size: int | None = None
    row_count = 0
    for line_number, raw_line in enumerate(content.splitlines(), start=1):
        line = raw_line.partition("#")[0].strip()
        if not line:
            continue
        tokens = shlex.split(line)
        keyword = tokens[0].upper()
        if keyword == "TITLE":
            if len(tokens) != 2 or not tokens[1].strip():
                raise ValueError(f"LUT 第 {line_number} 行 TITLE 无效")
            continue
        if keyword == "LUT_3D_SIZE":
            if size is not None or len(tokens) != 2:
                raise ValueError("LUT 必须只声明一次 LUT_3D_SIZE")
            try:
                size = int(tokens[1])
            except ValueError as error:
                raise ValueError("LUT_3D_SIZE 必须是整数") from error
            if not 2 <= size <= 65:
                raise ValueError("LUT_3D_SIZE 必须介于 2 和 65")
            continue
        if keyword in {"DOMAIN_MIN", "DOMAIN_MAX"}:
            if len(tokens) != 4:
                raise ValueError(f"LUT 第 {line_number} 行 {keyword} 必须有三个数值")
            _finite_triplet(tokens[1:], line_number)
            continue
        if keyword.startswith("LUT_"):
            raise ValueError(f"当前不支持 LUT 指令：{tokens[0]}")
        if len(tokens) != 3:
            raise ValueError(f"LUT 第 {line_number} 行必须是 RGB 三元组")
        _finite_triplet(tokens, line_number)
        row_count += 1
    if size is None:
        raise ValueError("LUT 缺少 LUT_3D_SIZE")
    expected = size**3
    if row_count != expected:
        raise ValueError(f"LUT 数据行数量应为 {expected}，实际为 {row_count}")
    return size


def _finite_triplet(tokens: list[str], line_number: int) -> None:
    try:
        values = [float(value) for value in tokens]
    except ValueError as error:
        raise ValueError(f"LUT 第 {line_number} 行包含无效数值") from error
    if not all(math.isfinite(value) for value in values):
        raise ValueError(f"LUT 第 {line_number} 行包含非有限数值")
