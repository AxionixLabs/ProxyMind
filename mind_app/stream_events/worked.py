# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from rich.text import Text
from mind_core.design import Design
from mind_core.design.status.elapsed import format_elapsed
from mind_app.stream_events.compact_rule import (
    COMPACT_RULE_MAX_INNER_WIDTH,
    COMPACT_RULE_PADDING,
    COMPACT_RULE_TERMINAL_MARGIN,
    compact_rule_max_width,
    compact_rule_min_width
)

WORKED_RULE_STYLE      = "dim #6F7A86"
WORKED_LABEL_STYLE     = "bold #AFC7D8"
WORKED_RULE_CHAR       = "─"
WORKED_MIN_INNER_WIDTH = max(COMPACT_RULE_MAX_INNER_WIDTH, 96)


def worked_footer_text(elapsed_sec: float, *, width: int | None = None) -> str:
    """生成单行耗时页脚文本。"""
    elapsed      = format_elapsed(max(0.0, float(elapsed_sec or 0.0)))
    label        = f" Worked for {elapsed} "
    target_width = worked_footer_width(label, terminal_width=width)
    right        = WORKED_RULE_CHAR * max(1, target_width - len(label) - 1)
    return f"{WORKED_RULE_CHAR}{label}{right}"


def worked_footer_width(label: str, *, terminal_width: int | None = None) -> int:
    """计算与审批卡一致的页脚宽度。"""
    natural_width = len(label) + 2

    target_width  = max(
        natural_width,
        compact_rule_max_width(),
        WORKED_MIN_INNER_WIDTH + COMPACT_RULE_PADDING
    )

    if terminal_width is None:
        return target_width

    available_width = max(
        compact_rule_min_width(),
        int(terminal_width) - COMPACT_RULE_TERMINAL_MARGIN
    )

    return min(target_width, available_width)


def render_worked_footer(elapsed_sec: float, *, width: int | None = None) -> Text:
    """渲染耗时页脚。"""
    line  = worked_footer_text(elapsed_sec, width=width)
    label = f" Worked for {format_elapsed(max(0.0, float(elapsed_sec or 0.0)))} "
    start = line.find(label)

    out = Text(line, style=WORKED_RULE_STYLE)
    if start >= 0:
        out.stylize(WORKED_LABEL_STYLE, start, start + len(label))
    return out


def print_worked_footer(elapsed_sec: float) -> None:
    """按当前终端宽度打印耗时页脚。"""
    Design.console.print(
        render_worked_footer(elapsed_sec, width=getattr(Design.console, "width", 80))
    )
    Design.console.print()


if __name__ == '__main__':
    pass
