# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from .models import (
    BatchCallView,
    BatchCompletedView,
    BatchResultView,
    BatchStartView
)


def build_batch_start_view(
    calls: typing.Iterable[tuple[str, dict[str, typing.Any]]],
) -> BatchStartView:
    """构建并行工具开始执行时的结构化展示数据。"""
    return BatchStartView(calls=tuple(
        BatchCallView(
            name=str(name),
            arguments=dict(arguments) if isinstance(arguments, dict) else {},
        )
        for name, arguments in calls
    ))


def build_batch_completed_view(
    results: typing.Iterable[tuple[str, bool, str]],
) -> BatchCompletedView:
    """构建并行工具执行完成时的结构化展示数据。"""
    return BatchCompletedView(results=tuple(
        BatchResultView(
            name=str(name),
            ok=bool(ok),
            text=str(text or ""),
        )
        for name, ok, text in results
    ))


if __name__ == '__main__':
    pass
