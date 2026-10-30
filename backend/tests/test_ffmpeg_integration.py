from pathlib import Path

import pytest

from app.ffmpeg import (
    build_clip_command,
    build_merged_command,
    probe_video,
    run_ffmpeg,
)
from app.planner import build_plan
from app.srt_parser import RawCue, parse_srt_bytes


def test_generate_clips_and_merged(short_media, tmp_path: Path):
    video, srt = short_media
    info = probe_video(video)
    cues, _ = parse_srt_bytes(srt.read_bytes())
    plan = build_plan(cues, info.duration, info.fps, 0.0, 0.0)
    items = [item.to_dict() for item in plan.items]

    clips_dir = tmp_path / "clips"
    clips_dir.mkdir()
    for item in items:
        output = clips_dir / item["output_name"]
        command = build_clip_command(
            video,
            item["start"],
            item["duration"],
            output,
            info,
            quality="standard",
        )
        run_ffmpeg(command, duration=item["duration"])
        assert output.exists()
        clip_info = probe_video(output)
        assert clip_info.duration == pytest.approx(item["duration"], abs=0.06)

    merged = tmp_path / "merged.mp4"
    command = build_merged_command(
        video, items, merged, info, quality="standard"
    )
    run_ffmpeg(command, duration=plan.total_duration)

    merged_info = probe_video(merged)
    assert merged_info.nb_frames == 45
    assert merged_info.duration == pytest.approx(1.5, abs=0.06)
    assert merged_info.has_audio is True


def test_generate_video_without_audio(short_video_no_audio: Path, tmp_path: Path):
    info = probe_video(short_video_no_audio)
    cues = [
        RawCue(index=1, source_index=1, start=2.0, end=2.8, text="后"),
        RawCue(index=2, source_index=2, start=0.5, end=1.2, text="前"),
    ]
    plan = build_plan(cues, info.duration, info.fps, 0.0, 0.0)
    items = [item.to_dict() for item in plan.items]

    merged = tmp_path / "merged_silent.mp4"
    command = build_merged_command(
        short_video_no_audio, items, merged, info, quality="standard"
    )
    run_ffmpeg(command, duration=plan.total_duration)

    merged_info = probe_video(merged)
    assert merged_info.has_audio is False
    assert merged_info.nb_frames == 45
