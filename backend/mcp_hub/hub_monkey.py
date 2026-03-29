# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import time
import typing
import asyncio
import contextlib
from collections import deque
from loguru import logger
from backend.mcp_hub.hub_device import Device
from backend.utilities import const
from backend.utilities.process import Flux


class Monkey(object):
    """Monkey class."""

    def __init__(self):
        self.proc_logcat: typing.Optional[asyncio.subprocess.Process] = None
        self.proc_monkey: typing.Optional[asyncio.subprocess.Process] = None

        self.task_logcat: typing.Optional[asyncio.Task] = None
        self.task_monkey: typing.Optional[asyncio.Task] = None

        # 保存最近 N 行，便于返回/诊断，不会爆内存
        self.tail: deque[str] = deque(maxlen=500)

        # 原样大小写，匹配原始 log 文本
        self.patterns: dict[str, list[str]] = {
            "crash": [
                "FATAL EXCEPTION", "AndroidRuntime", "Fatal signal", "SIGSEGV", "SIGABRT",
                "has died", "Killed process", "backtrace:"
            ],
            "anr": [
                "ANR in", "Application Not Responding", "Input dispatching timed out",
                "Activity pause timeout", "Broadcast of intent", "Executing service"
            ],
            "oom": [
                "OutOfMemoryError", "Failed to allocate", "OOM", "Low memory"
            ],
            "monkey_abort": [
                "Monkey aborted", "** ANR", "** CRASH", "Monkey finished", "Events injected:"
            ]
        }

        # 关键词命中统计 + 证据行（每类保留最近 10 行）
        self.stats: dict[str, int] = {k: 0 for k in self.patterns}
        self.evidence: dict[str, deque[str]] = {k: deque(maxlen=10) for k in self.patterns}

    def matcher(self, text: str) -> typing.Optional[str]:
        for key, kws in self.patterns.items():
            for kw in kws:
                if kw in text:
                    self.stats[key] += 1
                    self.evidence[key].append(text)
                    return key

        return None

    async def reader(self, proc: asyncio.subprocess.Process, name: str) -> None:

        async def pump(stream: typing.AsyncIterable, stream_name: str) -> None:
            async for line in stream:
                text = line.decode(const.CHARSET, const.IGNORE)
                if not (text := text.rstrip("\r\n")):
                    continue
                if not self.matcher(text):
                    continue  # 不命中就不打印、不入tail

                logger.info(f"{name}.{stream_name}: {text}")
                self.tail.append(f"[{name}.{stream_name}] {text}")

        tasks = [
            asyncio.create_task(pump(proc.stdout, "stdout")),
            asyncio.create_task(pump(proc.stderr, "stderr"))
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def shutdown(self) -> typing.Optional[int]:
        if not self.proc_logcat or self.proc_logcat.returncode is not None:
            return None

        try:
            self.proc_logcat.terminate()
        except ProcessLookupError:
            return None

        try:
            return await asyncio.wait_for(self.proc_logcat.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            pass

        try:
            self.proc_logcat.kill()
        except ProcessLookupError:
            return None

        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self.proc_logcat.wait(), timeout=2.0)

    # workflow: ==== MCP Tool ====
    async def injection(
        self,
        device: Device,
        package: str,
        seed: int = 42,
        throttle_ms: int = 150,
        touch: int = 65,
        motion: int = 20,
        nav: int = 10,
        events: int = 10000
    ) -> dict[str, typing.Any]:

        self.tail.clear()
        # 每轮清空统计
        for k in self.stats:
            self.stats[k] = 0
            self.evidence[k].clear()

        start_ms = int(time.time() * 1000)

        await device.file_logcat_clean()

        self.proc_logcat = await device.file_logcat_link()
        self.task_logcat = asyncio.create_task(
            self.reader(self.proc_logcat, "logcat")
        )
        await asyncio.sleep(0.2)

        cmd_monkey = [
            "adb", "-s", device.serial, "shell", "monkey", "-p", package,
            "-s", str(seed), "--throttle", str(throttle_ms),
            "--pct-touch", str(touch), "--pct-motion", str(motion), "--pct-nav", str(nav),
            "--pct-appswitch", "0", "--pct-syskeys", "0",
            "--ignore-crashes", "--ignore-timeouts", "--ignore-security-exceptions",
            "-v", "-v", str(events)
        ]

        rc: typing.Optional[int]  = None
        err: typing.Optional[str] = None

        try:
            self.proc_monkey = await Flux.cmd_link(cmd_monkey)
            self.task_monkey = asyncio.create_task(
                self.reader(self.proc_monkey, "monkey")
            )
            rc = await self.proc_monkey.wait()
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
        finally:
            await self.shutdown()

        end_ms = int(time.time() * 1000)
        duration_ms = end_ms - start_ms

        ok = (err is None) and (rc == 0 or rc is not None)

        text = (
            f"Monkey 注入完成：rc={rc}，duration={duration_ms}ms"
            if err is None else
            f"Monkey 注入异常：{err}"
        )

        return {
            "text"        : text,
            "attachments" : [],
            "data": {
                "ok"          : ok,
                "serial"      : device.serial,
                "package"     : package,
                "seed"        : seed,
                "throttle_ms" : throttle_ms,
                "pct": {
                    "touch"  : touch,
                    "motion" : motion,
                    "nav"    : nav
                },
                "events"             : events,
                "monkey_cmd"         : cmd_monkey,
                "monkey_return_code" : rc,
                "start_ms"           : start_ms,
                "end_ms"             : end_ms,
                "duration_ms"        : duration_ms,
                "tail"               : list(self.tail),
                **({"error": err} if err else {})
            },
            "logs": []
        }


if __name__ == '__main__':
    pass
