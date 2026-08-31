# -*- coding: utf-8 -*-

import typing
from dataclasses import dataclass

ProgressSource: typing.TypeAlias = typing.Literal[
    "tool",
    "enhancement",
]


@dataclass(frozen=True, slots=True)
class ProgressView:
    """描述工具执行期间的可见进度。"""

    text: str
    source: ProgressSource
    tool_name: str
