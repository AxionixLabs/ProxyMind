#   ____                 _____                    _
#  / ___|___  _ __ ___  |  ___| __ __ _ _ __ ___ (_)_  __
# | |   / _ \| '__/ _ \ | |_ | '__/ _` | '_ ` _ \| \ \/ /
# | |__| (_) | | |  __/ |  _|| | | (_| | | | | | | |>  <
#  \____\___/|_|  \___| |_|  |_|  \__,_|_| |_| |_|_/_/\_\
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import os
import re
import json
import time
import typing
import asyncio
from collections import deque
from loguru import logger
from engine.terminal import Terminal
from backend.mcp_core.core_buffer import (
    LineBuffer, GateMachine, FX_SPEC
)
from backend.utilities import (
    const, marked
)

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


class Framix(object):
    """Framix class."""

    __instance: typing.Optional["Framix"] = None
    __initialized: bool = False

    def __new__(cls, *args, **kwargs):
        if not cls.__instance:
            cls.__instance = super(Framix, cls).__new__(cls)
        return cls.__instance

    def __init__(self, *, fx_report_session: dict[str, typing.Any]):
        if not self.__initialized:
            self.__transports: typing.Optional[asyncio.subprocess.Process] = None
            self.fx_report_session = fx_report_session

            self.__prefix: str = "framix"

            self.agent_id: str = self.__prefix

            self.lock: asyncio.Lock = asyncio.Lock()

            self.label: str = time.strftime("%Y%m%d%H%M%S")
            self.total: str = ""

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

            if "FramixError" in text or "检测连接设备" in text:
                return self.out_fail.set()

            for ln in lb.feed(text):
                ln = ln.strip()
                if not ln: continue

                if "FramixError" in ln or "检测连接设备" in ln:
                    return self.out_fail.set()

                for gate in gates:
                    async with self.lock:
                        out = gate.feed_chunk(ln)
                    if out:
                        self.tool_events.setdefault(out["tool"], {})[out["phase"]] = out
                        logger.info(out)

        for ln in lb.flush():
            ln = ln.strip()
            if not ln: continue

            if "FramixError" in ln or "检测连接设备" in ln:
                return self.out_fail.set()

            for gate in gates:
                async with self.lock:
                    out = gate.feed_chunk(ln)
                if out:
                    self.tool_events.setdefault(out["tool"], {})[out["phase"]] = out
                    logger.info(out)

    async def __engine(self, *args, **__) -> dict[str, typing.Any]:
        self.tool_events = {}

        self.out_fail = asyncio.Event()
        self.out_ring = deque(maxlen=20)

        cmd = [self.prefix] + list(args)
        self.__transports = await Terminal.cmd_link(cmd)

        gates = [GateMachine(FX_SPEC)]

        asyncio.create_task(self.streaming(f"{self.prefix}.stdout", self.__transports.stdout, gates))
        asyncio.create_task(self.streaming(f"{self.prefix}.stderr", self.__transports.stderr, gates))

        await self.__transports.wait()

        if self.out_fail.is_set():
            logger.error("\n".join(self.out_ring))
            raise marked.subproc_fail(source=f"{self.prefix}.stream", out_ring=self.out_ring)

        return {
            "text"        : f"{self.agent_id.capitalize()}已输出结果。",
            "attachments" : [],
            "data": {
                "ok"     : True,
                "events" : self.tool_events.get(self.agent_id, {})
            },
            "logs": []
        }

    async def shutdown(self) -> None:
        """统一退出/清理。"""
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
    async def fx_frame_analysis(
        self,
        video: list[str],
        total: typing.Optional[str],
        scale: float = 0.3
    ) -> dict[str, typing.Any]:

        cmd = [
            "--keras",
            "--boost",
            "--scale", str(min(1.0, max(0.1, scale))),
            "--debug"
        ]

        if total:
            marked.ensure_d(total, "total")
            cmd += ["--total", total]

        for v in video or []:
            marked.ensure_f(v, "video_file")
            cmd += ["--video", v]

        resp = await self.__engine(*cmd)

        return resp

    # workflow: ==== MCP Tool ====
    async def fx_frame_analyzer(
        self,
        video: list[str],
        title: str,
        total: str,
        scale: float = 0.3
    ) -> dict[str, typing.Any]:

        self.total = total

        marked.ensure_i(video, "video")
        marked.ensure_d(self.total, "total")

        payload = {
            "label": self.label, "title": title, "video": video
        }

        cmd = [
            "--keras",
            "--boost",
            "--scale", str(min(1.0, max(0.1, scale))),
            "--frame", json.dumps(payload),
            "--total", self.total,
            "--debug"
        ]

        logger.warning(cmd)

        resp = await self.__engine(*cmd)

        self.fx_report_session.update({
            f"fx_frame_{self.label}": os.path.join(self.total, "FX" + "_" + self.label)
        })

        return resp

    # workflow: ==== MCP Tool ====
    async def fx_frame_reporter(self, total: typing.Optional[str] = None) -> dict[str, typing.Any]:
        if total:
            final_dir = total
        else:
            final_dir = os.path.join(self.total, "FX" + "_" + self.label)
        marked.ensure_d(final_dir, "final_dir FX_")

        cmd = ["--merge", final_dir, "--debug"]

        resp = await self.__engine(*cmd)

        self.fx_report_session.pop("fx_frame_" + self.label, None)
        self.label = time.strftime("%Y%m%d%H%M%S")

        return resp


if __name__ == '__main__':
    pass
