# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass

MISSING = object()

TITLE_STYLE                 = "bold #D7E7FF"
PREVIEW_STYLE               = "dim #8FA4B8"
PREVIEW_PATH_STYLE          = "bold #A9B8C8"
PREVIEW_LINE_STYLE          = "bold #95A6B8"
PREVIEW_TEXT_STYLE          = "dim #A5B3C2"
PREVIEW_HUNK_STYLE          = "bold #8FB8FF"
PREVIEW_CODE_TEXT_STYLE     = "#BCC9D6"
PREVIEW_CODE_KEYWORD_STYLE  = "bold #B9A6D8"
PREVIEW_CODE_NAME_STYLE     = "#CAD5DF"
PREVIEW_CODE_STRING_STYLE   = "#A9CDBB"
PREVIEW_CODE_NUMBER_STYLE   = "#D3C27C"
PREVIEW_CODE_COMMENT_STYLE  = "dim #8FA4B8"
PREVIEW_CODE_OPERATOR_STYLE = "#AAB8C6"
PREVIEW_MORE_STYLE          = "dim #7E8FA3"
PREVIEW_COUNT_STYLE         = "bold #A0ADBA"
ERROR_STYLE                 = "bold #FF7A7A"
ERROR_PREVIEW_HEAD_STYLE    = "dim #C66A6A"
ERROR_PREVIEW_LINE_STYLE    = "#7DD3FC"
ERROR_PREVIEW_MESSAGE_STYLE = "#D98A8A"
ERROR_PREVIEW_TEXT_STYLE    = "dim #C66A6A"
SUCCESS_DOT_STYLE           = "bold #6EE7A8"
ERROR_DOT_STYLE             = "bold #FF6B6B"
DELTA_ADD_STYLE             = "bold #6EE7A8"
DELTA_REMOVE_STYLE          = "bold #FF8A8A"
ACTION_EDIT_STYLE           = "bold #6EE7A8"
ACTION_RUN_STYLE            = "bold #B8C7D9"
ACTION_TOOL_STYLE           = "bold #7DD3FC"
COMMAND_STYLE               = "#C8D2DD"
COMMAND_HEAD_STYLE          = "bold #4DE3FF"
COMMAND_FLAG_STYLE          = "#9AB7D4"
COMMAND_PATH_STYLE          = "bold #D2DAE3"
COMMAND_STRING_STYLE        = "#A8D5C2"
COMMAND_NUMBER_STYLE        = "#CFC17A"
COMMAND_OPERATOR_STYLE      = "bold #7D8A98"

MAX_PREVIEW_LINES         = 8
SCREEN_PREVIEW_LINES      = 5
MAX_PREVIEW_WIDTH         = 120
MAX_CODE_PREVIEW_LINES    = 24
SCREEN_CODE_PREVIEW_LINES = 12


@dataclass(frozen=True, slots=True)
class TracePreview(object):
    """保存完整预览、屏幕预览和省略行数。"""
    full: str = ""
    screen: str = ""
    omitted_lines: int = 0


def _short_text(value: typing.Any, limit: int = 120) -> str:
    """把任意值压缩为单行短文本。"""
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return f"{text[:max(0, limit - 3)]}..."


def _short_line(value: typing.Any, limit: int = 120) -> str:
    """截断单行文本并保留原有空白结构。"""
    text = str(value or "").rstrip()
    if len(text) <= limit:
        return text
    return f"{text[:max(0, limit - 3)]}..."


def _normalize_preview_lines(value: typing.Any) -> list[str]:
    """把预览内容归一化为按行拆分的文本列表。"""
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    if not text:
        return []
    return text.split("\n")


def _format_preview_lines(lines: list[str], *, max_lines: int) -> tuple[str, int]:
    """按行数和宽度限制格式化预览文本。"""
    clipped = [
        _short_line(line, MAX_PREVIEW_WIDTH)
        for line in lines[:max_lines]
    ]
    omitted = max(0, len(lines) - max_lines)
    if omitted:
        clipped.append(f"… +{omitted} lines")
    return "\n".join(clipped), omitted


def _preview_text(value: typing.Any, *, max_lines: int = MAX_PREVIEW_LINES) -> str:
    """生成普通文本预览。"""
    screen, _ = _format_preview_lines(_normalize_preview_lines(value), max_lines=max_lines)
    return screen


def _trace_preview_from_lines(lines: list[str]) -> TracePreview:
    """从文本行生成普通轨迹预览。"""
    full, _ = _format_preview_lines(lines, max_lines=MAX_PREVIEW_LINES)
    screen, omitted = _format_preview_lines(lines, max_lines=SCREEN_PREVIEW_LINES)
    return TracePreview(full=full, screen=screen, omitted_lines=omitted)


def _trace_code_preview_from_lines(lines: list[str]) -> TracePreview:
    """从代码行生成轨迹预览。"""
    full, _ = _format_preview_lines(lines, max_lines=MAX_CODE_PREVIEW_LINES)
    screen, omitted = _format_preview_lines(lines, max_lines=SCREEN_CODE_PREVIEW_LINES)
    return TracePreview(full=full, screen=screen, omitted_lines=omitted)


def _result_payload(data: typing.Any) -> dict[str, typing.Any]:
    """从工具结果中提取可用于渲染的 data 载荷。"""
    if not isinstance(data, dict):
        return {}

    results = data.get("results")
    if isinstance(results, list):
        for item in results:
            if not isinstance(item, dict):
                continue
            item_data = item.get("data")
            if isinstance(item_data, dict):
                return item_data

    nested = data.get("data")
    if isinstance(nested, dict):
        return nested

    return data


def _summary_lines(*items: tuple[str, typing.Any]) -> list[str]:
    """把键值对转换为摘要行。"""
    lines: list[str] = []
    for label, value in items:
        if value is None:
            text = ""
        elif isinstance(value, bool):
            text = str(value)
        else:
            text = str(value or "").strip()
        if text:
            lines.append(f"{label}: {text}")
    return lines


if __name__ == '__main__':
    pass
