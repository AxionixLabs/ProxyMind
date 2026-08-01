# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import json
import typing
import asyncio
from dataclasses import dataclass
from mind_core.hooks import (
    HookDefinitionConfig,
    HookEventName
)
from mind_nova import const
from mind_app.runtime.processes import (
    subprocess_process_group_kwargs,
    terminate_process_tree
)
from .output_spill import (
    CapturedHookOutput,
    HookOutputSpillStore
)

HOOK_BUSINESS_BLOCK_EXIT_CODE = 2
MAX_HOOK_DIAGNOSTIC_CHARS     = 8 * 1024

_PLAIN_STDOUT_CONTEXT_EVENTS = frozenset({
    "SessionStart",
    "UserPromptSubmit",
    "SubagentStart",
})

_JSON_STDOUT_EVENTS = frozenset({
    "Stop",
    "SubagentStop"
})

_BUSINESS_BLOCK_EVENTS = frozenset({
    "PreToolUse",
    "PermissionRequest",
    "PostToolUse",
    "UserPromptSubmit",
    "SubagentStop",
    "Stop",
})


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
                **subprocess_process_group_kwargs(),
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

        if (
            return_code == HOOK_BUSINESS_BLOCK_EXIT_CODE
            and definition.event in _BUSINESS_BLOCK_EVENTS
        ):
            if not stderr_text:
                raise HookCommandError(
                    f"{definition.event} hook exited with code 2 without a "
                    "reason on stderr"
                )
            return HookCommandOutput(
                data=spill_data,
                stderr=stderr_text,
                business_block=True,
                block_reason=self._bounded_diagnostic(stderr_text),
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

        data = self._parse_stdout(definition.event, stdout)
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
    def _parse_stdout(
        event: HookEventName,
        output: CapturedHookOutput
    ) -> dict[str, typing.Any]:
        """按事件语义解析命令 Hook 的 stdout。"""
        stdout_text = output.text()
        if not stdout_text:
            return {}

        if event == "SessionEnd":
            return {}

        if output.spill is not None:
            if event in _PLAIN_STDOUT_CONTEXT_EVENTS:
                return {"stdout": stdout_text}
            if event in _JSON_STDOUT_EVENTS:
                raise HookCommandError(f"{event} hook output must be JSON")

            return {}

        try:
            data = json.loads(stdout_text)
        except json.JSONDecodeError as error:
            looks_like_json = stdout_text.lstrip().startswith(("{", "["))
            if event in _PLAIN_STDOUT_CONTEXT_EVENTS and not looks_like_json:
                return {"stdout": stdout_text}
            if event in _JSON_STDOUT_EVENTS or looks_like_json:
                raise HookCommandError(
                    f"{event} hook returned invalid JSON output"
                ) from error

            return {}

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
            await terminate_process_tree(process, force=False)
        await process.wait()

        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == '__main__':
    pass
