# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

from agent.application.turns.compact_result import compact_failure_message

from agent.application.views import (
    ContextCompactionView,
    FailureView,
    LifecycleView,
    RunIncompleteView,
)
from agent.ports.presentation import (
    StyledBlock,
    TextSpan,
)
from frontends.terminal.formatting import format_compaction_duration
from frontends.terminal.renderers.failure import render_failure_block
from frontends.terminal.renderers.lifecycle_parts import render_lifecycle_display_parts
from frontends.terminal.semantic_styles import (
    TerminalSemanticRole,
    semantic_text_style,
)


def render_failure_view(
    view: FailureView,
    *,
    terminal_width: int | None = None,
    measure_width: typing.Callable[[str], int] | None = None,
) -> StyledBlock:
    """把运行失败视图转换为中立展示块。"""
    return render_failure_block(
        view.phase,
        view.error,
        terminal_width=terminal_width,
        measure_width=measure_width,
    )


def render_incomplete_view(
    view: RunIncompleteView,
    *,
    terminal_width: int | None = None,
    measure_width: typing.Callable[[str], int] | None = None
) -> StyledBlock:
    """把未完整结束的运行转换为中立展示块。"""
    return render_failure_block(
        "turn.incomplete",
        view.reason or "The response is incomplete",
        terminal_width=terminal_width,
        measure_width=measure_width,
    )


def render_lifecycle_view(view: LifecycleView) -> StyledBlock:
    """把生命周期事件视图转换为中立展示块。"""
    title = f"• {view.text}"
    return StyledBlock(
        plain_text=title,
        spans=tuple(render_lifecycle_display_parts(title)),
    )


def render_context_compaction_view(
    view: ContextCompactionView,
) -> StyledBlock:
    """把上下文压缩完成事实转换为稳定信息块。"""
    if view.status == "failed":
        return render_compaction_failed(compact_failure_message(view.error_type or ""))
    return render_compaction_completed(view.latency_ms)


def render_compaction_completed(latency_ms: int | None) -> StyledBlock:
    """为自动、手动与历史压缩生成一致的稳定完成块。"""
    title = "• Context compacted"
    spans = render_lifecycle_display_parts(title)
    duration = format_compaction_duration(latency_ms)
    if duration:
        suffix = f"  · {duration}"
        title += suffix
        spans.append(TextSpan(suffix, semantic_text_style(TerminalSemanticRole.SECONDARY)))
    return StyledBlock(
        plain_text=title,
        spans=tuple(spans),
    )


def render_compaction_interrupted() -> StyledBlock:
    """展示压缩所属 Turn 已确认中断的事实。"""
    title = "• Context compaction"
    suffix = " · interrupted"
    return StyledBlock(
        plain_text=title + suffix,
        spans=(
            *render_lifecycle_display_parts(title),
            TextSpan(suffix, semantic_text_style(TerminalSemanticRole.SECONDARY)),
        ),
    )


def render_compaction_failed(message: str) -> StyledBlock:
    """展示压缩失败及简短原因，不生成完成耗时。"""
    title = "• Context compaction failed"
    detail = f"\n  └ {message}"
    return StyledBlock(
        plain_text=title + detail,
        spans=(
            *render_lifecycle_display_parts(title),
            TextSpan(detail, semantic_text_style(TerminalSemanticRole.SECONDARY)),
        ),
    )


if __name__ == '__main__':
    pass
