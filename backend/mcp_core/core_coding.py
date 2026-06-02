# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import os
import re
import time
import typing
import asyncio
from collections import deque
from loguru import logger
from backend.mcp_core.core_buffer import LineBuffer
from backend.mcp_core.coding_runtime import CodexRuntimeResolver
from backend.models.model_device import SemanticResult
from backend.utilities import const
from backend.utilities.process import Flux
from backend.utilities.trace import (
    clip_text, summarize_command
)

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


class Coding(object):
    """编码类 CLI 适配器。"""

    __instance: typing.Optional["Coding"] = None
    __initialized: bool = False

    def __new__(cls, *args, **kwargs):
        """实现单例分配，确保整个进程只维护一个编码会话对象。"""
        if not cls.__instance:
            cls.__instance = super(Coding, cls).__new__(cls)
        return cls.__instance

    def __init__(self):
        """初始化会话状态、输出缓冲区与并发控制对象。"""
        if not self.__initialized:
            self.__prefix: str = "coding"
            self.agent_id: str = self.__prefix

            self.__transports: typing.Optional[asyncio.subprocess.Process] = None
            self.stdout_task: typing.Optional[asyncio.Task[None]] = None
            self.stderr_task: typing.Optional[asyncio.Task[None]] = None
            self.wait_task: typing.Optional[asyncio.Task[dict[str, typing.Any]]] = None
            self.wait_result: typing.Optional[dict[str, typing.Any]] = None

            self.session_id: typing.Optional[str] = None
            self.prompt_preview: str = ""
            self.cwd: typing.Optional[str] = None
            self.codex_home: typing.Optional[str] = None
            self.command_preview: typing.Any = None
            self.started_at: typing.Optional[float] = None
            self.finished_at: typing.Optional[float] = None
            self.exit_code: typing.Optional[int] = None
            self.stop_reason: typing.Optional[str] = None
            self.stream_index: int = 0

            self.out_ring: deque[str] = deque(maxlen=60)
            self.lb_stdout: LineBuffer = LineBuffer(treat_cr_as_newline=True)
            self.lb_stderr: LineBuffer = LineBuffer(treat_cr_as_newline=True)

            self.lock: asyncio.Lock = asyncio.Lock()

        self.__initialized = True

    @property
    def prefix(self) -> str:
        """返回底层 CLI 前缀名，供统一命令构造复用。"""
        return self.__prefix

    def push(self, source: str, text: str) -> None:
        """把一行输出写入环形缓冲，并同步打印到日志。"""
        line = str(text or "").strip()
        if not line:
            return None
        self.out_ring.append(f"{source}: {line}")
        logger.info(f"{source} | {clip_text(line, limit=1000)}")

    def _active(self) -> bool:
        """判断当前是否存在仍在运行的 codex 子进程。"""
        return bool(self.__transports and self.__transports.returncode is None)

    @staticmethod
    def _prompt_preview(prompt: str, limit: int = 280) -> str:
        """生成适合日志与状态展示的提示词摘要。"""
        return clip_text(" ".join(str(prompt or "").split()), limit=limit)

    @staticmethod
    def _build_codex_exec_cmd(
        *,
        prompt: str,
        workdir: str,
        profile: typing.Optional[str] = None,
        model: typing.Optional[str] = None,
        sandbox: typing.Optional[str] = None,
        skip_git_repo_check: bool = True,
        ephemeral: bool = False,
        json_output: bool = False,
        extra_args: typing.Optional[list[str]] = None
    ) -> list[str]:
        """把启动参数组装成 `codex exec` 的参数数组。"""
        cmd = ["exec", prompt, "-C", workdir, "--color", "never"]

        if profile:
            cmd += ["-p", profile]
        if model:
            cmd += ["-m", model]
        if sandbox:
            cmd += ["-s", sandbox]
        if skip_git_repo_check:
            cmd.append("--skip-git-repo-check")
        if ephemeral:
            cmd.append("--ephemeral")
        if json_output:
            cmd.append("--json")

        for item in extra_args or []:
            token = str(item or "").strip()
            if token:
                cmd.append(token)

        return cmd

    @staticmethod
    def _build_codex_env(*, workdir: str) -> dict[str, str]:
        """复制当前环境变量，并补齐与执行目录相关的上下文。"""
        env = os.environ.copy()

        env["PWD"] = str(workdir)
        return env

    def _snapshot_unlocked(self) -> dict[str, typing.Any]:
        """在调用方已持锁时，读取当前会话的完整状态快照。"""
        proc = self.__transports
        active = bool(proc and proc.returncode is None)
        return {
            "ok"             : True,
            "active"         : active,
            "session_id"     : self.session_id,
            "pid"            : getattr(proc, "pid", None),
            "cwd"            : self.cwd,
            "codex_home"     : self.codex_home,
            "prompt_preview" : self.prompt_preview,
            "command"        : self.command_preview,
            "started_at"     : self.started_at,
            "finished_at"    : self.finished_at,
            "exit_code"      : self.exit_code if not active else None,
            "stop_reason"    : self.stop_reason,
            "last_lines"     : list(self.out_ring)[-20:]
        }

    async def snapshot(self) -> dict[str, typing.Any]:
        """返回对外可见的编码会话快照。"""
        async with self.lock:
            return {
                self.agent_id: self._snapshot_unlocked()
            }

    async def emit_output(
        self,
        source: str,
        text: str,
        output_callback: typing.Optional[
            typing.Callable[[str, str, int], typing.Awaitable[None]]
        ] = None
    ) -> None:
        """将单行输出按递增序号回推给上层进度回调。"""
        if output_callback is None:
            return None

        async with self.lock:
            self.stream_index += 1
            stream_index = self.stream_index

        await output_callback(source, text, stream_index)

    async def streaming(
        self,
        source: str,
        stream: typing.Optional[typing.AsyncIterator[bytes]],
        output_callback: typing.Optional[
            typing.Callable[[str, str, int], typing.Awaitable[None]]
        ] = None
    ) -> None:
        """持续消费 stdout/stderr 流，拆行为日志并转发进度。"""
        if stream is None:
            return None

        lb = self.lb_stdout if source.endswith(".stdout") else self.lb_stderr
        lb.reset()

        async for chunk in stream:
            text = chunk.decode(const.CHARSET, const.IGNORE)
            text = ANSI_RE.sub("", text)

            for line in lb.feed(text):
                self.push(source, line)
                await self.emit_output(source, line, output_callback=output_callback)

        for line in lb.flush():
            self.push(source, line)
            await self.emit_output(source, line, output_callback=output_callback)

    async def shutdown(self, reason: str = "user_stop") -> dict[str, typing.Any]:
        """停止当前会话并等待输出消费完成，返回停机后的状态。"""
        async with self.lock:
            proc = self.__transports
            active = self._active()
            if not proc or not active:
                return {
                    "text"        : f"{self.agent_id.capitalize()}当前没有运行中的编码会话。",
                    "attachments" : [],
                    "data"        : self._snapshot_unlocked(),
                    "logs"        : list(self.out_ring)[-20:]
                }

            self.stop_reason = reason

        try:
            proc.terminate()
        except ProcessLookupError:
            pass

        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()

        tasks = [task for task in (self.stdout_task, self.stderr_task) if task]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        async with self.lock:
            self.finished_at = time.time()
            self.exit_code = int(proc.returncode or 0)
            snapshot = self._snapshot_unlocked()

        return {
            "text"        : f"{self.agent_id} 已停止当前编码会话。",
            "attachments" : [],
            "data"        : snapshot,
            "logs"        : list(self.out_ring)[-20:]
        }

    async def start(
        self,
        *,
        prompt: str,
        profile: typing.Optional[str] = None,
        model: typing.Optional[str] = None,
        sandbox: typing.Optional[str] = "workspace-write",
        skip_git_repo_check: bool = True,
        ephemeral: bool = False,
        json_output: bool = False,
        timeout_sec: typing.Optional[int] = 300,
        extra_args: typing.Optional[list[str]] = None,
        output_callback: typing.Optional[
            typing.Callable[[str, str, int], typing.Awaitable[None]]
        ] = None
    ) -> dict[str, typing.Any]:
        """启动一次新的 codex 会话，并返回最小启动结果。"""
        prompt_text = str(prompt or "").strip()
        if not prompt_text:
            return {
                "text"        : "codex 启动失败：prompt 为空。",
                "attachments" : [],
                "data": {
                    "ok"     : False,
                    "active" : False,
                    "reason" : "prompt_empty"
                },
                "logs": []
            }

        final_workdir = str(os.getcwd())

        cmd = self._build_codex_exec_cmd(
            prompt=prompt_text,
            workdir=final_workdir,
            profile=profile,
            model=model,
            sandbox=sandbox,
            skip_git_repo_check=skip_git_repo_check,
            ephemeral=ephemeral,
            json_output=json_output,
            extra_args=extra_args
        )
        env = self._build_codex_env(workdir=final_workdir)
        launch_cmd = CodexRuntimeResolver.resolve_command(cmd, env=env)
        logger.debug(
            "coding start prepare "
            f"workdir={clip_text(final_workdir, 160)} "
            f"prompt={clip_text(self._prompt_preview(prompt_text), 160)} "
            f"codex_home={clip_text(str(env.get('CODEX_HOME', '')), 160)} "
            f"pwd={clip_text(str(env.get('PWD', '')), 160)} "
            f"path_head={clip_text(str(env.get('PATH', ''))[:240], 240)} "
            f"cmd={summarize_command(launch_cmd)}"
        )

        async with self.lock:
            if self._active():
                return {
                    "text"        : "codex 启动失败：已有编码会话在运行，不能重复启动。",
                    "attachments" : [],
                    "data": {
                        "ok"         : False,
                        "active"     : True,
                        "reason"     : "session_active",
                        "session_id" : self.session_id
                    },
                    "logs": []
                }

            self.out_ring.clear()

            self.session_id      = f"codex_{time.strftime('%Y%m%d%H%M%S')}"
            self.prompt_preview  = self._prompt_preview(prompt_text)
            self.cwd             = final_workdir
            self.codex_home      = env.get("CODEX_HOME")
            self.command_preview = summarize_command(launch_cmd)
            self.started_at      = time.time()
            self.finished_at     = None
            self.exit_code       = None
            self.stop_reason     = None
            self.stream_index    = 0
            self.wait_result     = None

            try:
                self.__transports = await Flux.cmd_link_exec(
                    launch_cmd,
                    cwd=final_workdir,
                    env=env,
                    stdin=asyncio.subprocess.DEVNULL
                )
            except Exception as e:
                logger.exception(
                    "coding start spawn failed "
                    f"session_id={self.session_id} "
                    f"workdir={clip_text(final_workdir, 160)} "
                    f"cmd={self.command_preview} "
                    f"error={type(e).__name__}: {e}"
                )
                raise
            proc = self.__transports
            self.stdout_task = asyncio.create_task(
                self.streaming(
                    "codex.stdout", proc.stdout, output_callback=output_callback
                )
            )
            self.stderr_task = asyncio.create_task(
                self.streaming(
                    "codex.stderr", proc.stderr, output_callback=output_callback
                )
            )

        logger.info(
            f"coding begin cli=codex session_id={self.session_id} "
            f"pid={proc.pid} cmd={self.command_preview}"
        )

        self.wait_task = asyncio.create_task(self._wait_impl(timeout_sec=timeout_sec))

        return {
            "text"        : f"codex 启动成功。pid={proc.pid}",
            "attachments" : [],
            "data": {
                "ok"         : True,
                "active"     : True,
                "session_id" : self.session_id,
                "pid"        : proc.pid,
                "cwd"        : self.cwd
            },
            "logs": []
        }

    async def _wait_impl(
        self,
        timeout_sec: typing.Optional[int] = None
    ) -> dict[str, typing.Any]:
        """等待底层进程结束，汇总最终状态、退出码与日志。"""
        proc = self.__transports
        if not proc:
            return {
                "text"        : f"{self.agent_id} 当前没有运行中的编码会话。",
                "attachments" : [],
                "data": {
                    "ok"     : False,
                    "active" : False
                },
                "logs": []
            }

        timed_out = False
        try:
            if timeout_sec is not None and int(timeout_sec) > 0:
                await asyncio.wait_for(proc.wait(), timeout=float(timeout_sec))
            else:
                await proc.wait()
        except asyncio.TimeoutError:
            timed_out = True
            await self.shutdown(reason="timeout")

        await asyncio.gather(
            *(task for task in (self.stdout_task, self.stderr_task) if task),
            return_exceptions=True
        )

        async with self.lock:
            self.finished_at = time.time()
            self.exit_code = int(proc.returncode or 0)
            snapshot = self._snapshot_unlocked()
            snapshot["timed_out"] = timed_out

        stopped = bool(snapshot.get("stop_reason")) and (not timed_out)
        ok = (snapshot["exit_code"] == 0) and (not timed_out) and (not stopped)
        snapshot["ok"] = ok

        text = (
            f"codex 执行完成。exit_code={snapshot['exit_code']}"
            if ok else
            f"codex 执行失败。exit_code={snapshot['exit_code']}"
        )

        if timed_out:
            text = f"codex 执行超时并已停止。exit_code={snapshot['exit_code']}"
        elif stopped:
            text = (
                f"codex 执行已停止。"
                f"exit_code={snapshot['exit_code']} reason={snapshot['stop_reason']}"
            )

        result = SemanticResult.from_text(
            text=text,
            data=snapshot,
            logs=list(self.out_ring)
        )
        result_dict = result.to_dict()

        async with self.lock:
            self.wait_result = result_dict
            self.wait_task = None

        return result_dict

    async def wait(
        self,
        timeout_sec: typing.Optional[int] = None
    ) -> dict[str, typing.Any]:
        """等待当前会话完成；若已有缓存结果则直接复用。"""
        async with self.lock:
            if self.wait_result is not None and not self._active():
                return self.wait_result

            task = self.wait_task
            if task is None:
                proc = self.__transports
                if not proc:
                    return {
                        "text"        : f"{self.agent_id} 当前没有运行中的编码会话。",
                        "attachments" : [],
                        "data": {
                            "ok"     : False,
                            "active" : False
                        },
                        "logs": []
                    }
                task = asyncio.create_task(self._wait_impl(timeout_sec=timeout_sec))
                self.wait_task = task

        return await task

    @staticmethod
    def with_flow_details(
        *,
        start_result: dict,
        final_result: dict
    ) -> dict[str, typing.Any]:
        """把 codex 启动结果与最终结果合并成包含流程摘要的返回结构。"""
        start_data = start_result.get("data") if isinstance(start_result, dict) else {}
        final_data = final_result.get("data") if isinstance(final_result, dict) else {}
        logs = final_result.get("logs") if isinstance(final_result.get("logs"), list) else []
        last_lines = final_data.get("last_lines") if isinstance(final_data.get("last_lines"), list) else []
        flow = [
            "1. codex exec 已启动",
            f"2. session_id={start_data.get('session_id')} pid={start_data.get('pid')} cwd={start_data.get('cwd')}",
            "3. 已持续消费 stdout/stderr 并等待进程结束",
            f"4. exit_code={final_data.get('exit_code')} timed_out={bool(final_data.get('timed_out'))}"
        ]
        if final_data.get("stop_reason"):
            flow.append(f"5. stop_reason={final_data.get('stop_reason')}")

        merged = dict(final_result)
        merged_data = dict(final_data) if isinstance(final_data, dict) else {}
        merged_data["flow"] = flow
        merged_data["start"] = start_data
        merged_data["output_tail"] = last_lines or logs[-20:]
        merged["data"] = merged_data
        merged["logs"] = logs
        merged["text"] = "\n".join([
            str(final_result.get("text") or "codex 执行结束。"),
            "",
            "执行过程：",
            *flow,
            "",
            "最近输出：",
            *(merged_data["output_tail"][-20:] or ["<empty>"])
        ])
        return merged


if __name__ == '__main__':
    pass
