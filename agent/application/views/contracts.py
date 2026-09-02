# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

from .approval import ApprovalView
from .hooks import HookRunView
from .patch import PatchView
from .plan import (
    PlanStepsStartView,
    PlanUpdateView,
)
from .progress import ProgressView
from .run import (
    FailureView,
    LifecycleView,
    RunCompletedView,
    RunIncompleteView,
    RunStartedView,
)
from .tools import (
    BatchCompletedView,
    BatchStartView,
    GenericToolResultView,
    NativeToolResultView,
    ToolStartView,
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
    | PatchView
    | PlanUpdateView
    | PlanStepsStartView
    | BatchStartView
    | BatchCompletedView
    | FailureView
    | LifecycleView
    | ProgressView
)


class PresentationSink(typing.Protocol):
    """接收跨前端共享的应用展示结果。"""

    async def emit(self, view: PresentationView) -> None:
        """发送一项结构化展示数据。"""
        ...


if __name__ == '__main__':
    pass
