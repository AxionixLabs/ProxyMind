# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from collections.abc import Mapping

from agent.domain.execution_policy import SandboxPermission
from agent.domain.permission_profiles import PermissionProfile
from agent.ports.capabilities import SandboxMode

__all__ = (
    "EXEC_COMMAND_DEFAULT_YIELD_MS",
    "EXEC_COMMAND_MAX_YIELD_MS",
    "EXEC_COMMAND_MIN_YIELD_MS",
    "EXEC_COMMAND_WINDOWS_MIN_YIELD_MS",
    "WRITE_STDIN_DEFAULT_WAIT_MS",
    "WRITE_STDIN_EMPTY_MAX_WAIT_MS",
    "WRITE_STDIN_EMPTY_MIN_WAIT_MS",
    "WRITE_STDIN_MAX_WAIT_MS",
    "WRITE_STDIN_MIN_WAIT_MS",
    "UserShellPort",
    "WorkspaceProcessPort",
)


EXEC_COMMAND_DEFAULT_YIELD_MS = 10_000
EXEC_COMMAND_MIN_YIELD_MS = 250
EXEC_COMMAND_WINDOWS_MIN_YIELD_MS = 10_000
EXEC_COMMAND_MAX_YIELD_MS = 30_000
WRITE_STDIN_DEFAULT_WAIT_MS = 250
WRITE_STDIN_MIN_WAIT_MS = 250
WRITE_STDIN_MAX_WAIT_MS = 30_000
WRITE_STDIN_EMPTY_MIN_WAIT_MS = 5_000
WRITE_STDIN_EMPTY_MAX_WAIT_MS = 300_000


class WorkspaceProcessPort(typing.Protocol):
    """定义绑定工作区的本地命令执行与持续会话操作。"""

    agent_id: str

    async def shell_command(
        self,
        *,
        command: str,
        cwd: str = ".",
        timeout_sec: int = 60,
        output_encoding: str = "auto",
        sandbox_mode: SandboxMode = "danger-full-access",
        sandbox_permissions: SandboxPermission = "use_default",
        additional_permissions: PermissionProfile | None = None,
        cid: str = "",
        sid: str = "",
        run_id: str = "",
        environment_id: str = "",
    ) -> Mapping[str, typing.Any]:
        """执行单条命令并返回由调用方校验的结果信封。"""
        ...

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
        environment_id: str = "",
        sandbox_mode: SandboxMode = "danger-full-access",
        sandbox_permissions: SandboxPermission = "use_default",
        additional_permissions: PermissionProfile | None = None,
    ) -> Mapping[str, typing.Any]:
        """启动持续命令会话并返回首批输出。"""
        ...

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
    ) -> Mapping[str, typing.Any]:
        """写入、轮询或控制持续命令会话。"""
        ...

    async def running_exec_sessions(self) -> dict[str, typing.Any]:
        """返回工作区全部持续命令会话的权威快照。"""
        ...

    async def stop_exec_sessions(
        self,
        *,
        session_ids: typing.Iterable[str] | None = None,
    ) -> dict[str, typing.Any]:
        """停止指定或全部持续命令会话并返回逐项结果。"""
        ...

    async def wait_exec_sessions_update(
        self,
        *,
        revision: int,
        timeout_sec: float,
    ) -> dict[str, typing.Any]:
        """等待工作区进程集合发生变化。"""
        ...

    async def exec_session_output_snapshot(
        self,
        *,
        session_id: str,
        max_output_chars: int = 12000,
    ) -> dict[str, typing.Any]:
        """返回持续命令会话的只读输出快照。"""
        ...

    async def control_exec_session(
        self,
        *,
        session_id: str,
        control: str,
    ) -> dict[str, typing.Any]:
        """对持续命令会话执行显式控制。"""
        ...


class UserShellPort(typing.Protocol):
    """定义用户显式 Shell 会话的启动、观察和控制边界。"""

    async def start_user_shell_session(
        self,
        *,
        command: str,
        args: typing.Sequence[str],
        timeout_sec: int = 3600,
        idle_timeout_sec: int = 1800,
        owner_cid: str = "",
        owner_sid: str = "",
    ) -> dict[str, typing.Any]:
        """启动一项绑定当前工作区的用户 Shell 会话。"""
        ...

    async def running_exec_sessions(self) -> dict[str, typing.Any]:
        """返回用户 Shell 会话快照。"""
        ...

    async def wait_exec_session_update(
        self,
        session_id: str,
        *,
        revision: int,
        timeout_sec: float,
    ) -> dict[str, typing.Any]:
        """等待指定用户 Shell 会话的输出或终态变化。"""
        ...

    async def exec_session_output_snapshot(
        self,
        *,
        session_id: str,
        max_output_chars: int = 12000,
    ) -> dict[str, typing.Any]:
        """返回用户 Shell 会话的只读输出快照。"""
        ...

    async def control_exec_session(
        self,
        *,
        session_id: str,
        control: str,
    ) -> dict[str, typing.Any]:
        """中断或终止指定用户 Shell 会话。"""
        ...

    async def mark_exec_session_background(self, session_id: str) -> bool:
        """把指定用户 Shell 会话转入后台观察。"""
        ...


if __name__ == '__main__':
    pass
