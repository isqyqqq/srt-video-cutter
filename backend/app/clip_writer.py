"""按提示词把完整直播字幕重排成 55~60 秒切片。

设计原则（与提示词一一对应）：
1. 只提取、不改写：模型只能选择原 SRT 的连续条目区间 [a, b]；
2. 单条成片字幕 = 一段相邻连续块，文本由原句直接拼接，时间戳取首条开始、末条结束；
3. 结果必须通过结构校验（范围、重叠、时长、钩子必须入选），不合格自动带着原因重试。

模型只负责"挑哪几句"，这里负责"挑得对不对"。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from typing import Callable, Iterable

from . import llm
from .srt_parser import RawCue

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"

# 成片总时长的硬性边界与推荐区间（秒）
MIN_DURATION_HARD = 40.0
MAX_DURATION_HARD = 72.0
MIN_DURATION_RECOMMENDED = 46.0
MAX_DURATION_RECOMMENDED = 60.0

# 单条成片字幕最长不超过 12 秒，避免一段拖垮节奏
MAX_SEGMENT_DURATION = 12.0

ALLOWED_HOOK_TYPES = ("价格锚", "价格乌龙", "痛点", "反差", "场景", "故事", "成分")
ALLOWED_STEPS = (
    "钩子",
    "需求",
    "价值",
    "场景",
    "展示",
    "赞美",
    "福利",
    "信任",
    "材质",
    "工艺",
    "尺码",
    "稀缺",
    "结尾",
)

# 高光类步骤：有则优先进成片/候选库，没有不硬套
HIGHLIGHT_STEPS = ("场景", "展示", "赞美")

ChatFn = Callable[[list[dict]], str]


class ClipWriterError(RuntimeError):
    """生成失败或模型输出不合格。"""


@dataclass
class Hook:
    """一个候选开头钩子，对应原 SRT 的连续条目区间。"""

    id: str
    type: str
    a: int
    b: int
    start: float
    end: float
    text: str
    reason: str

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "a": self.a,
            "b": self.b,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "duration": round(self.duration, 3),
            "text": self.text,
            "reason": self.reason,
        }


@dataclass
class Segment:
    """一条成片字幕，对应原 SRT 的连续条目区间。"""

    a: int
    b: int
    step: str
    reason: str
    start: float
    end: float
    text: str

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def to_dict(self) -> dict:
        return {
            "a": self.a,
            "b": self.b,
            "step": self.step,
            "reason": self.reason,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "duration": round(self.duration, 3),
            "text": self.text,
        }


@dataclass
class ClipResult:
    """一次完整的切片生成结果。"""

    hook: Hook
    segments: list[Segment]
    memory_point: str
    srt_text: str
    warnings: list[str] = field(default_factory=list)
    raw_response: str = ""
    # 候选片段库：未进成片但同样有用的片段，供用户替换/插入
    spare: list[Segment] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return sum(segment.duration for segment in self.segments)

    def to_dict(self) -> dict:
        return {
            "hook": self.hook.to_dict(),
            "segments": [segment.to_dict() for segment in self.segments],
            "memory_point": self.memory_point,
            "duration": round(self.duration, 3),
            "warnings": self.warnings,
            "spare": [segment.to_dict() for segment in self.spare],
        }


# ---------------------------------------------------------------- 通用工具


def format_srt_time(seconds: float) -> str:
    """把秒格式化为 SRT 时间戳 HH:MM:SS,mmm。"""

    total_ms = int(round(max(0.0, seconds) * 1000))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _extract_json(raw: str) -> dict:
    """从模型输出里抠出 JSON，兼容代码块、前后废话。"""

    text = (raw or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()

    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(text[start : end + 1])
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

    raise ClipWriterError(
        "模型没有返回合法 JSON。可以换一个更强的模型，或调低温度后重试。"
    )


def _cue_text(cues: list[RawCue], a: int, b: int) -> str:
    """把一段连续条目的文本拼成成片字幕，逐字取自原文。"""

    return "".join(cue.text.strip() for cue in cues[a - 1 : b]).strip()


def _range_bounds(cues: list[RawCue], a: int, b: int) -> tuple[float, float]:
    """取一段连续条目的起止时间，并确保它们在原片上是按时间向前的。"""

    items = cues[a - 1 : b]
    for previous, current in zip(items, items[1:]):
        if current.start < previous.start - 1e-6:
            raise ValueError("所选条目在原片时间上是乱序的，不能拼成一条字幕")
    return min(item.start for item in items), max(item.end for item in items)


def _to_int(value: object) -> int | None:
    """把模型给的数字（可能是字符串、浮点）转成整数。"""

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(round(value))
    if isinstance(value, str) and re.fullmatch(r"\s*\d+\s*", value):
        return int(value.strip())
    return None


def _cue_lines(cues: list[RawCue]) -> str:
    """拼出给模型看的字幕清单：编号 + 起止秒 + 文本。"""

    lines = []
    for cue in cues:
        text = cue.text.replace("\n", " ").strip()
        lines.append(f"[{cue.index}] {cue.start:.1f}-{cue.end:.1f} {text}")
    return "\n".join(lines)


def _render_prompt(template: str, **values: object) -> str:
    """用 str.format 渲染提示词；字幕文本里的花括号已在模板里转义。"""

    return template.format(**values)


def _load_template(name: str) -> str:
    path = PROMPT_DIR / name
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover - 文件缺失属于部署错误
        raise ClipWriterError(f"提示词模板缺失：{path}") from exc


# ---------------------------------------------------------------- 钩子生成


def _parse_hooks(payload: dict, cues: list[RawCue]) -> tuple[list[Hook], list[str]]:
    """校验模型给出的钩子区间，文本和时间一律以原 SRT 为准。"""

    raw_hooks = payload.get("hooks")
    if not isinstance(raw_hooks, list) or not raw_hooks:
        raise ClipWriterError("模型没有返回任何开头钩子，请重试。")

    warnings: list[str] = []
    hooks: list[Hook] = []
    seen: set[tuple[int, int]] = set()
    total = len(cues)

    for item in raw_hooks:
        if not isinstance(item, dict):
            continue
        a = _to_int(item.get("a"))
        b = _to_int(item.get("b"))
        if a is None or b is None:
            continue
        if a > b:
            a, b = b, a
        if a < 1 or b > total:
            continue
        if (a, b) in seen:
            continue

        text = _cue_text(cues, a, b)
        if not text:
            continue

        try:
            start, end = _range_bounds(cues, a, b)
        except ValueError as exc:
            warnings.append(f"钩子第 {a}~{b} 条已忽略：{exc}")
            continue
        duration = max(0.0, end - start)
        if duration <= 0.2:
            continue
        if duration > 8.0:
            warnings.append(f"钩子第 {a}~{b} 条时长 {duration:.1f}s 偏长，已忽略")
            continue

        seen.add((a, b))
        hook_type = str(item.get("type") or "").strip()
        if hook_type not in ALLOWED_HOOK_TYPES:
            hook_type = "其他"
        hooks.append(
            Hook(
                id=f"h{len(hooks) + 1}",
                type=hook_type,
                a=a,
                b=b,
                start=start,
                end=end,
                text=text,
                reason=str(item.get("reason") or "").strip()[:200],
            )
        )

    if not hooks:
        raise ClipWriterError(
            "模型给出的钩子区间都不合法（越界或时长过长），请重试。"
        )
    return hooks, warnings


def generate_hooks(
    cues: list[RawCue],
    count: int = 4,
    chat_fn: ChatFn | None = None,
    max_attempts: int = 2,
) -> tuple[list[Hook], list[str]]:
    """让模型挑 3~5 个候选开头，返回 (钩子列表, 警告)。"""

    if not cues:
        raise ClipWriterError("没有可用字幕，无法生成开头钩子。")
    if len(cues) > 1500:
        raise ClipWriterError(
            f"字幕共 {len(cues)} 条，单次提示过长。建议先把直播按商品切成几段再上传。"
        )

    chat_fn = chat_fn or llm.chat
    template = _load_template("hooks.md")
    base_prompt = _render_prompt(
        template,
        count=count,
        cues=_cue_lines(cues),
    )
    messages: list[dict] = [
        {
            "role": "system",
            "content": "你是严谨的抖音女装直播切片剪辑师，只输出 JSON，不改写任何原文。",
        },
        {"role": "user", "content": base_prompt},
    ]

    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        raw = chat_fn(messages)
        try:
            payload = _extract_json(raw)
            hooks, warnings = _parse_hooks(payload, cues)
        except ClipWriterError as exc:
            last_error = exc
            if attempt >= max_attempts:
                break
            messages = messages + [
                {"role": "assistant", "content": raw[:4000]},
                {
                    "role": "user",
                    "content": f"上一次输出不合格：{exc}。请严格按 JSON 结构重新输出，不要解释。",
                },
            ]
            continue

        if len(hooks) < 3:
            warnings.append(
                f"模型只给出 {len(hooks)} 个合格钩子，建议换一个更强的模型补足 3 个以上"
            )
        return hooks[:6], warnings

    raise ClipWriterError(str(last_error) if last_error else "钩子生成失败，请重试。")


# ---------------------------------------------------------------- 切片生成


def _normalize_segments(
    payload: dict, cues: list[RawCue]
) -> tuple[list[Segment], list[str]]:
    """校验片段区间：不越界、不重叠，相邻的自动合并。"""

    raw_segments = payload.get("segments")
    if not isinstance(raw_segments, list) or not raw_segments:
        raise ClipWriterError("模型没有返回任何片段，请重试。")

    total = len(cues)
    warnings: list[str] = []
    ranges: list[tuple[int, int, str, str]] = []
    seen: set[tuple[int, int]] = set()

    for item in raw_segments:
        if not isinstance(item, dict):
            continue
        a = _to_int(item.get("a"))
        b = _to_int(item.get("b"))
        if a is None or b is None:
            continue
        if a > b:
            a, b = b, a
        if a < 1 or b > total:
            continue
        if (a, b) in seen:
            warnings.append(f"片段 {a}~{b} 重复出现，已去重")
            continue
        seen.add((a, b))

        step = str(item.get("step") or "").strip()
        if step not in ALLOWED_STEPS:
            step = "价值"
        ranges.append((a, b, step, str(item.get("reason") or "").strip()[:200]))

    if not ranges:
        raise ClipWriterError("模型给出的片段区间全部越界或非法，请重试。")

    # 重叠检查按时间排序做，但输出顺序必须保持模型给的播放顺序
    by_time = sorted(ranges, key=lambda row: (row[0], row[1]))
    for left, right in zip(by_time, by_time[1:]):
        if right[0] <= left[1]:
            raise ClipWriterError(
                f"片段 {left[0]}~{left[1]} 与 {right[0]}~{right[1]} 在原片时间上重叠，"
                "同一段画面不能重复使用，请重剪。"
            )

    # 相邻但不重叠的片段保持独立，避免打乱播放顺序
    for left, right in zip(by_time, by_time[1:]):
        if right[0] == left[1] + 1:
            warnings.append(
                f"片段 {left[0]}~{left[1]} 与 {right[0]}~{right[1]} 在原片相邻，建议合并成一段"
            )

    segments: list[Segment] = []
    for a, b, step, reason in ranges:
        text = _cue_text(cues, a, b)
        if not text:
            warnings.append(f"片段 {a}~{b} 没有文字，已跳过")
            continue
        try:
            start, end = _range_bounds(cues, a, b)
        except ValueError as exc:
            raise ClipWriterError(f"片段 {a}~{b}：{exc}") from exc
        if end - start > MAX_SEGMENT_DURATION:
            raise ClipWriterError(
                f"片段 {a}~{b} 长达 {end - start:.1f}s，超过单条 {MAX_SEGMENT_DURATION:.0f}s 上限，请拆开。"
            )
        segments.append(
            Segment(a=a, b=b, step=step, reason=reason, start=start, end=end, text=text)
        )

    if not segments:
        raise ClipWriterError("没有可用的成片片段，请重试。")
    return segments, warnings


def _ensure_hook_first(
    segments: list[Segment], hook: Hook
) -> tuple[list[Segment], list[str]]:
    """确认所选钩子被选中，并把它放到成片第一条。"""

    index = next(
        (
            position
            for position, segment in enumerate(segments)
            if segment.a <= hook.a and segment.b >= hook.b
        ),
        None,
    )
    if index is None:
        raise ClipWriterError(
            f"生成的切片没有包含所选开头钩子（第 {hook.a}~{hook.b} 条），请重试。"
        )

    segment = segments[index]
    warnings: list[str] = []
    if segment.a != hook.a or segment.b != hook.b:
        warnings.append(
            f"开头段包含钩子且向两侧扩展为第 {segment.a}~{segment.b} 条，已保留"
        )
    if index != 0:
        warnings.append("开头钩子原本不在第一条，已按提示词规则前置")
        segments = [segment] + segments[:index] + segments[index + 1 :]
    return segments, warnings


def _parse_candidate_items(
    raw_items: object, cues: list[RawCue], occupied: list[tuple[int, int]]
) -> tuple[list[Segment], list[str]]:
    """把模型给的候选片段列表解析成 Segment，并做去重、防重叠、时长校验。

    occupied 是已占用的条目区间（通常是成片段），与它重叠的候选会被跳过。
    """

    if not isinstance(raw_items, list) or not raw_items:
        return [], []

    total = len(cues)
    warnings: list[str] = []
    result: list[Segment] = []
    used: list[tuple[int, int]] = list(occupied)

    def overlaps(a: int, b: int) -> bool:
        return any(not (b < x or a > y) for x, y in used)

    for item in raw_items:
        if not isinstance(item, dict):
            continue
        a = _to_int(item.get("a"))
        b = _to_int(item.get("b"))
        if a is None or b is None:
            continue
        if a > b:
            a, b = b, a
        if a < 1 or b > total:
            continue
        if overlaps(a, b):
            # 与已用片段重叠，跳过（不算错误，可能是模型不严谨）
            continue

        text = _cue_text(cues, a, b)
        if not text:
            continue
        try:
            start, end = _range_bounds(cues, a, b)
        except ValueError:
            continue
        if end - start <= 0.2:
            continue
        if end - start > MAX_SEGMENT_DURATION:
            warnings.append(
                f"候选片段 {a}~{b} 长达 {end - start:.1f}s，超过单条上限，已忽略"
            )
            continue

        step = str(item.get("step") or "").strip()
        if step not in ALLOWED_STEPS:
            step = "价值"
        result.append(
            Segment(
                a=a,
                b=b,
                step=step,
                reason=str(item.get("reason") or "").strip()[:200],
                start=start,
                end=end,
                text=text,
            )
        )
        used.append((a, b))

    return result, warnings


def _parse_spare(
    payload: dict, cues: list[RawCue], segments: list[Segment]
) -> tuple[list[Segment], list[str]]:
    """解析成片同一次输出里的 spare（可选字段）。与成片段不重叠，不合格项跳过。

    模型没给、或全部不合格时，返回空列表，不影响成片生成。
    """

    occupied = [(seg.a, seg.b) for seg in segments]
    return _parse_candidate_items(payload.get("spare"), cues, occupied)


# 展示类文案线索：主播在做“给看整体/转一圈/上身”等展示动作
SHOW_HINTS = (
    "转一圈", "转个圈", "看整体", "看全身", "全身效果", "上身效果",
    "上身给你看", "拉远看", "拉近看", "看下版型", "看这个版型",
    "这样搭", "搭一套", "搭配看", "走两步", "背影",
    "前后看", "侧面看", "看侧面",
)

# 赞美类文案线索：主播在夸这件衣服（避免“美”“香”等单字误报，用双字以上词）
PRAISE_HINTS = (
    "太漂亮", "好漂亮", "漂亮", "好看", "太好看了", "绝了", "绝美",
    "高级感", "有质感", "质感好", "显瘦", "显白", "谁穿谁好看",
    "爱了", "心动", "超级好看", "真的好看",
)

# 价格划算类文案线索：主播在感叹便宜/性价比（低优先级高光）。
# 都用双字以上词，避免“值”“香”等单字误报（如“价值”“香味”）。
PRICE_PRAISE_HINTS = (
    "便宜", "性价比", "划算", "太值", "真值", "很值", "超值",
    "更香", "太香", "捡漏", "抢漏", "薅到", "不贵", "实惠", "省钱",
)

# 尺码类文案线索：主播在报尺码表/怎么选码（高价值成交信息，不能漏）。
# 含“斤”时都算，因为女装直播报尺码几乎都用体重。
SIZE_HINTS = (
    "尺码", "码数", "卡码", "拍大", "拍小", "拍大一码", "拍小一码",
    "正常拍", "穿到", "能穿", "斤",
)

# 实穿优点（价值点）线索：保暖、柔软、不显胖等，都是卖点，不能当闲聊掠过。
# “轻”单独用正则判定（见 _has_value_point），避免“年轻”误报。
VALUE_POINT_HINTS = (
    "轻便", "轻盈", "不重", "轻薄",
    "保暖", "柔软", "绵密", "不易皱", "不起坨", "不起球", "不掉毛",
    "垂坠", "显高", "百搭", "不过时", "好打理", "不挑人",
)

# “很轻 / 非常的轻 / 特别轻 / 超轻”这种夸奖轻的句式（强调词 + 可选“的” + 轻）
LIGHT_PATTERN = re.compile(r"(很|非常|特别|超|整|挺|极|太|真的|相当)的?轻")


def _text_hits(text: str, hints: tuple[str, ...]) -> bool:
    """判断文本是否命中某类线索词，并排除紧跟“不/没/别/差”的否定/反讽。

    例如“它都不好看”里的“好看”被前面的“不”否定，“性价比不高”被后面的“不”否定，
    均不计入命中。
    """

    for hint in hints:
        start = 0
        while True:
            pos = text.find(hint, start)
            if pos < 0:
                break
            prev = text[pos - 1] if pos > 0 else ""
            next_ch = text[pos + len(hint)] if pos + len(hint) < len(text) else ""
            if prev not in {"不", "没", "别", "差"} and next_ch not in {"不", "没", "差"}:
                return True
            start = pos + len(hint)
    return False


def _cues_have_hint(cues: list[RawCue], hints: tuple[str, ...]) -> bool:
    """扫描整份字幕里是否存在某类线索词（不依赖模型，纯文本匹配）。"""

    return any(_text_hits(cue.text, hints) for cue in cues)


def _cues_have_value_point(cues: list[RawCue]) -> bool:
    """扫描整份字幕里是否存在“轻/保暖/柔软”这类实穿优点。"""

    return any(_has_value_point(cue.text) for cue in cues)


def _is_size_segment(segment: Segment, *, strict: bool = False) -> bool:
    """判断某段是不是尺码段。

    strict=True 时只认模型显式标了“尺码”的段（用于位置校验，避免一句
    “S 码能穿到 155 斤”这种中途飘过的句子误报）；否则也认文案在报尺码的段。
    """

    if segment.step == "尺码":
        return True
    return not strict and _text_hits(segment.text, SIZE_HINTS)


def _has_value_point(text: str) -> bool:
    """判断文本是否包含实穿优点（体感/实用性卖点）。

    “轻”用正则单独判（捕获“而且非常轻”“这件非常的非常的轻啊”），
    不会把“可能会年轻一点”误当成“轻”。
    """

    return LIGHT_PATTERN.search(text) is not None or _text_hits(text, VALUE_POINT_HINTS)


def _has_size_table(text: str) -> bool:
    """判断文本里是否包含“105 115 125 能穿到 140 斤”这类完整尺码表。

    规则：出现两个以上尺码数字（阿拉伯或中文，如“一百零五 一百一十五”），
    且带体重/穿码字样。单个“S 码能穿到 155 斤”不算尺码表。
    """

    if not _text_hits(text, ("穿到", "斤", "码")):
        return False
    # 阿拉伯数字：105 / 140
    numbers = re.findall(r"\d{2,3}", text)
    if len(numbers) >= 2:
        return True
    # 中文数字：一百零五 / 一百一十五 / 一百三十五（以“百”为核心特征）
    chinese = re.findall(r"[一二三四五六七八九十]{0,2}百[零一二三四五六七八九十]{0,3}", text)
    return len(chinese) >= 2


def _highlight_warnings(
    cues: list[RawCue], segments: list[Segment], spare: list[Segment]
) -> list[str]:
    """高光片段软提示：字幕里有展示/赞美/场景线索、但没被选进成片或候选库时提醒。

    只做提示，绝不阻断生成；字幕里本来就没有的，不提示、也不硬套。
    """

    warnings: list[str] = []
    picked_text = "".join(seg.text for seg in segments + spare)

    if _cues_have_hint(cues, SHOW_HINTS) and not _text_hits(picked_text, SHOW_HINTS):
        warnings.append(
            "字幕里有“展示/上身/看整体”等内容，但成片和候选库都没有展示类片段，"
            "建议补充一段完整展示画面"
        )

    if _cues_have_hint(cues, PRAISE_HINTS) and not _text_hits(picked_text, PRAISE_HINTS):
        warnings.append(
            "字幕里有“太漂亮/好看/绝了”等赞美内容，但成片和候选库都没有赞美类片段，"
            "建议补充一段赞美衣服的画面"
        )

    # 价格划算类（低优先级）：字幕里有、但没被选入时，轻声提醒一句
    if _cues_have_hint(cues, PRICE_PRAISE_HINTS) and not _text_hits(
        picked_text, PRICE_PRAISE_HINTS
    ):
        warnings.append(
            "字幕里有“好便宜/性价比高/划算”等价格感叹，可顺手放进候选库备用（优先级较低）"
        )

    # 尺码类：字幕里有尺码、但候选库没有一条完整尺码表时提醒（供替换备用）
    if _cues_have_hint(cues, SIZE_HINTS) and not any(
        _is_size_segment(segment) for segment in spare
    ):
        warnings.append(
            "候选库没有尺码片段，建议补一条完整尺码表（如“105 115 125 能穿到 140 斤”"
            "以及“卡码正常拍/卡码拍大”），方便随时替换进成片"
        )

    # 实穿优点：轻/保暖/柔软/不显胖 都是卖点，漏了要提醒
    if _cues_have_value_point(cues) and not _has_value_point(picked_text):
        warnings.append(
            "字幕里有“很轻/保暖/柔软/垂坠”等实穿优点，但成片和候选库都没体现，"
            "建议补一段（例如“而且非常轻”这种也是卖点，不要当闲聊跳过）"
        )

    return warnings


def build_srt_text(cues: list[RawCue], segments: Iterable[Segment]) -> str:
    """按成片顺序输出 SRT，时间戳完全沿用原片。"""

    blocks: list[str] = []
    for order, segment in enumerate(segments, start=1):
        blocks.append(
            f"{order}\n"
            f"{format_srt_time(segment.start)} --> {format_srt_time(segment.end)}\n"
            f"{segment.text}"
        )
    return "\n\n".join(blocks) + "\n"


def generate_clip(
    cues: list[RawCue],
    hook: Hook,
    chat_fn: ChatFn | None = None,
    max_attempts: int = 3,
) -> ClipResult:
    """按已选钩子生成完整切片 SRT。"""

    if not cues:
        raise ClipWriterError("没有可用字幕，无法生成切片。")

    chat_fn = chat_fn or llm.chat
    template = _load_template("clip.md")
    base_prompt = _render_prompt(
        template,
        hook_a=hook.a,
        hook_b=hook.b,
        hook_text=hook.text,
        cues=_cue_lines(cues),
    )
    messages: list[dict] = [
        {
            "role": "system",
            "content": "你是严谨的抖音女装直播切片剪辑师，只输出 JSON，不改写任何原文。",
        },
        {"role": "user", "content": base_prompt},
    ]

    warnings: list[str] = []
    last_error: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        raw = chat_fn(messages)
        try:
            payload = _extract_json(raw)
            segments, segment_warnings = _normalize_segments(payload, cues)
            segments, hook_warnings = _ensure_hook_first(segments, hook)
            warnings = segment_warnings + hook_warnings

            duration = sum(segment.duration for segment in segments)
            if duration < MIN_DURATION_HARD or duration > MAX_DURATION_HARD:
                raise ClipWriterError(
                    f"成片总时长 {duration:.1f}s，不在允许的 "
                    f"{MIN_DURATION_HARD:.0f}~{MAX_DURATION_HARD:.0f}s 内，"
                    "请增删片段后重新输出。"
                )
            if duration < MIN_DURATION_RECOMMENDED:
                warnings.append(
                    f"成片 {duration:.1f}s 偏短，建议补到 {MIN_DURATION_RECOMMENDED:.0f}s 以上"
                )
            elif duration > MAX_DURATION_RECOMMENDED:
                warnings.append(
                    f"成片 {duration:.1f}s 偏长，建议压到 {MAX_DURATION_RECOMMENDED:.0f}s 以内"
                )
            if len(segments) < 6:
                warnings.append(f"只有 {len(segments)} 段，节奏可能偏慢，建议 12~18 段")
            if segments[-1].step not in {"结尾", "尺码", "稀缺", "福利"}:
                warnings.append(
                    "最后一段没有落在成交价/尺码/稀缺上，建议重选钩子或重新生成"
                )

            # 尺码是成交必备信息：原文里有尺码、成片却既没有尺码段也没有完整尺码表时，要求重选
            if _cues_have_hint(cues, SIZE_HINTS):
                has_size = any(_is_size_segment(segment) for segment in segments) or any(
                    _has_size_table(segment.text) for segment in segments
                )
                if not has_size:
                    raise ClipWriterError(
                        "成片没有尺码片段。字幕里有完整尺码信息，必须选一段进来，"
                        "优先选“105 115 125 能穿到 140 斤”这类完整尺码表，"
                        "并放在结尾前 2~3 段内；若同时有“卡码正常拍/卡码拍大”，"
                        "也一并作为尺码段保留。"
                    )

        except ClipWriterError as exc:
            last_error = exc
            if attempt >= max_attempts:
                break
            messages = messages + [
                {"role": "assistant", "content": raw[:6000]},
                {
                    "role": "user",
                    "content": (
                        f"上一次输出不合格：{exc}"
                        "请只输出修正后的 JSON，结构和字段名保持不变。"
                    ),
                },
            ]
            continue

        memory_point = str(payload.get("memory_point") or "").strip()
        if not memory_point:
            memory_point = "未填写核心记忆点"
            warnings.append("模型没有给出核心记忆点")

        # 解析候选片段库（模型没给时为空，不影响成片）
        spare, spare_warnings = _parse_spare(payload, cues, segments)
        warnings = warnings + spare_warnings

        # 高光软提示：字幕里有展示/赞美线索但没选入时提醒（不阻断）
        warnings = warnings + _highlight_warnings(cues, segments, spare)
        # 尺码位置提醒：显式标“尺码”的段应该靠后（结尾前 2~3 段），方便看完价格直接对号入座
        size_positions = [
            position
            for position, segment in enumerate(segments, start=1)
            if _is_size_segment(segment, strict=True)
        ]
        if size_positions and min(size_positions) < len(segments) - 2:
            warnings.append(
                "尺码段位置偏前，建议移到结尾前 2~3 段，先讲价值再报尺码更容易成交"
            )

        return ClipResult(
            hook=hook,
            segments=segments,
            memory_point=memory_point,
            srt_text=build_srt_text(cues, segments),
            warnings=warnings,
            raw_response=raw,
            spare=spare,
        )

    raise ClipWriterError(str(last_error) if last_error else "切片生成失败，请重试。")
