# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass

from agent.application.views.tools import (
    GenericToolResultView,
    ToolStartView,
)
from agent.ports.presentation import (
    StyledBlock,
    TextSpan,
    TextStyle,
)
from frontends.terminal.text_layout import clip_display_text
from frontends.terminal.styles import ACTION_TOOL_CALL_STYLE
from .tool import (
    render_generic_tool_result_raw_text,
    render_generic_tool_result_transcript_view,
    render_generic_tool_result_view,
    render_tool_start_raw_text,
    render_tool_start_transcript_view,
    render_tool_start_view,
)


@dataclass(frozen=True, slots=True)
class ToolCallSnapshot:
    """保存单笔调用的展示快照；观察结束不代表工具已停止或执行失败。"""

    view: ToolStartView | GenericToolResultView
    observation_ended: typing.Literal["interrupted", "unconfirmed"] | None = None

    @property
    def pending(self) -> bool:
        """返回当前调用是否仍在等待结果。"""
        return isinstance(self.view, ToolStartView) and self.observation_ended is None


def render_call_snapshot(
    snapshot: ToolCallSnapshot,
    *,
    terminal_width: int,
    measure_width: typing.Callable[[str], int],
) -> StyledBlock:
    """渲染同一位置的开始、结果或观察结束摘要。"""
    view = snapshot.view
    if snapshot.observation_ended is not None:
        action = "Interrupted" if snapshot.observation_ended == "interrupted" else "Unconfirmed"
        title = clip_display_text(
            f"• {action} {view.name}", width=terminal_width, measure_width=measure_width,
        )
        detail = clip_display_text("  └ Result unconfirmed", width=terminal_width, measure_width=measure_width)
        return StyledBlock(plain_text=f"{title}\n{detail}", spans=(
            TextSpan(title[:2], TextStyle()),
            TextSpan(title[2:], ACTION_TOOL_CALL_STYLE),
            TextSpan(f"\n{detail}", TextStyle(dim=True)),
        ))
    if isinstance(view, ToolStartView):
        return render_tool_start_view(view, terminal_width=terminal_width, measure_width=measure_width)
    return render_generic_tool_result_view(view, terminal_width=terminal_width, measure_width=measure_width)


def call_transcript(snapshot: ToolCallSnapshot) -> str:
    """保留完整参数和结果，并标明未取得结果的观察终态。"""
    view = snapshot.view
    block = (
        render_tool_start_transcript_view(view)
        if isinstance(view, ToolStartView)
        else render_generic_tool_result_transcript_view(view)
    )
    suffix = "" if snapshot.observation_ended is None else f"\n{snapshot.observation_ended}: result unconfirmed"
    return block.plain_text + suffix


def call_raw_text(snapshot: ToolCallSnapshot) -> str:
    """返回同一调用可复制的完整内容。"""
    view = snapshot.view
    raw = render_tool_start_raw_text(view) if isinstance(view, ToolStartView) else render_generic_tool_result_raw_text(view)
    suffix = "" if snapshot.observation_ended is None else f"\n{snapshot.observation_ended}: result unconfirmed"
    return raw + suffix


if __name__ == '__main__':
    pass
