# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing


class ExecutionAuthorizationError(ValueError):
    """描述客户端工具参数不满足本地执行约束的错误。"""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


def reject_model_execution(arguments: dict[str, typing.Any]) -> None:
    """拒绝模型参数携带客户端内部执行状态。"""
    if "execution" in arguments:
        raise ExecutionAuthorizationError(
            "model_execution_forbidden",
            "execution is not a model tool argument",
        )


if __name__ == "__main__":
    pass
