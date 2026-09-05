# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import functools
import typing
from collections.abc import Mapping

from agent.application.turns.run_result import RunResult
from agent.domain.policies import PermissionSettings
from agent.ports import ModelRequestFrozenCallback
from agent.ports import RemoteTurnRequestRecorder
from agent.ports import remote_turn_binding
from agent.protocol import SubmitTurnCommand
from agent.protocol.json_value import ThawedJsonValue

__all__ = (
    "RootTurnArgument",
    "RootTurnCommandExecutor",
    "RootTurnOperation",
)

RootTurnArgument: typing.TypeAlias = (
    ThawedJsonValue
    | PermissionSettings
    | ModelRequestFrozenCallback
)


class RootTurnOperation(typing.Protocol):
    """执行一次已经映射为根轮次参数的入口操作。"""

    async def __call__(
        self,
        *,
        message: str,
        **kwargs: RootTurnArgument,
    ) -> RunResult:
        """执行根轮次并返回应用结果。"""
        ...


class RootTurnCommandExecutor:
    """把冻结的主动 Turn 命令映射为根轮次入口参数。"""

    def __init__(
        self,
        operation: RootTurnOperation,
        *,
        permissions: PermissionSettings | None = None,
        include_empty_attachments: bool = False,
        request_recorder: RemoteTurnRequestRecorder | None = None,
    ) -> None:
        """绑定入口操作以及可选权限和附件映射策略。"""
        if not callable(operation):
            raise TypeError("root turn operation must be callable")
        if not isinstance(include_empty_attachments, bool):
            raise TypeError("include_empty_attachments must be boolean")
        self._operation = operation
        self._permissions = permissions
        self._include_empty_attachments = include_empty_attachments
        self._request_recorder = request_recorder

    async def __call__(self, command: SubmitTurnCommand) -> RunResult:
        """解包冻结命令并执行一次根轮次。"""
        if not isinstance(command, SubmitTurnCommand):
            raise TypeError("root turn command executor requires SubmitTurnCommand")

        root_kwargs: dict[str, RootTurnArgument] = {
            "exec_env": command.environment_snapshot_value(),
        }

        pref_config = command.pref_config_value()
        if pref_config is not None:
            root_kwargs["pref_config"] = pref_config

        attachments = command.attachment_values()
        if attachments or self._include_empty_attachments:
            root_kwargs["attachments"] = attachments

        if self._permissions is not None:
            root_kwargs["permissions"] = self._permissions
        if (
            self._request_recorder is not None
            and remote_turn_binding(command) is not None
        ):
            root_kwargs["on_model_request_frozen"] = functools.partial(
                self._request_recorder.record_remote_request,
                command,
            )

        values = command.extras_value() or {}
        metadata = values.get("metadata")
        if isinstance(metadata, Mapping):
            root_kwargs["metadata"] = dict(metadata)

        request_extras = values.get("request_extras")
        if isinstance(request_extras, Mapping) and request_extras:
            root_kwargs["extras"] = dict(request_extras)

        turn_id = values.get("turn_id")
        if isinstance(turn_id, str) and turn_id.strip():
            root_kwargs["turn_id"] = turn_id

        return await self._operation(
            message=command.message,
            **root_kwargs,
        )


if __name__ == '__main__':
    pass
