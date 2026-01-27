#  _   _       _       __  __             _
# | | | |_   _| |__   |  \/  | ___  _ __ | | _____ _   _
# | |_| | | | | '_ \  | |\/| |/ _ \| '_ \| |/ / _ \ | | |
# |  _  | |_| | |_) | | |  | | (_) | | | |   <  __/ |_| |
# |_| |_|\__,_|_.__/  |_|  |_|\___/|_| |_|_|\_\___|\__, |
#                                                  |___/
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import time
import typing
import asyncio
from collections import deque
from loguru import logger
from engine.terminal import Terminal
from backend.mcp_hub.hub_device import Device
from backend.utilities import const


class Monkey(object):
    """Monkey class."""

    __instance: typing.Optional["Monkey"] = None
    __initialized: bool = False

    def __new__(cls, *args, **kwargs):
        if not cls.__instance:
            cls.__instance = super(Monkey, cls).__new__(cls)
        return cls.__instance

    def __init__(self):
        if not self.__initialized:

            self.__prefix: str = "monkey"

            self.agent_id: str = self.__prefix

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
            self.stats: dict[str, int] = {
                k: 0 for k in self.patterns
            }
            self.evidence: dict[str, deque[str]] = {
                k: deque(maxlen=10) for k in self.patterns
            }

        self.__initialized = True

    def matcher(self, text: str) -> typing.Optional[str]:
        for key, kws in self.patterns.items():
            for kw in kws:
                if kw in text:
                    self.stats[key] += 1
                    self.evidence[key].append(text)
                    return key

    async def read_streams(self, proc: asyncio.subprocess.Process, name: str) -> None:

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

    @staticmethod
    async def shutdown(process: typing.Optional[asyncio.subprocess.Process], name: str) -> None:
        if not process:
            return None
        try:
            if process.returncode is None:
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        except Exception as e:
            logger.warning(f"terminate {name} failed: {e}")

    @staticmethod
    async def logcat_clean(device: Device) -> typing.Any:
        return await Terminal.cmd_line(["adb", "-s", device.serial, "logcat", "-c"])

    async def logcat_start(self, device: Device) -> None:
        cmd = ["adb", "-s", device.serial, "logcat", "-v", "threadtime"]
        self.proc_logcat = await Terminal.cmd_link(cmd)
        self.task_logcat = asyncio.create_task(self.read_streams(self.proc_logcat, "logcat"))
        await asyncio.sleep(0.2)

    async def monkey_plugin(
        self,
        device: Device,
        package: str,
        seed: int = 42,
        throttle_ms: int = 150,
        touch: int = 65,
        motion: int = 20,
        nav: int = 10,
        events: int = 20000,
    ) -> dict[str, typing.Any]:

        self.tail.clear()
        # 每轮清空统计
        for k in self.stats:
            self.stats[k] = 0
            self.evidence[k].clear()

        start_ms = int(time.time() * 1000)

        await self.logcat_clean(device)
        await self.logcat_start(device)

        cmd_monkey = [
            "adb", "-s", device.serial, "shell", "monkey", "-p", package,
            "-s", str(seed), "--throttle", str(throttle_ms),
            "--pct-touch", str(touch), "--pct-motion", str(motion), "--pct-nav", str(nav),
            "--pct-appswitch", "0", "--pct-syskeys", "0",
            "--ignore-crashes", "--ignore-timeouts", "--ignore-security-exceptions",
            "-v", "-v", str(events),
        ]

        try:
            self.proc_monkey = await Terminal.cmd_link(cmd_monkey)
            self.task_monkey = asyncio.create_task(self.read_streams(self.proc_monkey, "monkey"))
            rc = await self.proc_monkey.wait()
        finally:
            await asyncio.sleep(0.3)
            await self.shutdown(self.proc_logcat, "logcat")

        end_ms = int(time.time() * 1000)

        return {
            "serial"             : device.serial,
            "package"            : package,
            "monkey_cmd"         : cmd_monkey,
            "monkey_return_code" : rc,
            "start_ms"           : start_ms,
            "end_ms"             : end_ms,
            "duration_ms"        : end_ms - start_ms,
            "tail"               : list(self.tail),  # 最近 500 行，够定位问题
        }


if __name__ == '__main__':
    pass
