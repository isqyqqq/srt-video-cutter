from pathlib import Path

import pytest

from app.srt_parser import SrtParseError, decode_srt, parse_srt_bytes, parse_srt_text


def test_parse_real_sample(sample_srt: Path):
    cues, encoding = parse_srt_bytes(sample_srt.read_bytes())

    assert encoding == "utf-8"
    assert len(cues) == 11
    assert cues[0].start == pytest.approx(117.466)
    assert cues[0].end == pytest.approx(123.700)
    assert cues[0].text.startswith("双十一")
    assert cues[-1].start == pytest.approx(129.266)
    assert cues[-1].end == pytest.approx(133.766)


def test_parse_crlf_and_dot_separator():
    text = (
        "1\r\n"
        "00:00:01.500 --> 00:00:02.750\r\n"
        "第一行\r\n"
        "第二行\r\n\r\n"
        "2\r\n"
        "00:00:03,000 --> 00:00:04,000\r\n"
        "另一段\r\n"
    )
    cues = parse_srt_text(text)

    assert len(cues) == 2
    assert cues[0].start == pytest.approx(1.5)
    assert cues[0].end == pytest.approx(2.75)
    assert cues[0].text == "第一行 第二行"
    assert cues[1].source_index == 2


def test_parse_bom_and_gb18030():
    text = "1\n00:00:01,000 --> 00:00:02,000\n中文测试\n"
    bom_data = ("\ufeff" + text).encode("utf-8")
    cues, encoding = parse_srt_bytes(bom_data)
    assert encoding == "utf-8-sig"
    assert cues[0].text == "中文测试"

    gbk_data = text.encode("gb18030")
    cues, encoding = parse_srt_bytes(gbk_data)
    assert encoding == "gb18030"
    assert cues[0].text == "中文测试"


def test_missing_sequence_number_is_allowed():
    cues = parse_srt_text("00:00:01,000 --> 00:00:02,000\n没有序号\n")
    assert len(cues) == 1
    assert cues[0].source_index is None
    assert cues[0].text == "没有序号"


def test_parse_without_blank_lines():
    text = (
        "1\n00:00:01,000 --> 00:00:02,000\n第一段\n"
        "2\n00:00:03,000 --> 00:00:04,000\n第二段\n"
    )
    cues = parse_srt_text(text)
    assert len(cues) == 2
    assert cues[1].start == pytest.approx(3.0)
    assert cues[1].text == "第二段"


def test_invalid_time_line_raises():
    with pytest.raises(SrtParseError, match="时间格式"):
        parse_srt_text("1\n00:00:xx --> 00:00:02,000\n坏数据\n")


def test_empty_file_raises():
    with pytest.raises(SrtParseError):
        parse_srt_text("   \n\n")


def test_decode_unknown_bytes_raises():
    # gb18030 几乎能解码所有字节，这里用非法 UTF-16 BOM 触发失败
    with pytest.raises(SrtParseError):
        decode_srt(b"\xff\xfe\x00")
