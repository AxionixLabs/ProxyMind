# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from backend.mcp_core.coding_native.base import NativeCodingBase
from backend.mcp_core.coding_native.workspace import WorkspaceTools
from backend.mcp_core.coding_native.parallel_read import ParallelReadTools
from backend.mcp_core.coding_native.repo_map import RepoMapTools
from backend.mcp_core.coding_native.patch_engine import PatchEngine
from backend.mcp_core.coding_native.shell_git import ShellGitTools
from backend.mcp_core.coding_native.command_policy import CommandPolicy
from backend.mcp_core.coding_native.file_audit import FileAudit
from backend.mcp_core.coding_native.change_summary import ChangeSummaryTools


class NativeCoding(NativeCodingBase):
    """由可组合工具组件支撑的原生编码门面。"""

    def __init__(self, root: str | None = None):
        super().__init__(root=root)

        self._repo_map       = RepoMapTools(self)
        self._workspace      = WorkspaceTools(self)
        self._parallel_read  = ParallelReadTools(self)
        self._patch_engine   = PatchEngine(self)
        self._command_policy = CommandPolicy(self)
        self._file_audit     = FileAudit(self)
        self._shell_git      = ShellGitTools(self)
        self._change_summary = ChangeSummaryTools(self)

        self._private_delegates = self._build_private_delegates()

    def __getattr__(self, name: str) -> typing.Any:
        if name.startswith("_") and "_private_delegates" in self.__dict__:
            if name in self._private_delegates:
                return self._private_delegates[name]
        raise AttributeError(f"{self.__class__.__name__!s} object has no attribute {name!r}")

    def _build_private_delegates(self) -> dict[str, typing.Any]:
        delegates: dict[str, typing.Any] = {}
        for component in (
            self._repo_map,
            self._parallel_read,
            self._patch_engine,
            self._command_policy,
            self._file_audit,
            self._shell_git,
            self._change_summary
        ):
            for name in dir(component.__class__):
                if name.startswith("_") and not name.startswith("__") and not hasattr(NativeCodingBase, name):
                    delegates.setdefault(name, getattr(component, name))
        return delegates

    def execution_metadata_policy(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any] | None:
        return self._command_policy.execution_metadata_policy(*args, **kwargs)

    def capture_file_fingerprints(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        return self._file_audit.capture_file_fingerprints(*args, **kwargs)

    def diff_file_fingerprints(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        return self._file_audit.diff_file_fingerprints(*args, **kwargs)

    def workspace_root(self) -> dict[str, typing.Any]:
        return self._workspace.workspace_root()

    def list_file(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        return self._workspace.list_file(*args, **kwargs)

    def read_file(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        return self._workspace.read_file(*args, **kwargs)

    def search(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        return self._workspace.search(*args, **kwargs)

    def write_file(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        return self._workspace.write_file(*args, **kwargs)

    def copy_file(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        return self._workspace.copy_file(*args, **kwargs)

    def move_file(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        return self._workspace.move_file(*args, **kwargs)

    def delete_file(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        return self._workspace.delete_file(*args, **kwargs)

    def apply_patch(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        return self._patch_engine.apply_patch(*args, **kwargs)

    def apply_unified_patch(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        return self._patch_engine.apply_unified_patch(*args, **kwargs)

    async def parallel_read(self, items: list[dict[str, typing.Any]]) -> dict[str, typing.Any]:
        return await self._parallel_read.parallel_read(items)

    async def shell_exec(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        return await self._shell_git.shell_exec(*args, **kwargs)

    async def git_status(self) -> dict[str, typing.Any]:
        return await self._shell_git.git_status()

    async def git_diff(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        return await self._shell_git.git_diff(*args, **kwargs)

    async def change_summary(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        return await self._change_summary.change_summary(*args, **kwargs)


if __name__ == '__main__':
    pass
