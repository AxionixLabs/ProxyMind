# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import typing

from agent.domain.patches.parsing import PatchParser
from agent.ports.capabilities import SandboxMode
from agent.ports.process_tools import (
    EXEC_COMMAND_DEFAULT_YIELD_MS,
    WRITE_STDIN_DEFAULT_WAIT_MS,
    ProcessSessionSnapshot,
)
from infrastructure.platform.process_sessions import ProcessSessionManager
from infrastructure.workspace.commands.audit import WorkspaceFileAudit
from infrastructure.workspace.commands.process import ProcessCommandExecutor
from infrastructure.workspace.commands.profile import CommandExecutionProfile
from infrastructure.workspace.commands.shell import ShellCommandExecutor
from infrastructure.workspace.commands.user_shell import UserShellExecution
from infrastructure.workspace.context import WorkspaceContext
from infrastructure.workspace.patches.applier import PatchApplier
from infrastructure.workspace.patches.diagnostics import PatchDiagnostics
from infrastructure.workspace.patches.operations import TextPatchOperations
from infrastructure.workspace.patches.planner import PatchPlanner
from infrastructure.workspace.patches.tracker import WorkspaceDiffTracker


class WorkspaceCoding(WorkspaceContext):
    """聚合绑定同一工作区生命周期的编码执行能力。"""

    def __init__(
        self,
        root: str | os.PathLike[str] | None = None,
        *,
        process_sessions: ProcessSessionManager,
    ) -> None:
        """初始化共享运行时状态并装配各能力组件。"""
        super().__init__(root=root)

        if not isinstance(process_sessions, ProcessSessionManager):
            raise TypeError("process_sessions must be ProcessSessionManager")
        self._process_sessions = process_sessions

        self.user_shell = UserShellExecution(
            root=self.root,
            sessions=self._process_sessions,
            relative_path=self.relative_path,
        )

        patch_diagnostics = PatchDiagnostics(self)
        patch_applier = PatchApplier(
            self,
            diagnostics=patch_diagnostics,
        )
        patch_planner = PatchPlanner(
            self,
            parser=PatchParser(),
            applier=patch_applier,
            diagnostics=patch_diagnostics,
        )
        self._patches = TextPatchOperations(
            self,
            planner=patch_planner,
            diagnostics=patch_diagnostics,
        )
        self._command_policy = CommandExecutionProfile(self)
        self._file_audit = WorkspaceFileAudit(self)
        self._turn_diff = WorkspaceDiffTracker()

        self._shell_command = ShellCommandExecutor(
            self,
            command_policy=self._command_policy,
            file_audit=self._file_audit,
            sessions=self._process_sessions,
        )

        self._exec_command = ProcessCommandExecutor(
            self,
            command_policy=self._command_policy,
            file_audit=self._file_audit,
            sessions=self._process_sessions,
        )

    async def shell_command(
        self,
        *,
        command: str,
        cwd: str = ".",
        timeout_sec: int = 60,
        output_encoding: str = "auto",
        sandbox_mode: SandboxMode = "danger-full-access",
        sandbox_permissions: object = "use_default",
        additional_permissions: dict[str, typing.Any] | None = None,
        cid: str = "",
        sid: str = "",
        run_id: str = "",
        environment_id: str = "",
    ) -> dict[str, typing.Any]:
        """执行单条 shell 命令。"""
        return await self._shell_command.shell_command(
            command=command,
            cwd=cwd,
            timeout_sec=timeout_sec,
            output_encoding=output_encoding,
            sandbox_mode=sandbox_mode,
            sandbox_permissions=sandbox_permissions,
            additional_permissions=additional_permissions,
            cid=cid,
            sid=sid,
            run_id=run_id,
            environment_id=environment_id,
        )

    async def exec_command(
        self,
        *,
        command: str,
        cwd: str = ".",
        shell: str | None = None,
        tty: bool = False,
        terminal_rows: int = 24,
        terminal_columns: int = 80,
        yield_time_ms: int = EXEC_COMMAND_DEFAULT_YIELD_MS,
        max_output_chars: int = 24000,
        timeout_sec: int = 1800,
        idle_timeout_sec: int = 300,
        cid: str = "",
        sid: str = "",
        run_id: str = "",
        turn_id: str = "",
        call_id: str = "",
        environment_id: str = "",
        sandbox_mode: SandboxMode = "danger-full-access",
        sandbox_permissions: object = "use_default",
        additional_permissions: dict[str, typing.Any] | None = None,
    ) -> dict[str, typing.Any]:
        """启动可持续读写的 shell 命令会话。"""
        return await self._exec_command.exec_command(
            command=command,
            cwd=cwd,
            shell=shell,
            tty=tty,
            terminal_rows=terminal_rows,
            terminal_columns=terminal_columns,
            yield_time_ms=yield_time_ms,
            max_output_chars=max_output_chars,
            timeout_sec=timeout_sec,
            idle_timeout_sec=idle_timeout_sec,
            cid=cid,
            sid=sid,
            run_id=run_id,
            turn_id=turn_id,
            call_id=call_id,
            environment_id=environment_id,
            sandbox_mode=sandbox_mode,
            sandbox_permissions=sandbox_permissions,
            additional_permissions=additional_permissions,
        )

    async def write_stdin(
        self,
        *,
        session_id: str,
        stdin: str = "",
        wait_ms: int = WRITE_STDIN_DEFAULT_WAIT_MS,
        max_output_chars: int = 12000,
        control: str = "none",
        terminal_rows: int | None = None,
        terminal_columns: int | None = None,
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
            terminal_rows=terminal_rows,
            terminal_columns=terminal_columns,
            cid=cid,
            sid=sid,
            call_id=call_id,
        )

    async def running_exec_sessions(self) -> dict[str, typing.Any]:
        """返回当前仍在运行的本地进程会话摘要。"""
        snapshot = await self._process_sessions.running_snapshot()
        snapshot["revision"] = self._process_sessions.change_revision
        return snapshot

    async def exec_session_snapshots(self) -> tuple[ProcessSessionSnapshot, ...]:
        """返回不消费工具输出的进程调用生命周期快照。"""
        return await self._process_sessions.execution_snapshots()

    async def wait_exec_sessions_update(
        self,
        *,
        revision: int,
        timeout_sec: float,
    ) -> dict[str, typing.Any]:
        """等待任意本地进程会话变更。"""
        return await self._process_sessions.wait_for_change(
            revision=revision,
            timeout_sec=timeout_sec,
        )

    async def stop_exec_sessions(
        self,
        *,
        session_ids: typing.Iterable[str] | None = None,
    ) -> dict[str, typing.Any]:
        """停止指定或全部仍在运行的本地进程会话。"""
        return await self._process_sessions.stop_running_sessions(
            session_ids=session_ids,
        )

    async def mark_exec_session_background(self, session_id: str) -> bool:
        """把指定本地进程会话移入后台终端集合。"""
        return await self._process_sessions.mark_background(session_id)

    async def wait_exec_session_update(
        self,
        session_id: str,
        *,
        revision: int,
        timeout_sec: float
    ) -> dict[str, typing.Any]:
        """等待本地进程会话事件并返回事件携带的最新快照。"""
        changed = await self._process_sessions.wait_for_update(
            session_id,
            revision=revision,
            timeout_sec=timeout_sec,
        )
        if not changed:
            return {"changed": False}

        delta = await self._process_sessions.output_delta(
            session_id,
            revision=revision,
        )

        snapshot = delta.get("snapshot")
        if not isinstance(snapshot, dict):
            return {
                "changed": True,
                "event": "failed",
                "delta": delta.get("items", []),
                "delta_reset": bool(delta.get("reset")),
                "snapshot": {
                    "ok": False,
                    "reason": "exec_session_update_unavailable",
                    "session_id": str(session_id or "").strip(),
                },
            }
        if str(snapshot.get("status") or "").strip() == "exited":
            snapshot = await self._process_sessions.output_snapshot(
                session_id,
                max_output_chars=120000,
            )

        return {
            "changed": True,
            "event": (
                "completed"
                if str(snapshot.get("status") or "").strip() == "exited"
                else "delta"
            ),
            "delta": delta.get("items", []),
            "delta_reset": bool(delta.get("reset")),
            "snapshot": snapshot,
        }

    async def exec_session_output_snapshot(
        self,
        *,
        session_id: str,
        max_output_chars: int = 12000
    ) -> dict[str, typing.Any]:
        """返回 exec_command 会话的只读输出快照。"""
        return await self._process_sessions.output_snapshot(
            session_id,
            max_output_chars=max_output_chars,
        )

    async def control_exec_session(
        self,
        *,
        session_id: str,
        control: str,
    ) -> dict[str, typing.Any]:
        """对本地进程会话执行显式控制动作。"""
        session = self._process_sessions.get(session_id)
        if session is None:
            return {
                "ok": False,
                "reason": "exec_session_not_found",
                "session_id": str(session_id or "").strip(),
            }

        reason = await self._process_sessions.apply(session, control=control)

        if reason is not None:
            return {
                "ok": False,
                "reason": reason,
                "session_id": session.session_id
            }

        return await self._process_sessions.output_snapshot(
            session.session_id,
            max_output_chars=12000,
        )

    async def close(self) -> None:
        """关闭全部本地进程会话。"""
        await self._process_sessions.close()

    def apply_patch(
        self,
        *,
        patch: str,
        expected_sha256: dict[str, str] | None = None,
        force: bool = False,
    ) -> dict[str, typing.Any]:
        """应用受支持的文本补丁。"""
        return self._patches.apply_patch(
            patch=patch,
            expected_sha256=expected_sha256,
            force=force,
        )

    def preview_patch(
        self,
        *,
        patch: str,
        expected_sha256: dict[str, str] | None = None,
        force: bool = False,
    ) -> dict[str, typing.Any]:
        """生成不写入工作区的补丁预览。"""
        return self._patches.preview_patch(
            patch=patch,
            expected_sha256=expected_sha256,
            force=force,
        )

    def reset_patch_diff(self) -> None:
        """清空本轮 apply_patch 差异记录。"""
        self._turn_diff = WorkspaceDiffTracker()

    def track_patch_delta(self, delta: dict[str, typing.Any]) -> str:
        """记录一次 apply_patch delta 并返回当前净差异。"""
        return self._turn_diff.track_delta(delta)

    def patch_diff_snapshot(self) -> dict[str, typing.Any]:
        """返回当前 apply_patch 净差异快照。"""
        return {
            "invalidated": self._turn_diff.invalidated,
            "diff": self._turn_diff.unified_diff
        }


if __name__ == '__main__':
    pass
