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

if typing.TYPE_CHECKING:
    from backend.utilities.runtime import Idle


class Monkey(object):
    """Monkey 长任务句柄。"""

    recent_runs: typing.ClassVar[dict[str, dict[str, typing.Any]]] = {}

    def __init__(self, device: Device, idle: "Idle"):
        self.device = device
        self.idle = idle

        self.agent_id: str = "monkey"

        self.proc_logcat: typing.Optional[asyncio.subprocess.Process] = None
        self.proc_monkey: typing.Optional[asyncio.subprocess.Process] = None

        self.task_logcat: typing.Optional[asyncio.Task] = None
        self.task_monkey: typing.Optional[asyncio.Task] = None
        self.task_runner: typing.Optional[asyncio.Task] = None

        self.release_lock: asyncio.Lock = asyncio.Lock()
        self.done_event: asyncio.Event = asyncio.Event()

        self.session_id: typing.Optional[str] = None
        self.status_text: str = "idle"
        self.stop_requested: bool = False
        self.finalized: bool = False

        self.start_ms: typing.Optional[int] = None
        self.end_ms: typing.Optional[int] = None
        self.return_code: typing.Optional[int] = None
        self.error: typing.Optional[str] = None
        self.remote_stop: typing.Optional[dict[str, typing.Any]] = None

        self.config: dict[str, typing.Any] = {}
        self.cmd_monkey: list[str] = []

        self.tail: deque[str] = deque(maxlen=500)
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
        self.stats: dict[str, int] = {k: 0 for k in self.patterns}
        self.evidence: dict[str, deque[str]] = {k: deque(maxlen=10) for k in self.patterns}

    @property
    def session_key(self) -> str:
        return f"{self.agent_id}:{self.device.serial}"

    def session_args(self, extra: typing.Mapping[str, typing.Any] | None = None) -> dict[str, typing.Any]:
        return {
            "serial" : self.device.serial,
            "brand"  : self.device.device_props.get("brand"),
            **dict(extra or {})
        }

    @classmethod
    def recent_pack(
        cls,
        serial: str,
        *,
        reason: typing.Optional[str] = None
    ) -> dict[str, typing.Any]:
        if item := cls.recent_runs.get(serial):
            data = dict(item.get("data") or {})
            if reason:
                data["reason"] = reason
            return {
                "text"        : item.get("text") or "Monkey 最近一次结果已返回。",
                "attachments" : [],
                "data"        : data,
                "logs"        : []
            }

        return {
            "text"        : "未找到活跃或最近一次 monkey 会话。",
            "attachments" : [],
            "data": {
                "ok"     : True,
                "serial" : serial,
                "status" : "idle",
                "reason" : reason or "no_session"
            },
            "logs": []
        }

    @classmethod
    def clear_recent(cls, serial: str) -> bool:
        return cls.recent_runs.pop(serial, None) is not None

    async def acquire(self, session_name: str) -> str:
        self.session_id = await self.idle.session_begin(
            key=self.session_key,
            name=session_name,
            args=self.session_args(self.snapshot_data()),
            replace=False,
            handle=self
        )
        return self.session_id

    async def release(self) -> None:
        async with self.release_lock:
            if self.finalized and self.session_id is None:
                return None
            self.session_id = None
        await self.idle.session_final(self.session_key)

    async def patch_session(self) -> None:
        await self.idle.session_patch_args(self.session_key, self.snapshot_data())

    def reset(self) -> None:
        self.tail.clear()
        self.done_event.clear()
        self.proc_logcat = None
        self.proc_monkey = None
        self.task_logcat = None
        self.task_monkey = None
        self.task_runner = None
        self.status_text = "starting"
        self.stop_requested = False
        self.finalized = False
        self.start_ms = int(time.time() * 1000)
        self.end_ms = None
        self.return_code = None
        self.error = None
        self.remote_stop = None
        self.cmd_monkey = []
        for key in self.stats:
            self.stats[key] = 0
            self.evidence[key].clear()

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
                    continue
                logger.info(f"{name}.{stream_name}: {text}")
                self.tail.append(f"[{name}.{stream_name}] {text}")

        tasks = [
            asyncio.create_task(pump(proc.stdout, "stdout")),
            asyncio.create_task(pump(proc.stderr, "stderr"))
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

    @staticmethod
    async def shutdown_proc(
        proc: typing.Optional[asyncio.subprocess.Process],
        *,
        term_timeout: float = 1.0,
        kill_timeout: float = 2.0
    ) -> typing.Optional[int]:
        if not proc or proc.returncode is not None:
            return None

        try:
            proc.terminate()
        except ProcessLookupError:
            return None

        try:
            return await asyncio.wait_for(proc.wait(), timeout=term_timeout)
        except asyncio.TimeoutError:
            pass

        try:
            proc.kill()
        except ProcessLookupError:
            return None

        with contextlib.suppress(asyncio.TimeoutError):
            return await asyncio.wait_for(proc.wait(), timeout=kill_timeout)
        return None

    async def shutdown(self) -> None:
        await self.shutdown_proc(self.proc_monkey)
        await self.shutdown_proc(self.proc_logcat)

        for task in (self.task_monkey, self.task_logcat):
            if task and not task.done():
                task.cancel()
            if task:
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    async def stop_remote_monkey(self) -> dict[str, typing.Any]:
        result_pack = await self.device.monkey_stop()
        result_data = dict(result_pack.get("data") or {})
        result = {
            "ok"     : bool(result_data.get("ok")),
            "reason" : result_data.get("reason") or "unknown"
        }
        self.tail.append(
            f"[remote.stop] ok={bool(result.get('ok'))} reason={result.get('reason') or 'unknown'}"
        )
        return result

    def snapshot_data(self) -> dict[str, typing.Any]:
        now_ms = int(time.time() * 1000)
        end_ms = self.end_ms or now_ms
        duration_ms = max(0, end_ms - self.start_ms) if self.start_ms else 0
        running = self.status_text in {"starting", "running", "stopping"}
        ok = (
            False if self.status_text == "failed" else
            True if self.status_text in {"finished", "stopped", "starting", "running", "stopping"} else
            False
        )

        data = {
            "ok"                 : ok,
            "active"             : running,
            "done"               : bool(self.finalized),
            "status"             : self.status_text,
            "serial"             : self.device.serial,
            "package"            : self.config.get("package"),
            "seed"               : self.config.get("seed"),
            "throttle_ms"        : self.config.get("throttle_ms"),
            "pct": {
                "touch"  : self.config.get("touch"),
                "motion" : self.config.get("motion"),
                "nav"    : self.config.get("nav")
            },
            "events"             : self.config.get("events"),
            "job_id"             : self.session_id,
            "session_key"        : self.session_key,
            "monkey_cmd"         : self.cmd_monkey,
            "monkey_return_code" : self.return_code,
            "stop_requested"     : self.stop_requested,
            "start_ms"           : self.start_ms,
            "end_ms"             : self.end_ms,
            "duration_ms"        : duration_ms,
            "tail"               : list(self.tail),
            "stats"              : dict(self.stats),
            "evidence"           : {key: list(values) for key, values in self.evidence.items()}
        }
        if self.error:
            data["error"] = self.error
        if self.remote_stop is not None:
            data["remote_stop"] = self.remote_stop
        return data

    def build_pack(
        self,
        text: str,
        *,
        reason: typing.Optional[str] = None
    ) -> dict[str, typing.Any]:
        data = self.snapshot_data()
        if reason:
            data["reason"] = reason
        return {
            "text"        : text,
            "attachments" : [],
            "data"        : data,
            "logs"        : []
        }

    def remember_recent(self, text: str) -> None:
        self.recent_runs[self.device.serial] = self.build_pack(text)

    async def finalize(self, *, rc: typing.Optional[int] = None, err: typing.Optional[str] = None) -> None:
        if self.finalized:
            return None
        self.finalized = True
        if rc is not None:
            self.return_code = rc
        if err is not None:
            self.error = err
        self.end_ms = int(time.time() * 1000)

        if self.error:
            self.status_text = "failed"
        elif self.stop_requested:
            self.status_text = "stopped"
        elif self.return_code == 0:
            self.status_text = "finished"
        else:
            self.status_text = "failed"

        if self.stop_requested:
            with contextlib.suppress(Exception):
                self.remote_stop = await self.stop_remote_monkey()

        await self.patch_session()
        self.remember_recent(
            (
                f"Monkey 已结束：status={self.status_text} rc={self.return_code} duration={self.snapshot_data().get('duration_ms')}ms"
                if not self.error else
                f"Monkey 运行失败：{self.error}"
            )
        )
        await self.shutdown()
        self.done_event.set()
        await self.release()

    async def runner(self) -> None:
        rc: typing.Optional[int] = None
        err: typing.Optional[str] = None
        try:
            if not self.proc_monkey:
                raise RuntimeError("monkey process not started")
            self.status_text = "running"
            await self.patch_session()
            rc = await self.proc_monkey.wait()
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
        finally:
            await self.finalize(rc=rc, err=err)

    async def start(
        self,
        package: str,
        seed: int = 42,
        throttle_ms: int = 150,
        touch: int = 65,
        motion: int = 20,
        nav: int = 10,
        events: int = 10000
    ) -> dict[str, typing.Any]:
        self.reset()
        self.config = {
            "package"     : package,
            "seed"        : seed,
            "throttle_ms" : throttle_ms,
            "touch"       : touch,
            "motion"      : motion,
            "nav"         : nav,
            "events"      : events
        }

        self.cmd_monkey = [
            "adb", "-s", self.device.serial, "shell", "monkey", "-p", package,
            "-s", str(seed), "--throttle", str(throttle_ms),
            "--pct-touch", str(touch), "--pct-motion", str(motion), "--pct-nav", str(nav),
            "--pct-appswitch", "0", "--pct-syskeys", "0",
            "--ignore-crashes", "--ignore-timeouts", "--ignore-security-exceptions",
            "-v", "-v", str(events)
        ]

        await self.acquire("monkey.start")
        try:
            await self.device.file_logcat_clean()
            self.proc_logcat = await self.device.file_logcat_link()
            self.task_logcat = asyncio.create_task(self.reader(self.proc_logcat, "logcat"))
            await asyncio.sleep(0.2)

            self.proc_monkey = await Flux.cmd_link(self.cmd_monkey)
            self.task_monkey = asyncio.create_task(self.reader(self.proc_monkey, "monkey"))
            self.task_runner = asyncio.create_task(self.runner())

            self.status_text = "running"
            await self.patch_session()
            return self.build_pack("Monkey 已启动，可通过 monkey_status 查询进度，或用 monkey_stop 主动停止。")
        except Exception as e:
            await self.finalize(err=f"{type(e).__name__}: {e}")
            return self.build_pack("Monkey 启动失败。")

    async def status(self, *, reason: typing.Optional[str] = None) -> dict[str, typing.Any]:
        text = (
            "Monkey 正在运行中。"
            if self.status_text in {"starting", "running", "stopping"} else
            f"Monkey 当前状态：{self.status_text}"
        )
        return self.build_pack(text, reason=reason)

    async def stop(self) -> dict[str, typing.Any]:
        if self.finalized or not self.proc_monkey or self.proc_monkey.returncode is not None:
            return await self.status(reason="already_finished")

        self.stop_requested = True
        self.status_text = "stopping"
        await self.patch_session()
        await self.shutdown_proc(self.proc_monkey, term_timeout=2.0, kill_timeout=3.0)

        if self.task_runner:
            with contextlib.suppress(asyncio.CancelledError):
                await self.task_runner

        return self.build_pack("Monkey 已停止。", reason="stop_requested")

    async def wait(self) -> dict[str, typing.Any]:
        await self.done_event.wait()
        return self.recent_pack(self.device.serial)

if __name__ == '__main__':
    pass
