# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import typing
from mind_app.native_coding.base import NativeCodingBase
from mind_app.native_coding.edit.patch_engine import PatchEngine
from mind_app.native_coding.exec.shell_exec import ShellCommandTools
from mind_app.native_coding.exec.exec_command import ExecCommandTools
from mind_app.native_coding.exec.command_policy import CommandPolicy
from mind_app.native_coding.exec.file_audit import FileAudit
from mind_app.native_coding.edit.turn_diff import TurnDiffTracker


class NativeCoding(NativeCodingBase):
    """由可组合工具组件支撑的原生编码服务入口。"""

    def __init__(self, root: str | os.PathLike[str] | None = None) -> None:
        """初始化共享运行时状态并装配各能力组件。"""
        super().__init__(root=root)

        self._patch_engine   = PatchEngine(self)
        self._command_policy = CommandPolicy(self)
        self._file_audit     = FileAudit(self)
        self._turn_diff      = TurnDiffTracker()
        self._shell_command  = ShellCommandTools(self, command_policy=self._command_policy, file_audit=self._file_audit)
        self._exec_command   = ExecCommandTools(self, command_policy=self._command_policy, file_audit=self._file_audit)

    async def shell_command(
        self,
        *,
        command: str,
        cwd: str = ".",
        timeout_sec: int = 60,
        output_encoding: str = "auto",
        execution: dict[str, typing.Any] | None = None
    ) -> dict[str, typing.Any]:
        """执行单条 shell 命令。"""
        return await self._shell_command.shell_command(
            command=command,
            cwd=cwd,
            timeout_sec=timeout_sec,
            output_encoding=output_encoding,
            execution=execution
        )

    async def exec_command(
        self,
        *,
        command: str,
        cwd: str = ".",
        yield_time_ms: int = 1000,
        max_output_chars: int = 24000,
        timeout_sec: int = 1800,
        idle_timeout_sec: int = 300,
        execution: dict[str, typing.Any] | None = None,
        cid: str = "",
        sid: str = "",
    ) -> dict[str, typing.Any]:
        """启动可持续读写的 shell 命令会话。"""
        return await self._exec_command.exec_command(
            command=command,
            cwd=cwd,
            yield_time_ms=yield_time_ms,
            max_output_chars=max_output_chars,
            timeout_sec=timeout_sec,
            idle_timeout_sec=idle_timeout_sec,
            execution=execution,
            cid=cid,
            sid=sid,
        )

    async def write_stdin(
        self,
        *,
        session_id: str,
        stdin: str = "",
        wait_ms: int = 1000,
        max_output_chars: int = 12000,
        control: str = "none",
        execution: dict[str, typing.Any] | None = None,
        cid: str = "",
        sid: str = "",
        call_id: str = "",
    ) -> dict[str, typing.Any]:
        """向 shell 命令会话写入输入或轮询输出。"""
        return await self._exec_command.write_stdin(
            session_id=session_id,
            stdin=stdin,
            wait_ms=wait_ms,
            max_output_chars=max_output_chars,
            control=control,
            execution=execution,
            cid=cid,
            sid=sid,
            call_id=call_id,
        )

    async def running_exec_sessions(self) -> dict[str, typing.Any]:
        """返回当前仍在运行的 exec_command 会话摘要。"""
        return await self._exec_command.running_sessions_snapshot()

    async def exec_session_output_snapshot(
        self,
        *,
        session_id: str,
        max_output_chars: int = 12000
    ) -> dict[str, typing.Any]:
        """返回 exec_command 会话的只读输出快照。"""
        return await self._exec_command.session_output_snapshot(
            session_id=session_id,
            max_output_chars=max_output_chars
        )

    def apply_patch(
        self,
        *args: typing.Any,
        **kwargs: typing.Any
    ) -> dict[str, typing.Any]:
        """应用严格 apply_patch 补丁。"""
        return self._patch_engine.apply_patch(*args, **kwargs)

    def reset_patch_diff(self) -> None:
        """清空本轮 apply_patch 差异记录。"""
        self._turn_diff = TurnDiffTracker()

    def track_patch_delta(self, delta: typing.Any) -> str:
        """记录一次 apply_patch delta 并返回当前净差异。"""
        return self._turn_diff.track_delta(delta)

    def patch_diff_snapshot(self) -> dict[str, typing.Any]:
        """返回当前 apply_patch 净差异快照。"""
        return {
            "invalidated" : self._turn_diff.invalidated,
            "diff"        : self._turn_diff.unified_diff
        }


if __name__ == '__main__':
    pass
