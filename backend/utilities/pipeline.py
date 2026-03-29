# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import os
import time
import uuid
import random
import signal
import typing
import asyncio
import contextlib
from loguru import logger
from rich.text import Text
from rich.console import Console
from rich.logging import (
    LogRecord, RichHandler
)
from backend.utilities.instance import Ins
from backend.utilities import const
from backend.utilities.storage.logs import ensure_log_path


class _HelixBaseError(BaseException):
    """_HelixBaseError class."""
    pass


class HelixError(_HelixBaseError):
    """HelixError class."""

    def __init__(self, msg: typing.Any):
        self.msg = msg

    def __str__(self):
        return f"<{const.APP_DESC}Error> {self.msg}"

    __repr__ = __str__


class Active(object):
    """Active class."""

    console: Console = Console()

    class _RichSink(RichHandler):
        debug_color = [
            "#00E5FF",  # 电青
            "#39FF14",  # 霓虹绿
            "#FF2D95",  # 霓虹粉
        ]
        info_color = [
            "#FFD300",  # 电黄
            "#7CFF6B",  # 亮绿
            "#64748B",  # 蓝灰
        ]
        level_style = {
            "DEBUG"    : f"bold {random.choice(debug_color)}",
            "INFO"     : f"bold {random.choice(info_color)}",
            "WARNING"  : "bold #FFD700",
            "ERROR"    : "bold #FF4500",
            "CRITICAL" : "bold #FF1493",
        }

        def __init__(self, console: "Console"):
            super().__init__(
                console=console,
                rich_tracebacks=True,
                show_path=False,
                show_time=False,
                markup=False
            )

        def emit(self, record: "LogRecord") -> None:
            self.console.print(
                const.PRINT_HEAD, Text(self.format(record), style=self.level_style.get(
                    record.levelname, "bold #ADD8E6"
                ))
            )

    @staticmethod
    def active(log_level: str) -> None:
        logger.remove()
        logger.add(
            Active._RichSink(Active.console), level=log_level, format=const.PRINT_FORMAT
        )
        logger.add(
            ensure_log_path(),
            level=log_level,
            format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | {message}",
            encoding=const.CHARSET,
            rotation="10 MB",
            retention="14 days",
            compression="zip"
        )


class Idle(object):
    """Idle class."""

    def __init__(self, *, ttl_sec: float = 300.0):
        self.agent_id = "idle"

        self.ttl_sec = float(ttl_sec)

        self.last_touch: float = time.monotonic()
        self.active_jobs: int  = 0
        self.closing: bool     = False

        self.lock: asyncio.Lock = asyncio.Lock()
        self.task: typing.Optional[asyncio.Task] = None

        self.jobs: dict[str, dict] = {}
        self.sessions_map: dict[str, str] = {}

    class JobToken(object):
        """JobToken class."""

        __slots__ = ("idle", "id", "name", "args", "job_id_len")

        def __init__(self, idle: "Idle", name: str, args: typing.Optional[dict] = None, job_id_len: int = 8):
            self.idle = idle
            self.id: typing.Optional[str] = None
            self.name: str = name
            self.args: dict = args or {}
            self.job_id_len: int = job_id_len

        async def begin(self, extra: typing.Optional[dict] = None) -> str:
            payload = dict(self.args)
            if extra:
                payload.update(extra)
            self.id = await self.idle.job_begin(self.name, args=payload, job_id_len=self.job_id_len)
            return self.id

        async def final(self) -> None:
            if self.id:
                await self.idle.job_final(self.id)

    @staticmethod
    def short_uuid(n: int = 8) -> str:
        return uuid.uuid4().hex[: max(4, int(n))]

    def hooks(
        self,
        name: str,
        *,
        args: typing.Optional[dict] = None,
        args_fn: typing.Callable[[], typing.Mapping[str, typing.Any]] | None = None,
        job_id_len: int = 8
    ) -> tuple[
        JobToken,
        typing.Callable[[], typing.Awaitable[None]],
        typing.Callable[[], typing.Awaitable[None]]
    ]:

        token = self.JobToken(self, name=name, args=args, job_id_len=job_id_len)

        async def on_begin() -> None:
            extra = dict(args_fn() or {}) if args_fn else None
            await token.begin(extra)

        async def on_final() -> None:
            await token.final()

        return token, on_begin, on_final

    async def session_begin(
        self,
        key: str,
        name: str,
        *,
        args: typing.Mapping[str, typing.Any] | None = None,
        replace: bool = True,
        job_id_len: int = 8
    ) -> str:

        async with self.lock:
            old = self.sessions_map.get(key)

        if old and replace:
            await self.job_final(old)

        job_id = await self.job_begin(name, args=args, job_id_len=job_id_len)

        async with self.lock:
            self.sessions_map[key] = job_id
        return job_id

    async def session_final(self, key: str) -> None:
        async with self.lock:
            job_id = self.sessions_map.pop(key, None)
        if job_id:
            await self.job_final(job_id)

    async def session_patch_args(self, key: str, patch: typing.Mapping[str, typing.Any]) -> None:
        """
        更新某个 session 对应 job 的 args（增量合并）。
        用于把 token / 状态 / 统计信息写回 idle session。
        """
        async with self.lock:
            if not (job_id:=self.sessions_map.get(key)):
                return None

            if not (meta := self.jobs.get(job_id)):
                return None

            if not isinstance(args := meta.get("args"), dict):
                args = {}
                meta["args"] = args

            args.update(dict(patch))
            self.last_touch = time.monotonic()

    async def start_idle(self) -> None:
        if self.task and not self.task.done():
            return None
        self.closing = False
        self.task = asyncio.create_task(self.looper())

    async def close_idle(self) -> None:
        self.closing = True

        if not self.task:
            return None

        if not self.task.done():
            self.task.cancel()

        with contextlib.suppress(asyncio.CancelledError):
            await self.task
        self.task = None

    async def touch(self) -> None:
        async with self.lock:
            self.last_touch = time.monotonic()

    async def job_begin(
        self,
        name: str,
        *,
        args: typing.Mapping[str, typing.Any] | None = None,
        job_id: typing.Optional[str] = None,
        job_id_len: int = 8
    ) -> str:

        jid = job_id or self.short_uuid(job_id_len)

        meta = {
            "name": str(name), "args": dict(args or {}), "ts": time.monotonic()
        }

        async with self.lock:
            if jid not in self.jobs:
                self.active_jobs += 1
            self.jobs[jid] = meta
            self.last_touch = time.monotonic()

        return jid

    async def job_final(self, job_id: str) -> None:
        async with self.lock:
            if job_id in self.jobs:
                self.jobs.pop(job_id, None)
                if self.active_jobs > 0: self.active_jobs -= 1
            else:
                if self.active_jobs > 0: self.active_jobs -= 1

            self.last_touch = time.monotonic()

    async def snapshot(self) -> dict:
        async with self.lock:
            now = time.monotonic()

            jobs = [
                {
                    "id"      : jid,
                    "name"    : meta.get("name"),
                    "args"    : meta.get("args"),
                    "age_sec" : max(0.0, now - float(meta.get("ts", now)))
                }
                for jid, meta in self.jobs.items()
            ]

            sessions = {
                key: {
                    "job_id"  : jid,
                    "name"    : self.jobs.get(jid, {}).get("name"),
                    "args"    : self.jobs.get(jid, {}).get("args"),
                    "age_sec" : max(0.0, now - float(self.jobs.get(jid, {}).get("ts", now)))
                }
                for key, jid in self.sessions_map.items()
            }

            return {
                "ttl_sec"     : self.ttl_sec,
                "active_jobs" : self.active_jobs,
                "idle_sec"    : max(0.0, now - self.last_touch),
                "jobs"        : jobs,
                "sessions"    : sessions,
                **Ins.instance_snapshots()
            }

    async def looper(self) -> None:
        if self.ttl_sec <= 0:
            return None

        try:
            while True:
                await asyncio.sleep(1.0)
                async with self.lock:
                    idle      = time.monotonic() - self.last_touch
                    active    = self.active_jobs
                    jobs_copy = list(self.jobs.items())

                # ===== idle 到期，准备 kill =====
                if active == 0 and idle >= self.ttl_sec:
                    logger.warning(
                        f"[IDLE-KILL] ttl={self.ttl_sec}s idle={idle:.1f}s active_jobs=0 -> exit"
                    )
                    return os.kill(os.getpid(), signal.SIGINT)

                # ===== 有任务但 idle 超时：仅告警 =====
                if active > 0 and idle >= self.ttl_sec:
                    top = ", ".join([f"{jid}:{m.get('name')}" for jid, m in jobs_copy][:5])
                    logger.warning(f"[IDLE-BLOCKED] idle={idle:.1f}s active_jobs={active} jobs={top}")

        except asyncio.CancelledError:
            raise


if __name__ == '__main__':
    pass
