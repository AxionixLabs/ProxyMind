#  ____            __
# |  _ \ ___ _ __ / _| ___  _ __ _ __ ___   __ _ _ __   ___ ___
# | |_) / _ \ '__| |_ / _ \| '__| '_ ` _ \ / _` | '_ \ / __/ _ \
# |  __/  __/ |  |  _| (_) | |  | | | | | | (_| | | | | (_|  __/
# |_|   \___|_|  |_|  \___/|_|  |_| |_| |_|\__,_|_| |_|\___\___|
#

import re
import time
import socket
import typing
import asyncio
from loguru import logger
from engine.terminal import Terminal
from utils import const


class Memrix(object):

    __instance: typing.Optional["Memrix"] = None
    __initialized: bool = False

    def __new__(cls, *args, **kwargs):
        if not cls.__instance:
            cls.__instance = super(Memrix, cls).__new__(cls)
        return cls.__instance

    def __init__(self):
        if not self.__initialized:
            self.transports: typing.Optional[asyncio.subprocess.Process] = None
            self.token: typing.Optional[str] = None

            self.prefix = "memrix"
            self.host   = "127.0.0.1"
            self.port   = 8765
            self.scene  = time.strftime("%Y%m%d%H%M%S")

        self.__initialized = True

    async def input_stream(self) -> None:
        async for line in self.transports.stdout:
            stream = line.decode(const.CHARSET, const.IGNORE)
            if matched := re.search(r"(?<=Token:\s).*", stream, re.S):
                self.token = matched.group()
            logger.info(stream)

    async def error_stream(self) -> None:
        async for line in self.transports.stderr:
            stream = line.decode(const.CHARSET, const.IGNORE)
            logger.info(stream)

    async def engine(self, cmd: list[str]) -> None:
        cmd = [self.prefix] + cmd
        self.transports = await Terminal.cmd_link(cmd)

        asyncio.create_task(self.input_stream())
        asyncio.create_task(self.error_stream())

        await asyncio.sleep(5)

    async def task_begin(self, mode: typing.Literal["--storm", "--sleek"], focus: str, imply: str) -> None:
        cmd = [mode, "--seed", focus, "--scene", self.scene, "--imply", imply, "--canopy"]
        await self.engine(cmd)

    async def task_final(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect((self.host, self.port))
            s.sendall(self.token.encode(const.CHARSET))

        await self.transports.wait()

    async def mem_reporter(self, layer: typing.Optional[typing.Literal["--layer"]] = None) -> None:
        cmd = ["--forge", self.scene + "_" + "Storm"]
        if layer: cmd += [layer]
        await self.engine(cmd)

        await self.transports.wait()

    async def gfx_reporter(self) -> None:
        cmd = ["--forge", self.scene + "_" + "Sleek"]
        await self.engine(cmd)

        await self.transports.wait()


if __name__ == '__main__':
    pass
