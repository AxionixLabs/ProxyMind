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
from backend.mcp_core.core_buffer import (
    LineBuffer, GateMachine, MX_SPEC
)
from backend.utilities import (
    const, marked, toolbox
)

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


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

    async def __streaming(
        self,
        source: str,
        stream: typing.AsyncIterator[bytes],
        gates: list[GateMachine]
    ) -> None:
        """
        通用 streaming 消费器：
        - chunk -> (LineBuffer) -> lines
        - 每行：
          - push 到 out_ring（可选，留 50 条）
          - engine start / token / fail 检测
          - 喂给 gates，gate 产出 start/end 事件则保存 + 打印
        """

        # stdout/stderr 各自一个 LineBuffer，避免两路输出拼行互相污染
        lb = self.lb_stdout if source.endswith(".stdout") else self.lb_stderr

        async for chunk in stream:
            text = chunk.decode(const.CHARSET, const.IGNORE)
            text = ANSI_RE.sub("", text)

            # 原始 chunk 仍然进 ring
            self.push(source, text)

            # 关键：chunk 拆成完整行
            for ln in lb.feed(text):
                if not ln:
                    continue

                # 1) Start 门：只要看到开始信号就置位
                if ("Engine Start" in ln) or ("Report Start" in ln):
                    self.is_start.set()

                # 2) Token：按行提取，避免 re.S 吞多行
                if "Token:" in ln:
                    self.__token = ln.split("Token:", 1)[1].strip()

                # 3) Fail fast：命中错误直接标记失败
                if "MemrixError" in ln or "检测连接设备" in ln:               
                    return self.out_fail.set()

                # 4) Gate：逐行喂所有 gate
                for gate in gates:
                    async with self.lock:
                        out = gate.feed_line(ln)
                    if out:
                        # 保存事件（start/end）
                        self.tool_events.setdefault(out["tool"], {})[out["phase"]] = out
                        logger.info(f"[{self.agent_id.capitalize()}] {out}")

        # stream 结束：flush 半行（如果最后没有换行符）
        for ln in lb.flush():
            if not ln:
                continue

            if "Engine Start" in ln or "Report Start" in ln:
                self.is_start.set()

            if "Token:" in ln:
                self.__token = ln.split("Token:", 1)[1].strip()

            if "MemrixError" in ln or "检测连接设备" in ln:
                return self.out_fail.set()

            for gate in gates:
                async with self.lock:
                    out = gate.feed_line(ln)
                if out:
                    self.tool_events.setdefault(out["tool"], {})[out["phase"]] = out
                    logger.info(f"[{self.agent_id.capitalize()}] {out}")

    async def __engine(self, *args, **__) -> dict[str, typing.Any]:
        # ✅ 每次任务清空事件池
        self.tool_events = {}

        self.is_start = asyncio.Event()

        self.out_fail = asyncio.Event()
        self.out_ring = deque(maxlen=20)

        cmd = [self.prefix] + list(args)
        self.__transports = await Terminal.cmd_link(cmd)

        # ✅ 共享同一个 GateMachine（重要：stdout/stderr 合并状态）
        gates = [GateMachine(MX_SPEC)]

        asyncio.create_task(self.__streaming(f"{self.prefix}.stdout", self.__transports.stdout, gates))
        asyncio.create_task(self.__streaming(f"{self.prefix}.stderr", self.__transports.stderr, gates))

        for _ in range(30):
            await asyncio.sleep(1.0)

            if self.is_start.is_set():
                return {
                    "text"        : "启动成功。",
                    "attachments" : [],
                    "data": {
                        "ok"     : True,
                        "result" : "\n".join(map(str, list(self.out_ring))),
                        "events" : self.tool_events.get(self.agent_id, {}),
                        "token"  : self.__token
                    },
                    "logs": []
                }

            if self.out_fail.is_set():
                self.__transports.terminate()
                logger.error("\n".join(self.out_ring))
                raise marked.subproc_fail(source=f"{self.prefix}.stream", out_ring=self.out_ring)

        return {
            "text"        : "启动超时。",
            "attachments" : [],
            "data": {
                "ok"     : False,
                "result" : "\n".join(map(str, list(self.out_ring))),
                "events" : self.tool_events.get(self.agent_id, {})
            },
            "logs": []
        }

    # workflow: ==== MCP Tool ====
    async def mx_task_begin(
        self,
        style: typing.Literal["--storm", "--sleek"],
        focus: str,
        imply: typing.Optional[str] = None,
        title: typing.Optional[str] = None
    ) -> dict[str, typing.Any]:

        if not await toolbox.port_listen(self.port):
            logger.error(f"Port {self.port} is liveness.")
            raise marked.port_busy(self.port, "liveness", host=self.host)

        cmd = [style, "--scene", self.scene, "--focus", focus]
        if imply: cmd += ["--imply", imply]
        if title: cmd += ["--title", title]
        cmd += ["--watch"]

        return await self.__engine(*cmd)

    # workflow: ==== MCP Tool ====
    async def mx_task_final(self) -> dict[str, typing.Any]:
        if not self.__token:
            # token 为空，说明 begin 没成功或 token 没抓到
            return {
                "text"        : "结束失败：token为空。",
                "attachments" : [],
                "data": {
                    "ok"     : False,
                    "events" : self.tool_events.get(self.agent_id, {})
                },
                "logs": []
            }

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect((self.host, self.port))
            s.sendall(self.__token.encode(const.CHARSET))

        self.__token = None
        await self.__transports.wait()

        return {
            "text"        : "已结束。",
            "attachments" : [],
            "data": {
                "ok"     : True,
                "events" : self.tool_events.get(self.agent_id, {})
            },
            "logs": []
        }

    # workflow: ==== MCP Tool ====
    async def mx_mem_reporter(self, layer: bool = False) -> dict[str, typing.Any]:
        final_dir = self.scene + "_" + "Storm"
        marked.ensure_d(final_dir, "final_dir _Storm")

        cmd = ["--forge", final_dir, "--watch"]
        if layer: cmd += ["--layer"]
        begin = await self.__engine(*cmd)

        await self.__transports.wait()

        self.scene = time.strftime("%Y%m%d%H%M%S")

        return {
            "text"        : "报告任务完成。",
            "attachments" : [],
            "data": {
                "ok"     : True,
                "begin"  : begin.get("data", {}),
                "events" : self.tool_events.get(self.agent_id, {})
            },
            "logs": []
        }

    # workflow: ==== MCP Tool ====
    async def mx_gfx_reporter(self) -> dict[str, typing.Any]:
        final_dir = self.scene + "_" + "Sleek"
        marked.ensure_d(final_dir, "final_dir _Sleek")

        begin = await self.__engine("--forge", final_dir, "--watch")

        await self.__transports.wait()

        self.scene = time.strftime("%Y%m%d%H%M%S")

        return {
            "text"        : "报告任务完成。",
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
