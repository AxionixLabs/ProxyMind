#   ____                 __  ____  __
#  / ___|___  _ __ ___  |  \/  \ \/ /
# | |   / _ \| '__/ _ \ | |\/| |\  /
# | |__| (_) | | |  __/ | |  | |/  \
#  \____\___/|_|  \___| |_|  |_/_/\_\
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import re
import time
import socket
import typing
import asyncio
from loguru import logger
from engine.terminal import Terminal
from backend.utilities import const


class Memrix(object):
    """Memrix class."""

    __instance: typing.Optional["Memrix"] = None
    __initialized: bool = False

    def __new__(cls, *args, **kwargs):
        if not cls.__instance:
            cls.__instance = super(Memrix, cls).__new__(cls)
        return cls.__instance

    def __init__(self):
        if not self.__initialized:
            self.__transports: typing.Optional[asyncio.subprocess.Process] = None
            self.__token: typing.Optional[str] = None

            self.__prefix: str = "memrix"

            self.agent_id: str = self.__prefix

            self.host: str = "127.0.0.1"
            self.port: int = 8765

            self.scene: str = time.strftime("%Y%m%d%H%M%S")

        self.__initialized = True

    async def __input_stream(self) -> None:
        async for line in self.__transports.stdout:
            stream = line.decode(const.CHARSET, const.IGNORE)
            if matched := re.search(r"(?<=Token:\s).*", stream, re.S):
                self.__token = matched.group()
            logger.info(stream)

    async def __error_stream(self) -> None:
        async for line in self.__transports.stderr:
            stream = line.decode(const.CHARSET, const.IGNORE)
            logger.info(stream)

    async def __engine(self, *args, **__) -> None:
        cmd = [self.__prefix] + list(args)
        self.__transports = await Terminal.cmd_link(cmd)

        asyncio.create_task(self.__input_stream())
        asyncio.create_task(self.__error_stream())

        await asyncio.sleep(5)

    async def task_begin(
        self,
        style: typing.Literal["--storm", "--sleek"],
        focus: str,
        imply: str,
        title: typing.Optional[str]
    ) -> typing.Any:

        cmd = [style, "--scene", self.scene, "--focus", focus, "--imply", imply]
        if title: cmd += ["--title", title]
        cmd += ["--watch"]
        return await self.__engine(*cmd)

    async def task_final(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect((self.host, self.port))
            s.sendall(self.__token.encode(const.CHARSET))

        await self.__transports.wait()

    async def mem_reporter(self, layer: bool = False) -> None:
        cmd = ["--forge", self.scene + "_" + "Storm"]
        if layer: cmd += ["--layer"]
        await self.__engine(*cmd)

        await self.__transports.wait()

    async def gfx_reporter(self) -> None:
        await self.__engine("--forge", self.scene + "_" + "Sleek")

        await self.__transports.wait()


if __name__ == '__main__':
    pass
