#   ____                 _______  __
#  / ___|___  _ __ ___  |  ___\ \/ /
# | |   / _ \| '__/ _ \ | |_   \  /
# | |__| (_) | | |  __/ |  _|  /  \
#  \____\___/|_|  \___| |_|   /_/\_\
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


class Framix(object):
    """Framix class."""

    __instance: typing.Optional["Framix"] = None
    __initialized: bool = False

    def __new__(cls, *args, **kwargs):
        if not cls.__instance:
            cls.__instance = super(Framix, cls).__new__(cls)
        return cls.__instance

    def __init__(self):
        if not self.__initialized:
            self.transports: typing.Optional[asyncio.subprocess.Process] = None
            self.token: typing.Optional[str] = None

            self.prefix = "framix"
            self.host   = "127.0.0.1"
            self.port   = 8766
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

    async def engine(self) -> None:
        pass

    async def start_record(self) -> None:
        pass

    async def close_record(self) -> None:
        pass

    async def task_analysis(self) -> None:
        pass

    async def pfm_reporter(self) -> None:
        pass


if __name__ == '__main__':
    pass
