# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import typing
import asyncio
from dataclasses import dataclass
from pathlib import Path
from infrastructure.platform.encoding import decode_process_output
from infrastructure.platform.processes import (
    subprocess_process_group_kwargs,
    terminate_process_tree
)


@dataclass(frozen=True, slots=True)
class WorkspaceCommand(object):
    """描述不经过 shell 插值的工作区命令。"""
    argv: tuple[str, ...]
    cwd: Path
    env: tuple[tuple[str, str | None], ...] = ()
    timeout_sec: float = 5.0
    output_bytes_cap: int | None = 64 * 1024


@dataclass(frozen=True, slots=True)
class WorkspaceCommandOutput(object):
    """保存工作区命令的退出状态和完整输出。"""
    exit_code: int
    stdout: str
    stderr: str

    @property
    def succeeded(self) -> bool:
        """返回命令是否以零状态退出。"""
        return self.exit_code == 0


class WorkspaceCommandError(RuntimeError):
    """表示工作区命令未能产生可用的进程结果。"""


class WorkspaceCommandRunner(typing.Protocol):
    """定义工作区命令执行方的单次调用契约。"""

    async def run(self, command: WorkspaceCommand) -> WorkspaceCommandOutput:
        """执行固定 argv 命令并返回捕获结果。"""
        ...


class LocalWorkspaceCommandRunner(object):
    """在当前主机执行有界的非交互工作区命令。"""

    async def run(self, command: WorkspaceCommand) -> WorkspaceCommandOutput:
        """执行命令，并在取消或超时时终止独立进程树。"""
        if not command.argv:
            raise WorkspaceCommandError("workspace command argv is empty")

        environment = os.environ.copy()
        for key, value in command.env:
            if value is None:
                environment.pop(key, None)
            else:
                environment[key] = value

        try:
            process = await asyncio.create_subprocess_exec(
                *command.argv,
                cwd=str(command.cwd),
                env=environment,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **subprocess_process_group_kwargs(),
            )
        except (OSError, RuntimeError, ValueError) as error:
            raise WorkspaceCommandError(str(error)) from error

        communicate = asyncio.create_task(process.communicate())
        try:
            stdout, stderr = await asyncio.wait_for(
                asyncio.shield(communicate),
                timeout=max(0.001, float(command.timeout_sec)),
            )
        except asyncio.TimeoutError as error:
            await terminate_process_tree(process, force=True)
            await asyncio.gather(communicate, return_exceptions=True)
            raise WorkspaceCommandError(
                f"workspace command timed out after {command.timeout_sec:g}s"
            ) from error
        except asyncio.CancelledError:
            await terminate_process_tree(process, force=True)
            await asyncio.gather(communicate, return_exceptions=True)
            raise

        stdout = _cap_output(stdout, command.output_bytes_cap)
        stderr = _cap_output(stderr, command.output_bytes_cap)
        return WorkspaceCommandOutput(
            exit_code=int(process.returncode if process.returncode is not None else -1),
            stdout=decode_process_output(stdout),
            stderr=decode_process_output(stderr),
        )


def _cap_output(value: bytes, limit: int | None) -> bytes:
    """按可选字节上限保留命令输出尾部。"""
    if limit is None:
        return value
    bounded = max(1, int(limit))
    return value if len(value) <= bounded else value[-bounded:]


if __name__ == '__main__':
    pass
