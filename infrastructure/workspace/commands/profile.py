# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

from infrastructure.workspace.context import WorkspaceComponent


class CommandExecutionProfile(WorkspaceComponent):
    """根据可信执行授权生成命令运行参数。"""

    LONG_TASK_TIMEOUT_SEC = 300
    DEFAULT_OUTPUT_LIMIT = 24000
    LONG_TASK_OUTPUT_LIMIT = 12000

    @staticmethod
    def local_command_policy(
        *,
        command: str | None = None,
        cwd: str = ".",
        timeout_sec: int,
        tool: str = "shell_command",
        arguments: dict[str, typing.Any] | None = None,
    ) -> dict[str, typing.Any]:
        """根据本地工具参数生成统一的命令运行参数。"""
        raw_arguments = dict(arguments or {})
        raw_arguments.setdefault("command", str(command or ""))
        raw_arguments.setdefault("cwd", str(cwd or "."))
        raw_arguments.setdefault("timeout_sec", int(timeout_sec or 60))
        if tool == "shell_command":
            raw_arguments.setdefault("output_encoding", "auto")

        canonical = raw_arguments
        timeout = max(1, int(canonical.get("timeout_sec") or 60))

        return CommandExecutionProfile._allow(
            risk="local",
            category="command",
            reasons=[],
            execution_target="local",
            timeout_sec=timeout,
            output_limit=CommandExecutionProfile.LONG_TASK_OUTPUT_LIMIT if timeout >= CommandExecutionProfile.LONG_TASK_TIMEOUT_SEC else CommandExecutionProfile.DEFAULT_OUTPUT_LIMIT,
            long_task=timeout >= CommandExecutionProfile.LONG_TASK_TIMEOUT_SEC,
            canonical_arguments=canonical,
        )

    @staticmethod
    def _allow(
        **data: typing.Any
    ) -> dict[str, typing.Any]:
        """构造允许执行的策略结果。"""
        payload: dict[str, typing.Any] = {"ok": True, **data}
        payload.setdefault("execution_target", "local")
        return payload


if __name__ == '__main__':
    pass
