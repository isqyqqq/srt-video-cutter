"""FFmpeg / FFprobe 封装。

所有外部进程都在这里启动，便于统一处理进度、日志和取消。
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import subprocess
import threading
import time
from typing import Callable


class FFmpegError(RuntimeError):
    """FFmpeg 执行失败。"""


class CancelledError(RuntimeError):
    """任务被用户取消。"""


QUALITY_PRESETS: dict[str, dict[str, str]] = {
    "high": {"preset": "medium", "crf": "18"},
    "standard": {"preset": "veryfast", "crf": "20"},
}


@dataclass
class VideoInfo:
    """ffprobe 提取出的视频关键信息。"""

    duration: float
    fps: float
    width: int
    height: int
    display_width: int
    display_height: int
    rotation: int
    codec: str
    has_audio: bool
    audio_codec: str | None
    sample_rate: int
    channels: int
    size: int
    nb_frames: int
    is_vfr: bool
    bit_rate: int

    def to_dict(self) -> dict:
        return {
            "duration": round(self.duration, 6),
            "fps": round(self.fps, 6),
            "width": self.width,
            "height": self.height,
            "display_width": self.display_width,
            "display_height": self.display_height,
            "rotation": self.rotation,
            "codec": self.codec,
            "has_audio": self.has_audio,
            "audio_codec": self.audio_codec,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "size": self.size,
            "nb_frames": self.nb_frames,
            "is_vfr": self.is_vfr,
            "bit_rate": self.bit_rate,
        }


def _parse_rate(value: str | None) -> float:
    """解析 ffprobe 的分数帧率。"""

    if not value:
        return 0.0
    try:
        if "/" in value:
            numerator, denominator = value.split("/", 1)
            denominator_value = float(denominator)
            if denominator_value == 0:
                return 0.0
            return float(numerator) / denominator_value
        return float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def probe_video(path: Path) -> VideoInfo:
    """调用 ffprobe 获取视频信息。"""

    command = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise FFmpegError(f"读取视频信息失败：{result.stderr.strip()[:500]}")

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise FFmpegError("ffprobe 返回的数据无法解析") from exc

    video_stream = next(
        (stream for stream in data.get("streams", []) if stream.get("codec_type") == "video"),
        None,
    )
    audio_stream = next(
        (stream for stream in data.get("streams", []) if stream.get("codec_type") == "audio"),
        None,
    )
    if not video_stream:
        raise FFmpegError("视频中没有找到画面流")

    format_info = data.get("format", {})
    duration = float(format_info.get("duration") or video_stream.get("duration") or 0)
    if duration <= 0:
        raise FFmpegError("无法读取视频时长")

    fps = _parse_rate(video_stream.get("avg_frame_rate"))
    real_fps = _parse_rate(video_stream.get("r_frame_rate"))
    if fps <= 0:
        fps = real_fps
    if fps <= 0:
        fps = 30.0

    width = int(video_stream.get("width") or 0)
    height = int(video_stream.get("height") or 0)
    rotation = 0
    for side_data in video_stream.get("side_data_list", []) or []:
        if "rotation" in side_data:
            try:
                rotation = int(float(side_data["rotation"]))
            except (TypeError, ValueError):
                rotation = 0
            break

    if abs(rotation) % 180 == 90:
        display_width, display_height = height, width
    else:
        display_width, display_height = width, height

    is_vfr = bool(real_fps and abs(fps - real_fps) > 0.1)
    nb_frames = int(video_stream.get("nb_frames") or 0)

    return VideoInfo(
        duration=duration,
        fps=fps,
        width=width,
        height=height,
        display_width=display_width,
        display_height=display_height,
        rotation=rotation,
        codec=str(video_stream.get("codec_name") or "unknown"),
        has_audio=audio_stream is not None,
        audio_codec=str(audio_stream.get("codec_name")) if audio_stream else None,
        sample_rate=int((audio_stream or {}).get("sample_rate") or 44100),
        channels=int((audio_stream or {}).get("channels") or 2),
        size=int(format_info.get("size") or 0),
        nb_frames=nb_frames,
        is_vfr=is_vfr,
        bit_rate=int(format_info.get("bit_rate") or 0),
    )


def ffmpeg_version() -> str:
    """返回 ffmpeg 版本号。"""

    try:
        result = subprocess.run(
            ["ffmpeg", "-version"], capture_output=True, text=True, timeout=15
        )
        first_line = result.stdout.splitlines()[0] if result.stdout else ""
        match = re.search(r"ffmpeg version (\S+)", first_line)
        return match.group(1) if match else first_line or "unknown"
    except Exception:
        return "unknown"


def _extract_progress_seconds(line: str) -> float | None:
    """从 ffmpeg 的 progress 输出中提取已处理秒数。"""

    line = line.strip()
    if line.startswith("out_time_us="):
        try:
            return int(line.split("=", 1)[1]) / 1_000_000
        except ValueError:
            return None
    # ffmpeg 的 out_time_ms 实际单位是微秒，这里兼容两种写法
    if line.startswith("out_time_ms="):
        try:
            return int(line.split("=", 1)[1]) / 1_000_000
        except ValueError:
            return None
    if line.startswith("out_time="):
        value = line.split("=", 1)[1]
        match = re.match(r"(\d+):(\d+):(\d+(?:\.\d+)?)", value)
        if match:
            return (
                int(match.group(1)) * 3600
                + int(match.group(2)) * 60
                + float(match.group(3))
            )
    return None


def run_ffmpeg(
    command: list[str],
    duration: float,
    on_progress: Callable[[float], None] | None = None,
    on_log: Callable[[str], None] | None = None,
    cancel_event: threading.Event | None = None,
    label: str = "ffmpeg",
) -> None:
    """执行 ffmpeg，解析进度，支持取消。"""

    full_command = list(command)
    if "-progress" not in full_command:
        full_command[1:1] = ["-progress", "pipe:1", "-nostats"]
    if "-hide_banner" not in full_command:
        full_command.insert(1, "-hide_banner")
    if "-loglevel" not in full_command:
        full_command[1:1] = ["-loglevel", "warning"]

    if on_log:
        on_log(f"[{label}] 开始执行 ffmpeg")

    process = subprocess.Popen(
        full_command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    stderr_lines: list[str] = []
    last_progress = 0.0
    cancel_sent = False
    kill_deadline: float | None = None

    def read_stdout() -> None:
        nonlocal last_progress
        assert process.stdout is not None
        for line in process.stdout:
            seconds = _extract_progress_seconds(line)
            if seconds is None:
                continue
            fraction = 0.0 if duration <= 0 else min(1.0, max(0.0, seconds / duration))
            if fraction > last_progress:
                last_progress = fraction
                if on_progress:
                    on_progress(fraction)

    def read_stderr() -> None:
        assert process.stderr is not None
        for line in process.stderr:
            text = line.rstrip()
            if not text:
                continue
            stderr_lines.append(text)
            if len(stderr_lines) > 400:
                del stderr_lines[:200]
            if on_log:
                on_log(f"[{label}] {text}")

    stdout_thread = threading.Thread(target=read_stdout, daemon=True)
    stderr_thread = threading.Thread(target=read_stderr, daemon=True)
    stdout_thread.start()
    stderr_thread.start()

    while process.poll() is None:
        if cancel_event is not None and cancel_event.is_set() and not cancel_sent:
            cancel_sent = True
            kill_deadline = time.time() + 3.0
            if on_log:
                on_log(f"[{label}] 收到取消指令，正在终止 ffmpeg")
            process.terminate()
        if kill_deadline is not None and time.time() >= kill_deadline:
            process.kill()
            kill_deadline = None
        time.sleep(0.2)

    stdout_thread.join(timeout=2)
    stderr_thread.join(timeout=2)

    if cancel_sent or (cancel_event is not None and cancel_event.is_set()):
        raise CancelledError("任务已取消")

    if process.returncode != 0:
        tail = "\n".join(stderr_lines[-25:])
        raise FFmpegError(f"ffmpeg 执行失败（退出码 {process.returncode}）：\n{tail}")

    if on_progress:
        on_progress(1.0)
    if on_log:
        on_log(f"[{label}] ffmpeg 执行完成")


def build_clip_command(
    source: Path,
    start: float,
    duration: float,
    output: Path,
    video_info: VideoInfo,
    quality: str,
    audio_fade_ms: int = 0,
) -> list[str]:
    """构造单个片段的 ffmpeg 命令（重编码，帧级准确）。"""

    preset = QUALITY_PRESETS.get(quality, QUALITY_PRESETS["high"])
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-progress",
        "pipe:1",
        "-nostats",
        "-ss",
        f"{start:.6f}",
        "-i",
        str(source),
        "-t",
        f"{duration:.6f}",
        "-map",
        "0:v:0",
    ]

    if video_info.has_audio:
        command += ["-map", "0:a:0?"]

    command += [
        "-c:v",
        "libx264",
        "-preset",
        preset["preset"],
        "-crf",
        preset["crf"],
        "-pix_fmt",
        "yuv420p",
    ]

    if video_info.has_audio:
        channels = max(1, min(2, video_info.channels or 2))
        command += [
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            str(video_info.sample_rate or 44100),
            "-ac",
            str(channels),
        ]
        fade_seconds = audio_fade_ms / 1000.0
        if fade_seconds > 0 and duration > fade_seconds * 2:
            fade_out_start = max(0.0, duration - fade_seconds)
            command += [
                "-af",
                (
                    f"afade=t=in:st=0:d={fade_seconds:.6f},"
                    f"afade=t=out:st={fade_out_start:.6f}:d={fade_seconds:.6f}"
                ),
            ]
    else:
        command += ["-an"]

    command += [
        "-movflags",
        "+faststart",
        "-avoid_negative_ts",
        "make_zero",
        "-y",
        str(output),
    ]
    return command


def build_merged_command(
    source: Path,
    plan_items: list[dict],
    output: Path,
    video_info: VideoInfo,
    quality: str,
    audio_fade_ms: int = 0,
) -> list[str]:
    """构造成片命令。

    成片必须一次性用滤镜合成，不能直接拼接片段文件：
    每个 AAC 片段都有编码器填充，逐个拼接会累计音画漂移。
    """

    preset = QUALITY_PRESETS.get(quality, QUALITY_PRESETS["high"])
    filter_parts: list[str] = []
    concat_labels: list[str] = []
    has_audio = video_info.has_audio
    fade_seconds = audio_fade_ms / 1000.0

    for position, item in enumerate(plan_items):
        start = float(item["start"])
        end = float(item["end"])
        duration = max(0.0, end - start)
        filter_parts.append(
            f"[0:v]trim=start={start:.6f}:end={end:.6f},"
            f"setpts=PTS-STARTPTS[v{position}]"
        )
        if has_audio:
            audio_chain = (
                f"[0:a]atrim=start={start:.6f}:end={end:.6f},"
                f"asetpts=PTS-STARTPTS"
            )
            if fade_seconds > 0 and duration > fade_seconds * 2:
                fade_out_start = max(0.0, duration - fade_seconds)
                audio_chain += (
                    f",afade=t=in:st=0:d={fade_seconds:.6f}"
                    f",afade=t=out:st={fade_out_start:.6f}:d={fade_seconds:.6f}"
                )
            audio_chain += f"[a{position}]"
            filter_parts.append(audio_chain)
            concat_labels.append(f"[v{position}][a{position}]")
        else:
            concat_labels.append(f"[v{position}]")

    concat_inputs = "".join(concat_labels)
    if has_audio:
        filter_parts.append(
            f"{concat_inputs}concat=n={len(plan_items)}:v=1:a=1[outv][outa]"
        )
    else:
        filter_parts.append(
            f"{concat_inputs}concat=n={len(plan_items)}:v=1:a=0[outv]"
        )

    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-progress",
        "pipe:1",
        "-nostats",
        "-i",
        str(source),
        "-filter_complex",
        ";".join(filter_parts),
        "-map",
        "[outv]",
    ]
    if has_audio:
        command += ["-map", "[outa]"]

    command += [
        "-c:v",
        "libx264",
        "-preset",
        preset["preset"],
        "-crf",
        preset["crf"],
        "-pix_fmt",
        "yuv420p",
    ]

    if has_audio:
        channels = max(1, min(2, video_info.channels or 2))
        command += [
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            str(video_info.sample_rate or 44100),
            "-ac",
            str(channels),
        ]
    else:
        command += ["-an"]

    command += [
        "-movflags",
        "+faststart",
        "-y",
        str(output),
    ]
    return command


def build_fast_clip_command(
    source: Path,
    start: float,
    duration: float,
    output: Path,
    video_info: VideoInfo,
) -> list[str]:
    """极速模式：按关键帧复制，切点可能有 1–2 秒偏移。"""

    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-progress",
        "pipe:1",
        "-nostats",
        "-ss",
        f"{start:.6f}",
        "-i",
        str(source),
        "-t",
        f"{duration:.6f}",
        "-map",
        "0:v:0",
    ]
    if video_info.has_audio:
        command += ["-map", "0:a:0?"]
    command += [
        "-c",
        "copy",
        "-avoid_negative_ts",
        "make_zero",
        "-movflags",
        "+faststart",
        "-y",
        str(output),
    ]
    return command


def build_fast_concat_command(list_file: Path, output: Path) -> list[str]:
    """极速模式：直接拼接已生成的片段文件。"""

    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-progress",
        "pipe:1",
        "-nostats",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_file),
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        "-y",
        str(output),
    ]
