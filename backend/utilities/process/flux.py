# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import os
import sys
import time
import typing
import asyncio
from loguru import logger
from backend.utilities import const
from backend.utilities.trace import (
    clip_text, summarize_command
)


class Flux(object):
    """Flux class."""

    @staticmethod
    async def cmd_line(cmd: list[str]) -> typing.Any:
        """以参数数组方式执行子进程，并返回标准输出或错误输出文本。"""
        t0 = time.perf_counter()
        logger.debug(f"process begin mode=exec cmd={summarize_command(cmd)}")
        transports = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await transports.communicate()
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        rc = transports.returncode

        out_text = stdout.decode(const.CHARSET, const.IGNORE).strip() if stdout else ""
        err_text = stderr.decode(const.CHARSET, const.IGNORE).strip() if stderr else ""

        level = logger.debug if rc == 0 else logger.warning
        level(
            f"process end mode=exec rc={rc} elapsed_ms={elapsed_ms} cmd={summarize_command(cmd)} "
            f"stdout={clip_text(out_text, 120)} stderr={clip_text(err_text, 120)}"
        )

        if stdout:
            return out_text
        if stderr:
            return err_text

    @staticmethod
    async def cmd_link(cmd: list[str]) -> asyncio.subprocess.Process:
        """以参数数组方式启动长生命周期子进程，并返回进程句柄。"""
        transports = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        logger.debug(
            f"process link mode=exec pid={transports.pid} cmd={summarize_command(cmd)}"
        )

        return transports

    @staticmethod
    async def cmd_link_exec(
        cmd: list[str],
        *,
        cwd: typing.Optional[str] = None,
        env: typing.Optional[dict[str, str]] = None
    ) -> asyncio.subprocess.Process:
        """以参数数组方式启动长生命周期子进程，并返回进程句柄。"""
        transports = await asyncio.create_subprocess_exec(
            *cmd, cwd=cwd or None, env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        logger.debug(
            f"process link mode=exec pid={transports.pid} cwd={clip_text(cwd or '', 120)} "
            f"cmd={summarize_command(cmd)}"
        )

        return transports

    @staticmethod
    async def cmd_link_pty(cmd: list[str]) -> typing.Optional[asyncio.subprocess.Process]:
        """在类 Unix 环境下通过 PTY 启动子进程，便于消费合并后的交互输出。"""
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
        """以 shell 字符串方式执行命令，并返回标准输出或错误输出文本。"""
        t0 = time.perf_counter()
        logger.debug(f"process begin mode=shell cmd={summarize_command(cmd)}")
        transports = await asyncio.create_subprocess_shell(
            cmd,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await transports.communicate()
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        rc = transports.returncode

        out_text = stdout.decode(const.CHARSET, const.IGNORE).strip() if stdout else ""
        err_text = stderr.decode(const.CHARSET, const.IGNORE).strip() if stderr else ""
        level = logger.debug if rc == 0 else logger.warning
        level(
            f"process end mode=shell rc={rc} elapsed_ms={elapsed_ms} cmd={summarize_command(cmd)} "
            f"stdout={clip_text(out_text, 120)} stderr={clip_text(err_text, 120)}"
        )

        if stdout:
            return out_text
        if stderr:
            return err_text

    @staticmethod
    async def cmd_link_shell(cmd: str) -> "asyncio.subprocess.Process":
        """以 shell 字符串方式启动长生命周期子进程，并返回进程句柄。"""
        transports = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        logger.debug(
            f"process link mode=shell pid={transports.pid} cmd={summarize_command(cmd)}"
        )

        return transports


if __name__ == '__main__':
    pass
