# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import contextlib
import typing
import uuid
from dataclasses import dataclass

import anyio
from anyio.abc import (
    ObjectReceiveStream,
    ObjectSendStream,
)
from mcp import (
    ClientSession,
    types as mcp_types,
)
from mcp.shared.context import RequestContext
from mcp.shared.message import SessionMessage

from agent.domain.mcp_elicitation import (
    ElicitationResponse,
    McpInvocation,
)
from agent.ports.mcp_elicitation import McpElicitationHandler
from infrastructure.mcp.elicitation_schema import elicitation_request

BrowserOpener: typing.TypeAlias = typing.Callable[[str], typing.Awaitable[bool]]


@dataclass(slots=True)
class _Pending:
    """保留一个连接请求及其调用范围，取消只能影响这一请求。"""

    scope: str
    task: asyncio.Task[None]
    cancelled: bool = False


class ElicitationChannel(ObjectReceiveStream[SessionMessage | Exception]):
    """在 SDK 公开流边界分发交互，使接收循环继续读取取消、响应和断线。

    通道与连接同栈创建和关闭，任务不跨连接。单连接调用串行归属；请求结束后撤下表面，
    调用异常时关闭连接，避免缺少父调用 wire 字段的迟到请求被归入下次调用。
    """

    def __init__(
        self, read: ObjectReceiveStream[SessionMessage | Exception], write: ObjectSendStream[SessionMessage],
        *, server: str, handler: McpElicitationHandler | None, open_browser: BrowserOpener,
        disconnected: asyncio.Event, timeout_sec: float = 120,
    ) -> None:
        """绑定连接流、已组装交互端及浏览器能力，所有等待都有上限。"""
        self._read = read
        self._write = write
        self._server = server
        self._handler = handler
        self._open_browser = open_browser
        self._disconnected = disconnected
        self._timeout = timeout_sec
        self._call_lock = asyncio.Lock()
        self._invocation: McpInvocation | None = None
        self._scope = ""
        self._pending: dict[mcp_types.RequestId, _Pending] = {}
        self._early_cancellations: set[mcp_types.RequestId] = set()
        self._saturated = False
        self._closed = False

    async def __call__(self, context: RequestContext[ClientSession, None], params: mcp_types.ElicitRequestParams) -> mcp_types.ElicitResult:
        """向 SDK 声明两种已实现能力；实际交互由流边界分发，意外旁路明确拒绝。"""
        return mcp_types.ElicitResult(action="decline")

    @contextlib.asynccontextmanager
    async def invocation(self, invocation: McpInvocation | None) -> typing.AsyncIterator[None]:
        """串行绑定一次调用，在成功、异常和取消时收束所属未决输入。"""
        if self._handler is None:
            yield
            return
        async with self._call_lock:
            if self._closed or self._disconnected.is_set():
                raise anyio.ClosedResourceError
            scope = uuid.uuid4().hex
            self._scope, self._invocation = scope, invocation
            try:
                yield
            except BaseException:
                self._disconnected.set()
                raise
            finally:
                self._invocation, self._scope = None, ""
                tasks = tuple(item.task for item in self._pending.values() if item.scope == scope)
                for task in tasks:
                    task.cancel()
                with anyio.CancelScope(shield=True):
                    await asyncio.gather(*tasks, return_exceptions=True)

    async def receive(self) -> SessionMessage | Exception:
        """截取交互及其取消，其余消息原样进入 SDK；不让等待用户阻塞接收。"""
        while True:
            message = await self._read.receive()
            if isinstance(message, Exception):
                return message
            root = message.message.root
            if isinstance(root, mcp_types.JSONRPCNotification) and root.method == "notifications/cancelled":
                try:
                    notification = mcp_types.CancelledNotification.model_validate(root.model_dump(by_alias=True))
                except ValueError:
                    self._disconnected.set()
                    raise anyio.BrokenResourceError from None
                key = notification.params.requestId
                pending = self._pending.get(key)
                if pending is not None:
                    if pending.cancelled:
                        continue
                    pending.cancelled = True
                    pending.task.cancel()
                    await self._respond(key, mcp_types.ElicitResult(action="cancel"))
                    continue
                if len(self._early_cancellations) < 1024:
                    self._early_cancellations.add(key)
                else:
                    self._saturated = True
            if not isinstance(root, mcp_types.JSONRPCRequest) or root.method != "elicitation/create":
                return message
            if root.id in self._pending:
                self._disconnected.set()
                raise anyio.BrokenResourceError
            if self._saturated or root.id in self._early_cancellations or len(self._pending) >= 32:
                self._early_cancellations.discard(root.id)
                await self._respond(root.id, mcp_types.ElicitResult(action="cancel"))
                continue
            task = asyncio.create_task(self._serve(root, self._invocation, self._scope), name="MCP elicitation")
            self._pending[root.id] = _Pending(self._scope, task)
            task.add_done_callback(self._finished)

    def _finished(self, task: asyncio.Task[None]) -> None:
        """消费后台异常并让连接 owner 收束，错误不包含表单、URL 或答案。"""
        for key, pending in tuple(self._pending.items()):
            if pending.task is task:
                del self._pending[key]
        if not task.cancelled() and task.exception() is not None:
            self._disconnected.set()

    async def _serve(self, raw: mcp_types.JSONRPCRequest, invocation: McpInvocation | None, scope: str) -> None:
        """校验输入并只交付仍属于当前调用的答案，超时和取消返回 cancel。"""
        result: mcp_types.ElicitResult | mcp_types.ErrorData = mcp_types.ElicitResult(action="cancel")
        try:
            if self._handler is None or invocation is None or not invocation.allow_elicitation:
                result = mcp_types.ElicitResult(action="decline")
            else:
                if len(raw.model_dump_json().encode("utf-8")) > 65536:
                    raise ValueError("Elicitation request is too large")
                params = mcp_types.ElicitRequest.model_validate(raw.model_dump(by_alias=True)).params
                request = elicitation_request(params, request_id=f"mcp-input-{uuid.uuid4().hex}", server=self._server, invocation=invocation)
                deadline = asyncio.timeout(self._timeout)
                async with deadline:
                    response = await self._handler.request_elicitation(request)
                    request.validate_response(response)
                    pending = self._pending.get(raw.id)
                    if (self._scope != scope or self._closed or self._disconnected.is_set()
                            or deadline.expired() or pending is None or pending.cancelled):
                        response = ElicitationResponse("cancel")
                    if response.action == "accept" and request.url is not None:
                        if not await self._open_browser(request.url):
                            response = ElicitationResponse("cancel")
                    content = {name: list(value) if isinstance(value, tuple) else value for name, value in response.content}
                    result = mcp_types.ElicitResult(action=response.action,
                        content=content if response.action == "accept" and request.fields is not None else None)
        except (TimeoutError, asyncio.CancelledError):
            result = mcp_types.ElicitResult(action="cancel")
        except ValueError:
            result = mcp_types.ErrorData(code=mcp_types.INVALID_PARAMS, message="Unsupported or invalid elicitation request or response")
        finally:
            try:
                pending = self._pending.get(raw.id)
                if not self._closed and pending is not None and not pending.cancelled:
                    with anyio.CancelScope(shield=True):
                        async with asyncio.timeout(2):
                            await self._respond(raw.id, result)
            finally:
                self._pending.pop(raw.id, None)

    async def _respond(self, request_id: mcp_types.RequestId, result: mcp_types.ElicitResult | mcp_types.ErrorData) -> None:
        """只把本请求的终态送回原始传输，不记录正文且不重放工具。"""
        if isinstance(result, mcp_types.ErrorData):
            message = mcp_types.JSONRPCError(jsonrpc="2.0", id=request_id, error=result)
        else:
            message = mcp_types.JSONRPCResponse(jsonrpc="2.0", id=request_id, result=result.model_dump(by_alias=True, exclude_none=True))
        await self._write.send(SessionMessage(mcp_types.JSONRPCMessage(message)))

    async def aclose(self) -> None:
        """关闭该连接的全部交互任务，拒绝任何迟到响应。"""
        self._closed = True
        tasks = tuple(item.task for item in self._pending.values())
        for task in tasks:
            task.cancel()
        with anyio.CancelScope(shield=True):
            await asyncio.gather(*tasks, return_exceptions=True)
        self._pending.clear()
        self._early_cancellations.clear()
        await self._read.aclose()


if __name__ == '__main__':
    pass
