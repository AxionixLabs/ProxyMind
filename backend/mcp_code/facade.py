# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from backend.mcp_code.base import NativeCodingBase
from backend.mcp_code.workspace.tools import WorkspaceTools
from backend.mcp_code.workspace.parallel_read import ParallelReadTools
from backend.mcp_code.workspace.repo_map import RepoMapTools
from backend.mcp_code.edit.patch_engine import PatchEngine
from backend.mcp_code.exec.shell_exec import ShellExecTools
from backend.mcp_code.vcs.git_tools import GitTools
from backend.mcp_code.exec.command_policy import CommandPolicy
from backend.mcp_code.exec.file_audit import FileAudit
from backend.mcp_code.vcs.change_summary import ChangeSummaryTools


class NativeCoding(NativeCodingBase):
    """由可组合工具组件支撑的原生编码门面。"""

    def __init__(self, root: str | None = None) -> None:
        """初始化共享运行时状态并装配各能力组件。"""
        super().__init__(root=root)

        self._repo_map       = RepoMapTools(self)
        self._workspace      = WorkspaceTools(self)
        self._parallel_read  = ParallelReadTools(self)
        self._patch_engine   = PatchEngine(self)
        self._command_policy = CommandPolicy(self)
        self._file_audit     = FileAudit(self)
        self._shell_exec     = ShellExecTools(self, command_policy=self._command_policy, file_audit=self._file_audit)
        self._git_tools      = GitTools(self)
        self._change_summary = ChangeSummaryTools(self, git_tools=self._git_tools)

    @property
    def parallel_read_limits(self) -> dict[str, int]:
        """返回并行读取工具的数量和并发限制。"""
        return {
            "max_items": self._parallel_read.MAX_ITEMS,
            "max_concurrency": self._parallel_read.MAX_CONCURRENCY
        }

    @property
    def git_tools(self) -> GitTools:
        """返回 Git 工具组件。"""
        return self._git_tools

    def execution_metadata_policy(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any] | None:
        """返回命令执行元数据的策略校验结果。"""
        return self._command_policy.execution_metadata_policy(*args, **kwargs)

    def capture_file_fingerprints(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        """采集工作区文件指纹。"""
        return self._file_audit.capture_file_fingerprints(*args, **kwargs)

    def diff_file_fingerprints(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        """比较两次文件指纹并返回变更摘要。"""
        return self._file_audit.diff_file_fingerprints(*args, **kwargs)

    def read_file(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        """读取工作区文本文件内容。"""
        return self._workspace.read_file(*args, **kwargs)

    def search(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        """执行文件、文本或符号搜索。"""
        return self._workspace.search(*args, **kwargs)

    def write_file(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        """创建或覆盖工作区文本文件。"""
        return self._workspace.write_file(*args, **kwargs)

    def copy_file(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        """复制工作区文件。"""
        return self._workspace.copy_file(*args, **kwargs)

    def move_file(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        """移动或重命名工作区文件。"""
        return self._workspace.move_file(*args, **kwargs)

    def delete_file(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        """删除工作区文件。"""
        return self._workspace.delete_file(*args, **kwargs)

    def apply_patch(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        """应用精确文本替换补丁。"""
        return self._patch_engine.apply_patch(*args, **kwargs)

    def apply_unified_patch(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        """应用 unified diff 补丁。"""
        return self._patch_engine.apply_unified_patch(*args, **kwargs)

    def is_git_workspace(self) -> bool:
        """判断当前工作区是否为 Git 仓库。"""
        return self._git_tools.is_git_workspace()

    def audit_mode_for_command(self, command: list[str], *, audit_files: bool) -> str:
        """返回指定命令对应的文件审计模式。"""
        return self._shell_exec.audit_mode_for_command(command, audit_files=audit_files)

    async def parallel_read(self, items: list[dict[str, typing.Any]]) -> dict[str, typing.Any]:
        """并行执行多个只读工具请求。"""
        return await self._parallel_read.parallel_read(items)

    async def shell_exec(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        """按策略执行本地 shell 命令。"""
        return await self._shell_exec.shell_exec(*args, **kwargs)

    async def git_status(self) -> dict[str, typing.Any]:
        """返回 git status 摘要。"""
        return await self._git_tools.git_status()

    async def git_diff(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        """返回 git diff 和结构化统计。"""
        return await self._git_tools.git_diff(*args, **kwargs)

    async def run_git(self, args: list[str], *, output_limit: int | None = None) -> dict[str, typing.Any]:
        """执行指定 git 子命令。"""
        return await self._git_tools.run_git(args, output_limit=output_limit)

    async def change_summary(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        """返回当前变更、风险和验证状态摘要。"""
        return await self._change_summary.change_summary(*args, **kwargs)


if __name__ == '__main__':
    pass
