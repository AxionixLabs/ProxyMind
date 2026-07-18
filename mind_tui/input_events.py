# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass

InputEventKind = typing.Literal["submit", "queue", "rollback", "interrupt"]


@dataclass(frozen=True, slots=True)
class InputEvent:
    """表示由终端输入转换得到的语义事件。"""

    kind: InputEventKind
    text: str = ""
    shell_mode: bool = False


if __name__ == '__main__':
    pass
