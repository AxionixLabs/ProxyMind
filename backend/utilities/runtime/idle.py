# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import os
import time
import uuid
import signal
import typing
import asyncio
import inspect
import contextlib
from loguru import logger


class Idle(object):
    """Idle class."""

    def __init__(
        self,
        *,
        ttl_sec: float = 300.0,
        snapshot_provider: typing.Callable[
            [],
            typing.Awaitable[dict[str, typing.Any]] | dict[str, typing.Any]
        ] | None = None
    ):
        """初始化空闲管理器及统一运行态注册表。"""
        self.agent_id = "idle"

        self.ttl_sec = float(ttl_sec)

        self.last_touch: float = time.monotonic()

        self.lock: asyncio.Lock = asyncio.Lock()
        self.task: typing.Optional[asyncio.Task] = None

        self.runs: dict[str, dict[str, typing.Any]] = {}
        self.snapshot_provider = snapshot_provider or (lambda: {})

    @staticmethod
    def short_uuid(n: int = 8) -> str:
        """生成指定长度的短 UUID 文本。"""
        return uuid.uuid4().hex[: max(4, int(n))]

    @staticmethod
    def _run_meta(
        *,
        run_kind: typing.Literal["job", "session"],
        run_id: str,
        name: str,
        args: typing.Mapping[str, typing.Any] | None = None,
        handle: typing.Any = None
    ) -> dict[str, typing.Any]:
        """构造统一的运行态元数据结构。"""
        return {
            "kind"   : run_kind,
            "id"     : str(run_id),
            "name"   : str(name),
            "args"   : dict(args or {}),
            "ts"     : time.monotonic(),
            "handle" : handle
        }

    async def session_begin(
        self,
        key: str,
        name: str,
        *,
        args: typing.Mapping[str, typing.Any] | None = None,
        replace: bool = True,
        job_id_len: int = 8,
        handle: typing.Any = None
    ) -> str:
        """注册一个长生命周期会话，并返回本次会话的运行 ID。"""
        async with self.lock:
            old = self.runs.get(key)
            if old:
                if old.get("kind") != "session":
                    raise RuntimeError(f"run key already occupied by non-session: {key}")
                if not replace:
                    raise RuntimeError(f"session already active: {key}")

                self.runs.pop(key, None)

            job_id = self.short_uuid(job_id_len)
            self.runs[key] = self._run_meta(
                run_kind="session",
                run_id=job_id,
                name=name,
                args=args,
                handle=handle
            )
            self.last_touch = time.monotonic()
        return job_id

    async def session_final(self, key: str) -> None:
        """按稳定会话键结束一个长生命周期会话。"""
        async with self.lock:
            meta = self.runs.get(key)
            if meta and meta.get("kind") == "session":
                self.runs.pop(key, None)
                self.last_touch = time.monotonic()

    async def session_get_handle(self, key: str) -> typing.Any:
        """读取指定会话关联的真实运行句柄。"""
        async with self.lock:
            meta = self.runs.get(key)
            if meta and meta.get("kind") == "session":
                return meta.get("handle")
            return None

    async def session_patch_args(self, key: str, patch: typing.Mapping[str, typing.Any]) -> None:
        """
        更新某个 session 对应 job 的 args（增量合并）。
        用于把 token / 状态 / 统计信息写回 idle session。
        """
        async with self.lock:
            if not (meta := self.runs.get(key)):
                return None
            if meta.get("kind") != "session":
                return None

            if not isinstance(args := meta.get("args"), dict):
                args = {}
                meta["args"] = args

            args.update(dict(patch))
            self.last_touch = time.monotonic()

    async def start_idle(self) -> None:
        """启动空闲检测循环。"""
        if self.task and not self.task.done():
            return None
        self.task = asyncio.create_task(self.looper())

    async def close_idle(self) -> None:
        """关闭空闲检测循环并等待后台任务结束。"""
        if not self.task:
            return None

        if not self.task.done():
            self.task.cancel()

        with contextlib.suppress(asyncio.CancelledError):
            await self.task
        self.task = None

    async def touch(self) -> None:
        """刷新最近活动时间，阻止被误判为空闲。"""
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
        """注册一个短生命周期任务，并返回任务 ID。"""
        jid = job_id or self.short_uuid(job_id_len)

        async with self.lock:
            self.runs[jid] = self._run_meta(
                run_kind="job",
                run_id=jid,
                name=name,
                args=args
            )
            self.last_touch = time.monotonic()

        return jid

    async def job_final(self, job_id: str) -> None:
        """结束指定短任务。"""
        async with self.lock:
            meta = self.runs.get(job_id)
            if meta and meta.get("kind") == "job":
                self.runs.pop(job_id, None)
            self.last_touch = time.monotonic()

    async def snapshot(self) -> dict:
        """生成当前运行时任务、会话和实例状态快照。"""
        async with self.lock:
            now: float = time.monotonic()
            jobs: list = []

            sessions: dict[str, typing.Any] = {}

            for run_key, meta in self.runs.items():
                age_sec = max(0.0, now - float(meta.get("ts", now)))
                if meta.get("kind") == "job":
                    jobs.append(
                        {
                            "id"      : meta.get("id", run_key),
                            "name"    : meta.get("name"),
                            "args"    : meta.get("args"),
                            "age_sec" : age_sec
                        }
                    )
                    continue

                if meta.get("kind") == "session":
                    sessions[run_key] = {
                        "job_id"  : meta.get("id", run_key),
                        "name"    : meta.get("name"),
                        "args"    : meta.get("args"),
                        "age_sec" : age_sec
                    }

            active_total = len(self.runs)
            provider_data = self.snapshot_provider()
            if inspect.isawaitable(provider_data):
                provider_data = await provider_data

            return {
                "ttl_sec"            : self.ttl_sec,
                "active_runtime_jobs": len(jobs),
                "active_sessions"    : len(sessions),
                "active_total"       : active_total,
                "idle_sec"           : max(0.0, now - self.last_touch),
                "jobs"               : jobs,
                "sessions"           : sessions,
                **provider_data
            }

    async def looper(self) -> None:
        """后台循环检测空闲超时，并在需要时触发退出或告警。"""
        if self.ttl_sec <= 0:
            return None

        try:
            while True:
                await asyncio.sleep(1.0)

                async with self.lock:
                    idle   = time.monotonic() - self.last_touch
                    active = len(self.runs)

                    runs_copy = [
                        (run_key, meta.get("name"))
                        for run_key, meta in self.runs.items()
                    ]

                if active == 0 and idle >= self.ttl_sec:
                    logger.warning(
                        f"[IDLE-KILL] ttl={self.ttl_sec}s idle={idle:.1f}s active_total=0 -> exit"
                    )
                    return os.kill(os.getpid(), signal.SIGINT)

                if active > 0 and idle >= self.ttl_sec:
                    top = ", ".join([f"{run_key}:{name}" for run_key, name in runs_copy[:5]])
                    logger.warning(f"[IDLE-BLOCKED] idle={idle:.1f}s active_total={active} jobs={top}")

        except asyncio.CancelledError:
            raise


if __name__ == '__main__':
    pass
