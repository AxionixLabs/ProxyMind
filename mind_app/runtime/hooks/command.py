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

MAX_HOOK_OUTPUT_BYTES = 256 * 1024


class HookCommandError(RuntimeError):
    """表示命令 Hook 启动、执行或输出解析失败。"""


@dataclass(frozen=True, slots=True)
class HookCommandOutput:
    """保存命令 Hook 返回的结构化输出。"""
    data: dict[str, typing.Any]
    stderr: str = ""


class HookCommandExecutor:
    """通过本地子进程执行命令 Hook。"""

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
        ).encode("utf-8")
        handler = definition.handler

        try:
            process = await asyncio.create_subprocess_shell(
                handler.command,
                cwd=str(payload.get("cwd") or "") or None,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **self._process_group_options(),
            )
        except (OSError, ValueError) as error:
            raise HookCommandError(f"hook command could not start: {error}") from error

        stdout_task = asyncio.create_task(
            self._read_limited(process.stdout),
            name="hook stdout",
        )

        stderr_task = asyncio.create_task(
            self._read_limited(process.stderr),
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

        stderr_text = stderr.decode("utf-8", errors="replace").strip()
        if return_code != 0:
            raise HookCommandError(
                f"hook command exited with code {return_code}"
            )

        stdout_text = stdout.decode("utf-8", errors="strict").strip()
        if not stdout_text:
            return HookCommandOutput(data={}, stderr=stderr_text)

        try:
            data = json.loads(stdout_text)
        except (UnicodeError, json.JSONDecodeError) as error:
            raise HookCommandError(f"hook output is not valid JSON: {error}") from error
        if not isinstance(data, dict):
            raise HookCommandError("hook output must be a JSON object")
        return HookCommandOutput(data=data, stderr=stderr_text)

    @staticmethod
    async def _wait_for_process(
        process: asyncio.subprocess.Process,
        stdout_task: "asyncio.Task[bytes]",
        stderr_task: "asyncio.Task[bytes]",
    ) -> tuple[int, bytes, bytes]:
        """等待进程和输出读取任务全部结束。"""
        return_code, stdout, stderr = await asyncio.gather(
            process.wait(),
            stdout_task,
            stderr_task,
        )
        return int(return_code), stdout, stderr

    @staticmethod
    async def _read_limited(
        stream: asyncio.StreamReader | None,
    ) -> bytes:
        """读取有界命令输出。"""
        if stream is None:
            return b""

        output = bytearray()
        while chunk := await stream.read(65536):
            output.extend(chunk)
            if len(output) > MAX_HOOK_OUTPUT_BYTES:
                raise HookCommandError(
                    f"hook output exceeds {MAX_HOOK_OUTPUT_BYTES} bytes"
                )
        return bytes(output)

    @staticmethod
    async def _terminate(
        process: asyncio.subprocess.Process,
        *tasks: "asyncio.Task[typing.Any]",
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
