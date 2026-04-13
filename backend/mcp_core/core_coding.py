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
from backend.models.model_device import SemanticResult
from backend.utilities import const
from backend.utilities.process import Flux
from backend.utilities.trace import (
    clip_text,
    summarize_command,
)
from backend.utilities.validation import marked

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


class Coding(object):
    """编码类 CLI 适配器。"""

    __instance: typing.Optional["Coding"] = None
    __initialized: bool = False

    def __new__(cls, *args, **kwargs):
        if not cls.__instance:
            cls.__instance = super(Coding, cls).__new__(cls)
        return cls.__instance

    def __init__(self):
        if not self.__initialized:
            self.__prefix: str = "coding"
            self.agent_id: str = self.__prefix

            self.__transports: typing.Optional[asyncio.subprocess.Process] = None
            self.stdout_task: typing.Optional[asyncio.Task[None]] = None
            self.stderr_task: typing.Optional[asyncio.Task[None]] = None
            self.wait_task: typing.Optional[asyncio.Task[dict[str, typing.Any]]] = None
            self.wait_result: typing.Optional[dict[str, typing.Any]] = None

            self.provider: typing.Optional[str] = None
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
        return self.__prefix

    def _active(self) -> bool:
        return bool(self.__transports and self.__transports.returncode is None)

    @staticmethod
    def _prompt_preview(prompt: str, limit: int = 280) -> str:
        return clip_text(" ".join(str(prompt or "").split()), limit=limit)

    @staticmethod
    def _build_codex_exec_cmd(
        *,
        prompt: str,
        workdir: str,
        profile: typing.Optional[str] = None,
        model: typing.Optional[str] = None,
        sandbox: typing.Optional[str] = None,
        full_auto: bool = True,
        skip_git_repo_check: bool = False,
        ephemeral: bool = False,
        json_output: bool = False,
        extra_args: typing.Optional[list[str]] = None
    ) -> list[str]:
        cmd = ["codex", "exec", prompt, "-C", workdir, "--color", "never"]

        if profile:
            cmd += ["-p", profile]
        if model:
            cmd += ["-m", model]
        if sandbox:
            cmd += ["-s", sandbox]
        if full_auto:
            cmd.append("--full-auto")
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
    def _build_provider_env(*, provider: str, workdir: str) -> dict[str, str]:
        env = os.environ.copy()

        env["PWD"] = str(workdir)
        return env

    def _snapshot_unlocked(self) -> dict[str, typing.Any]:
        proc = self.__transports
        active = bool(proc and proc.returncode is None)
        return {
            "ok"             : True,
            "active"         : active,
            "provider"       : self.provider,
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
        async with self.lock:
            return {
                self.agent_id: self._snapshot_unlocked()
            }

    def push(self, source: str, text: str) -> None:
        line = str(text or "").strip()
        if not line:
            return None
        self.out_ring.append(f"{source}: {line}")
        logger.info(f"{source} | {clip_text(line, limit=1000)}")

    async def emit_output(
        self,
        source: str,
        text: str,
        output_callback: typing.Optional[
            typing.Callable[[str, str, int], typing.Awaitable[None]]
        ] = None
    ) -> None:
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
        provider: str,
        prompt: str,
        workdir: typing.Optional[str] = None,
        profile: typing.Optional[str] = None,
        model: typing.Optional[str] = None,
        sandbox: typing.Optional[str] = "workspace-write",
        full_auto: bool = True,
        skip_git_repo_check: bool = False,
        ephemeral: bool = False,
        json_output: bool = False,
        timeout_sec: typing.Optional[int] = 300,
        extra_args: typing.Optional[list[str]] = None,
        output_callback: typing.Optional[
            typing.Callable[[str, str, int], typing.Awaitable[None]]
        ] = None
    ) -> dict[str, typing.Any]:
        provider_name = str(provider or "").strip().lower()
        if provider_name != "codex":
            raise marked.fail_tip(
                "provider 当前只支持 codex。",
                code=const.CODE_EXC,
                hint=const.HINT_HLT,
                provider=provider
            )

        prompt_text = str(prompt or "").strip()
        if not prompt_text:
            raise marked.fail_tip(
                "prompt 为空。",
                code=const.CODE_EXC,
                hint=const.HINT_HLT,
                field="prompt",
                expect="non_empty",
                got=prompt
            )

        final_workdir = marked.ensure_d(workdir, "workdir") if workdir else str(os.getcwd())
        cmd = self._build_codex_exec_cmd(
            prompt=prompt_text,
            workdir=final_workdir,
            profile=profile,
            model=model,
            sandbox=sandbox,
            full_auto=full_auto,
            skip_git_repo_check=skip_git_repo_check,
            ephemeral=ephemeral,
            json_output=json_output,
            extra_args=extra_args
        )
        env = self._build_provider_env(
            provider=provider_name,
            workdir=final_workdir
        )

        async with self.lock:
            if self._active():
                raise marked.fail_tip(
                    "已有编码会话在运行，不能重复启动。",
                    code=const.CODE_EXC,
                    hint=const.HINT_HLT,
                    provider=self.provider,
                    session_id=self.session_id
                )

            self.out_ring.clear()
            self.provider = provider_name
            self.session_id = f"{provider_name}_{time.strftime('%Y%m%d%H%M%S')}"
            self.prompt_preview = self._prompt_preview(prompt_text)
            self.cwd = final_workdir
            self.codex_home = env.get("CODEX_HOME")
            self.command_preview = summarize_command(cmd)
            self.started_at = time.time()
            self.finished_at = None
            self.exit_code = None
            self.stop_reason = None
            self.stream_index = 0
            self.wait_result = None

            self.__transports = await Flux.cmd_link_exec(
                cmd,
                cwd=final_workdir,
                env=env
            )
            proc = self.__transports
            self.stdout_task = asyncio.create_task(
                self.streaming(
                    f"{provider_name}.stdout",
                    proc.stdout,
                    output_callback=output_callback
                )
            )
            self.stderr_task = asyncio.create_task(
                self.streaming(
                    f"{provider_name}.stderr",
                    proc.stderr,
                    output_callback=output_callback
                )
            )

        logger.info(
            f"coding begin provider={provider_name} session_id={self.session_id} "
            f"pid={proc.pid} cmd={self.command_preview}"
        )

        self.wait_task = asyncio.create_task(self._wait_impl(timeout_sec=timeout_sec))

        return {
            "text"        : f"{provider_name} 启动成功。pid={proc.pid}",
            "attachments" : [],
            "data"        : self._snapshot_unlocked(),
            "logs"        : []
        }

    async def _wait_impl(
        self,
        timeout_sec: typing.Optional[int] = None
    ) -> dict[str, typing.Any]:
        proc = self.__transports
        if not proc:
            return {
                "text"        : f"{self.agent_id.capitalize()}当前没有运行中的编码会话。",
                "attachments" : [],
                "data"        : {"ok": False, "active": False},
                "logs"        : []
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
            f"{self.provider} 执行完成。exit_code={snapshot['exit_code']}"
            if ok else
            f"{self.provider} 执行失败。exit_code={snapshot['exit_code']}"
        )
        if timed_out:
            text = f"{self.provider} 执行超时并已停止。exit_code={snapshot['exit_code']}"
        elif stopped:
            text = (
                f"{self.provider} 执行已停止。"
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
        async with self.lock:
            if self.wait_result is not None and not self._active():
                return self.wait_result

            task = self.wait_task
            if task is None:
                proc = self.__transports
                if not proc:
                    return {
                        "text"        : f"{self.agent_id.capitalize()}当前没有运行中的编码会话。",
                        "attachments" : [],
                        "data"        : {"ok": False, "active": False},
                        "logs"        : []
                    }
                task = asyncio.create_task(self._wait_impl(timeout_sec=timeout_sec))
                self.wait_task = task

        return await task


if __name__ == '__main__':
    pass
