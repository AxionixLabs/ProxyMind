# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from backend.mcp_code.base import NativeCodingBase
from backend.mcp_code.workspace.tools import WorkspaceTools
from backend.mcp_code.workspace.parallel_shell_calls import ParallelShellCallTools
from backend.mcp_code.workspace.repo_map import RepoMapTools
from backend.mcp_code.edit.patch_engine import PatchEngine
from backend.mcp_code.exec.shell_exec import ShellCommandTools
from backend.mcp_code.exec.command_policy import CommandPolicy
from backend.mcp_code.exec.file_audit import FileAudit


class NativeCoding(NativeCodingBase):
    """由可组合工具组件支撑的原生编码门面。"""

    def __init__(self, root: str | None = None) -> None:
        """初始化共享运行时状态并装配各能力组件。"""
        super().__init__(root=root)

        self._repo_map             = RepoMapTools(self)
        self._workspace            = WorkspaceTools(self)
        self._patch_engine         = PatchEngine(self)
        self._command_policy       = CommandPolicy(self)
        self._file_audit           = FileAudit(self)
        self._shell_command        = ShellCommandTools(self, command_policy=self._command_policy, file_audit=self._file_audit)
        self._parallel_shell_calls = ParallelShellCallTools(self)

    @property
    def parallel_shell_call_limits(self) -> dict[str, int]:
        """返回并行 shell 调用工具的数量和并发限制。"""
        return {
            "max_items"       : self._parallel_shell_calls.MAX_ITEMS,
            "max_concurrency" : self._parallel_shell_calls.MAX_CONCURRENCY
        }

    def execution_metadata_policy(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any] | None:
        """返回命令执行元数据的策略校验结果。"""
        return self._command_policy.execution_metadata_policy(*args, **kwargs)

    def capture_file_fingerprints(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        """采集工作区文件指纹。"""
        return self._file_audit.capture_file_fingerprints(*args, **kwargs)

    def diff_file_fingerprints(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        """比较两次文件指纹并返回变更摘要。"""
        return self._file_audit.diff_file_fingerprints(*args, **kwargs)

    def write_file(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        """创建或覆盖工作区文本文件。"""
        return self._workspace.write_file(*args, **kwargs)

    def apply_patch(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        """应用精确文本替换补丁。"""
        return self._patch_engine.apply_patch(*args, **kwargs)

    def apply_unified_patch(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        """应用 unified diff 补丁。"""
        return self._patch_engine.apply_unified_patch(*args, **kwargs)

    def audit_mode_for_command(self, command: str, *, audit_files: bool) -> str:
        """返回指定命令对应的文件审计模式。"""
        return self._shell_command.audit_mode_for_command(command, audit_files=audit_files)

    async def parallel_shell_calls(self, items: list[dict[str, typing.Any]]) -> dict[str, typing.Any]:
        """并行执行多个 shell 调用。"""
        return await self._parallel_shell_calls.parallel_shell_calls(items)

    async def shell_command(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        """按策略执行本地 shell 命令。"""
        return await self._shell_command.shell_command(*args, **kwargs)


if __name__ == '__main__':
    pass
