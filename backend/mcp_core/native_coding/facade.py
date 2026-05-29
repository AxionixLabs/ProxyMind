# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from backend.mcp_core.native_coding.base import NativeCodingBase
from backend.mcp_core.native_coding.workspace import WorkspaceTools
from backend.mcp_core.native_coding.parallel_read import ParallelReadTools
from backend.mcp_core.native_coding.repo_map import RepoMapTools
from backend.mcp_core.native_coding.patch_engine import PatchEngine
from backend.mcp_core.native_coding.shell_git import ShellGitTools
from backend.mcp_core.native_coding.command_policy import CommandPolicy
from backend.mcp_core.native_coding.file_audit import FileAudit
from backend.mcp_core.native_coding.repair_steps import RepairSteps
from backend.mcp_core.native_coding.change_summary import ChangeSummaryTools
from backend.mcp_core.native_coding.snapshots import SnapshotTools
from backend.mcp_core.native_coding.plan import PlanTools
from backend.mcp_core.native_coding.diagnostics import DiagnosticsTools
from backend.mcp_core.native_coding.sandbox_result import SandboxResultTools
from backend.mcp_core.native_coding.session import SessionTools


class NativeCoding(NativeCodingBase):
    """由可组合工具组件支撑的原生编码门面。"""

    def __init__(self, root: str | None = None):
        super().__init__(root=root)

        self._workspace         = WorkspaceTools(self)
        self._parallel_read     = ParallelReadTools(self)
        self._repo_map          = RepoMapTools(self)
        self._patch_engine      = PatchEngine(self)
        self._command_policy    = CommandPolicy(self)
        self._file_audit        = FileAudit(self)
        self._repair_steps      = RepairSteps(self)
        self._shell_git         = ShellGitTools(self)
        self._change_summary    = ChangeSummaryTools(self)
        self._snapshots         = SnapshotTools(self)
        self._plan              = PlanTools(self)
        self._diagnostics       = DiagnosticsTools(self)
        self._sandbox_result    = SandboxResultTools(self)
        self._session           = SessionTools(self)
        self._private_delegates = self._build_private_delegates()

    def _build_private_delegates(self) -> dict[str, typing.Any]:
        delegates: dict[str, typing.Any] = {}
        for component in (
            self._repo_map,
            self._parallel_read,
            self._patch_engine,
            self._command_policy,
            self._file_audit,
            self._repair_steps,
            self._shell_git,
            self._change_summary,
            self._snapshots,
            self._plan,
            self._diagnostics,
            self._sandbox_result,
            self._session
        ):
            for name in dir(component.__class__):
                if name.startswith("_") and not name.startswith("__") and not hasattr(NativeCodingBase, name):
                    delegates.setdefault(name, getattr(component, name))
        return delegates

    def __getattr__(self, name: str) -> typing.Any:
        if name.startswith("_") and "_private_delegates" in self.__dict__:
            if name in self._private_delegates:
                return self._private_delegates[name]
        raise AttributeError(f"{self.__class__.__name__!s} object has no attribute {name!r}")

    def check_command_policy(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        return self._command_policy.check_command_policy(*args, **kwargs)

    def capture_file_fingerprints(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        return self._file_audit.capture_file_fingerprints(*args, **kwargs)

    def diff_file_fingerprints(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        return self._file_audit.diff_file_fingerprints(*args, **kwargs)

    def validate_repair_steps(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        return self._repair_steps.validate_repair_steps(*args, **kwargs)

    def workspace_root(self) -> dict[str, typing.Any]:
        return self._workspace.workspace_root()

    def list_files(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        return self._workspace.list_files(*args, **kwargs)

    def read_file(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        return self._workspace.read_file(*args, **kwargs)

    def search_text(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        return self._workspace.search_text(*args, **kwargs)

    async def parallel_read(self, items: list[dict[str, typing.Any]]) -> dict[str, typing.Any]:
        return await self._parallel_read.parallel_read(items)

    def write_file(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        return self._workspace.write_file(*args, **kwargs)

    def copy_file(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        return self._workspace.copy_file(*args, **kwargs)

    def move_file(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        return self._workspace.move_file(*args, **kwargs)

    def delete_file(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        return self._workspace.delete_file(*args, **kwargs)

    def repo_map(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        return self._repo_map.repo_map(*args, **kwargs)

    def find_symbol(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        return self._repo_map.find_symbol(*args, **kwargs)

    def apply_patch(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        return self._patch_engine.apply_patch(*args, **kwargs)

    def apply_unified_patch(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        return self._patch_engine.apply_unified_patch(*args, **kwargs)

    def rollback_run(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        return self._snapshots.rollback_run(*args, **kwargs)

    def update_plan(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        return self._plan.update_plan(*args, **kwargs)

    def get_plan(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        return self._plan.get_plan(*args, **kwargs)

    def record_sandbox_result(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        return self._sandbox_result.record_sandbox_result(*args, **kwargs)

    def session_snapshot(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        return self._session.session_snapshot(*args, **kwargs)

    def preflight_native_steps(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        return self._session.preflight_native_steps(*args, **kwargs)

    async def shell_exec(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        return await self._shell_git.shell_exec(*args, **kwargs)

    async def git_status(self) -> dict[str, typing.Any]:
        return await self._shell_git.git_status()

    async def git_diff(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        return await self._shell_git.git_diff(*args, **kwargs)

    async def change_summary(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        return await self._change_summary.change_summary(*args, **kwargs)

    async def native_loop(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        return await self._session.native_loop(*args, **kwargs)

    async def native_repair_loop(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        return await self._session.native_repair_loop(*args, **kwargs)

    async def run_native_step(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        return await self._session.run_native_step(*args, **kwargs)


if __name__ == '__main__':
    pass
