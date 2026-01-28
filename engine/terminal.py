#  _____                   _             _
# |_   _|__ _ __ _ __ ___ (_)_ __   __ _| |
#   | |/ _ \ '__| '_ ` _ \| | '_ \ / _` | |
#   | |  __/ |  | | | | | | | | | | (_| | |
#   |_|\___|_|  |_| |_| |_|_|_| |_|\__,_|_|
#

import os
import sys
import typing
import asyncio
from mindnova import const


class Terminal(object):
    """Terminal class."""

    @staticmethod
    async def cmd_line(cmd: list[str]) -> typing.Any:
        transports = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await transports.communicate()

        if stdout:
            return stdout.decode(const.CHARSET, const.IGNORE).strip()
        if stderr:
            return stderr.decode(const.CHARSET, const.IGNORE).strip()

    @staticmethod
    async def cmd_link(cmd: list[str]) -> asyncio.subprocess.Process:
        transports = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )

        return transports

    @staticmethod
    async def cmd_link_pty(cmd: list[str]) -> typing.Optional[asyncio.subprocess.Process]:
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
        transports = await asyncio.create_subprocess_shell(
            cmd,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await transports.communicate()

        if stdout:
            return stdout.decode(encoding=const.CHARSET, errors="ignore").strip()
        if stderr:
            return stderr.decode(encoding=const.CHARSET, errors="ignore").strip()

    @staticmethod
    async def cmd_link_shell(cmd: str) -> "asyncio.subprocess.Process":
        transports = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )

        return transports


if __name__ == '__main__':
    pass
