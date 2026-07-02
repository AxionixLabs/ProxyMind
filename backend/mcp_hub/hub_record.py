# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import os
import re
import sys
import time
import uuid
import random
import typing
import shutil
import asyncio
from collections import deque
from loguru import logger
from backend.mcp_hub.hub_device import Device
from backend.utilities import const
from backend.utilities.process import Flux
from backend.utilities.tool_result import ToolOutput

if typing.TYPE_CHECKING:
    from backend.utilities.runtime import Idle


class Record(object):
    """Record class."""

    def __init__(self, device: Device, idle: "Idle", version: str) -> None:
        self.__prefix: str = "scrcpy"
        self.agent_id: str = self.__prefix

        self.device  = device
        self.version = version

        self.is_windows = sys.platform == "win32"

        self.idle = idle

        self.start_event: asyncio.Event = asyncio.Event()
        self.close_event: asyncio.Event = asyncio.Event()
        self.error_event: asyncio.Event = asyncio.Event()

        self.err_message: typing.Optional[str] = None

        self.transports: typing.Optional[asyncio.subprocess.Process] = None

        self.released: bool = False
        self.release_lock: asyncio.Lock = asyncio.Lock()

        self.tail: deque[str] = deque(maxlen=5)

    @property
    def prefix(self) -> str:
        return self.__prefix

    @property
    def session_key(self) -> str:
        return f"{self.prefix}:{self.device.serial}"

    @staticmethod
    def as_token(s: str, *, max_len: int = 32) -> str:
        s = (s or "unknown").strip()
        s = re.sub(r"[^a-zA-Z0-9_.-]+", "_", s)
        return s[:max_len] if max_len > 0 else s

    def as_video(self, video_suffix: str = "mkv") -> str:
        sn  = self.as_token(self.device.serial, max_len=24)
        uid = uuid.uuid4().hex[:6]
        ts  = time.strftime("%Y%m%d%H%M%S")
        day = time.strftime("%Y%m%d")
        rnd = random.randint(100, 999)

        token = f"{ts}_{sn}_{uid}_{rnd}.{video_suffix}"
        return os.path.join(day, self.agent_id, self.device.serial, token)

    def session_args(self, extra: typing.Mapping[str, typing.Any] | None = None) -> dict[str, typing.Any]:
        return {
            "serial" : self.device.serial,
            "brand"  : self.device.device_props.get("brand"),
            **dict(extra or {})
        }

    @staticmethod
    def no_active_session(serial: str) -> ToolOutput:
        """返回无活跃 scrcpy 会话的统一结果。"""
        return ToolOutput(
            ok=True,
            text="未找到活跃的 scrcpy 会话，无需关闭。",
            data={
                "serial" : serial,
                "reason" : "no_active_session"
            }
        )

    async def acquire(
        self,
        session_name: str,
        *,
        session_args: typing.Mapping[str, typing.Any] | None = None
    ) -> None:
        await self.idle.session_begin(
            key=self.session_key,
            name=session_name,
            args=self.session_args(session_args),
            replace=False,
            handle=self
        )

    async def release(self) -> None:
        async with self.release_lock:
            if self.released:
                return None
            self.released = True

        await self.idle.session_final(self.session_key)

    async def input_stream(self) -> None:
        try:
            async for line in self.transports.stdout:
                logger.debug(stream := line.decode(const.CHARSET, const.IGNORE).strip())
                self.tail.append(stream)
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
                self.tail.append(stream)
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

                self.tail.append(stream)
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
        if self.is_windows:
            self.transports = await Flux.cmd_link(cmd)
            asyncio.create_task(self.input_stream())
            asyncio.create_task(self.error_stream())
        else:
            self.transports = await Flux.cmd_link_pty(cmd)
            asyncio.create_task(self.merge_stream())

    # workflow: ==== MCP Tool ====
    async def scrcpy_mirror(self) -> ToolOutput:
        await self.acquire("scrcpy.scrcpy_mirror")
        try:
            cmd = [
                self.prefix, "-s", self.device.serial, "--no-audio", "-b", "8M"
            ]
            await self.launcher(cmd)
            await self.check_timer()
            return ToolOutput(
                ok=True,
                text="scrcpy 镜像已启动。",
                data={
                    "serial" : self.device.serial,
                    "status" : list(self.tail),
                    "cmd"    : cmd
                }
            )
        except Exception as e:
            await self.release()
            return ToolOutput(
                ok=False,
                text=f"scrcpy 镜像启动失败：{type(e).__name__}: {e}",
                data={
                    "serial" : self.device.serial,
                    "status" : list(self.tail),
                    "error"  : f"{type(e).__name__}: {e}"
                }
            )

    # workflow: ==== MCP Tool ====
    async def scrcpy_record(self, directory: str, fps: int = 60, silence: bool = False) -> ToolOutput:
        await self.acquire(
            "scrcpy.scrcpy_record",
            session_args={
                "directory" : directory,
                "fps"       : fps,
                "silence"   : silence
            }
        )
        try:
            cmd = [
                self.prefix, "-s", self.device.serial, "--no-audio", "-b", "8M", f"--max-fps={fps}"
            ]

            if silence:
                try:
                    vs = float(re.search(r"(?<=scrcpy\s)\d.*(?=\.\d\s)", self.version).group())
                except (AttributeError, TypeError):
                    vs = 2.5
                cmd += ["--no-display"] if vs <= 2.4 else ["--no-window"]

            video_temp = os.path.join(directory, self.as_video())
            os.makedirs(os.path.dirname(video_temp), exist_ok=True)
            cmd += ["-r", video_temp]

            await self.launcher(cmd)
            await self.check_timer(video_temp)

            return ToolOutput(
                ok=True,
                text="scrcpy 录制已启动。",
                data={
                    "serial" : self.device.serial,
                    "status" : list(self.tail),
                    "path"   : video_temp,
                    "cmd"    : cmd
                }
            )

        except Exception as e:
            await self.release()
            return ToolOutput(
                ok=False,
                text=f"scrcpy 录制启动失败：{type(e).__name__}: {e}",
                data={
                    "serial" : self.device.serial,
                    "status" : list(self.tail),
                    "error"  : f"{type(e).__name__}: {e}"
                }
            )

    # workflow: ==== MCP Tool ====
    async def scrcpy_close(self) -> ToolOutput:

        async def win_stop_child(pid: typing.Union[str, int]) -> str:
            off = await Flux.cmd_line([pwsh, "-Command", "Stop-Process", "-Id", pid, "-Force"])
            logger.debug(msg := f"{desc} PID={pid} OFF={off}")
            return msg

        async def mac_stop_child(pid: typing.Union[str, int]) -> str:
            off = await Flux.cmd_line_shell(f"pgrep -P {pid} | xargs kill -15")
            logger.debug(msg := f"{desc} PID={pid} OFF={off}")
            return msg

        if self.close_event.is_set():
            await self.release()
            return ToolOutput(
                ok=True,
                text="scrcpy 已关闭（或已结束）。",
                data={
                    "serial" : self.device.serial,
                    "status" : list(self.tail),
                    "closed" : True
                }
            )

        desc = f"{self.device.serial} PPID={(ppid := self.transports.pid)}"

        try:
            if self.is_windows:
                pwsh = shutil.which("pwsh") or shutil.which("powershell")
                line = [
                    pwsh, "-Command", "Get-CimInstance", "Win32_Process", "|", "Where-Object",
                    f"{{ $_.ParentProcessId -eq {ppid} }}", "|", "Select-Object", "-ExpandProperty", "ProcessId"
                ]

                if not (child_pids := await Flux.cmd_line(line)):
                    return ToolOutput(
                        ok=True,
                        text="未发现可关闭的子进程（可能已退出）。",
                        data={
                            "serial" : self.device.serial,
                            "status" : list(self.tail),
                            "reason" : "no_child_process",
                            "ppid"   : ppid
                        }
                    )

                pids_list = [line.strip() for line in child_pids.splitlines()]
                off_state = await asyncio.gather(*(win_stop_child(pid) for pid in pids_list))

            else:
                off_state = await mac_stop_child(ppid)

            return ToolOutput(
                ok=True,
                text="已尝试关闭 scrcpy。",
                data={
                    "serial"    : self.device.serial,
                    "status"    : list(self.tail),
                    "off_state" : off_state
                }
            )

        except Exception as e:
            return ToolOutput(
                ok=False,
                text=f"关闭 scrcpy 失败：{type(e).__name__}: {e}",
                data={
                    "serial" : self.device.serial,
                    "status" : list(self.tail),
                    "error"  : f"{type(e).__name__}: {e}"
                }
            )

        finally:
            await self.clean_event()
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
