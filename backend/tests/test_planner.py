from pathlib import Path

import pytest

from app.planner import PlanError, build_plan
from app.srt_parser import RawCue, parse_srt_bytes


def sample_cues(sample_srt: Path):
    cues, _ = parse_srt_bytes(sample_srt.read_bytes())
    return cues


def cue(index: int, start: float, end: float, text: str = "文案") -> RawCue:
    return RawCue(index=index, source_index=index, start=start, end=end, text=text)


def test_no_padding_real_sample(sample_srt: Path):
    plan = build_plan(sample_cues(sample_srt), 178.966667, 30.0, 0.0, 0.0)

    assert len(plan.items) == 11
    assert plan.total_frames == 1694
    assert plan.total_duration == pytest.approx(56.466667, abs=1e-6)
    assert plan.pad_before == 0
    assert plan.pad_after == 0

    first = plan.items[0]
    assert first.start == pytest.approx(117.433333, abs=1e-6)
    assert first.end == pytest.approx(123.700, abs=1e-6)
    assert first.frame_start == 3523
    assert first.frame_end == 3711

    # 第 5、6 条间隔只有 0.067s，无余量时各自保留原边界
    assert plan.items[4].end == pytest.approx(88.566667, abs=1e-6)
    assert plan.items[5].start == pytest.approx(88.600, abs=1e-6)


def test_padding_real_sample(sample_srt: Path):
    plan = build_plan(sample_cues(sample_srt), 178.966667, 30.0, 0.10, 0.15)

    assert len(plan.items) == 11
    assert plan.total_frames == 1775
    assert plan.total_duration == pytest.approx(59.166667, abs=1e-6)
    assert plan.items[0].start == pytest.approx(117.333333, abs=1e-6)
    assert plan.items[0].end == pytest.approx(123.866667, abs=1e-6)

    # 间隔不足时，两条共享中点 88.600，避免重复
    assert plan.items[4].end == pytest.approx(88.600, abs=1e-6)
    assert plan.items[5].start == pytest.approx(88.600, abs=1e-6)


def test_padding_uses_midpoint_for_tight_gap():
    cues = [cue(1, 1.0, 2.0), cue(2, 2.05, 3.0)]
    plan = build_plan(cues, 10.0, 30.0, 0.10, 0.15)
    shared = round(2.025 * 30) / 30
    assert plan.items[0].end == pytest.approx(shared, abs=1e-6)
    assert plan.items[1].start == pytest.approx(shared, abs=1e-6)
    assert plan.items[0].frame_end == plan.items[1].frame_start


def test_audio_segment_frames_are_aligned():
    cues = [cue(1, 0.01, 0.04)]
    plan = build_plan(cues, 10.0, 30.0, 0.0, 0.0)
    item = plan.items[0]
    assert item.frame_end > item.frame_start
    assert item.duration == pytest.approx(
        (item.frame_end - item.frame_start) / 30.0, abs=1e-9
    )


def test_end_after_video_is_clamped_with_warning():
    cues = [cue(1, 1.0, 10.2)]
    plan = build_plan(cues, 10.0, 30.0)
    assert plan.items[0].end == pytest.approx(10.0)
    assert any("截断" in warning for warning in plan.warnings)


def test_end_far_after_video_raises():
    cues = [cue(1, 1.0, 10.5)]
    with pytest.raises(PlanError, match="超出视频时长"):
        build_plan(cues, 10.0, 30.0)


def test_start_after_video_raises():
    cues = [cue(1, 10.0, 11.0)]
    with pytest.raises(PlanError, match="超出视频时长"):
        build_plan(cues, 10.0, 30.0)


def test_invalid_range_raises():
    cues = [cue(1, 2.0, 2.0)]
    with pytest.raises(PlanError, match="结束时间"):
        build_plan(cues, 10.0, 30.0)


def test_overlap_produces_warning():
    cues = [cue(1, 1.0, 3.0), cue(2, 2.0, 4.0)]
    plan = build_plan(cues, 10.0, 30.0)
    assert any("重叠" in warning for warning in plan.warnings)


def test_duplicate_range_produces_warning():
    cues = [cue(1, 1.0, 2.0), cue(2, 1.0, 2.0)]
    plan = build_plan(cues, 10.0, 30.0)
    assert any("完全相同" in warning for warning in plan.warnings)


def test_output_names_are_ordered_and_include_time():
    cues = [cue(1, 2.0, 3.0), cue(2, 0.5, 1.0)]
    plan = build_plan(cues, 10.0, 30.0)
    assert plan.items[0].output_name.startswith("001_00-00-02.000")
    assert plan.items[1].output_name.startswith("002_00-00-00.500")


def test_very_short_cues_keep_positive_duration():
    cues = [cue(1, 1.000, 1.010), cue(2, 1.011, 1.020)]
    plan = build_plan(cues, 10.0, 30.0, 0.10, 0.15)
    for item in plan.items:
        assert item.frame_end > item.frame_start
        assert item.duration > 0
    assert plan.items[0].frame_end == plan.items[1].frame_start


def test_resolve_inside_rejects_traversal(tmp_path):
    from app.storage import resolve_inside

    with pytest.raises(ValueError):
        resolve_inside(tmp_path, "../secret.txt")
