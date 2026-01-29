#  _   _       _       ____                        _
# | | | |_   _| |__   |  _ \ ___  ___ ___  _ __ __| |
# | |_| | | | | '_ \  | |_) / _ \/ __/ _ \| '__/ _` |
# |  _  | |_| | |_) | |  _ <  __/ (_| (_) | | | (_| |
# |_| |_|\__,_|_.__/  |_| \_\___|\___\___/|_|  \__,_|
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import os
import re
import time
import random
import typing
import shutil
import asyncio
from loguru import logger
from backend.mcp_hub.hub_device import Device
from engine.terminal import Terminal
from backend.utilities import const


class Record(object):
    """Record class."""

    def __init__(
        self,
        device: Device,
        version: str,
        station: str,
        sessions: dict[str, "Record"],
        sessions_lock: asyncio.Lock,
        *,
        on_begin: typing.Callable[[], typing.Awaitable[typing.Any]] | None = None,
        on_final: typing.Callable[[], typing.Awaitable[typing.Any]] | None = None
    ) -> None:

        self.agent_id: str = "scrcpy"

        self.device = device

        self.version = version
        self.station = station

        self.sessions      = sessions
        self.sessions_lock = sessions_lock
        self.on_begin      = on_begin
        self.on_final      = on_final

        self.start_event: asyncio.Event = asyncio.Event()
        self.close_event: asyncio.Event = asyncio.Event()
        self.error_event: asyncio.Event = asyncio.Event()

        self.err_message: typing.Optional[str] = None

        self.transports: typing.Optional[asyncio.subprocess.Process] = None

        self.released: bool = False
        self.release_lock: asyncio.Lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self.sessions_lock:
            if self.device.serial in self.sessions:
                raise RuntimeError(
                    f"device busy: {self.device.serial} already has an active scrcpy session"
                )
            self.sessions[self.device.serial] = self

        if self.on_begin: await self.on_begin()

    async def release(self) -> None:
        async with self.release_lock:
            if self.released:
                return None
            self.released = True

        try:
            if self.on_final: await self.on_final()
        finally:
            async with self.sessions_lock:
                if self.sessions.get(self.device.serial) is self:
                    self.sessions.pop(self.device.serial, None)

    async def input_stream(self) -> None:
        try:
            async for line in self.transports.stdout:
                logger.debug(stream := line.decode(const.CHARSET, const.IGNORE).strip())
                if "Recording started" in stream or "Texture" in stream:
                    self.start_event.set()
                elif "Recording complete" in stream:
                    self.close_event.set()
                    asyncio.create_task(self.release())
                    return None
        finally:
            if not self.close_event.is_set() and not self.error_event.is_set():
                self.close_event.set()
                asyncio.create_task(self.release())
                return None

    async def error_stream(self) -> None:
        try:
            async for line in self.transports.stderr:
                logger.debug(stream := line.decode(const.CHARSET, const.IGNORE).strip())
                if (
                    "Could not find" in stream
                    or "connection failed" in stream
                    or "Recorder error" in stream
                    or "ERROR:" in stream
                    or "FATAL:" in stream
                ):
                    self.err_message = stream
                    self.error_event.set()
                    asyncio.create_task(self.release())
                    return None

        finally:
            if not self.close_event.is_set() and not self.error_event.is_set():
                self.close_event.set()
                asyncio.create_task(self.release())
                return None

    async def merge_stream(self) -> None:
        try:
            async for line in self.transports.stdout:
                if isinstance(line, (bytes, bytearray)):
                    stream = line.decode(const.CHARSET, const.IGNORE).strip()
                else:
                    stream = str(line).strip()

                if not stream: continue

                logger.debug(stream)

                if "Recording started" in stream or "Texture" in stream:
                    self.start_event.set()
                    continue

                if "Recording complete" in stream:
                    self.close_event.set()
                    asyncio.create_task(self.release())
                    return None

                if (
                    "Could not find" in stream
                    or "connection failed" in stream
                    or "Recorder error" in stream
                    or "ERROR:" in stream
                    or "FATAL:" in stream
                ):
                    self.err_message = stream
                    self.error_event.set()
                    asyncio.create_task(self.release())
                    return None

        finally:
            if not self.close_event.is_set() and not self.error_event.is_set():
                self.close_event.set()
                asyncio.create_task(self.release())
                return None

    async def launcher(self, cmd: list[str]) -> None:
        if self.station == "win32":
            self.transports = await Terminal.cmd_link(cmd)
            asyncio.create_task(self.input_stream())
            asyncio.create_task(self.error_stream())
        else:
            self.transports = await Terminal.cmd_link_pty(cmd)
            asyncio.create_task(self.merge_stream())

    async def ask_start_mirror(self) -> None:
        await self.acquire()
        try:
            cmd = ["scrcpy", "-s", self.device.serial, "--no-audio", "-b", "8M"]
            await self.launcher(cmd)
            return await self.check_timer()
        except Exception as e:
            await self.release()
            raise e

    async def ask_start_record(self, directory: str, fps: int = 60, silence: bool = False) -> str:
        await self.acquire()
        try:
            cmd = ["scrcpy", "-s", self.device.serial, "--no-audio", "-b", "8M", f"--max-fps={fps}"]

            if silence:
                try:
                    vs = float(re.search(r"(?<=scrcpy\s)\d.*(?=\.\d\s)", self.version).group())
                except (AttributeError, TypeError):
                    vs = 2.5
                cmd += ["--no-display"] if vs <= 2.4 else ["--no-window"]

            video_flag = f"{time.strftime('%Y%m%d%H%M%S')}_{random.randint(100, 999)}.mkv"

            cmd += ["-r", video_temp := f"{os.path.join(directory, 'screen')}_{video_flag}"]
            await self.launcher(cmd)
            return await self.check_timer(video_temp)
        except Exception as e:
            await self.release()
            raise e

    async def ask_close_record(self) -> typing.Optional[str]:

        async def win_stop_child(pid: str | int) -> None:
            off = await Terminal.cmd_line([pwsh, "-Command", "Stop-Process", "-Id", pid, "-Force"])
            logger.debug(f"{desc} PID={pid} OFF={off}")

        async def mac_stop_child(pid: str | int) -> None:
            off = await Terminal.cmd_line_shell(f"pgrep -P {pid} | xargs kill -15")
            logger.debug(f"{desc} PID={pid} OFF={off}")

        if self.close_event.is_set():
            await self.release()
            return None

        desc = f"{self.device.brand} {self.device.serial} PPID={(ppid := self.transports.pid)}"

        try:
            if self.station == "win32":
                pwsh = shutil.which("pwsh") or shutil.which("powershell")
                line = [
                    pwsh, "-Command", "Get-CimInstance", "Win32_Process", "|", "Where-Object",
                    f"{{ $_.ParentProcessId -eq {ppid} }}", "|", "Select-Object", "-ExpandProperty", "ProcessId"
                ]

                if not (child_pids := await Terminal.cmd_line(line)):
                    return None

                pids_list = [line.strip() for line in child_pids.splitlines()]
                await asyncio.gather(*(win_stop_child(pid) for pid in pids_list))

            elif self.station == "darwin":
                await mac_stop_child(ppid)

            await self.clean_event()
            return desc

        finally:
            await self.release()

    async def check_timer(self, video_temp: typing.Optional[str] = None) -> typing.Optional[str]:
        for _ in range(10):
            if self.start_event.is_set():
                return video_temp
            elif self.error_event.is_set():
                raise RuntimeError(self.err_message or "启动失败")

            await asyncio.sleep(0.5)

        raise RuntimeError("启动失败")

    async def clean_event(self) -> None:
        self.start_event.clear()
        self.close_event.clear()
        self.error_event.clear()


if __name__ == '__main__':
    pass
