# -*- coding: utf-8 -*-
# Notes: ==== Mind(TM) ====

import asyncio
import contextlib
import uuid
from dataclasses import (
    dataclass,
    field,
)
from pathlib import Path

from agent.ports.capabilities import SandboxMode
from agent.ports.javascript import (
    JavaScriptExecution,
    JavaScriptExecutionError,
    JavaScriptFailureKind,
    NestedToolDispatch,
)
from agent.protocol.json_value import ThawedJsonValue
from infrastructure.sidecars.javascript.bundle import JavaScriptBundle
from infrastructure.sidecars.javascript.process import JavaScriptSidecarProcess
from infrastructure.sidecars.javascript.protocol import (
    EmitImageMessage,
    ExecResultMessage,
    KernelMessage,
    RunToolMessage,
    encode_exec,
    encode_image_result,
    encode_tool_result,
    parse_arguments,
)

SUPPORTED_IMAGE_TYPES = {
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/webp",
}


@dataclass(slots=True)
class _ExecutionContext:
    """保存单次 Cell 的 delegate 和附件状态。"""

    call_tool: NestedToolDispatch
    attachments: list[dict[str, ThawedJsonValue]] = field(default_factory=list)
    request_tasks: set[asyncio.Task[None]] = field(default_factory=set)


@dataclass(frozen=True, slots=True)
class _ExecutionRequest:
    """关联一次 Cell、完成信号和 delegate 上下文。"""

    request_id: str
    future: asyncio.Future[ExecResultMessage]
    context: _ExecutionContext


class JavaScriptSidecarSession:
    """串行管理一个安全信封内的 JavaScript Kernel 会话。"""

    def __init__(
        self,
        *,
        cwd: Path,
        session_id: str,
        access_mode: SandboxMode,
        bundle: JavaScriptBundle,
        configured_node_path: str | None = None,
    ) -> None:
        """创建尚未启动进程的惰性 Session。"""
        self.cwd = cwd.resolve()
        self.session_id = session_id
        self.access_mode = access_mode
        self._process = JavaScriptSidecarProcess(
            cwd=self.cwd,
            session_id=session_id,
            access_mode=access_mode,
            bundle=bundle,
            configured_node_path=configured_node_path,
        )
        self._execution_lock = asyncio.Lock()
        self._reader_task: asyncio.Task[None] | None = None
        self._active_request: _ExecutionRequest | None = None
        self._closed = False

    async def execute(
        self,
        code: str,
        *,
        timeout_ms: int,
        call_tool: NestedToolDispatch,
    ) -> JavaScriptExecution:
        """在当前 Session 中串行执行一个 Cell。"""
        if self._closed:
            raise JavaScriptExecutionError(
                JavaScriptFailureKind.UNAVAILABLE,
                "JavaScript sidecar session is closed",
            )
        if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or timeout_ms < 0:
            raise JavaScriptExecutionError(
                JavaScriptFailureKind.RUNTIME_ERROR,
                "js_repl timeout_ms must be a non-negative integer",
            )

        async with self._execution_lock:
            if self._closed:
                raise JavaScriptExecutionError(
                    JavaScriptFailureKind.UNAVAILABLE,
                    "JavaScript sidecar session is closed",
                )
            try:
                await self._ensure_kernel()
            except asyncio.CancelledError:
                await asyncio.shield(self._reset_kernel())
                raise

            request_id = _request_id()
            request = _ExecutionRequest(
                request_id=request_id,
                future=asyncio.get_running_loop().create_future(),
                context=_ExecutionContext(call_tool=call_tool),
            )
            self._active_request = request
            try:
                try:
                    await self._process.write(encode_exec(
                        request_id=request_id,
                        code=code,
                        timeout_ms=timeout_ms,
                    ))
                    message = await asyncio.wait_for(
                        asyncio.shield(request.future),
                        timeout_ms / 1000,
                    )
                except asyncio.TimeoutError as error:
                    request.future.cancel()
                    await self._reset_kernel()
                    raise JavaScriptExecutionError(
                        JavaScriptFailureKind.EXECUTION_TIMEOUT,
                        "js_repl execution timed out; kernel reset, rerun your request",
                    ) from error
                except JavaScriptExecutionError:
                    request.future.cancel()
                    await self._reset_kernel()
                    raise
            except asyncio.CancelledError:
                request.future.cancel()
                await asyncio.shield(self._reset_kernel())
                raise
            finally:
                if self._active_request is request:
                    self._active_request = None
                await self._cancel_request_tasks(request.context)

            if not message.ok:
                raise JavaScriptExecutionError(
                    JavaScriptFailureKind.RUNTIME_ERROR,
                    message.error or "js_repl execution failed",
                )
            return JavaScriptExecution(
                output=message.output,
                attachments=tuple(request.context.attachments),
            )

    async def reset(self) -> None:
        """清空 Kernel 上下文并保留可惰性重启的 Session。"""
        async with self._execution_lock:
            if self._closed:
                raise JavaScriptExecutionError(
                    JavaScriptFailureKind.UNAVAILABLE,
                    "JavaScript sidecar session is closed",
                )
            await self._reset_kernel()

    async def close(self) -> None:
        """关闭 Session 及其进程、任务和临时目录。"""
        if self._closed:
            return
        self._closed = True
        async with self._execution_lock:
            await self._reset_kernel()
            await self._process.close()

    async def _ensure_kernel(self) -> None:
        """惰性启动 Kernel 和当前 Session 的唯一读取任务。"""
        if self._process.running:
            reader_task = self._reader_task
            if reader_task is not None and not reader_task.done():
                return
        previous_reader = self._reader_task
        if previous_reader is not None:
            await asyncio.gather(previous_reader, return_exceptions=True)
        await self._process.start()
        self._reader_task = asyncio.create_task(
            self._read_messages(),
            name="javascript sidecar stdout",
        )

    async def _read_messages(self) -> None:
        """读取、校验并分派当前进程的 Kernel 消息。"""
        failure: JavaScriptExecutionError | None = None
        cancelled = False
        try:
            while True:
                message = await self._process.read()
                if message is None:
                    break
                await self._dispatch_message(message)
        except asyncio.CancelledError:
            cancelled = True
            raise
        except JavaScriptExecutionError as error:
            failure = error
        finally:
            if self._reader_task is asyncio.current_task():
                self._reader_task = None
            if not cancelled:
                request = self._active_request
                if request is not None:
                    if failure is not None:
                        await self._cancel_request_tasks(request.context)
                    else:
                        await self._wait_for_request_tasks(request.context)

                process = self._process.process
                if failure is None and process is not None and process.returncode is None:
                    await self._process.wait()
                exit_code = process.returncode if process is not None else None
                exit_detail = self._process.stderr_detail
                await self._process.terminate()

                if failure is None:
                    detail = f"js_repl kernel exited unexpectedly (exit_code={exit_code})"
                    if exit_detail:
                        detail = f"{detail}: {exit_detail}"
                    failure = JavaScriptExecutionError(
                        JavaScriptFailureKind.UNAVAILABLE,
                        detail,
                    )
                self._fail_pending(failure)

    async def _dispatch_message(self, message: KernelMessage) -> None:
        """将已验证消息关联到当前 execution。"""
        if isinstance(message, ExecResultMessage):
            request = self._active_request
            if request is None or request.request_id != message.request_id:
                raise JavaScriptExecutionError(
                    JavaScriptFailureKind.PROTOCOL_ERROR,
                    "JavaScript sidecar returned an unrecognized execution result",
                )
            await self._wait_for_request_tasks(request.context)
            if not request.future.done():
                request.future.set_result(message)
            return

        request = self._active_request
        if request is None or request.request_id != message.execution_id:
            await self._reply_missing_context(message)
            return
        context = request.context
        if isinstance(message, RunToolMessage):
            task = asyncio.create_task(
                self._handle_tool_request(message, context),
                name="javascript nested tool",
            )
        else:
            task = asyncio.create_task(
                self._handle_image_request(message, context),
                name="javascript image attachment",
            )
        context.request_tasks.add(task)
        task.add_done_callback(context.request_tasks.discard)

    async def _handle_tool_request(
        self,
        message: RunToolMessage,
        context: _ExecutionContext,
    ) -> None:
        """执行 Kernel 请求的嵌套工具并返回现有 wire 结果。"""
        try:
            if message.tool_name in {"js_repl", "js_repl_reset"}:
                raise JavaScriptExecutionError(
                    JavaScriptFailureKind.RUNTIME_ERROR,
                    "js_repl cannot invoke itself",
                )
            arguments = parse_arguments(message.arguments_json)
            response = await context.call_tool(
                message.tool_name,
                arguments,
                message.request_id,
            )
            payload = encode_tool_result(
                request_id=message.request_id,
                ok=True,
                response=response,
                error=None,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            payload = encode_tool_result(
                request_id=message.request_id,
                ok=False,
                response=None,
                error=str(error).strip() or type(error).__name__,
            )
        with contextlib.suppress(JavaScriptExecutionError):
            await self._process.write(payload)

    async def _handle_image_request(
        self,
        message: EmitImageMessage,
        context: _ExecutionContext,
    ) -> None:
        """校验并记录 Kernel 显式发出的图片附件。"""
        try:
            context.attachments.append(_image_attachment(
                message.image_url,
                message.detail,
            ))
            payload = encode_image_result(
                request_id=message.request_id,
                ok=True,
                error=None,
            )
        except Exception as error:
            payload = encode_image_result(
                request_id=message.request_id,
                ok=False,
                error=str(error).strip() or type(error).__name__,
            )
        with contextlib.suppress(JavaScriptExecutionError):
            await self._process.write(payload)

    async def _reply_missing_context(
        self,
        message: RunToolMessage | EmitImageMessage,
    ) -> None:
        """拒绝已离开活动 Cell 的迟到 delegate。"""
        if isinstance(message, RunToolMessage):
            payload = encode_tool_result(
                request_id=message.request_id,
                ok=False,
                response=None,
                error="js_repl exec context not found",
            )
        else:
            payload = encode_image_result(
                request_id=message.request_id,
                ok=False,
                error="js_repl exec context not found",
            )
        with contextlib.suppress(JavaScriptExecutionError):
            await self._process.write(payload)

    async def _reset_kernel(self) -> None:
        """终止进程并收束当前 request、reader 和 delegate。"""
        request = self._active_request
        if request is not None:
            await self._cancel_request_tasks(request.context)
            self._fail_pending(JavaScriptExecutionError(
                JavaScriptFailureKind.CANCELLED,
                "js_repl kernel reset",
            ))

        reader_task = self._reader_task
        self._reader_task = None
        if reader_task is not None and reader_task is not asyncio.current_task():
            reader_task.cancel()
            await asyncio.gather(reader_task, return_exceptions=True)
        await self._process.terminate()

    def _fail_pending(self, error: JavaScriptExecutionError) -> None:
        """以具名失败收束当前执行请求。"""
        request = self._active_request
        if request is not None and not request.future.done():
            request.future.set_exception(error)

    @staticmethod
    async def _cancel_request_tasks(context: _ExecutionContext) -> None:
        """取消并回收一次 execution 的全部 delegate task。"""
        tasks = tuple(context.request_tasks)
        context.request_tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    @staticmethod
    async def _wait_for_request_tasks(context: _ExecutionContext) -> None:
        """等待当前 execution 已发起的未等待 delegate 完成。"""
        while context.request_tasks:
            tasks = tuple(context.request_tasks)
            await asyncio.gather(*tasks, return_exceptions=True)
            context.request_tasks.difference_update(tasks)


def _request_id() -> str:
    """生成仅在当前进程协议内使用的 execution 标识。"""
    return str(uuid.uuid4())


def _image_attachment(
    image_url: str,
    detail: str | None,
) -> dict[str, ThawedJsonValue]:
    """校验 data URL 并构造图片附件。"""
    if not image_url.lower().startswith("data:"):
        raise JavaScriptExecutionError(
            JavaScriptFailureKind.RUNTIME_ERROR,
            "host.emitImage only accepts data URLs",
        )
    header, separator, _ = image_url.partition(",")
    mime_type = header[5:].split(";", 1)[0].lower() if separator else ""
    if mime_type not in SUPPORTED_IMAGE_TYPES:
        raise JavaScriptExecutionError(
            JavaScriptFailureKind.RUNTIME_ERROR,
            "host.emitImage only supports image/png, image/jpeg, image/webp, or image/gif",
        )
    if detail is not None and detail not in {"auto", "low", "high", "original"}:
        raise JavaScriptExecutionError(
            JavaScriptFailureKind.RUNTIME_ERROR,
            "host.emitImage received an invalid image detail",
        )
    return {
        "kind": "image",
        "mime_type": mime_type,
        "data_url": image_url,
        "detail": detail or "high",
    }


if __name__ == "__main__":
    pass
