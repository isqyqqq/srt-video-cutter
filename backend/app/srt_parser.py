"""SRT 字幕解析。

只负责把 SRT 文本解析成结构化条目，不做业务校验。
时间戳是后续剪辑的唯一依据，文案仅用于界面展示。
"""

from __future__ import annotations

from dataclasses import dataclass
import re


class SrtParseError(ValueError):
    """SRT 无法解析时抛出的异常。"""


# 兼容 00:00:01,000 与 00:00:01.000 两种毫秒分隔符
_TIME_RANGE_RE = re.compile(
    r"^\s*(\d{1,3}):(\d{2}):(\d{2})[,.](\d{1,3})"
    r"\s*-->\s*"
    r"(\d{1,3}):(\d{2}):(\d{2})[,.](\d{1,3})"
)


@dataclass
class RawCue:
    """SRT 中的一条字幕。index 为在文件中的顺序（从 1 开始）。"""

    index: int
    source_index: int | None
    start: float
    end: float
    text: str


def decode_srt(data: bytes) -> tuple[str, str]:
    """按常见编码依次尝试解码，返回 (文本, 实际编码)。"""

    if data.startswith(b"\xef\xbb\xbf"):
        try:
            return data.decode("utf-8-sig"), "utf-8-sig"
        except UnicodeDecodeError:
            pass
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return data.decode("utf-16"), "utf-16"
        except UnicodeDecodeError:
            pass

    # 先试 UTF-8，失败后再用 GB18030 兜底（兼容 GBK/GB2312）
    for encoding in ("utf-8", "gb18030"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue

    raise SrtParseError("无法识别 SRT 编码，请另存为 UTF-8 后重试")


def _parse_timestamp(hours: str, minutes: str, seconds: str, millis: str) -> float:
    """把字幕时间转换成秒。"""

    return (
        int(hours) * 3600
        + int(minutes) * 60
        + int(seconds)
        + int(millis.ljust(3, "0")) / 1000.0
    )


def parse_srt_text(text: str) -> list[RawCue]:
    """解析 SRT 文本，返回按文件顺序排列的字幕条目。"""

    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        raise SrtParseError("SRT 文件是空的")

    blocks = [block for block in re.split(r"\n\s*\n", normalized) if block.strip()]

    # 兼容不规范 SRT：条目之间没有空行时，按「序号 + 时间轴」重新切块
    if len(blocks) < normalized.count("-->"):
        blocks = [
            chunk
            for chunk in re.split(
                r"\n(?=\s*(?:\d+\s*\n\s*)?\d{1,3}:\d{2}:\d{2}[,.]\d{1,3}\s*-->)",
                normalized,
            )
            if chunk.strip()
        ]
    cues: list[RawCue] = []

    for block_number, block in enumerate(blocks, start=1):
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        if not lines:
            continue

        time_line_index = next(
            (i for i, line in enumerate(lines) if "-->" in line), None
        )
        if time_line_index is None:
            # 没有时间轴的行通常是文件头或空块，直接跳过
            continue

        match = _TIME_RANGE_RE.match(lines[time_line_index])
        if not match:
            snippet = lines[time_line_index][:80]
            raise SrtParseError(
                f"第 {block_number} 个字幕块的时间格式无法识别：{snippet}"
            )

        start = _parse_timestamp(*match.group(1, 2, 3, 4))
        end = _parse_timestamp(*match.group(5, 6, 7, 8))

        source_index: int | None = None
        if time_line_index > 0 and lines[0].isdigit():
            source_index = int(lines[0])

        cue_text = " ".join(lines[time_line_index + 1 :]).strip()
        cues.append(
            RawCue(
                index=len(cues) + 1,
                source_index=source_index,
                start=start,
                end=end,
                text=cue_text,
            )
        )

    if not cues:
        raise SrtParseError("没有解析到任何字幕条目，请检查 SRT 格式")

    return cues


def parse_srt_bytes(data: bytes) -> tuple[list[RawCue], str]:
    """解析 SRT 字节内容，返回 (字幕列表, 实际编码)。"""

    text, encoding = decode_srt(data)
    return parse_srt_text(text), encoding
