# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import (
    dataclass,
    field
)
from pathlib import Path

FormattedText: typing.TypeAlias = list[tuple[str, str]]

TranscriptExportFormat: typing.TypeAlias = typing.Literal["markdown", "raw"]


class TranscriptExportResult(typing.Protocol):
    """描述记录导出回调返回的结构化结果。"""
    path: Path
    format: TranscriptExportFormat
    cell_count: int


@dataclass(frozen=True, slots=True)
class LineFill(object):
    """描述单行内容随终端宽度延伸的填充规则。"""
    character: str
    margin: int = 0


@dataclass(frozen=True, slots=True)
class FragmentBlock(object):
    """保存无需再次转换的 prompt_toolkit 文本片段。"""
    fragments: tuple[tuple[str, str], ...]
    line_fill: LineFill | None = None


@dataclass(frozen=True, slots=True)
class TranscriptBacktrackRequest(object):
    """描述从完整记录中重新编辑一条用户输入的请求。"""
    turn_id: str
    prompt: str
    attachments: tuple[dict[str, typing.Any], ...] = ()
    extras: dict[str, typing.Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MenuOption(object):
    """描述运行期选择菜单中的一项。"""
    value: typing.Any
    label: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class MenuRequest(object):
    """描述运行期内嵌选择菜单。"""
    title: str
    options: tuple[MenuOption, ...] = ()
    body: tuple[str, ...] = ()
    selected: int = 0
    status: str = ""
    help_text: str = "Up/Down select · Enter apply · Esc/q cancel"


@dataclass(frozen=True, slots=True)
class MailboxEntry(object):
    """描述全屏收件箱中的一条只读消息。"""
    key: str
    title: str
    message: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class MailboxRunRequest(object):
    """描述需要由 TUI 主循环串行执行的收件箱消息。"""
    message_id: str
    automatic: bool = False


if __name__ == '__main__':
    pass
