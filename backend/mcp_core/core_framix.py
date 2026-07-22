# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import re
import json
import typing
import asyncio
from datetime import datetime
from collections import deque
from loguru import logger
from backend.mcp_core.core_buffer import (
    LineBuffer,
    GateMachine,
    FX_SPEC
)
from backend.utilities.process import (
    Flux,
    spawn_env
)
from backend.utilities.validation import marked
from backend.utilities.storage.roots import output_base_dir
from backend.utilities import const

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
            self.run_lock: asyncio.Lock = asyncio.Lock()

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

        lb = self.lb_stdout if source.endswith(".stdout") else self.lb_stderr
        logger.debug(f"[{self.prefix}] stream open source={source}")

        async for chunk in stream:
            text = chunk.decode(const.CHARSET, const.IGNORE)
            text = ANSI_RE.sub("", text)

            self.push(source, text)
            logger.debug(f"[{self.prefix}] {source} chunk={text.strip()}")

            if "FramixError" in text or "检测连接设备" in text:
                logger.error(f"[{self.prefix}] failfast hit source={source} text={text.strip()}")
                return self.out_fail.set()

            for ln in lb.feed(text):
                ln = ln.strip()
                if not ln: continue
                logger.debug(f"[{self.prefix}] {source} line={ln}")

                if "FramixError" in ln or "检测连接设备" in ln:
                    logger.error(f"[{self.prefix}] failfast hit source={source} line={ln}")
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
            logger.debug(f"[{self.prefix}] {source} flush={ln}")

            if "FramixError" in ln or "检测连接设备" in ln:
                logger.error(f"[{self.prefix}] failfast hit source={source} flush={ln}")
                return self.out_fail.set()

            for gate in gates:
                async with self.lock:
                    out = gate.feed_chunk(ln)
                if out:
                    self.tool_events.setdefault(out["tool"], {})[out["phase"]] = out
                    logger.info(out)

    async def __engine(self, *args, **__) -> dict[str, typing.Any]:
        async with self.run_lock:
            self.tool_events = {}
            self.lb_stdout.reset()
            self.lb_stderr.reset()

            self.out_fail = asyncio.Event()
            self.out_ring = deque(maxlen=20)

            cmd = [self.prefix] + list(args)
            env = spawn_env()
            logger.info(f"[{self.prefix}] engine spawn cmd={cmd}")
            logger.info(
                f"[{self.prefix}] engine spawn env="
                f"PYTHONUTF8={env.get('PYTHONUTF8')} "
                f"PYTHONIOENCODING={env.get('PYTHONIOENCODING')} "
                f"TERM={env.get('TERM')} NO_COLOR={env.get('NO_COLOR')}"
            )
            self.__transports = await Flux.cmd_link_exec(cmd, env=env)
            logger.info(
                f"[{self.prefix}] engine linked pid={self.__transports.pid}"
            )

            gates = [GateMachine(FX_SPEC)]
            stream_tasks = (
                asyncio.create_task(self.streaming(
                    f"{self.prefix}.stdout",
                    self.__transports.stdout,
                    gates,
                )),
                asyncio.create_task(self.streaming(
                    f"{self.prefix}.stderr",
                    self.__transports.stderr,
                    gates,
                )),
            )

            await self.__transports.wait()
            await asyncio.gather(*stream_tasks)
            logger.info(
                f"[{self.prefix}] engine exit rc={self.__transports.returncode} "
                f"recent_logs={list(self.out_ring)}"
            )

            if self.out_fail.is_set():
                logger.error("\n".join(self.out_ring))
                raise marked.subproc_fail(
                    source=f"{self.prefix}.stream",
                    out_ring=self.out_ring,
                )

            return {
                "ok"          : True,
                "text"        : f"{self.agent_id.capitalize()} results exported.",
                "attachments" : [],
                "data": {
                    "events" : self.tool_events.get(self.agent_id, {})
                },
                "logs": []
            }

    async def shutdown(self) -> None:
        """统一退出/清理。"""
        if self.__transports is None or self.__transports.returncode is not None:
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

    async def fx_frame_analysis(
        self,
        video: list[str],
        total: str,
        scale: float = 0.3
    ) -> dict[str, typing.Any]:

        output_root = output_base_dir(total)

        cmd = [
            "--keras",
            "--boost",
            "--scale", str(min(1.0, max(0.1, scale))),
            "--total", str(output_root),
            "--debug"
        ]

        for v in video or []:
            marked.ensure_f(v, "video_file")
            cmd += ["--video", v]

        resp = await self.__engine(*cmd)
        resp["data"]["total"] = str(output_root)

        return resp

    async def fx_frame_analyzer(
        self,
        title: str,
        video: list[str],
        label: str,
        total: str,
        scale: float = 0.3
    ) -> dict[str, typing.Any]:

        def validate_label() -> str:
            """校验 Framix 任务标识的压缩时间戳格式。"""
            if not isinstance(label, str) or re.fullmatch(r"\d{14}", label) is None:
                raise marked.fail_tip(
                    "label must use the YYYYMMDDhhmmss format.",
                    code=const.CODE_EXC,
                    hint=const.HINT_HLT,
                    field="label",
                    expect="YYYYMMDDhhmmss",
                    got=repr(label),
                )

            try:
                datetime.strptime(label, "%Y%m%d%H%M%S")
            except ValueError as exc:
                raise marked.fail_tip(
                    "label contains an invalid date or time.",
                    code=const.CODE_EXC,
                    hint=const.HINT_HLT,
                    field="label",
                    expect="YYYYMMDDhhmmss",
                    got=label,
                ) from exc

            return label

        marked.ensure_i(video, "video")
        for path in video:
            marked.ensure_f(path, "video_file")

        label       = validate_label()
        output_root = output_base_dir(total)
        report_dir  = output_root / f"FX_{label}"

        payload = {
            "label": label, "title": title, "video": video
        }

        cmd = [
            "--keras",
            "--boost",
            "--scale", str(min(1.0, max(0.1, scale))),
            "--frame", json.dumps(payload),
            "--total", str(output_root),
            "--debug"
        ]

        resp = await self.__engine(*cmd)

        resp["data"].update({
            "label"      : label,
            "total"      : str(output_root),
            "report_dir" : str(report_dir),
        })

        return resp

    async def fx_frame_reporter(
        self,
        total: str
    ) -> dict[str, typing.Any]:
        final_dir = marked.ensure_d(total, "total")

        cmd = ["--merge", final_dir, "--debug"]

        resp = await self.__engine(*cmd)
        resp["data"]["report_dir"] = final_dir

        return resp


if __name__ == '__main__':
    pass
