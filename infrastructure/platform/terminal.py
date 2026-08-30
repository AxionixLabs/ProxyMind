# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import sys
import typing
import asyncio
from infrastructure.platform.encoding import decode_process_output


class Terminal(object):
    """进程执行辅助工具。"""

    @staticmethod
    async def cmd_line(cmd: list[str]) -> typing.Any:
        """执行参数列表命令，并返回 stdout 或 stderr 文本。"""
        transports = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await transports.communicate()

        if stdout:
            return decode_process_output(stdout).strip()
        if stderr:
            return decode_process_output(stderr).strip()

    @staticmethod
    async def cmd_link(cmd: list[str]) -> asyncio.subprocess.Process:
        """启动参数列表命令，并返回进程句柄。"""
        transports = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )

        return transports

    @staticmethod
    async def cmd_link_exec(
        cmd: list[str],
        *,
        cwd: typing.Optional[str] = None,
        env: typing.Optional[dict[str, str]] = None,
        stdin: typing.Any = None
    ) -> asyncio.subprocess.Process:
        """使用执行选项启动参数列表命令，并返回进程句柄。"""
        transports = await asyncio.create_subprocess_exec(
            *cmd, cwd=cwd or None, env=env, stdin=stdin,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )

        return transports

    @staticmethod
    async def cmd_link_pty(cmd: list[str]) -> typing.Optional[asyncio.subprocess.Process]:
        """在支持 PTY 时启动参数列表命令，并返回进程句柄。"""
        if (os.name == "nt") or sys.platform.startswith("win"):
            return None

        import pty

        master_fd, slave_fd = pty.openpty()

        transports = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True
        )
        os.close(slave_fd)

        looper   = asyncio.get_running_loop()
        reader   = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)

        await looper.connect_read_pipe(
            lambda: protocol, os.fdopen(master_fd, "rb", buffering=0)
        )

        transports.stdout = reader
        transports.stderr = None

        return transports

    @staticmethod
    async def cmd_line_shell(cmd: str) -> typing.Any:
        """执行 shell 字符串命令，并返回 stdout 或 stderr 文本。"""
        transports = await asyncio.create_subprocess_shell(
            cmd,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await transports.communicate()

        if stdout:
            return decode_process_output(stdout).strip()
        if stderr:
            return decode_process_output(stderr).strip()

    @staticmethod
    async def cmd_link_shell(cmd: str) -> "asyncio.subprocess.Process":
        """启动 shell 字符串命令，并返回进程句柄。"""
        transports = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )

        return transports

    @staticmethod
    async def cmd_link_shell_exec(
        cmd: str,
        *,
        shell: typing.Optional[list[str]] = None,
        cwd: typing.Optional[str] = None,
        env: typing.Optional[dict[str, str]] = None,
        stdin: typing.Any = None
    ) -> asyncio.subprocess.Process:
        """使用执行选项启动 shell 字符串命令，并返回进程句柄。"""
        prefix = [str(item) for item in (shell or []) if str(item or "").strip()]
        if prefix:
            transports = await asyncio.create_subprocess_exec(
                *prefix, cmd,
                cwd=cwd or None, env=env, stdin=stdin,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
        else:
            transports = await asyncio.create_subprocess_shell(
                cmd, cwd=cwd or None, env=env, stdin=stdin,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )

        return transports


if __name__ == '__main__':
    pass
