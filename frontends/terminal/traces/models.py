# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TracePreview:
    """保存终端轨迹的完整预览、屏幕预览和省略行数。"""

    full: str = ""
    screen: str = ""
    omitted_lines: int = 0
    kind: str = "text"


@dataclass(frozen=True, slots=True)
class TraceEntry:
    """保存一条可独立渲染的终端工具轨迹。"""

    title: str
    preview: TracePreview
    ok: bool = True


if __name__ == '__main__':
    pass
