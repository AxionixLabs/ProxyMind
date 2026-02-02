#   ____                 __  __                     _
#  / ___|___  _ __ ___  |  \/  | ___ _ __ ___  _ __(_)_  __
# | |   / _ \| '__/ _ \ | |\/| |/ _ \ '_ ` _ \| '__| \ \/ /
# | |__| (_) | | |  __/ | |  | |  __/ | | | | | |  | |>  <
#  \____\___/|_|  \___| |_|  |_|\___|_| |_| |_|_|  |_/_/\_\
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import re
import time
import socket
import typing
import asyncio
from collections import deque
from loguru import logger
from engine.terminal import Terminal
from backend.utilities import (
    const, marked, toolbox
)


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
            if matched := re.search(r"(?<=Token:\s).*", text, re.S):
                self.__token = matched.group()
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

        for _ in range(30):
            await asyncio.sleep(1.0)

            if self.__token: return None

            if self.out_fail.is_set():
                self.__transports.terminate()
                logger.error("\n".join(self.out_ring))
                raise marked.subproc_fail(source=f"{self.prefix}.stream", out_ring=self.out_ring)

    # workflow: ==== MCP Tool ====
    async def mx_task_begin(
        self,
        style: typing.Literal["--storm", "--sleek"],
        focus: str,
        imply: typing.Optional[str] = None,
        title: typing.Optional[str] = None
    ) -> typing.Any:

        if not await toolbox.port_listen(self.port):
            logger.error(f"Port {self.port} is liveness.")
            raise marked.port_busy(self.port, "liveness", host=self.host)

        cmd = [style, "--scene", self.scene, "--focus", focus]
        if imply: cmd += ["--imply", imply]
        if title: cmd += ["--title", title]
        cmd += ["--watch"]

        return await self.__engine(*cmd)

    # workflow: ==== MCP Tool ====
    async def mx_task_final(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect((self.host, self.port))
            s.sendall(self.__token.encode(const.CHARSET))

        self.__token = None
        await self.__transports.wait()

    # workflow: ==== MCP Tool ====
    async def mx_mem_reporter(self, layer: bool = False) -> None:
        final_dir = self.scene + "_" + "Storm"
        marked.ensure_d(final_dir, "final_dir _Storm")

        cmd = ["--forge", final_dir]
        if layer: cmd += ["--layer"]
        await self.__engine(*cmd)

        await self.__transports.wait()

        self.scene = time.strftime("%Y%m%d%H%M%S")

    # workflow: ==== MCP Tool ====
    async def mx_gfx_reporter(self) -> None:
        final_dir = self.scene + "_" + "Sleek"
        marked.ensure_d(final_dir, "final_dir _Sleek")

        await self.__engine("--forge", final_dir)

        await self.__transports.wait()

        self.scene = time.strftime("%Y%m%d%H%M%S")


if __name__ == '__main__':
    pass
