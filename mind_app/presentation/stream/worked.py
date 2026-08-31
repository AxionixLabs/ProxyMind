# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from agent.ports.presentation import (
    ApplicationSink,
    ApplicationView
)
from mind_app.presentation.models import StyledBlock, TextSpan, TextStyle
from mind_app.presentation.stream.compact_rule import full_rule_width
from mind_app.presentation.formatting import format_elapsed

WORKED_RULE_STYLE  = TextStyle(foreground="#414A54")
WORKED_LABEL_STYLE = TextStyle(foreground="#626D78")
WORKED_RULE_CHAR   = "─"


def worked_footer_text(elapsed_sec: float, *, width: int | None = None) -> str:
    """生成单行耗时页脚文本。"""
    elapsed      = format_elapsed(max(0.0, float(elapsed_sec or 0.0)))
    label        = f" Finished in {elapsed} "
    target_width = worked_footer_width(label, terminal_width=width)
    right        = WORKED_RULE_CHAR * max(1, target_width - len(label) - 1)
    return f"{WORKED_RULE_CHAR}{label}{right}"


def worked_footer_width(label: str, *, terminal_width: int | None = None) -> int:
    """按终端完整宽度计算页脚宽度。"""
    return full_rule_width(
        terminal_width=terminal_width, natural_width=len(label) + 2, margin=0
    )


def render_worked_footer(
    elapsed_sec: float,
    *,
    width: int | None = None,
) -> StyledBlock:
    """渲染耗时页脚。"""
    line  = worked_footer_text(elapsed_sec, width=width)
    label = f" Finished in {format_elapsed(max(0.0, float(elapsed_sec or 0.0)))} "
    start = line.find(label)

    if start < 0:
        return StyledBlock(
            plain_text=line,
            spans=(TextSpan(line, WORKED_RULE_STYLE),),
        )
    end = start + len(label)
    return StyledBlock(
        plain_text=line,
        spans=(
            TextSpan(line[:start], WORKED_RULE_STYLE),
            TextSpan(line[start:end], WORKED_LABEL_STYLE),
            TextSpan(line[end:], WORKED_RULE_STYLE),
        ),
    )


def emit_worked_footer(
    application: ApplicationSink,
    elapsed_sec: float
) -> None:
    """按当前前端宽度发送耗时页脚。"""
    application.emit(ApplicationView(
        type="run.worked",
        renderable=render_worked_footer(
            elapsed_sec,
            width=application.viewport.width,
        ),
        payload={"line_fill_character": WORKED_RULE_CHAR},
    ))
    application.emit(ApplicationView(type="run.gap"))


if __name__ == '__main__':
    pass
