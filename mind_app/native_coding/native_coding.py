# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import typing
from agent.application import ProcessCapability
from infrastructure.config.paths import ApplicationLayout
from mind_app.native_coding.base import NativeCodingBase
from mind_app.native_coding.edit.patch_engine import PatchEngine
from mind_app.native_coding.exec.shell_exec import ShellCommandTools
from mind_app.native_coding.exec.exec_command import ExecCommandTools
from mind_app.native_coding.exec.process_session import ProcessSessionManager
from infrastructure.platform.sandbox import SandboxClient
from mind_app.native_coding.exec.user_shell import UserShellExecution
from mind_app.native_coding.exec.command_policy import CommandPolicy
from mind_app.native_coding.exec.file_audit import FileAudit
from mind_app.native_coding.edit.turn_diff import TurnDiffTracker
from mind_app.native_coding.js_repl import (
    JavaScriptReplPool,
    ReplRuntimeError
)


class NativeCoding(NativeCodingBase):
    """由可组合工具组件支撑的原生编码服务入口。"""

    def __init__(
        self,
        root: str | os.PathLike[str] | None = None,
        *,
        application_layout: ApplicationLayout | None = None,
        process_capability: ProcessCapability | None = None,
    ) -> None:
        """初始化共享运行时状态并装配各能力组件。"""
        super().__init__(root=root)

        self._sandbox_client   = SandboxClient(
            workspace_root=self.root,
            application_root=(
                application_layout.root if application_layout is not None else None
            ),
            packaged=(
                application_layout.packaged
                if application_layout is not None
                else None
            ),
            platform=(
                application_layout.platform
                if application_layout is not None
                else None
            ),
        )
        self._process_sessions = ProcessSessionManager(
            self._sandbox_client,
            process_capability=process_capability,
        )

        self.user_shell = UserShellExecution(
            root=self.root,
            sessions=self._process_sessions,
            relative_path=self.relative_path,
        )

        self._javascript_repls = JavaScriptReplPool(self.root)

        self._patch_engine   = PatchEngine(self)
        self._command_policy = CommandPolicy(self)
        self._file_audit     = FileAudit(self)
        self._turn_diff      = TurnDiffTracker()

        self._shell_command = ShellCommandTools(
            self,
            command_policy=self._command_policy,
            file_audit=self._file_audit,
            sessions=self._process_sessions,
        )

        self._exec_command = ExecCommandTools(
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
        sandbox_mode: str = "danger-full-access",
        sandbox_permissions: object = "use_default",
        additional_permissions: dict[str, typing.Any] | None = None,
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
        )

    async def exec_command(
        self,
        *,
        command: str,
        cwd: str = ".",
        shell: str | None = None,
        yield_time_ms: int = 1000,
        max_output_chars: int = 24000,
        timeout_sec: int = 1800,
        idle_timeout_sec: int = 300,
        cid: str = "",
        sid: str = "",
        sandbox_mode: str = "danger-full-access",
        sandbox_permissions: object = "use_default",
        additional_permissions: dict[str, typing.Any] | None = None,
    ) -> dict[str, typing.Any]:
        """启动可持续读写的 shell 命令会话。"""
        return await self._exec_command.exec_command(
            command=command,
            cwd=cwd,
            shell=shell,
            yield_time_ms=yield_time_ms,
            max_output_chars=max_output_chars,
            timeout_sec=timeout_sec,
            idle_timeout_sec=idle_timeout_sec,
            cid=cid,
            sid=sid,
            sandbox_mode=sandbox_mode,
            sandbox_permissions=sandbox_permissions,
            additional_permissions=additional_permissions,
        )

    async def write_stdin(
        self,
        *,
        session_id: str,
        stdin: str = "",
        wait_ms: int = 1000,
        max_output_chars: int = 12000,
        control: str = "none",
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
            cid=cid,
            sid=sid,
            call_id=call_id,
        )

    async def running_exec_sessions(self) -> dict[str, typing.Any]:
        """返回当前仍在运行的本地进程会话摘要。"""
        snapshot = await self._process_sessions.running_snapshot()
        snapshot["revision"] = self._process_sessions.change_revision
        return snapshot

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
        await self._javascript_repls.close()
        await self._process_sessions.close()

    async def js_repl(
        self,
        *,
        session_id: str,
        code: str,
        cwd: str,
        access_mode: str,
        timeout_ms: int,
        call_tool: typing.Any
    ) -> dict[str, typing.Any]:
        """在会话持有的持久 JavaScript 内核中执行代码。"""
        try:
            result = await self._javascript_repls.execute(
                session_id,
                code,
                cwd=cwd,
                access_mode=access_mode,
                timeout_ms=timeout_ms,
                call_tool=call_tool,
            )
        except (OSError, ReplRuntimeError) as exc:
            return self.fail_result(
                "js_repl_execution_failed",
                error=str(exc).strip() or type(exc).__name__,
            )

        output = self.clip_output(result.output)

        return {
            "ok": True,
            "text": output or "JavaScript cell completed.",
            "attachments": list(result.attachments),
            "data": {
                "output": output,
                "output_truncated": output != result.output,
            },
            "logs": [],
        }

    async def reset_js_repl(self, session_id: str) -> dict[str, typing.Any]:
        """重置指定会话的 JavaScript 内核。"""
        try:
            reset = await self._javascript_repls.reset_session(session_id)
        except (OSError, ReplRuntimeError) as exc:
            return self.fail_result(
                "js_repl_reset_failed",
                error=str(exc).strip() or type(exc).__name__,
            )

        return {
            "ok": True,
            "text": "JavaScript kernel reset.",
            "data": {"reset": reset},
            "logs": []
        }

    async def close_js_repl_session(self, session_id: str) -> bool:
        """关闭指定会话持有的 JavaScript 内核。"""
        return await self._javascript_repls.close_session(session_id)

    def apply_patch(
        self,
        *args: typing.Any,
        **kwargs: typing.Any
    ) -> dict[str, typing.Any]:
        """应用受支持的文本补丁。"""
        return self._patch_engine.apply_patch(*args, **kwargs)

    def preview_patch(
        self,
        *args: typing.Any,
        **kwargs: typing.Any
    ) -> dict[str, typing.Any]:
        """生成不写入工作区的补丁预览。"""
        return self._patch_engine.preview_patch(*args, **kwargs)

    def reset_patch_diff(self) -> None:
        """清空本轮 apply_patch 差异记录。"""
        self._turn_diff = TurnDiffTracker()

    def track_patch_delta(self, delta: typing.Any) -> str:
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
