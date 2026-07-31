# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import (
    dataclass,
    field
)

FormattedText: typing.TypeAlias = list[tuple[str, str]]


@dataclass(frozen=True, slots=True)
class FragmentBlock(object):
    """保存无需再次转换的 prompt_toolkit 文本片段。"""
    fragments: tuple[tuple[str, str], ...]


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


if __name__ == '__main__':
    pass
