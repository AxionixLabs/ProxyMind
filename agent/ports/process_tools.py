# -*- coding: utf-8 -*-
# Notes: ==== Mind(TM) ====

import typing
from collections.abc import Mapping

from agent.domain.execution_policy import SandboxPermission
from agent.domain.permission_profiles import PermissionProfile
from agent.ports.capabilities import SandboxMode


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
    ) -> Mapping[str, typing.Any]:
        """执行单条命令并返回由调用方校验的结果信封。"""
        ...

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
        wait_ms: int = 1000,
        max_output_chars: int = 12000,
        control: str = "none",
        cid: str = "",
        sid: str = "",
        call_id: str = "",
    ) -> Mapping[str, typing.Any]:
        """写入、轮询或控制持续命令会话。"""
        ...


__all__ = ("WorkspaceProcessPort",)


if __name__ == "__main__":
    pass
