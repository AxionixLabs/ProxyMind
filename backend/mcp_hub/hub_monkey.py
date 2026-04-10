# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import re
import time
import typing
import asyncio
import contextlib
from collections import deque
from pathlib import Path
from loguru import logger
from backend.mcp_hub.hub_device import Device
from backend.models.model_base import Attachment
from backend.utilities.storage.output import mk_out_dir
from backend.utilities.process import Flux
from backend.utilities import const

if typing.TYPE_CHECKING:
    from backend.utilities.runtime import Idle

_PROGRESS_RE   = re.compile(r"Events injected:\s*(\d+)")
_EVENT_LINE_RE = re.compile(r"^:(Sending|Switch)\b")

_PATTERNS: dict[str, list[str]] = {
    "crash": [
        "FATAL EXCEPTION", "AndroidRuntime", "Fatal signal", "SIGSEGV", "SIGABRT",
        "has died", "Killed process", "backtrace:",
    ],
    "anr": [
        "ANR in", "Application Not Responding", "Input dispatching timed out",
        "Activity pause timeout", "Broadcast of intent", "Executing service",
    ],
    "oom": [
        "OutOfMemoryError", "Failed to allocate", "OOM", "Low memory",
    ],
    "monkey_abort": [
        "Monkey aborted", "** ANR", "** CRASH", "Monkey finished", "Events injected:",
    ],
}


class Monkey(object):
    """Monkey 长任务入口。"""

    recent_runs: typing.ClassVar[dict[str, dict[str, typing.Any]]] = {}

    def __init__(self, device: Device, idle: "Idle"):
        self.device: Device = device
        self.idle: "Idle" = idle
        self.agent_id: str = "monkey"

        self.release_lock: asyncio.Lock = asyncio.Lock()
        self.done_event: asyncio.Event = asyncio.Event()

        self.proc_logcat: typing.Optional[asyncio.subprocess.Process] = None
        self.proc_monkey: typing.Optional[asyncio.subprocess.Process] = None
        self.task_logcat: typing.Optional[asyncio.Task[None]] = None
        self.task_monkey: typing.Optional[asyncio.Task[None]] = None
        self.task_runner: typing.Optional[asyncio.Task[None]] = None
        self.task_guard: typing.Optional[asyncio.Task[None]] = None

        self.session_id: typing.Optional[str] = None
        self.status_text: str = "idle"
        self.stop_requested: bool = False
        self.finalized: bool = False

        self.start_ms: typing.Optional[int] = None
        self.end_ms: typing.Optional[int] = None
        self.return_code: typing.Optional[int] = None
        self.error: typing.Optional[str] = None
        self.remote_stop: typing.Optional[dict[str, typing.Any]] = None
        self.logcat_saved: typing.Optional[str] = None
        self.logcat_summary: int = 0
        self.logcat_count: int = 0
        self.result_reason: typing.Optional[str] = None

        self.config: dict[str, typing.Any] = {}
        self.cmd_monkey: list[str] = []
        self.attachments: list[dict[str, typing.Any]] = []

        self.guard_miss_count: int = 0
        self.guard_hit_count: int = 0
        self.guard_last_focus: dict[str, typing.Any] = {"package": None, "activity": None, "raw": ""}
        self.guard_last_ok_ms: typing.Optional[int] = None
        self.guard_last_miss_ms: typing.Optional[int] = None
        self.guard_armed: bool = False
        self.guard_grace_until_ms: typing.Optional[int] = None
        self.guard_trigger_reason: typing.Optional[str] = None
        self.foreground_lost_count: int = 0

        self.events_done: int = 0
        self.events_remaining: int = 0
        self.segment_index: int = 0
        self.segment_target_events: int = 0
        self.segment_observed_events: int = 0
        self.segment_reported_events: int = 0
        self.segment_start_done: int = 0
        self.segment_stop_requested: bool = False
        self.tail: deque[str] = deque(maxlen=500)
        self.stats: dict[str, int] = {key: 0 for key in _PATTERNS}
        self.evidence: dict[str, deque[str]] = {key: deque(maxlen=10) for key in _PATTERNS}

    @property
    def session_key(self) -> str:
        return f"{self.agent_id}:{self.device.serial}"

    def session_args(
        self,
        extra: typing.Mapping[str, typing.Any] | None = None
    ) -> dict[str, typing.Any]:
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
        query_reason: typing.Optional[str] = None
    ) -> dict[str, typing.Any]:
        if item := cls.recent_runs.get(serial):
            data = dict(item.get("data") or {})
            if query_reason:
                data["query_reason"] = query_reason
            return {
                "text"        : item.get("text") or "Monkey 最近一次结果已返回。",
                "attachments" : list(item.get("attachments") or []),
                "data"        : data,
                "logs"        : list(item.get("logs") or [])
            }

        return {
            "text"        : "未找到活跃或最近一次 monkey 会话。",
            "attachments" : [],
            "data": {
                "ok"           : True,
                "serial"       : serial,
                "status"       : "idle",
                "reason"       : None,
                "query_reason" : query_reason or "no_session"
            },
            "logs": []
        }

    @classmethod
    def clear_recent(cls, serial: str) -> bool:
        return cls.recent_runs.pop(serial, None) is not None

    @staticmethod
    def _build_monkey_config(
        *,
        package: str,
        seed: int,
        throttle_ms: int,
        touch: int,
        motion: int,
        nav: int,
        events: int,
        activity: typing.Optional[str],
        saved: typing.Optional[str]
    ) -> dict[str, typing.Any]:
        return {
            "package"     : package,
            "activity"    : activity,
            "seed"        : seed,
            "throttle_ms" : throttle_ms,
            "touch"       : touch,
            "motion"      : motion,
            "nav"         : nav,
            "events"      : max(1, int(events)),
            "saved"       : saved
        }

    @staticmethod
    def _build_guard_config(
        *,
        guard_foreground: bool,
        guard_interval_s: float,
        guard_startup_grace_s: float,
        guard_miss_threshold: int,
        guard_action: str
    ) -> dict[str, typing.Any]:
        guard_action = str(guard_action or "observe").strip().lower()
        if guard_action not in {"observe", "stop", "fail"}:
            guard_action = "observe"

        return {
            "guard_foreground"      : guard_foreground,
            "guard_interval_s"      : max(0.2, float(guard_interval_s)),
            "guard_startup_grace_s" : max(0.0, float(guard_startup_grace_s)),
            "guard_miss_threshold"  : max(1, int(guard_miss_threshold)),
            "guard_action"          : guard_action,
        }

    def reset(self) -> None:
        self.done_event.clear()
        self.proc_logcat = None
        self.proc_monkey = None
        self.task_logcat = None
        self.task_monkey = None
        self.task_runner = None
        self.task_guard  = None

        self.session_id     = None
        self.status_text    = "starting"
        self.stop_requested = False
        self.finalized      = False

        self.start_ms       = int(time.time() * 1000)
        self.end_ms         = None
        self.return_code    = None
        self.error          = None
        self.remote_stop    = None
        self.logcat_saved   = None
        self.logcat_summary = 0
        self.logcat_count   = 0
        self.result_reason  = None

        self.config      = {}
        self.cmd_monkey  = []
        self.attachments = []

        self.guard_miss_count      = 0
        self.guard_hit_count       = 0
        self.guard_last_focus      = {"package": None, "activity": None, "raw": ""}
        self.guard_last_ok_ms      = None
        self.guard_last_miss_ms    = None
        self.guard_armed           = False
        self.guard_grace_until_ms  = None
        self.guard_trigger_reason  = None
        self.foreground_lost_count = 0

        self.events_done      = 0
        self.events_remaining = 0

        self.segment_index           = 0
        self.segment_target_events   = 0
        self.segment_observed_events = 0
        self.segment_reported_events = 0
        self.segment_start_done      = 0
        self.segment_stop_requested  = False

        self.tail.clear()
        self.stats = {key: 0 for key in _PATTERNS}
        self.evidence = {key: deque(maxlen=10) for key in _PATTERNS}

    def matcher(self, text: str) -> typing.Optional[str]:
        for key, keywords in _PATTERNS.items():
            for keyword in keywords:
                if keyword in text:
                    self.stats[key] += 1
                    self.evidence[key].append(text)
                    return key
        return None

    def _snapshot_timing(self) -> tuple[bool, bool, int]:
        now_ms = int(time.time() * 1000)
        end_ms = self.end_ms or now_ms

        duration_ms = max(0, end_ms - self.start_ms) if self.start_ms else 0
        running     = self.status_text in {"starting", "running", "stopping"}

        ok = self.status_text != "failed" and self.status_text in {
            "finished", "stopped", "starting", "running", "stopping"
        }
        return ok, running, duration_ms

    def snapshot_data(self) -> dict[str, typing.Any]:
        ok, running, duration_ms = self._snapshot_timing()

        data = {
            "ok"                 : ok,
            "active"             : running,
            "done"               : bool(self.finalized),
            "status"             : self.status_text,
            "reason"             : self.result_reason,
            "serial"             : self.device.serial,
            "job_id"             : self.session_id,
            "session_key"        : self.session_key,
            "monkey_cmd"         : self.cmd_monkey,
            "monkey_return_code" : self.return_code,
            "stop_requested"     : self.stop_requested,
            "start_ms"           : self.start_ms,
            "end_ms"             : self.end_ms,
            "duration_ms"        : duration_ms,
            "package"            : self.config.get("package"),
            "activity"           : self.config.get("activity"),
            "seed"               : self.config.get("seed"),
            "throttle_ms"        : self.config.get("throttle_ms"),
            "pct": {
                "touch"  : self.config.get("touch"),
                "motion" : self.config.get("motion"),
                "nav"    : self.config.get("nav")
            },
            "guard_foreground"        : bool(self.config.get("guard_foreground")),
            "guard_interval_s"        : self.config.get("guard_interval_s"),
            "guard_startup_grace_s"   : self.config.get("guard_startup_grace_s"),
            "guard_miss_threshold"    : self.config.get("guard_miss_threshold"),
            "guard_action"            : self.config.get("guard_action"),
            "events"                  : self.config.get("events"),
            "events_done"             : self.events_done,
            "events_remaining"        : self.events_remaining,
            "segment_index"           : self.segment_index,
            "segment_target_events"   : self.segment_target_events,
            "segment_observed_events" : self.segment_observed_events,
            "segment_reported_events" : self.segment_reported_events,
            "tail"                    : list(self.tail),
            "stats"                   : dict(self.stats),
            "evidence"                : {key: list(values) for key, values in self.evidence.items()},
            "guard": {
                "enabled"               : bool(self.config.get("guard_foreground")),
                "miss_count"            : self.guard_miss_count,
                "hit_count"             : self.guard_hit_count,
                "last_focus"            : dict(self.guard_last_focus),
                "last_ok_ms"            : self.guard_last_ok_ms,
                "last_miss_ms"          : self.guard_last_miss_ms,
                "armed"                 : self.guard_armed,
                "grace_until_ms"        : self.guard_grace_until_ms,
                "triggered"             : bool(self.guard_trigger_reason),
                "trigger_reason"        : self.guard_trigger_reason,
                "foreground_lost_count" : self.foreground_lost_count
            }
        }
        if self.error:
            data["error"] = self.error
        if self.remote_stop is not None:
            data["remote_stop"] = self.remote_stop
        if self.logcat_saved:
            data["logcat_saved"] = self.logcat_saved
            data["logcat_summary"] = self.logcat_summary
            data["logcat_count"] = self.logcat_count
        return data

    def build_pack(
        self,
        text: str,
        *,
        query_reason: typing.Optional[str] = None
    ) -> dict[str, typing.Any]:
        data = self.snapshot_data()
        if query_reason:
            data["query_reason"] = query_reason
        return {
            "text"        : text,
            "attachments" : list(self.attachments),
            "data"        : data,
            "logs"        : []
        }

    def remember_recent(self, text: str) -> None:
        self.recent_runs[self.device.serial] = self.build_pack(text)

    def build_monkey_cmd(self, events: int) -> list[str]:
        package = str(self.config.get("package") or "").strip()
        return [
            "adb", "-s", self.device.serial, "shell", "monkey", "-p", package,
            "-s", str(self.config.get("seed")),
            "--throttle", str(self.config.get("throttle_ms")),
            "--pct-touch", str(self.config.get("touch")),
            "--pct-motion", str(self.config.get("motion")),
            "--pct-nav", str(self.config.get("nav")),
            "--pct-appswitch", "0",
            "--pct-syskeys", "0",
            "--ignore-crashes",
            "--ignore-timeouts",
            "--ignore-security-exceptions",
            "-v", "-v",
            str(events)
        ]

    def reset_guard_window(self) -> None:
        now_ms   = int(time.time() * 1000)
        grace_ms = int(float(self.config.get("guard_startup_grace_s") or 0.0) * 1000)

        self.guard_miss_count = 0
        self.guard_last_miss_ms = None
        self.guard_grace_until_ms = now_ms + grace_ms

    def mark_foreground_ok(
        self,
        focus: typing.Optional[dict[str, typing.Any]] = None,
    ) -> None:
        now_ms = int(time.time() * 1000)
        package = str(self.config.get("package") or "").strip()
        if focus:
            self.guard_last_focus = {
                "package"  : focus.get("package"),
                "activity" : focus.get("activity"),
                "raw"      : focus.get("raw", ""),
            }
        elif not self.guard_last_focus.get("package"):
            self.guard_last_focus = {"package": package, "activity": None, "raw": ""}
        self.guard_hit_count += 1
        self.guard_miss_count = 0
        self.guard_last_ok_ms = now_ms
        self.guard_armed = True
        self.reset_guard_window()

    def handle_monkey_output(self, text: str) -> None:
        if matched := _PROGRESS_RE.search(text):
            self.segment_reported_events = min(
                self.segment_target_events,
                max(self.segment_reported_events, int(matched.group(1))),
            )
            total_done = min(
                int(self.config.get("events") or 0),
                self.segment_start_done + self.segment_reported_events,
            )
            self.tail.append(f"[monkey.progress] reported={total_done}/{self.config.get('events')}")
            return None
        if not _EVENT_LINE_RE.match(text):
            return None
        if self.segment_observed_events >= self.segment_target_events:
            return None
        self.segment_observed_events += 1
        if self.segment_observed_events % 250 == 0:
            total_done = min(
                int(self.config.get("events") or 0),
                self.segment_start_done + self.segment_observed_events,
            )
            self.tail.append(f"[monkey.progress] observed={total_done}/{self.config.get('events')}")

    def segment_consumed_events(self, rc: typing.Optional[int]) -> int:
        consumed = max(self.segment_reported_events, self.segment_observed_events)
        if rc == 0 and not self.segment_stop_requested:
            consumed = max(consumed, self.segment_target_events)
        return max(0, min(self.segment_target_events, consumed))

    def _resolve_final_state(self) -> None:
        if self.error:
            self.status_text = "failed"
            self.result_reason = self.result_reason or "runtime_error"
            return None
        if self.stop_requested:
            self.status_text = "stopped"
            self.result_reason = self.result_reason or "stop_requested"
            return None
        if self.events_remaining <= 0 and (self.return_code is None or self.return_code == 0):
            self.status_text = "finished"
            self.result_reason = self.result_reason or "completed"
            self.return_code = 0 if self.return_code is None else self.return_code
            return None
        if self.return_code == 0:
            self.status_text = "finished"
            self.result_reason = self.result_reason or "completed"
            return None
        self.status_text = "failed"
        self.result_reason = self.result_reason or "monkey_exit_nonzero"

    def _build_final_summary(self) -> str:
        if self.error:
            return f"Monkey 运行失败：{self.error}" + (f" logcat={self.logcat_saved}" if self.logcat_saved else "")
        return (
            "Monkey 已结束："
            f"status={self.status_text} rc={self.return_code} events={self.events_done}/{self.config.get('events')}"
            f" duration={self.snapshot_data().get('duration_ms')}ms"
            + (f" logcat={self.logcat_saved}" if self.logcat_saved else "")
        )

    def _prepare_start_config(
        self,
        *,
        package: str,
        seed: int,
        throttle_ms: int,
        touch: int,
        motion: int,
        nav: int,
        events: int,
        activity: typing.Optional[str],
        guard_foreground: bool,
        guard_interval_s: float,
        guard_startup_grace_s: float,
        guard_miss_threshold: int,
        guard_action: str,
        saved: typing.Optional[str]
    ) -> None:
        self.config = {
            **self._build_monkey_config(
                package=package,
                seed=seed,
                throttle_ms=throttle_ms,
                touch=touch,
                motion=motion,
                nav=nav,
                events=events,
                activity=activity,
                saved=saved
            ),
            **self._build_guard_config(
                guard_foreground=guard_foreground,
                guard_interval_s=guard_interval_s,
                guard_startup_grace_s=guard_startup_grace_s,
                guard_miss_threshold=guard_miss_threshold,
                guard_action=guard_action
            )
        }
        self.events_remaining = int(self.config["events"])
        self.reset_guard_window()

    def _record_guard_miss(
        self,
        *,
        current_package: typing.Optional[str],
        now_ms: int
    ) -> None:
        self.guard_armed = True
        self.guard_miss_count += 1
        self.guard_last_miss_ms = now_ms
        self.tail.append(
            f"[guard.miss] expected={self.config.get('package') or ''} "
            f"actual={current_package or ''} count={self.guard_miss_count}"
        )

    async def _trigger_guard_stop(
        self,
        *,
        action: str,
        reason: str,
        guard_reason: str,
        stop_session: bool = True,
        error: typing.Optional[str] = None
    ) -> None:
        self.tail.append(f"[guard.trigger] action={action}")
        await self.stop_proc(
            reason=reason,
            error=error,
            guard_reason=guard_reason,
            stop_session=stop_session,
        )

    async def _finalize_artifacts(self) -> None:
        if self.stop_requested and self.remote_stop is None:
            with contextlib.suppress(Exception):
                self.remote_stop = await self.stop_remote_monkey()
        await self.shutdown()
        await self.capture_logcat()
        await self.patch_session()

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

    async def stop_proc(
        self,
        *,
        reason: str,
        error: typing.Optional[str] = None,
        guard_reason: typing.Optional[str] = None,
        stop_session: bool = True
    ) -> None:
        if self.finalized:
            return None
        self.segment_stop_requested = True
        self.status_text = "stopping"
        if stop_session:
            self.stop_requested = True
            self.result_reason = reason
        elif reason:
            self.result_reason = reason
        if error is not None:
            self.error = error
        if guard_reason:
            self.guard_trigger_reason = guard_reason
        await self.patch_session()
        await self.shutdown_proc(self.proc_monkey, term_timeout=2.0, kill_timeout=3.0)

    async def patch_session(self) -> None:
        await self.idle.session_patch_args(self.session_key, self.snapshot_data())

    async def guard_foreground(self) -> None:
        package   = str(self.config.get("package") or "").strip()
        interval  = max(0.2, float(self.config.get("guard_interval_s") or 1.0))
        threshold = max(1, int(self.config.get("guard_miss_threshold") or 1))
        action    = str(self.config.get("guard_action") or "observe").strip() or "observe"

        while not self.finalized:
            await asyncio.sleep(interval)
            if self.finalized or not self.proc_monkey or self.proc_monkey.returncode is not None:
                return None
            try:
                focus = await self.device.phone.focus_info()
            except Exception as exc:
                self.tail.append(f"[guard.error] {type(exc).__name__}: {exc}")
                await self.patch_session()
                continue

            self.guard_last_focus = {
                "package"  : focus.get("package"),
                "activity" : focus.get("activity"),
                "raw"      : focus.get("raw", "")
            }
            now_ms = int(time.time() * 1000)
            current_package = self.guard_last_focus.get("package")

            if current_package == package:
                if self.guard_miss_count > 0:
                    self.tail.append(
                        f"[guard.recovered] package={current_package or ''} misses={self.guard_miss_count}"
                    )
                if self.guard_trigger_reason == "observe":
                    self.guard_trigger_reason = None
                self.mark_foreground_ok(focus)
                await self.patch_session()
                continue

            if not self.guard_armed and self.guard_grace_until_ms and now_ms < self.guard_grace_until_ms:
                await self.patch_session()
                continue

            self._record_guard_miss(current_package=current_package, now_ms=now_ms)
            if self.guard_miss_count < threshold:
                await self.patch_session()
                continue
            if action == "observe":
                if self.guard_trigger_reason != "observe":
                    self.foreground_lost_count += 1
                    self.guard_trigger_reason = "observe"
                    self.tail.append("[guard.trigger] action=observe")
                await self.patch_session()
                continue
            if action == "fail":
                self.foreground_lost_count += 1
                await self._trigger_guard_stop(
                    action="fail",
                    reason="lost_foreground",
                    error=f"foreground lost: expected {package}, got {current_package or 'unknown'}",
                    guard_reason="lost_foreground",
                )
                return None
            self.foreground_lost_count += 1
            await self._trigger_guard_stop(
                action="stop",
                reason="guard_stop",
                guard_reason="lost_foreground"
            )
            return None

    async def reader(self, proc: asyncio.subprocess.Process, name: str) -> None:

        async def pump(
            stream: typing.Optional[typing.AsyncIterable[bytes]],
            stream_name: str
        ) -> None:
            if stream is None:
                return None
            async for line in stream:
                text = line.decode(const.CHARSET, const.IGNORE)
                if not (text := text.rstrip("\r\n")):
                    continue
                if name == "monkey":
                    self.handle_monkey_output(text)
                if not self.matcher(text):
                    continue
                logger.info(f"{name}.{stream_name}: {text}")
                self.tail.append(f"[{name}.{stream_name}] {text}")

        tasks = [
            asyncio.create_task(pump(proc.stdout, "stdout")),
            asyncio.create_task(pump(proc.stderr, "stderr")),
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

    async def finish_active_segment(self) -> None:
        if self.task_guard:
            if not self.task_guard.done():
                self.task_guard.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task_guard
            self.task_guard = None
        if self.task_monkey:
            with contextlib.suppress(asyncio.CancelledError):
                await self.task_monkey
            self.task_monkey = None
        self.proc_monkey = None

    async def shutdown(self) -> None:
        await self.shutdown_proc(self.proc_monkey)
        await self.shutdown_proc(self.proc_logcat)
        await self.finish_active_segment()
        if self.task_logcat:
            if not self.task_logcat.done():
                self.task_logcat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task_logcat
            self.task_logcat = None
        self.proc_logcat = None

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

    async def capture_logcat(self) -> None:
        saved_root = str(self.config.get("saved") or "").strip()
        if not saved_root:
            return None
        try:
            out_dir = mk_out_dir(saved_root, engine="perf", tool="monkey_logcat")
            result = await self.device.file_logcat_dump(level="W", saved=str(out_dir))
            data = dict(result.get("data") or {})
            saved_path = str(data.get("saved") or "").strip()
            if not saved_path:
                return None
            self.logcat_saved = saved_path
            self.logcat_summary = int(data.get("summary") or 0)
            self.logcat_count = int(data.get("count") or 0)
            self.attachments.append(
                Attachment(
                    kind="file",
                    local=saved_path,
                    filename=Path(saved_path).name,
                    mime_type="text/plain"
                ).to_dict()
            )
            self.tail.append(
                f"[logcat.saved] lines={self.logcat_count} summary={self.logcat_summary} path={saved_path}"
            )
        except Exception as exc:
            self.tail.append(f"[logcat.save.failed] {type(exc).__name__}: {exc}")

    async def finalize(
        self,
        *,
        rc: typing.Optional[int] = None,
        err: typing.Optional[str] = None
    ) -> None:
        if self.finalized:
            return None
        self.finalized = True
        if rc is not None:
            self.return_code = rc
        if err is not None:
            self.error = err
        self.end_ms = int(time.time() * 1000)
        self._resolve_final_state()
        await self._finalize_artifacts()
        self.remember_recent(self._build_final_summary())
        self.done_event.set()
        await self.release()

    async def launch_segment(self, events: int) -> None:
        self.segment_index += 1
        self.segment_target_events = events
        self.segment_observed_events = 0
        self.segment_reported_events = 0
        self.segment_start_done = self.events_done
        self.segment_stop_requested = False
        self.reset_guard_window()

        self.cmd_monkey = self.build_monkey_cmd(events)
        self.proc_monkey = await Flux.cmd_link(self.cmd_monkey)
        self.task_monkey = asyncio.create_task(self.reader(self.proc_monkey, "monkey"))
        if self.config.get("guard_foreground"):
            self.task_guard = asyncio.create_task(self.guard_foreground())

        self.status_text = "running"
        self.tail.append(
            f"[monkey.segment] index={self.segment_index} target={events} done={self.events_done}"
        )
        await self.patch_session()

    async def runner(self) -> None:
        err: typing.Optional[str] = None
        try:
            total_events = max(1, int(self.config.get("events") or 1))

            self.events_remaining = max(0, total_events - self.events_done)
            self.status_text = "running"

            await self.patch_session()

            while not self.stop_requested and self.events_remaining > 0:
                await self.launch_segment(self.events_remaining)
                rc = await self.proc_monkey.wait() if self.proc_monkey else None
                self.return_code = rc

                await self.finish_active_segment()

                consumed = self.segment_consumed_events(rc)
                self.events_done = min(total_events, self.events_done + consumed)
                self.events_remaining = max(0, total_events - self.events_done)

                tail = (
                    f"[monkey.segment.done] index={self.segment_index} "
                    f"rc={rc} consumed={consumed} remaining={self.events_remaining}"
                )
                self.tail.append(tail)

                await self.patch_session()

                if self.stop_requested:
                    break
                if rc != 0:
                    self.result_reason = self.result_reason or "monkey_exit_nonzero"
                    break

        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"

        finally:
            await self.finalize(rc=self.return_code, err=err)

    async def _start_logcat_capture(self) -> None:
        """Start logcat streaming before monkey launch so evidence covers the full run."""
        await self.device.file_logcat_clean()
        self.proc_logcat = await self.device.file_logcat_link()
        self.task_logcat = asyncio.create_task(self.reader(self.proc_logcat, "logcat"))
        await asyncio.sleep(0.2)

    async def start(
        self,
        package: str,
        seed: int = 42,
        throttle_ms: int = 150,
        touch: int = 65,
        motion: int = 20,
        nav: int = 10,
        events: int = 10000,
        activity: typing.Optional[str] = None,
        guard_foreground: bool = True,
        guard_interval_s: float = 10.0,
        guard_startup_grace_s: float = 3.0,
        guard_miss_threshold: int = 1,
        guard_action: str = "observe",
        saved: typing.Optional[str] = None
    ) -> dict[str, typing.Any]:
        self.reset()
        self._prepare_start_config(
            package=package,
            seed=seed,
            throttle_ms=throttle_ms,
            touch=touch,
            motion=motion,
            nav=nav,
            events=events,
            activity=activity,
            guard_foreground=guard_foreground,
            guard_interval_s=guard_interval_s,
            guard_startup_grace_s=guard_startup_grace_s,
            guard_miss_threshold=guard_miss_threshold,
            guard_action=guard_action,
            saved=saved
        )
        try:
            try:
                await self.acquire("monkey.start")
            except RuntimeError as e:
                if "session already active" in str(e):
                    return await self.status(query_reason="already_running")
                raise

            await self._start_logcat_capture()

            self.task_runner = asyncio.create_task(self.runner())
            self.status_text = "running"
            await self.patch_session()
            return self.build_pack(
                "Monkey 已启动。默认请先用 monkey_status 查询进度，或用 monkey_stop 主动停止；"
                "除非用户明确要求等待最终结果，否则不要立刻调用 monkey_wait。"
            )
        except Exception as e:
            self.result_reason = self.result_reason or "start_failed"
            await self.finalize(err=f"{type(e).__name__}: {e}")
            return self.build_pack("Monkey 启动失败。")

    async def status(self, *, query_reason: typing.Optional[str] = None) -> dict[str, typing.Any]:
        text = (
            "Monkey 正在运行中。"
            if self.status_text in {"starting", "running", "stopping"} else
            f"Monkey 当前状态：{self.status_text}"
        )
        return self.build_pack(text, query_reason=query_reason)

    async def stop(self) -> dict[str, typing.Any]:
        if self.finalized:
            return await self.status(query_reason="already_finished")

        self.stop_requested = True
        self.result_reason = self.result_reason or "stop_requested"
        self.status_text = "stopping"
        await self.patch_session()
        await self.shutdown_proc(self.proc_monkey, term_timeout=2.0, kill_timeout=3.0)

        if self.task_runner:
            with contextlib.suppress(asyncio.CancelledError):
                await self.task_runner

        return self.build_pack("Monkey 已停止。", query_reason="stop_requested")

    async def wait(self) -> dict[str, typing.Any]:
        await self.done_event.wait()
        return self.recent_pack(self.device.serial)


if __name__ == '__main__':
    pass
