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

    def __init__(self):
        if not self.__initialized:
            self.__transports: typing.Optional[asyncio.subprocess.Process] = None

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

    async def __streaming(
        self,
        source: str,
        stream: typing.AsyncIterator[bytes],
        gates: list[GateMachine]
    ) -> None:
        """
        Framix streaming：
        - chunk -> LineBuffer -> line
        - 每行：
          - push 到 out_ring（少量兜底）
          - fail fast
          - 喂 gate，产出 start/end 事件则保存 + 打印
        """
        lb = self.lb_stdout if source.endswith(".stdout") else self.lb_stderr

        async for chunk in stream:
            text = chunk.decode(const.CHARSET, const.IGNORE)
            text = ANSI_RE.sub("", text)

            self.push(source, text)

            for ln in lb.feed(text):
                if not ln:
                    continue

                # fail fast（按行）
                if "FramixError" in ln or "检测连接设备" in ln:
                    return self.out_fail.set()

                # gate：逐行喂
                for gate in gates:
                    async with self.lock:
                        out = gate.feed_line(ln)
                    if out:
                        self.tool_events.setdefault(out["tool"], {})[out["phase"]] = out
                        logger.info(f"[{self.agent_id.capitalize()}] {out}")

        # stream 结束：flush 半行（如果最后没有换行符）
        for ln in lb.flush():
            if not ln:
                continue

            if "FramixError" in ln or "检测连接设备" in ln:
                return self.out_fail.set()

            for gate in gates:
                async with self.lock:
                    out = gate.feed_line(ln)
                if out:
                    self.tool_events.setdefault(self.agent_id, {})[out["phase"]] = out
                    logger.info(f"[{self.agent_id.capitalize()}] {out}")

    async def __engine(self, *args, **__) -> dict[str, typing.Any]:
        # ✅ 每次任务清空
        self.tool_events = {}

        self.out_fail = asyncio.Event()
        self.out_ring = deque(maxlen=20)

        cmd = [self.prefix] + list(args)
        self.__transports = await Terminal.cmd_link(cmd)

        # ✅ 共享同一个 GateMachine（重要：stdout/stderr 合并状态）
        gates = [GateMachine(FX_SPEC)]

        asyncio.create_task(self.__streaming(f"{self.prefix}.stdout", self.__transports.stdout, gates))
        asyncio.create_task(self.__streaming(f"{self.prefix}.stderr", self.__transports.stderr, gates))

        await self.__transports.wait()

        if self.out_fail.is_set():
            logger.error("\n".join(self.out_ring))
            raise marked.subproc_fail(source=f"{self.prefix}.stream", out_ring=self.out_ring)

        return {
            "text"        : "已输出结果。",
            "attachments" : [],
            "data": {
                "ok"     : True,
                "result" : "\n".join(map(str, list(self.out_ring))),
                "events" : self.tool_events.get(self.agent_id, {})
            },
            "logs": []
        }

    # workflow: ==== MCP Tool ====
    async def fx_frame_analyzer(
        self,
        title: str,
        video: list[str],
        scale: float = 0.3
    ) -> dict[str, typing.Any]:

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
    async def fx_frame_reporter(self) -> dict[str, typing.Any]:
        final_dir = os.path.join(self.total, "FX" + "_" + self.label)
        marked.ensure_d(final_dir, "final_dir FX_")

        resp = await self.__engine("--merge", final_dir, "--debug")

        self.label = time.strftime("%Y%m%d%H%M%S")

        return resp


if __name__ == '__main__':
    pass
