# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import re
import time
import socket
import typing
import asyncio
from collections import deque
from loguru import logger
from backend.mcp_core.core_buffer import (
    LineBuffer, GateMachine, MX_SPEC
)
from backend.utilities import const
from backend.utilities.process import Flux, port_listen
from backend.utilities.validation import marked

if typing.TYPE_CHECKING:
    from backend.utilities.state import ItemSessionStore

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


class Memrix(object):
    """Memrix class."""

    __instance: typing.Optional["Memrix"] = None
    __initialized: bool = False

    def __new__(cls, *args, **kwargs):
        if not cls.__instance:
            cls.__instance = super(Memrix, cls).__new__(cls)
        return cls.__instance

    def __init__(self, *, mx_report_store: "ItemSessionStore"):
        if not self.__initialized:
            self.__transports: typing.Optional[asyncio.subprocess.Process] = None
            self.mx_report_store = mx_report_store

            self.token: typing.Optional[str] = None
            self.style: typing.Optional[str] = None

            self.__prefix: str = "memrix"

            self.agent_id: str = self.__prefix

            self.lock: asyncio.Lock = asyncio.Lock()

            self.host: str = "127.0.0.1"
            self.port: int = 8765

            self.scene: str = time.strftime("%Y%m%d%H%M%S")

            self.is_start: typing.Optional[asyncio.Event] = None
            self.out_fail: typing.Optional[asyncio.Event] = None
            self.out_ring: typing.Optional[deque[str]] = None

            self.lb_stdout: LineBuffer = LineBuffer()
            self.lb_stderr: LineBuffer = LineBuffer()

            self.tool_events: dict[str, typing.Any] = {}

        self.__initialized = True

    @property
    def prefix(self) -> str:
        return self.__prefix

    def push(self, source: str, text: str) -> None:
        string = (text or "").strip()
        if not string: return None
        self.out_ring.append(f"{source}: {string}")

    async def streaming(
        self,
        source: str,
        stream: typing.AsyncIterator[bytes],
        gates: list[GateMachine]
    ) -> None:
        """通用 streaming 消费器。"""

        self.lb_stdout.reset()
        self.lb_stderr.reset()

        lb = self.lb_stdout if source.endswith(".stdout") else self.lb_stderr

        async for chunk in stream:
            text = chunk.decode(const.CHARSET, const.IGNORE)
            text = ANSI_RE.sub("", text)

            self.push(source, text)

            for ln in lb.feed(text):
                ln = ln.strip()
                if not ln: continue

                if "Engine Start" in ln or "Report Start" in ln:
                    self.is_start.set()

                if "Token:" in ln:
                    self.token = ln.split("Token:", 1)[1].strip()

                if "MemrixError" in ln or "检测连接设备" in ln:
                    self.out_fail.set()
                    return logger.error(f"failfast hit: {ln}")

                for gate in gates:
                    async with self.lock:
                        out = gate.feed_chunk(ln)
                    if out:
                        self.tool_events.setdefault(out["tool"], {})[out["phase"]] = out
                        logger.warning(out)

        for ln in lb.flush():
            ln = ln.strip()
            if not ln: continue

            if "Engine Start" in ln or "Report Start" in ln:
                self.is_start.set()

            if "Token:" in ln:
                self.token = ln.split("Token:", 1)[1].strip()

            if "MemrixError" in ln or "检测连接设备" in ln:
                self.out_fail.set()
                logger.error(f"failfast hit: {ln}")
                return

            for gate in gates:
                async with self.lock:
                    out = gate.feed_chunk(ln)
                if out:
                    self.tool_events.setdefault(out["tool"], {})[out["phase"]] = out
                    logger.warning(out)

    async def __engine(self, *args, **__) -> dict[str, typing.Any]:
        self.tool_events = {}

        self.is_start = asyncio.Event()

        self.out_fail = asyncio.Event()
        self.out_ring = deque(maxlen=20)

        cmd = [self.prefix] + list(args)
        self.__transports = await Flux.cmd_link(cmd)

        gates = [GateMachine(MX_SPEC)]

        asyncio.create_task(self.streaming(f"{self.prefix}.stdout", self.__transports.stdout, gates))
        asyncio.create_task(self.streaming(f"{self.prefix}.stderr", self.__transports.stderr, gates))

        for _ in range(60):
            await asyncio.sleep(1.0)

            if self.is_start.is_set():
                return {
                    "text"        : f"{self.agent_id.capitalize()}启动成功。",
                    "attachments" : [],
                    "data": {
                        "ok"     : True,
                        "events" : self.tool_events.get(self.agent_id, {}),
                        "token"  : self.token
                    },
                    "logs": []
                }

            if self.out_fail.is_set():
                await self.shutdown()
                logger.error("\n".join(self.out_ring))
                raise marked.subproc_fail(source=f"{self.prefix}.stream", out_ring=self.out_ring)

        await self.shutdown()

        return {
            "text"        : f"{self.agent_id.capitalize()}启动超时。",
            "attachments" : [],
            "data": {
                "ok"     : False,
                "result" : "\n".join(map(str, list(self.out_ring))),
                "events" : self.tool_events.get(self.agent_id, {})
            },
            "logs": []
        }

    async def shutdown(self) -> None:
        """统一退出/清理。"""
        self.token = None
        self.style = None

        if self.__transports and self.__transports.returncode is not None:
            return None

        try:
            self.__transports.terminate()
        except ProcessLookupError:
            return None

        try:
            await asyncio.wait_for(self.__transports.wait(), timeout=3.0)
        except asyncio.TimeoutError:
            try:
                self.__transports.kill()
            except ProcessLookupError:
                return None
            await self.__transports.wait()

    # workflow: ==== MCP Tool ====
    async def mx_task_begin(
        self,
        style: typing.Literal["--storm", "--sleek"],
        focus: str,
        imply: typing.Optional[str] = None,
        title: typing.Optional[str] = None
    ) -> dict[str, typing.Any]:

        if not await port_listen(self.port):
            logger.error(f"Port {self.port} is liveness.")
            raise marked.port_busy(self.port, "liveness", host=self.host)

        self.style = style.removeprefix("--")

        cmd = [style, "--scene", self.scene, "--focus", focus]
        if imply: cmd += ["--imply", imply]
        if title: cmd += ["--title", title]
        cmd += ["--watch"]

        return await self.__engine(*cmd)

    # workflow: ==== MCP Tool ====
    async def mx_task_final(self, token: typing.Optional[str] = None) -> dict[str, typing.Any]:
        cur_token = token or self.token
        if not cur_token:
            return {
                "text"        : f"{self.agent_id.capitalize()}结束失败：token为空。",
                "attachments" : [],
                "data": {
                    "ok"     : False,
                    "events" : self.tool_events.get(self.agent_id, {})
                },
                "logs": []
            }

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect((self.host, self.port))
            s.sendall(cur_token.encode(const.CHARSET))

        self.token = None

        await self.__transports.wait()

        await self.mx_report_store.set(
            f"mx_{self.scene}",
            self.scene + "_" + self.style.capitalize()
        )

        return {
            "text"        : f"{self.agent_id.capitalize()}已结束。",
            "attachments" : [],
            "data": {
                "ok"     : True,
                "events" : self.tool_events.get(self.agent_id, {})
            },
            "logs": []
        }

    # workflow: ==== MCP Tool ====
    async def mx_mem_reporter(
        self,
        scene: typing.Optional[str] = None,
        layer: bool = False
    ) -> dict[str, typing.Any]:

        final_scene = scene if scene else self.scene + "_" + "Storm"

        cmd = ["--forge", final_scene, "--watch"]
        if layer: cmd += ["--layer"]
        begin = await self.__engine(*cmd)

        await self.__transports.wait()

        await self.mx_report_store.pop(f"mx_{final_scene}")
        self.scene = time.strftime("%Y%m%d%H%M%S")

        return {
            "text"        : f"{self.agent_id.capitalize()}报告任务完成。",
            "attachments" : [],
            "data": {
                "ok"     : True,
                "begin"  : begin.get("data", {}),
                "events" : self.tool_events.get(self.agent_id, {})
            },
            "logs": []
        }

    # workflow: ==== MCP Tool ====
    async def mx_gfx_reporter(
        self,
        scene: typing.Optional[str] = None
    ) -> dict[str, typing.Any]:

        final_scene = scene if scene else self.scene + "_" + "Sleek"

        cmd = ["--forge", final_scene, "--watch"]
        begin = await self.__engine(*cmd)

        await self.__transports.wait()

        await self.mx_report_store.pop(f"mx_{final_scene}")
        self.scene = time.strftime("%Y%m%d%H%M%S")

        return {
            "text"        : f"{self.agent_id.capitalize()}报告任务完成。",
            "attachments" : [],
            "data": {
                "ok"     : True,
                "begin"  : begin.get("data", {}),
                "events" : self.tool_events.get(self.agent_id, {}),
            },
            "logs": []
        }


if __name__ == '__main__':
    pass
