# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import json
import signal
import typing
import asyncio
import contextlib
import subprocess
from dataclasses import dataclass
from mind_core.hooks import HookDefinitionConfig
from mind_nova import const
from .output_spill import (
    CapturedHookOutput,
    HookOutputSpillStore
)

HOOK_BUSINESS_BLOCK_EXIT_CODE = 2
MAX_HOOK_DIAGNOSTIC_CHARS     = 8 * 1024


class HookCommandError(RuntimeError):
    """表示命令 Hook 启动、执行或输出解析失败。"""


@dataclass(frozen=True, slots=True)
class HookCommandOutput:
    """保存命令 Hook 返回的结构化输出。"""
    data: dict[str, typing.Any]
    stderr: str = ""
    business_block: bool = False
    block_reason: str = ""


class HookCommandExecutor:
    """通过本地子进程执行命令 Hook。"""

    def __init__(
        self,
        *,
        spill_store: HookOutputSpillStore | None = None
    ) -> None:
        self._spill_store = spill_store or HookOutputSpillStore()

    async def execute(
        self,
        definition: HookDefinitionConfig,
        payload: dict[str, typing.Any],
    ) -> HookCommandOutput:
        """向命令写入 JSON 并解析结构化输出。"""
        input_bytes = json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            default=str,
        ).encode(const.CHARSET)

        handler    = definition.handler
        session_id = str(payload.get("session_id") or "")

        try:
            process = await asyncio.create_subprocess_shell(
                handler.command_for_platform(os.name),
                cwd=str(payload.get("cwd") or "") or None,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **self._process_group_options(),
            )
        except (OSError, ValueError) as error:
            raise HookCommandError(f"hook command could not start: {error}") from error

        stdout_task = asyncio.create_task(
            self._spill_store.capture(
                process.stdout,
                session_id=session_id,
                channel="stdout",
            ),
            name="hook stdout",
        )

        stderr_task = asyncio.create_task(
            self._spill_store.capture(
                process.stderr,
                session_id=session_id,
                channel="stderr",
            ),
            name="hook stderr",
        )

        wait_task = asyncio.create_task(
            self._wait_for_process(process, stdout_task, stderr_task),
            name="hook process",
        )

        try:
            if process.stdin is not None:
                process.stdin.write(input_bytes)
                await process.stdin.drain()
                process.stdin.close()

            completed, _ = await asyncio.wait(
                (wait_task,),
                timeout=handler.timeout_sec,
            )
            if not completed:
                raise asyncio.TimeoutError
            return_code, stdout, stderr = await wait_task
        except asyncio.TimeoutError as error:
            await self._terminate(
                process,
                wait_task,
                stdout_task,
                stderr_task,
            )
            raise HookCommandError(
                f"hook command timed out after {handler.timeout_sec:g}s"
            ) from error
        except asyncio.CancelledError:
            await self._terminate(
                process,
                wait_task,
                stdout_task,
                stderr_task,
            )
            raise
        except BaseException:
            await self._terminate(
                process,
                wait_task,
                stdout_task,
                stderr_task,
            )
            raise

        stderr_text = stderr.text()
        spill_data  = self._spill_data(stdout, stderr)

        if return_code == HOOK_BUSINESS_BLOCK_EXIT_CODE:
            reason = stderr_text or stdout.text() or "hook command blocked execution"
            return HookCommandOutput(
                data=spill_data,
                stderr=stderr_text,
                business_block=True,
                block_reason=self._bounded_diagnostic(reason),
            )

        if return_code != 0:
            diagnostic = stderr_text or stdout.text()

            suffix = (
                f": {self._bounded_diagnostic(diagnostic)}"
                if diagnostic
                else ""
            )
            raise HookCommandError(
                f"hook command exited with code {return_code}{suffix}"
            )

        data = self._parse_stdout(stdout)
        data.update(spill_data)

        return HookCommandOutput(data=data, stderr=stderr_text)

    async def spill_context(
        self,
        text: str,
        *,
        session_id: str,
    ) -> str:
        """把过大的 Hook 上下文写入会话临时文件。"""
        spill = await self._spill_store.spill_text(
            text,
            session_id=session_id,
            channel="additional-context",
        )
        return spill.summary()

    async def cleanup_session(self, session_id: str) -> None:
        """清理指定会话产生的大输出临时文件。"""
        await self._spill_store.cleanup_session(session_id)

    async def close(self) -> None:
        """清理执行器持有的全部大输出临时文件。"""
        await self._spill_store.close()

    @staticmethod
    async def _wait_for_process(
        process: asyncio.subprocess.Process,
        stdout_task: "asyncio.Task[CapturedHookOutput]",
        stderr_task: "asyncio.Task[CapturedHookOutput]"
    ) -> tuple[int, CapturedHookOutput, CapturedHookOutput]:
        """等待进程和输出读取任务全部结束。"""
        return_code, stdout, stderr = await asyncio.gather(
            process.wait(),
            stdout_task,
            stderr_task,
        )
        return int(return_code), stdout, stderr

    @staticmethod
    def _parse_stdout(output: CapturedHookOutput) -> dict[str, typing.Any]:
        """把小输出解析为 JSON，把大输出或普通文本转换为上下文。"""
        stdout_text = output.text()
        if not stdout_text:
            return {}
        if output.spill is not None:
            return {"stdout": stdout_text}

        try:
            data = json.loads(stdout_text)
        except json.JSONDecodeError:
            return {"stdout": stdout_text}
        if not isinstance(data, dict):
            raise HookCommandError("hook output must be a JSON object")
        return data

    @staticmethod
    def _spill_data(
        stdout: CapturedHookOutput,
        stderr: CapturedHookOutput
    ) -> dict[str, typing.Any]:
        """返回 stdout 和 stderr 的结构化 spill 信息。"""
        spills = {
            channel: captured.spill.metadata()
            for channel, captured in (
                ("stdout", stdout),
                ("stderr", stderr),
            )
            if captured.spill is not None
        }
        return {"outputSpill": spills} if spills else {}

    @staticmethod
    def _bounded_diagnostic(value: str) -> str:
        """返回适合错误和审计记录的有界诊断文本。"""
        text = str(value or "").strip()
        if len(text) <= MAX_HOOK_DIAGNOSTIC_CHARS:
            return text
        return f"{text[:MAX_HOOK_DIAGNOSTIC_CHARS]}..."

    @staticmethod
    async def _terminate(
        process: asyncio.subprocess.Process,
        *tasks: "asyncio.Task[typing.Any]"
    ) -> None:
        """停止进程树并回收输出读取任务。"""
        if process.returncode is None:
            await HookCommandExecutor._terminate_process_tree(process)
        await process.wait()

        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    @staticmethod
    def _process_group_options() -> dict[str, typing.Any]:
        """返回当前平台创建独立 Hook 进程组所需的参数。"""
        if os.name == "nt":
            return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        return {"start_new_session": True}

    @staticmethod
    async def _terminate_process_tree(
        process: asyncio.subprocess.Process,
    ) -> None:
        """终止指定 Hook 进程及其派生进程。"""
        if os.name == "nt":
            break_signal = getattr(signal, "CTRL_BREAK_EVENT", None)
            if break_signal is not None:
                with contextlib.suppress(
                    ProcessLookupError,
                    RuntimeError,
                    ValueError,
                    OSError,
                ):
                    process.send_signal(break_signal)
                for _ in range(10):
                    if process.returncode is not None:
                        return None
                    await asyncio.sleep(0.05)

            try:
                killer = await asyncio.create_subprocess_exec(
                    "taskkill",
                    "/PID",
                    str(process.pid),
                    "/T",
                    "/F",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                killer_wait = asyncio.create_task(killer.wait())
                completed, _ = await asyncio.wait(
                    (killer_wait,),
                    timeout=2,
                )
                if not completed and killer.returncode is None:
                    killer.kill()
                await killer_wait
            except (OSError, RuntimeError, ValueError):
                pass
        else:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(process.pid, signal.SIGKILL)

        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.kill()


if __name__ == '__main__':
    pass
