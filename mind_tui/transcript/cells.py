# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass

TranscriptCellKind = typing.Literal[
    "header",
    "user",
    "assistant",
    "trace",
    "approval",
    "error",
    "lifecycle"
]


@dataclass(slots=True)
class TranscriptCell:
    """保存一个可独立渲染的会话内容单元。"""

    kind: TranscriptCellKind
    text: str
    streaming: bool = False


if __name__ == '__main__':
    pass
