#   ____                 _______  __
#  / ___|___  _ __ ___  |  ___\ \/ /
# | |   / _ \| '__/ _ \ | |_   \  /
# | |__| (_) | | |  __/ |  _|  /  \
#  \____\___/|_|  \___| |_|   /_/\_\
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import os
import re
import json
import time
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
            self.__transports: typing.Optional[asyncio.subprocess.Process] = None
            self.__token: typing.Optional[str] = None

            self.__prefix = "framix"
            self.__host   = "127.0.0.1"
            self.__port   = 8766
            self.__label  = time.strftime("%Y%m%d%H%M%S")
            self.__total  = ""

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

        await self.__transports.wait()

    # async def start_record(self) -> None:
    #     pass

    # async def close_record(self) -> None:
    #     pass

    async def analyzer(self, title: str, video: list[str]) -> typing.Any:
        payload = {
            "label": self.__label, "title": title, "video": video
        }
        return await self.__engine(
            "--keras", "--boost", "--scale", "0.3", "--frame", json.dumps(payload), "--total", self.__total
        )

    async def reporter(self) -> None:
        return await self.__engine(
            "--merge", os.path.join(self.__total, "FX" + "_" + self.__label)
        )


if __name__ == '__main__':
    pass
