# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from .models import (
    ApprovalView,
    BatchCompletedView,
    BatchStartView,
    FailureView,
    GenericToolResultView,
    HookRunView,
    LifecycleView,
    NativeToolResultView,
    PlanStepsStartView,
    PlanUpdateView,
    ProgressView,
    RunCompletedView,
    RunIncompleteView,
    RunStartedView,
    ToolStartView
)

PresentationView: typing.TypeAlias = (
    RunStartedView
    | RunCompletedView
    | RunIncompleteView
    | ApprovalView
    | HookRunView
    | ToolStartView
    | GenericToolResultView
    | NativeToolResultView
    | PlanUpdateView
    | PlanStepsStartView
    | BatchStartView
    | BatchCompletedView
    | FailureView
    | LifecycleView
    | ProgressView
)


class PresentationSink(typing.Protocol):
    """接收与具体终端渲染方式无关的展示数据。"""

    async def emit(self, view: PresentationView) -> None:
        """发送一项结构化展示数据。"""
        ...


if __name__ == '__main__':
    pass
