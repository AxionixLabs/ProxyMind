"""通过正式 MCP SDK 提供有可核验身份及故障注入的独立测试服务。"""

import argparse
import asyncio
import contextlib
import os
import socket
import time

import uvicorn

from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

from mcp import types as mcp_types
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.server.stdio import stdio_server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from pydantic import JsonValue
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.routing import (
    Mount,
    Route,
)
from starlette.types import (
    Receive,
    Scope,
    Send,
)

from tests.external_mcp.fixtures import (
    FixtureEvent,
    FixtureFact,
    FixtureMode,
    FixtureReply,
    FixtureTransport,
)


class FixtureState:
    """拥有单个 fixture 进程的身份与计数，事实只记录验收元数据。"""

    def __init__(
        self, facts: Path, release: Path, transport: FixtureTransport, mode: FixtureMode,
    ) -> None:
        """绑定显式文件路径，不访问日常配置或秘密环境变量。"""
        self.facts = facts
        self.release = release
        self.transport = transport
        self.mode = mode
        self.instance_id = uuid4()
        self.created_ns = time.time_ns()
        self.sequence = 0
        self.calls = 0

    def record(
        self, event: FixtureEvent, *, session_id: str | None = None,
        tool: str | None = None, port: int | None = None,
    ) -> None:
        """追加一条完整且经过校验的进程事实，不保存工具参数。"""
        self.sequence += 1
        fact = FixtureFact(
            event=event, instance_id=self.instance_id, pid=os.getpid(),
            created_ns=self.created_ns, sequence=self.sequence,
            transport=self.transport, session_id=session_id, tool=tool, port=port,
        )
        self.facts.parent.mkdir(parents=True, exist_ok=True)
        with self.facts.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(fact.model_dump_json() + "\n")


def create_server(name: str, state: FixtureState) -> Server[str, Request]:
    """创建只读测试工具并为每次 MCP 会话记录独立生命周期。"""
    @contextlib.asynccontextmanager
    async def lifespan(_server: Server[str, Request]) -> AsyncIterator[str]:
        """记录会话打开和关闭，关闭阻塞只作用于显式故障模式。"""
        session_id = str(uuid4())
        state.record("session.opened", session_id=session_id)
        try:
            yield session_id
        finally:
            if state.mode == "close-stall":
                state.record("fault.injected", session_id=session_id)
                await asyncio.Event().wait()
            state.record("session.closed", session_id=session_id)

    server: Server[str, Request] = Server(name, version="fixture", lifespan=lifespan)

    async def initialized(_notification: mcp_types.InitializedNotification) -> None:
        """记录客户端实际发出的初始化完成通知。"""
        state.record("initialized")

    server.notification_handlers[mcp_types.InitializedNotification] = initialized

    async def list_tools() -> list[mcp_types.Tool]:
        """返回有效空目录、普通目录或显式工具发现失败。"""
        state.record("tools.listed")
        if state.mode == "discovery-failure":
            state.record("fault.injected")
            raise RuntimeError("fixture tool discovery failed")
        if state.mode == "empty":
            return []
        return [
            mcp_types.Tool(
                name=tool, description="Read-only MCP acceptance fixture.",
                inputSchema={
                    "type": "object", "properties": {"value": {"type": "string"}},
                    "additionalProperties": False,
                },
            )
            for tool in ("ping", "block")
        ]

    async def call_tool(name: str, arguments: dict[str, JsonValue]) -> list[mcp_types.TextContent]:
        """返回实例和调用身份，阻塞与断线只由测试配置控制。"""
        if name not in {"ping", "block"}:
            raise ValueError("unknown fixture tool")
        value = arguments.get("value", "ok")
        if not isinstance(value, str):
            raise ValueError("fixture value must be a string")
        session_id = server.request_context.lifespan_context
        state.calls += 1
        call_count = state.calls
        state.record("tool.started", session_id=session_id, tool=name)
        if state.mode == "disconnect":
            state.record("fault.injected", session_id=session_id, tool=name)
            os._exit(23)
        if name == "block":
            while not state.release.exists():
                await asyncio.sleep(0.025)
        reply = FixtureReply(
            instance_id=state.instance_id, pid=os.getpid(), session_id=session_id,
            call_count=call_count, value=value,
        )
        state.record("tool.completed", session_id=session_id, tool=name)
        return [mcp_types.TextContent(type="text", text=reply.model_dump_json())]

    if state.mode != "no-tools":
        server.list_tools()(list_tools)
        server.call_tool()(call_tool)
    return server


class SseEndpoint:
    """将正式 SSE transport 接入测试 HTTP 服务，不改变 SDK 消息行为。"""

    def __init__(self, server: Server[str, Request], transport: SseServerTransport) -> None:
        """绑定本机验收服务和 SSE transport。"""
        self.server = server
        self.transport = transport

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """在同一任务内进入和退出 SSE/MCP 资源。"""
        async with self.transport.connect_sse(scope, receive, send) as streams:
            await self.server.run(*streams, self.server.create_initialization_options())


async def run_remote(server: Server[str, Request], state: FixtureState) -> None:
    """绑定操作系统分配的本机端口并服务，避免先探空闲端口的竞争。"""
    manager = StreamableHTTPSessionManager(server)

    @contextlib.asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        """持有 HTTP 会话管理器直到测试服务关闭。"""
        async with manager.run():
            yield

    sse = SseServerTransport("/messages/")
    routes = (
        [Route("/sse", endpoint=SseEndpoint(server, sse), methods=["GET"]), Mount("/messages/", app=sse.handle_post_message)]
        if state.transport == "sse"
        else [Mount("/mcp", app=manager.handle_request)]
    )
    app = Starlette(routes=routes, lifespan=lifespan)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.setblocking(False)
        port = listener.getsockname()[1]
        host = uvicorn.Server(uvicorn.Config(
            app, log_level="warning", timeout_graceful_shutdown=2,
        ))
        state.record("listening", port=port)
        await host.serve(sockets=[listener])


async def run(name: str, state: FixtureState) -> None:
    """运行一次 fixture 进程，并在正常退出路径记录释放事实。"""
    state.record("process.started")
    try:
        if state.mode == "startup-failure":
            state.record("fault.injected")
            raise SystemExit(7)
        if state.mode == "handshake-timeout":
            state.record("fault.injected")
            await asyncio.Event().wait()
        server = create_server(name, state)
        if state.transport == "stdio":
            async with stdio_server() as streams:
                await server.run(*streams, server.create_initialization_options())
        else:
            await run_remote(server, state)
    finally:
        state.record("process.closed")


def main() -> None:
    """解析仅供验收使用的显式参数并启动独立服务。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--facts", required=True, type=Path)
    parser.add_argument("--release", required=True, type=Path)
    parser.add_argument("--transport", choices=("stdio", "streamable_http", "sse"), default="stdio")
    parser.add_argument("--mode", choices=(
        "ready", "empty", "no-tools", "discovery-failure", "startup-failure",
        "handshake-timeout", "disconnect", "close-stall",
    ), default="ready")
    values = parser.parse_args()
    state = FixtureState(values.facts, values.release, values.transport, values.mode)
    asyncio.run(run(values.name, state))


if __name__ == "__main__":
    main()
