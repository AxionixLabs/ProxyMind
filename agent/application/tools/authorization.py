# -*- coding: utf-8 -*-
# Notes: ==== Mind(TM) ====

import typing


class ExecutionAuthorizationError(ValueError):
    """描述本地工具参数不满足客户端执行约束的错误。"""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


class ToolTurnInterrupted(RuntimeError):
    """表示本地工具已请求并确认中断当前 Turn。"""


def reject_model_execution(arguments: dict[str, typing.Any]) -> None:
    """拒绝模型参数携带客户端内部执行状态。"""
    if "execution" in arguments:
        raise ExecutionAuthorizationError(
            "model_execution_forbidden",
            "execution is not a model tool argument",
        )


__all__ = (
    "ExecutionAuthorizationError",
    "ToolTurnInterrupted",
    "reject_model_execution",
)
