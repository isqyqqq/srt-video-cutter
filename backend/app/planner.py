"""切点规划。

核心规则：
1. 以 SRT 时间戳为准，按条目顺序输出；
2. 默认不加前后余量，只做整帧对齐；
3. 如果用户设置了余量，相邻条目间隔不足时按中点共享切点，避免重复；
4. 起点向下取整、终点向上取整，保证不丢帧。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math

from .srt_parser import RawCue


class PlanError(ValueError):
    """切点规划失败，errors 中保存具体原因。"""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("；".join(errors))


@dataclass
class PlanItem:
    index: int
    source_index: int | None
    text: str
    srt_start: float
    srt_end: float
    start: float
    end: float
    frame_start: int
    frame_end: int
    duration: float
    output_name: str

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "source_index": self.source_index,
            "text": self.text,
            "srt_start": round(self.srt_start, 6),
            "srt_end": round(self.srt_end, 6),
            "start": round(self.start, 6),
            "end": round(self.end, 6),
            "frame_start": self.frame_start,
            "frame_end": self.frame_end,
            "duration": round(self.duration, 6),
            "output_name": self.output_name,
        }


@dataclass
class PlanResult:
    items: list[PlanItem]
    warnings: list[str]
    total_duration: float
    total_frames: int
    fps: float
    video_duration: float
    pad_before: float
    pad_after: float
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "items": [item.to_dict() for item in self.items],
            "warnings": self.warnings,
            "total_duration": round(self.total_duration, 6),
            "total_frames": self.total_frames,
            "fps": self.fps,
            "video_duration": round(self.video_duration, 6),
            "pad_before": self.pad_before,
            "pad_after": self.pad_after,
            **self.extra,
        }


def format_tc(seconds: float) -> str:
    """把秒格式化为 HH:MM:SS.mmm，用于界面展示。"""

    if seconds < 0:
        seconds = 0
    total_ms = int(round(seconds * 1000))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def format_filename_tc(seconds: float) -> str:
    """把秒格式化为文件名可用的 HH-MM-SS.mmm。"""

    return format_tc(seconds).replace(":", "-")


def _dedupe(messages: list[str]) -> list[str]:
    """去重但保持原顺序。"""

    seen: set[str] = set()
    result: list[str] = []
    for message in messages:
        if message not in seen:
            seen.add(message)
            result.append(message)
    return result


def build_plan(
    cues: list[RawCue],
    video_duration: float,
    fps: float,
    pad_before: float = 0.0,
    pad_after: float = 0.0,
) -> PlanResult:
    """根据字幕和视频信息计算每个片段的实际切点。"""

    if not cues:
        raise PlanError(["没有可用的字幕条目"])
    if video_duration <= 0:
        raise PlanError(["视频时长无效，无法规划切点"])
    if fps <= 0:
        raise PlanError(["视频帧率无效，无法规划切点"])
    if pad_before < 0 or pad_after < 0:
        raise PlanError(["前后余量不能为负数"])
    if pad_before > 5 or pad_after > 5:
        raise PlanError(["前后余量不能超过 5 秒"])

    errors: list[str] = []
    warnings: list[str] = []
    work: list[dict] = []

    for cue in cues:
        start = cue.start
        end = cue.end
        label = f"第 {cue.index} 条字幕"

        if end <= start + 1e-6:
            errors.append(f"{label} 的结束时间不大于开始时间（{start:.3f}s → {end:.3f}s）")
            continue
        if start < -1e-6:
            warnings.append(f"{label} 开始时间小于 0，已按 0 处理")
            start = 0.0
        if start >= video_duration - 1e-6:
            errors.append(
                f"{label} 开始时间 {start:.3f}s 超出视频时长 {video_duration:.3f}s"
            )
            continue
        if end > video_duration:
            overflow = end - video_duration
            if overflow > 0.25:
                errors.append(
                    f"{label} 结束时间 {end:.3f}s 超出视频时长 "
                    f"{video_duration:.3f}s（超出 {overflow:.3f}s）"
                )
                continue
            warnings.append(
                f"{label} 结束时间超出视频末尾 {overflow:.3f}s，已截断到视频结尾"
            )
            end = video_duration

        work.append(
            {
                "cue": cue,
                "start": start,
                "end": end,
                "planned_start": max(0.0, start - pad_before),
                "planned_end": min(video_duration, end + pad_after),
            }
        )

    if errors:
        raise PlanError(errors)

    # 按原片时间排序，找出时间上相邻的条目
    by_time = sorted(work, key=lambda item: (item["start"], item["cue"].index))
    padding_total = pad_before + pad_after
    shared_boundaries: list[tuple[int, int, float]] = []

    for left, right in zip(by_time, by_time[1:]):
        gap = right["start"] - left["end"]
        left_label = f"第 {left['cue'].index} 条"
        right_label = f"第 {right['cue'].index} 条"

        if gap < -1e-6:
            warnings.append(
                f"{left_label} 与 {right_label} 在原片时间上重叠 "
                f"{abs(gap):.3f}s，成片会重复播放重叠部分"
            )
        elif gap < padding_total - 1e-6 and padding_total > 0:
            # 间隔不够放两侧余量，两条在间隔中点碰头
            midpoint = left["end"] + gap / 2.0
            left["planned_end"] = min(left["planned_end"], midpoint)
            right["planned_start"] = max(right["planned_start"], midpoint)
            shared_boundaries.append(
                (left["cue"].index, right["cue"].index, midpoint)
            )

    # 重复时间段提示
    seen_ranges: dict[tuple[int, int], int] = {}
    for item in work:
        key = (round(item["start"] * 1000), round(item["end"] * 1000))
        previous = seen_ranges.get(key)
        if previous is not None:
            warnings.append(
                f"第 {previous} 条与第 {item['cue'].index} 条时间范围完全相同，将重复输出"
            )
        else:
            seen_ranges[key] = item["cue"].index

    total_video_frames = max(1, int(round(video_duration * fps)))
    items: list[PlanItem] = []

    for item in work:
        cue: RawCue = item["cue"]
        frame_start = int(math.floor(item["planned_start"] * fps + 1e-6))
        frame_end = int(math.ceil(item["planned_end"] * fps - 1e-6))
        frame_start = max(0, min(frame_start, total_video_frames - 1))
        frame_end = max(0, min(frame_end, total_video_frames))

        if frame_end <= frame_start:
            frame_end = min(total_video_frames, frame_start + 1)
            warnings.append(
                f"第 {cue.index} 条字幕时长不足 1 帧，已补足为 1 帧"
            )

        items.append(
            PlanItem(
                index=cue.index,
                source_index=cue.source_index,
                text=cue.text,
                srt_start=item["start"],
                srt_end=item["end"],
                start=frame_start / fps,
                end=frame_end / fps,
                frame_start=frame_start,
                frame_end=frame_end,
                duration=(frame_end - frame_start) / fps,
                output_name="",
            )
        )

    # 共享切点统一到同一条帧边界，避免相邻片段重复一帧
    item_by_index = {item.index: item for item in items}
    for left_index, right_index, midpoint in shared_boundaries:
        left = item_by_index.get(left_index)
        right = item_by_index.get(right_index)
        if not left or not right:
            continue

        shared_frame = int(round(midpoint * fps))
        if shared_frame <= left.frame_start or shared_frame >= right.frame_end:
            warnings.append(
                f"第 {left_index}、{right_index} 条字幕间隔过小，无法共享切点，已保留各自边界"
            )
            continue
        left.frame_end = shared_frame
        right.frame_start = shared_frame
        left.end = shared_frame / fps
        left.duration = (left.frame_end - left.frame_start) / fps
        right.start = shared_frame / fps
        right.duration = (right.frame_end - right.frame_start) / fps

    for item in items:
        item.output_name = (
            f"{item.index:03d}_"
            f"{format_filename_tc(item.start)}-"
            f"{format_filename_tc(item.end)}.mp4"
        )

    items.sort(key=lambda item: item.index)
    total_duration = sum(item.duration for item in items)
    total_frames = sum(item.frame_end - item.frame_start for item in items)

    return PlanResult(
        items=items,
        warnings=_dedupe(warnings),
        total_duration=total_duration,
        total_frames=total_frames,
        fps=fps,
        video_duration=video_duration,
        pad_before=pad_before,
        pad_after=pad_after,
    )
