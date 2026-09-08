# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

from agent.application.turns.run_result import RunResult
from agent.ports import RemoteTurnRequestRecorder
from agent.protocol import (
    ReviewStreamRequest,
    RunCommand,
    SubmitReviewCommand,
)
from agent.protocol.json_value import ThawedJsonValue

__all__ = (
    "ReviewCommandExecutor",
    "ReviewTurnOperation",
)


class ReviewTurnOperation(typing.Protocol):
    """执行一次已持久化 Review 请求且不拥有本地 Run 状态。"""

    async def __call__(
        self,
        request: ReviewStreamRequest,
        environment_snapshot: dict[str, ThawedJsonValue] | None,
    ) -> RunResult:
        """提交或观察冻结请求并返回权威终态结果。"""
        ...


class ReviewCommandExecutor:
    """在远端操作前持久化完整 Review 请求。"""

    def __init__(
        self,
        operation: ReviewTurnOperation,
        *,
        request_recorder: RemoteTurnRequestRecorder,
    ) -> None:
        """绑定 Review 操作和不可省略的远端请求账本。"""
        if not callable(operation):
            raise TypeError("review turn operation must be callable")
        if not isinstance(request_recorder, RemoteTurnRequestRecorder):
            raise TypeError("review request recorder is required")
        self._operation = operation
        self._request_recorder = request_recorder

    async def __call__(self, command: RunCommand) -> RunResult:
        """保存冻结事实后执行一次 Review 远端操作。"""
        if not isinstance(command, SubmitReviewCommand):
            raise TypeError(
                "review command executor requires SubmitReviewCommand"
            )
        await self._request_recorder.record_remote_request(
            command,
            command.request,
        )
        return await self._operation(
            command.request,
            command.environment_snapshot_value(),
        )


if __name__ == '__main__':
    pass
