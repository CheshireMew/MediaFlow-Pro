from __future__ import annotations

import re
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from mediaflow.domain.srt_time import format_srt_timestamp
from mediaflow.domain.timebase import frames_to_seconds, seconds_to_frames

_TIME_LINE = re.compile(
    r"^\s*(?P<start>(?:\d{1,3}:)?\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*"
    r"(?P<end>(?:\d{1,3}:)?\d{2}:\d{2}[,.]\d{1,3})(?:\s+.*)?$"
)


@dataclass(frozen=True, slots=True)
class SubtitleCue:
    start_frame: int
    end_frame: int
    text: str


class SubtitleFile:
    """Pure subtitle parsing, formatting, and language inference rules."""

    @classmethod
    def parse_srt(
        cls,
        content: str,
        *,
        fps_numerator: int,
        fps_denominator: int,
    ) -> list[SubtitleCue]:
        content = content.replace("\r\n", "\n").replace("\r", "\n")
        blocks = re.split(r"\n[ \t]*\n+", content.strip()) if content.strip() else []
        cues: list[SubtitleCue] = []
        for block_number, block in enumerate(blocks, start=1):
            lines = block.split("\n")
            if lines and lines[0].strip().isdigit():
                lines = lines[1:]
            if not lines:
                continue
            timing_index = next(
                (index for index, line in enumerate(lines) if _TIME_LINE.match(line)),
                None,
            )
            if timing_index is None:
                marker = lines[0].strip().upper()
                if marker.startswith(("WEBVTT", "NOTE", "STYLE", "REGION")):
                    continue
                raise ValueError(f"字幕第 {block_number} 段缺少有效时间轴")
            match = _TIME_LINE.match(lines[timing_index])
            assert match is not None
            text = cls._normalize_text("\n".join(lines[timing_index + 1 :]))
            if not text:
                raise ValueError(f"字幕第 {block_number} 段字幕为空")
            start_seconds = cls._parse_time(match.group("start"))
            end_seconds = cls._parse_time(match.group("end"))
            if end_seconds <= start_seconds:
                raise ValueError(f"字幕第 {block_number} 段结束时间必须晚于开始时间")
            start_frame = seconds_to_frames(
                start_seconds,
                fps_numerator,
                fps_denominator,
            )
            end_frame = max(
                start_frame + 1,
                seconds_to_frames(
                    end_seconds,
                    fps_numerator,
                    fps_denominator,
                ),
            )
            cues.append(SubtitleCue(start_frame=start_frame, end_frame=end_frame, text=text))
        if not cues:
            raise ValueError("字幕文件中没有可用字幕")
        return cues

    @classmethod
    def parse_ass(
        cls,
        content: str,
        *,
        fps_numerator: int,
        fps_denominator: int,
    ) -> list[SubtitleCue]:
        lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        in_events = False
        columns: list[str] = []
        cues: list[SubtitleCue] = []
        for line in lines:
            value = line.strip()
            if not value:
                continue
            if value.startswith("["):
                in_events = value.casefold() == "[events]"
                continue
            if not in_events:
                continue
            if value.casefold().startswith("format:"):
                columns = [item.strip().casefold() for item in value.split(":", 1)[1].split(",")]
                continue
            if not value.casefold().startswith("dialogue:") or not columns:
                continue
            try:
                start_index = columns.index("start")
                end_index = columns.index("end")
                text_index = columns.index("text")
            except ValueError as error:
                raise ValueError("ASS/SSA 的 Events Format 缺少 Start、End 或 Text") from error
            values = value.split(":", 1)[1].split(",", max(0, len(columns) - 1))
            if len(values) != len(columns):
                continue
            start_seconds = cls._parse_time(values[start_index].strip())
            end_seconds = cls._parse_time(values[end_index].strip())
            text = cls._normalize_text(values[text_index])
            if end_seconds <= start_seconds or not text:
                continue
            start_frame = seconds_to_frames(
                start_seconds,
                fps_numerator,
                fps_denominator,
            )
            cues.append(
                SubtitleCue(
                    start_frame=start_frame,
                    end_frame=max(
                        start_frame + 1,
                        seconds_to_frames(
                            end_seconds,
                            fps_numerator,
                            fps_denominator,
                        ),
                    ),
                    text=text,
                )
            )
        if not cues:
            raise ValueError("ASS/SSA 文件中没有可用 Dialogue 字幕")
        return cues

    @classmethod
    def dumps_srt(
        cls,
        cues: list[SubtitleCue],
        *,
        fps_numerator: int,
        fps_denominator: int,
    ) -> str:
        lines: list[str] = []
        for index, cue in enumerate(cues, start=1):
            if cue.end_frame <= cue.start_frame:
                raise ValueError("Subtitle cue must have a positive frame range")
            start = frames_to_seconds(cue.start_frame, fps_numerator, fps_denominator)
            end = frames_to_seconds(cue.end_frame, fps_numerator, fps_denominator)
            lines.extend(
                [
                    str(index),
                    f"{format_srt_timestamp(start)} --> {format_srt_timestamp(end)}",
                    cue.text,
                    "",
                ]
            )
        return "\n".join(lines)

    @staticmethod
    def infer_language(path: str | Path, fallback: str | None = None) -> str:
        tokens = re.split(r"[._\- ]+", Path(path).stem.lower())
        aliases = {
            "zh": "zh_CN",
            "cn": "zh_CN",
            "chs": "zh_CN",
            "zhcn": "zh_CN",
            "zh_cn": "zh_CN",
            "cht": "zh_TW",
            "zhtw": "zh_TW",
            "zh_tw": "zh_TW",
            "en": "en",
            "eng": "en",
            "ja": "ja",
            "jp": "ja",
            "jpn": "ja",
            "ko": "ko",
            "kor": "ko",
            "es": "es",
            "spa": "es",
        }
        for token in reversed(tokens):
            if token in aliases:
                return aliases[token]
        value = (fallback or "").strip()
        return value if value and value != "auto" else "und"

    @staticmethod
    def decode(data: bytes) -> str:
        for encoding in ("utf-8-sig", "utf-8", "gb18030", "utf-16"):
            try:
                return data.decode(encoding)
            except UnicodeDecodeError:
                continue
        raise ValueError("SRT 文件编码无法识别，请转换为 UTF-8、GB18030 或 UTF-16")

    @staticmethod
    def _parse_time(value: str) -> Fraction:
        parts = value.replace(",", ".").split(":")
        if len(parts) == 2:
            hours = "0"
            minutes, seconds = parts
        elif len(parts) == 3:
            hours, minutes, seconds = parts
        else:
            raise ValueError(f"无效字幕时间：{value}")
        whole_seconds, milliseconds = seconds.split(".")
        milliseconds = milliseconds.ljust(3, "0")[:3]
        total_ms = (
            int(hours) * 3_600_000 + int(minutes) * 60_000 + int(whole_seconds) * 1_000 + int(milliseconds)
        )
        return Fraction(total_ms, 1_000)

    @staticmethod
    def _normalize_text(value: str) -> str:
        text = re.sub(r"\{[^}]*\}", "", value)
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"\\[Nn]", "\n", text)
        return "\n".join(line.strip() for line in text.splitlines() if line.strip()).strip()
