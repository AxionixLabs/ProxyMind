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
from engine.terminal import Terminal
from backend.utilities import const


class Record(object):
    """Record class."""

    def __init__(self, version: str, station: str):
        self.version = version
        self.station = station

        self.start_event: asyncio.Event = asyncio.Event()
        self.close_event: asyncio.Event = asyncio.Event()

        self.transports: typing.Optional[asyncio.subprocess.Process] = None

    async def input_stream(self) -> None:
        async for line in self.transports.stdout:
            logger.debug(stream := line.decode(const.CHARSET, const.IGNORE).strip())
            if "Recording started" in stream:
                self.start_event.set()
            elif "Recording complete" in stream:
                self.close_event.set()

    async def error_stream(self) -> None:
        async for line in self.transports.stderr:
            logger.debug(stream := line.decode(const.CHARSET, const.IGNORE).strip())
            if "Could not find" in stream or "connection failed" in stream or "Recorder error" in stream:
                raise RuntimeError(stream)

    async def ask_start_record(self, serial: str, local: str, silence: bool = False) -> str:
        video_flag = f"{time.strftime('%Y%m%d%H%M%S')}_{random.randint(100, 999)}.mkv"

        cmd = ["scrcpy", "-s", serial, "--no-audio", "-b=8M"]

        if silence:
            try:
                vs = float(re.search(r"(?<=scrcpy\s)\d.*(?=\.\d\s)", self.version).group())
            except (AttributeError, TypeError):
                vs = 2.5
            cmd += ["--no-display"] if vs <= 2.4 else ["--no-window"]

        cmd += ["--record", video_temp := f"{os.path.join(local, 'screen')}_{video_flag}"]

        self.transports = await Terminal.cmd_link(cmd)

        asyncio.create_task(self.input_stream())
        asyncio.create_task(self.error_stream())

        await asyncio.sleep(1)

        return video_temp

    async def ask_close_record(self, serial: str) -> typing.Optional[Exception]:

        async def win_stop_child(pid: str | int) -> None:
            off = await Terminal.cmd_line([pwsh, "-Command", "Stop-Process", "-Id", pid, "-Force"])
            logger.debug(f"{desc} PID={pid} OFF={off}")

        async def mac_stop_child(pid: str | int) -> None:
            off = await Terminal.cmd_line_shell(f"pgrep -P {pid} | xargs kill -15")
            logger.debug(f"{desc} PID={pid} OFF={off}")

        if self.close_event.is_set():
            return None

        desc = f"{serial} PPID={(ppid := self.transports.pid)}"

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

    async def clean_events(self) -> None:
        self.start_event.clear()
        self.close_event.clear()


if __name__ == '__main__':
    pass
