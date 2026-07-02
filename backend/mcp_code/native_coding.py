# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from backend.mcp_code.base import NativeCodingBase
from backend.mcp_code.exec.shell_batch import ShellBatchTools
from backend.mcp_code.edit.patch_engine import PatchEngine
from backend.mcp_code.exec.shell_exec import ShellCommandTools
from backend.mcp_code.exec.command_policy import CommandPolicy
from backend.mcp_code.exec.file_audit import FileAudit


class NativeCoding(NativeCodingBase):
    """由可组合工具组件支撑的原生编码服务入口。"""

    def __init__(self, root: str | None = None) -> None:
        """初始化共享运行时状态并装配各能力组件。"""
        super().__init__(root=root)

        self._patch_engine   = PatchEngine(self)
        self._command_policy = CommandPolicy(self)
        self._file_audit     = FileAudit(self)
        self._shell_command  = ShellCommandTools(self, command_policy=self._command_policy, file_audit=self._file_audit)
        self._shell_batch    = ShellBatchTools(self, shell_command=self._shell_command)

    async def shell_command(
        self,
        *,
        command: str,
        cwd: str = ".",
        timeout_sec: int = 60,
        execution: dict[str, typing.Any] | None = None
    ) -> dict[str, typing.Any]:
        """执行单条 shell 命令。"""
        return await self._shell_command.shell_command(
            command=command,
            cwd=cwd,
            timeout_sec=timeout_sec,
            execution=execution
        )

    async def shell_calls(
        self,
        *,
        items: list[dict[str, typing.Any]],
        execution: dict[str, typing.Any] | None = None
    ) -> dict[str, typing.Any]:
        """批量执行 shell 命令。"""
        return await self._shell_batch.shell_calls(items=items, execution=execution)

    def apply_patch(
        self,
        *args: typing.Any,
        **kwargs: typing.Any
    ) -> dict[str, typing.Any]:
        """应用严格 apply_patch 补丁。"""
        return self._patch_engine.apply_patch(*args, **kwargs)


if __name__ == '__main__':
    pass
