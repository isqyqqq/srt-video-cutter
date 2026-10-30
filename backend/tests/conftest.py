"""测试公共配置。"""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))


@pytest.fixture(scope="session")
def project_root() -> Path:
    return PROJECT_ROOT


@pytest.fixture(scope="session")
def sample_video(project_root: Path) -> Path:
    path = project_root / "tests" / "完整的粗剪视频.mov"
    if not path.exists():
        pytest.skip("缺少真实样例视频")
    return path


@pytest.fixture(scope="session")
def sample_srt(project_root: Path) -> Path:
    path = project_root / "tests" / "剪辑后的文案.srt"
    if not path.exists():
        pytest.skip("缺少真实样例 SRT")
    return path


@pytest.fixture()
def short_media(tmp_path: Path) -> tuple[Path, Path]:
    """生成 4 秒测试视频和一份乱序 SRT，避免集成测试依赖大文件。"""

    video = tmp_path / "source.mp4"
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=320x240:rate=30:duration=4",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=4",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-preset",
        "ultrafast",
        "-c:a",
        "aac",
        "-shortest",
        "-y",
        str(video),
    ]
    subprocess.run(command, check=True)

    srt = tmp_path / "source.srt"
    srt.write_text(
        "1\n"
        "00:00:02,000 --> 00:00:02,800\n"
        "这是第二段\n\n"
        "2\n"
        "00:00:00,500 --> 00:00:01,200\n"
        "这是第一段\n",
        encoding="utf-8",
    )
    return video, srt


@pytest.fixture()
def short_video_no_audio(tmp_path: Path) -> Path:
    """生成 3 秒纯视频，用于验证无音轨分支。"""

    video = tmp_path / "silent.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x240:rate=30:duration=3",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-preset",
            "ultrafast",
            "-y",
            str(video),
        ],
        check=True,
    )
    return video
