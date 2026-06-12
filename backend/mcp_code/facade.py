# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from backend.mcp_code.base import NativeCodingBase
from backend.mcp_code.workspace.tools import WorkspaceTools
from backend.mcp_code.workspace.shell_calls import ShellCallTools
from backend.mcp_code.edit.patch_engine import PatchEngine
from backend.mcp_code.exec.shell_exec import ShellCommandTools
from backend.mcp_code.exec.command_policy import CommandPolicy
from backend.mcp_code.exec.file_audit import FileAudit


class NativeCoding(NativeCodingBase):
    """由可组合工具组件支撑的原生编码门面。"""

    def __init__(self, root: str | None = None) -> None:
        """初始化共享运行时状态并装配各能力组件。"""
        super().__init__(root=root)

        self._workspace      = WorkspaceTools(self)
        self._patch_engine   = PatchEngine(self)
        self._command_policy = CommandPolicy(self)
        self._file_audit     = FileAudit(self)
        self._shell_command  = ShellCommandTools(self, command_policy=self._command_policy, file_audit=self._file_audit)
        self._shell_calls    = ShellCallTools(self)

    def write_file(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        """创建或覆盖工作区文本文件。"""
        return self._workspace.write_file(*args, **kwargs)

    def apply_unified_patch(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        """应用 unified diff 补丁。"""
        return self._patch_engine.apply_unified_patch(*args, **kwargs)

    async def shell_calls(self, items: list[dict[str, typing.Any]]) -> dict[str, typing.Any]:
        """并行执行多个 shell 调用。"""
        return await self._shell_calls.shell_calls(items)

    async def shell_command(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        """按策略执行本地 shell 命令。"""
        return await self._shell_command.shell_command(*args, **kwargs)


if __name__ == '__main__':
    pass
