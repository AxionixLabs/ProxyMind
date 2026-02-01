#   ____                 _____                    _
#  / ___|___  _ __ ___  |  ___| __ __ _ _ __ ___ (_)_  __
# | |   / _ \| '__/ _ \ | |_ | '__/ _` | '_ ` _ \| \ \/ /
# | |__| (_) | | |  __/ |  _|| | | (_| | | | | | | |>  <
#  \____\___/|_|  \___| |_|  |_|  \__,_|_| |_| |_|_/_/\_\
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import os
import json
import time
import typing
import asyncio
from collections import deque
from loguru import logger
from engine.terminal import Terminal
from backend.utilities import (
    const, marked
)


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

            self.__prefix: str = "framix"

            self.agent_id: str = self.__prefix

            self.label: str = time.strftime("%Y%m%d%H%M%S")
            self.total: str = ""

            self.out_fail: typing.Optional[asyncio.Event] = None
            self.out_ring: typing.Optional[deque[str]] = None

        self.__initialized = True

    @property
    def prefix(self) -> str:
        return self.__prefix

    def __push(self, source: str, text: str) -> None:
        string = (text or "").strip()
        if not string: return None
        self.out_ring.append(f"{source}: {string}")

    async def __streaming(self, source: str, stream: typing.AsyncIterator[bytes]) -> None:
        async for line in stream:
            text = line.decode(const.CHARSET, const.IGNORE)
            self.__push(source, text)
            if "Error" in text or "检测连接设备" in text:
                return self.out_fail.set()

            logger.info(text.rstrip())

    async def __engine(self, *args, **__) -> None:
        self.out_fail = asyncio.Event()
        self.out_ring = deque(maxlen=10)

        cmd = [self.prefix] + list(args)
        self.__transports = await Terminal.cmd_link(cmd)

        asyncio.create_task(self.__streaming(f"{self.prefix}.stdout", self.__transports.stdout))
        asyncio.create_task(self.__streaming(f"{self.prefix}.stderr", self.__transports.stderr))

        await self.__transports.wait()

        if self.out_fail.is_set():
            logger.error("\n".join(self.out_ring))
            raise marked.subproc_fail(source=f"{self.prefix}.stream", out_ring=self.out_ring)

    # workflow: ==== MCP Tool ====
    async def fx_frame_analyzer(self, title: str, video: list[str], scale: float = 0.3) -> typing.Any:
        marked.ensure_i(video, "video")
        marked.ensure_d(self.total, "total")

        payload = {
            "label": self.label, "title": title, "video": video
        }
        return await self.__engine(
            "--keras", "--boost", "--scale", str(min(1.0, max(0.1, scale))),
            "--frame", json.dumps(payload), "--total", self.total, "--debug"
        )

    # workflow: ==== MCP Tool ====
    async def fx_frame_reporter(self) -> None:
        final_dir = os.path.join(self.total, "FX" + "_" + self.label)
        marked.ensure_d(final_dir, "final_dir FX_")

        resp = await self.__engine("--merge", final_dir, "--debug")

        self.label = time.strftime("%Y%m%d%H%M%S")

        return resp


if __name__ == '__main__':
    pass
