"""切片生成器测试：只选区间、不改写、时间戳沿用原片。"""

from __future__ import annotations

import json

import pytest

from app import clip_writer
from app.clip_writer import ClipWriterError, generate_clip, generate_hooks
from app.srt_parser import parse_srt_text

# 用完整文案里的 14 段拼出约 59.5 秒的成片，顺序刻意打乱
SEGMENTS = [
    (130, 132, "钩子"),
    (111, 113, "需求"),
    (58, 62, "材质"),
    (82, 84, "工艺"),
    (75, 78, "价值"),
    (108, 108, "价值"),
    (67, 70, "福利"),
    (87, 88, "福利"),
    (122, 126, "信任"),
    (97, 98, "尺码"),
    (104, 106, "场景"),
    (115, 116, "需求"),
    (119, 120, "信任"),
    (91, 91, "结尾"),
]


def _chat(*payloads: dict | str):
    """构造一个按顺序返回固定内容的假模型，用完后重复最后一条。"""

    queue = list(payloads)
    calls: list[list[dict]] = []

    def chat(messages: list[dict]) -> str:
        calls.append(messages)
        value = queue.pop(0) if len(queue) > 1 else queue[0]
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)

    chat.calls = calls  # type: ignore[attr-defined]
    return chat


def _hooks_payload() -> dict:
    return {
        "hooks": [
            {"a": 130, "b": 132, "type": "价格乌龙", "reason": "错价事故"},
            {"a": 1, "b": 3, "type": "价格锚", "reason": "低价锚点"},
            {"a": 111, "b": 112, "type": "痛点", "reason": "怕显胖"},
        ]
    }


def _clip_payload() -> dict:
    return {
        "memory_point": "加绒加厚也不显胖",
        "segments": [
            {"a": a, "b": b, "step": step, "reason": "承接上一句"}
            for a, b, step in SEGMENTS
        ],
    }


@pytest.fixture(scope="module")
def cues(project_root):
    path = project_root / "tests" / "完整文案.srt"
    if not path.exists():
        pytest.skip("缺少完整文案样例")
    return parse_srt_text(path.read_text(encoding="utf-8"))


def test_generate_hooks_uses_original_text(cues):
    chat = _chat(_hooks_payload())
    hooks, warnings = generate_hooks(cues, count=3, chat_fn=chat)

    assert len(hooks) == 3
    assert [hook.id for hook in hooks] == ["h1", "h2", "h3"]
    first = hooks[0]
    assert first.type == "价格乌龙"
    # 文案和时间戳必须完全来自原 SRT
    assert first.text == "".join(cue.text for cue in cues[129:132])
    assert first.start == cues[129].start
    assert first.end == cues[131].end
    assert warnings == []


def test_generate_hooks_retries_invalid_json(cues):
    chat = _chat("我看不懂，给你一段废话", _hooks_payload())
    hooks, _ = generate_hooks(cues, count=3, chat_fn=chat)

    assert len(hooks) == 3
    assert len(chat.calls) == 2  # type: ignore[attr-defined]
    # 第二次请求要带上一次的错误说明
    assert "上一次输出不合格" in chat.calls[1][-1]["content"]  # type: ignore[attr-defined]


def test_generate_hooks_skips_out_of_range(cues):
    payload = {
        "hooks": [
            {"a": 5000, "b": 5001, "type": "痛点"},
            {"a": 3, "b": 2, "type": "未知类型"},
        ]
    }
    hooks, _ = generate_hooks(cues, count=3, chat_fn=_chat(payload))

    assert len(hooks) == 1
    assert (hooks[0].a, hooks[0].b) == (2, 3)
    assert hooks[0].type == "其他"


def test_generate_clip_keeps_original_timestamps(cues):
    hooks, _ = generate_hooks(cues, count=3, chat_fn=_chat(_hooks_payload()))
    result = generate_clip(cues, hooks[0], chat_fn=_chat(_clip_payload()))

    assert 46 <= result.duration <= 60
    assert result.segments[0].a == 130  # 钩子被放在第一条
    assert result.memory_point == "加绒加厚也不显胖"

    parsed = parse_srt_text(result.srt_text)
    assert len(parsed) == len(SEGMENTS)

    for cue, (a, b, _) in zip(parsed, SEGMENTS):
        assert cue.start == cues[a - 1].start
        assert cue.end == cues[b - 1].end
        assert cue.text == "".join(item.text for item in cues[a - 1 : b])

    # 时间戳不递增是正常的：这是从原片不同位置抽的片段
    starts = [cue.start for cue in parsed]
    assert starts != sorted(starts)


def test_generate_clip_rejects_overlap(cues):
    payload = _clip_payload()
    payload["segments"] = [
        {"a": 130, "b": 132, "step": "钩子"},
        {"a": 131, "b": 133, "step": "价值"},
    ]
    hooks, _ = generate_hooks(cues, count=3, chat_fn=_chat(_hooks_payload()))

    with pytest.raises(ClipWriterError) as excinfo:
        generate_clip(cues, hooks[0], chat_fn=_chat(payload), max_attempts=1)
    assert "重叠" in str(excinfo.value)


def test_generate_clip_rejects_wrong_duration(cues):
    payload = {
        "memory_point": "太短",
        "segments": [
            {"a": 130, "b": 132, "step": "钩子"},
            {"a": 111, "b": 112, "step": "需求"},
        ],
    }
    hooks, _ = generate_hooks(cues, count=3, chat_fn=_chat(_hooks_payload()))

    with pytest.raises(ClipWriterError) as excinfo:
        generate_clip(cues, hooks[0], chat_fn=_chat(payload), max_attempts=1)
    assert "总时长" in str(excinfo.value)


def test_generate_clip_requires_selected_hook(cues):
    payload = {
        "memory_point": "跑题了",
        "segments": [
            {"a": 58, "b": 62, "step": "钩子"},
            {"a": 67, "b": 70, "step": "价值"},
            {"a": 75, "b": 78, "step": "价值"},
            {"a": 82, "b": 84, "step": "工艺"},
            {"a": 87, "b": 88, "step": "福利"},
            {"a": 97, "b": 98, "step": "尺码"},
            {"a": 104, "b": 106, "step": "场景"},
            {"a": 108, "b": 108, "step": "价值"},
            {"a": 111, "b": 113, "step": "需求"},
            {"a": 115, "b": 116, "step": "需求"},
            {"a": 119, "b": 120, "step": "信任"},
            {"a": 122, "b": 126, "step": "信任"},
            {"a": 91, "b": 91, "step": "结尾"},
        ],
    }
    hooks, _ = generate_hooks(cues, count=3, chat_fn=_chat(_hooks_payload()))

    with pytest.raises(ClipWriterError) as excinfo:
        generate_clip(cues, hooks[0], chat_fn=_chat(payload), max_attempts=1)
    assert "没有包含所选开头钩子" in str(excinfo.value)


def test_srt_time_format():
    assert clip_writer.format_srt_time(3725.5) == "01:02:05,500"
    assert clip_writer.format_srt_time(0) == "00:00:00,000"


def test_highlight_steps_accepted():
    """新增的 展示 / 赞美 step 应被合法接受。"""

    assert "展示" in clip_writer.ALLOWED_STEPS
    assert "赞美" in clip_writer.ALLOWED_STEPS
    assert "场景" in clip_writer.ALLOWED_STEPS


def test_hint_negation_filter():
    """反向/否定表述不应误判为赞美（如“它都不好看”）。"""

    assert clip_writer._text_hits("它都不好看", clip_writer.PRAISE_HINTS) is False
    assert clip_writer._text_hits("不太好听", clip_writer.PRAISE_HINTS) is False
    assert clip_writer._text_hits("这个撞色很好看", clip_writer.PRAISE_HINTS) is True
    assert clip_writer._text_hits("转一圈给大家看看", clip_writer.SHOW_HINTS) is True


def test_price_praise_hints():
    """价格划算类（低优先级高光）应能识别，并排除否定。"""

    assert clip_writer._text_hits("好便宜真的", clip_writer.PRICE_PRAISE_HINTS) is True
    assert clip_writer._text_hits("这个大衣性价比太高了", clip_writer.PRICE_PRAISE_HINTS) is True
    assert clip_writer._text_hits("确实很划算", clip_writer.PRICE_PRAISE_HINTS) is True

    # 否定：前否定“不便宜”、后否定“性价比不高”，都不算命中
    assert clip_writer._text_hits("不便宜", clip_writer.PRICE_PRAISE_HINTS) is False
    assert clip_writer._text_hits("性价比不高", clip_writer.PRICE_PRAISE_HINTS) is False


def _cues_from(text: str):
    return parse_srt_text(text)


def test_highlight_warning_when_show_and_praise_missing(monkeypatch):
    """字幕里有展示/赞美线索但没选进成片时，应给出软提示。"""

    # 测试素材很短，放宽时长硬边界
    monkeypatch.setattr(clip_writer, "MIN_DURATION_HARD", 0.1)
    monkeypatch.setattr(clip_writer, "MAX_DURATION_HARD", 30.0)
    monkeypatch.setattr(clip_writer, "MIN_DURATION_RECOMMENDED", 0.1)
    monkeypatch.setattr(clip_writer, "MAX_DURATION_RECOMMENDED", 30.0)

    srt = (
        "1\n00:00:00,500 --> 00:00:01,200\n太漂亮了吧\n\n"
        "2\n00:00:01,300 --> 00:00:02,000\n转一圈给大家看看\n\n"
        "3\n00:00:02,100 --> 00:00:02,900\n这件料子很好\n\n"
        "4\n00:00:03,000 --> 00:00:03,800\n价格一百九十九\n"
    )
    cues = _cues_from(srt)
    hook = clip_writer.Hook(
        id="h1", type="价格锚", a=4, b=4, start=3.0, end=3.8,
        text="价格一百九十九", reason="价格",
    )
    # 成片只选了钩子和材质，没选展示/赞美
    payload = {
        "memory_point": "料子好",
        "segments": [
            {"a": 4, "b": 4, "step": "钩子"},
            {"a": 3, "b": 3, "step": "材质"},
        ],
        "spare": [],
    }
    result = generate_clip(cues, hook, chat_fn=_chat(payload), max_attempts=1)
    joined = "\n".join(result.warnings)
    assert "展示" in joined
    assert "赞美" in joined


def test_no_highlight_warning_when_source_lacks_hints(monkeypatch):
    """字幕里本来就没有展示/赞美时，不应硬提示（不硬套）。"""

    monkeypatch.setattr(clip_writer, "MIN_DURATION_HARD", 0.1)
    monkeypatch.setattr(clip_writer, "MAX_DURATION_HARD", 30.0)
    monkeypatch.setattr(clip_writer, "MIN_DURATION_RECOMMENDED", 0.1)
    monkeypatch.setattr(clip_writer, "MAX_DURATION_RECOMMENDED", 30.0)

    srt = (
        "1\n00:00:00,500 --> 00:00:01,200\n这件料子很好\n\n"
        "2\n00:00:01,300 --> 00:00:02,000\n加厚加绒\n\n"
        "3\n00:00:02,100 --> 00:00:02,900\n价格一百九十九\n"
    )
    cues = _cues_from(srt)
    hook = clip_writer.Hook(
        id="h1", type="价格锚", a=3, b=3, start=2.1, end=2.9,
        text="价格一百九十九", reason="价格",
    )
    payload = {
        "memory_point": "料子好",
        "segments": [
            {"a": 3, "b": 3, "step": "钩子"},
            {"a": 1, "b": 2, "step": "材质"},
        ],
        "spare": [],
    }
    result = generate_clip(cues, hook, chat_fn=_chat(payload), max_attempts=1)
    joined = "\n".join(result.warnings)
    assert "展示类片段" not in joined
    assert "赞美类片段" not in joined


def _size_cues():
    """构造一份含完整尺码表的短字幕（并放宽时长硬边界）。"""

    srt = (
        "1\n00:00:00,500 --> 00:00:01,200\n这件料子很好\n\n"
        "2\n00:00:01,300 --> 00:00:02,000\n加厚加绒\n\n"
        "3\n00:00:02,100 --> 00:00:02,900\n105 115 125 能穿到 140 斤\n\n"
        "4\n00:00:03,000 --> 00:00:03,800\n价格一百九十九\n"
    )
    return _cues_from(srt)


def _relax(monkeypatch):
    monkeypatch.setattr(clip_writer, "MIN_DURATION_HARD", 0.1)
    monkeypatch.setattr(clip_writer, "MAX_DURATION_HARD", 30.0)
    monkeypatch.setattr(clip_writer, "MIN_DURATION_RECOMMENDED", 0.1)
    monkeypatch.setattr(clip_writer, "MAX_DURATION_RECOMMENDED", 30.0)


def test_size_hints_recognize_size_table():
    """完整尺码表与卡码说法都应被识别为尺码线索。"""

    assert clip_writer._text_hits("105 115 125 能穿到 140 斤", clip_writer.SIZE_HINTS)
    assert clip_writer._text_hits("卡码正常拍", clip_writer.SIZE_HINTS)
    assert clip_writer._text_hits("卡码拍大一码", clip_writer.SIZE_HINTS)
    assert clip_writer._text_hits("个子高不用拍大一码", clip_writer.SIZE_HINTS)


def test_missing_size_triggers_retry(monkeypatch):
    """字幕里有尺码、成片却没选，应判定不合格并重试，最终带上尺码。"""

    _relax(monkeypatch)
    cues = _size_cues()
    hook = clip_writer.Hook(
        id="h1", type="价格锚", a=4, b=4, start=3.0, end=3.8,
        text="价格一百九十九", reason="价格",
    )
    bad = {
        "memory_point": "料子好",
        "segments": [
            {"a": 4, "b": 4, "step": "钩子"},
            {"a": 1, "b": 2, "step": "材质"},
        ],
        "spare": [],
    }
    good = {
        "memory_point": "料子好",
        "segments": [
            {"a": 4, "b": 4, "step": "钩子"},
            {"a": 1, "b": 2, "step": "材质"},
            {"a": 3, "b": 3, "step": "尺码"},
        ],
        "spare": [],
    }
    chat = _chat(bad, good)
    result = generate_clip(cues, hook, chat_fn=chat, max_attempts=2)

    assert len(chat.calls) == 2  # type: ignore[attr-defined]
    # 重试提示里必须明确要求补尺码
    assert "尺码" in chat.calls[1][-1]["content"]  # type: ignore[attr-defined]
    assert any(seg.step == "尺码" for seg in result.segments)


def test_missing_size_still_fails_after_max_attempts(monkeypatch):
    """一直不给尺码时，最终应报错（不能悄悄生成没有尺码的成片）。"""

    _relax(monkeypatch)
    cues = _size_cues()
    hook = clip_writer.Hook(
        id="h1", type="价格锚", a=4, b=4, start=3.0, end=3.8,
        text="价格一百九十九", reason="价格",
    )
    bad = {
        "memory_point": "料子好",
        "segments": [
            {"a": 4, "b": 4, "step": "钩子"},
            {"a": 1, "b": 2, "step": "材质"},
        ],
        "spare": [],
    }
    with pytest.raises(ClipWriterError) as excinfo:
        generate_clip(cues, hook, chat_fn=_chat(bad), max_attempts=2)
    assert "尺码" in str(excinfo.value)


def test_no_size_in_source_does_not_require_size(monkeypatch):
    """字幕里本来没有尺码时，不硬要求尺码段。"""

    _relax(monkeypatch)
    srt = (
        "1\n00:00:00,500 --> 00:00:01,200\n这件料子很好\n\n"
        "2\n00:00:01,300 --> 00:00:02,000\n价格一百九十九\n"
    )
    cues = _cues_from(srt)
    hook = clip_writer.Hook(
        id="h1", type="价格锚", a=2, b=2, start=1.3, end=2.0,
        text="价格一百九十九", reason="价格",
    )
    payload = {
        "memory_point": "料子好",
        "segments": [
            {"a": 2, "b": 2, "step": "钩子"},
            {"a": 1, "b": 1, "step": "材质"},
        ],
        "spare": [],
    }
    result = generate_clip(cues, hook, chat_fn=_chat(payload), max_attempts=1)
    assert not any(seg.step == "尺码" for seg in result.segments)


def test_size_segment_far_from_end_warns(monkeypatch):
    """尺码段位置太靠前时应给软提示（结尾前 2~3 段才算合适）。"""

    _relax(monkeypatch)
    srt = "".join(
        f"{i}\n00:00:0{i},000 --> 00:00:0{i},900\n第{i}句话\n\n"
        for i in range(1, 7)
    ) + "7\n00:00:07,000 --> 00:00:07,900\n105 115 125 能穿到 140 斤\n"
    cues = _cues_from(srt)
    hook = clip_writer.Hook(
        id="h1", type="价格锚", a=1, b=1, start=0.0, end=0.9,
        text="第1句话", reason="价格",
    )
    # 6 段里尺码排第 2 段，离结尾还远（结尾前 2~3 段应是第 4、5 段）
    payload = {
        "memory_point": "料子好",
        "segments": [
            {"a": 1, "b": 1, "step": "钩子"},
            {"a": 7, "b": 7, "step": "尺码"},
            {"a": 2, "b": 2, "step": "材质"},
            {"a": 3, "b": 3, "step": "材质"},
            {"a": 4, "b": 4, "step": "工艺"},
            {"a": 6, "b": 6, "step": "结尾"},
        ],
        "spare": [],
    }
    result = generate_clip(cues, hook, chat_fn=_chat(payload), max_attempts=1)
    assert "位置偏前" in "\n".join(result.warnings)


def test_has_size_table_detects_full_table():
    """完整尺码表识别：多档数字 + 体重/穿码字样。"""

    assert clip_writer._has_size_table("105 115 125 能穿到 140 斤")
    assert clip_writer._has_size_table("一百一十五一百这是一百零五一百一十五一百三十五可以穿到 140 斤")
    # 单个体重不算尺码表
    assert not clip_writer._has_size_table("s 码能穿到 155 斤了随便穿")


def test_loose_size_sentence_does_not_trigger_position_warning(monkeypatch):
    """“S 码能穿到 155 斤”这种中途句子不应被判为尺码段位置偏前。"""

    _relax(monkeypatch)
    srt = "".join(
        f"{i}\n00:00:0{i},000 --> 00:00:0{i},900\n第{i}句话\n\n"
        for i in range(1, 5)
    ) + "5\n00:00:05,000 --> 00:00:05,900\n105 115 125 能穿到 140 斤\n"
    cues = _cues_from(srt)
    hook = clip_writer.Hook(
        id="h1", type="价格锚", a=1, b=1, start=0.0, end=0.9,
        text="第1句话", reason="价格",
    )
    payload = {
        "memory_point": "料子好",
        "segments": [
            {"a": 1, "b": 1, "step": "钩子"},
            {"a": 2, "b": 2, "step": "需求"},  # 含“斤”？不含，但仍不该误判
            {"a": 3, "b": 3, "step": "价值"},
            {"a": 5, "b": 5, "step": "尺码"},
            {"a": 4, "b": 4, "step": "结尾"},
        ],
        "spare": [],
    }
    result = generate_clip(cues, hook, chat_fn=_chat(payload), max_attempts=1)
    assert "位置偏前" not in "\n".join(result.warnings)


def test_value_point_hints_cover_light_and_warm():
    """“轻”这类实穿优点必须能识别，但不能把“年轻”误判为“轻”。"""

    assert clip_writer._has_value_point("而且非常轻")
    assert clip_writer._has_value_point("这件非常的非常的轻啊")
    assert clip_writer._has_value_point("这件很保暖而且非常轻")
    assert clip_writer._has_value_point("这料子手感非常的绵密啊")
    assert clip_writer._has_value_point("这个大衣很轻")
    # “可能会年轻一点”不该算轻
    assert clip_writer._has_value_point("可能会年轻一点") is False
    assert clip_writer._has_value_point("这个价格很合适") is False


def test_missing_value_point_warns(monkeypatch):
    """字幕里有“轻”等实穿优点、成片与候选库都没体现时，给软提示。"""

    _relax(monkeypatch)
    srt = (
        "1\n00:00:00,500 --> 00:00:01,200\n这件料子很好\n\n"
        "2\n00:00:01,300 --> 00:00:02,000\n这件很保暖而且非常轻\n\n"
        "3\n00:00:02,100 --> 00:00:02,900\n价格一百九十九\n"
    )
    cues = _cues_from(srt)
    hook = clip_writer.Hook(
        id="h1", type="价格锚", a=3, b=3, start=2.1, end=2.9,
        text="价格一百九十九", reason="价格",
    )
    payload = {
        "memory_point": "料子好",
        "segments": [
            {"a": 3, "b": 3, "step": "钩子"},
            {"a": 1, "b": 1, "step": "材质"},
        ],
        "spare": [],
    }
    result = generate_clip(cues, hook, chat_fn=_chat(payload), max_attempts=1)
    assert "实穿优点" in "\n".join(result.warnings)


def test_value_point_present_no_warning(monkeypatch):
    """成片已经选了“很轻”那段时，不该再提示。"""

    _relax(monkeypatch)
    srt = (
        "1\n00:00:00,500 --> 00:00:01,200\n这件料子很好\n\n"
        "2\n00:00:01,300 --> 00:00:02,000\n这件很保暖而且非常轻\n\n"
        "3\n00:00:02,100 --> 00:00:02,900\n价格一百九十九\n"
    )
    cues = _cues_from(srt)
    hook = clip_writer.Hook(
        id="h1", type="价格锚", a=3, b=3, start=2.1, end=2.9,
        text="价格一百九十九", reason="价格",
    )
    payload = {
        "memory_point": "料子好",
        "segments": [
            {"a": 3, "b": 3, "step": "钩子"},
            {"a": 1, "b": 2, "step": "价值"},
        ],
        "spare": [],
    }
    result = generate_clip(cues, hook, chat_fn=_chat(payload), max_attempts=1)
    assert "实穿优点" not in "\n".join(result.warnings)
