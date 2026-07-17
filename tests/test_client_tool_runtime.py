# -*- coding: utf-8 -*-

import asyncio

from mind_app.client_tools.registry import ClientToolRegistry
from mind_app.client_tools.result import client_tool_result
from mind_app.client_tools.types import ClientTool, ClientToolRuntime
from mind_app.mcp.session_adapter import CompositeToolSession


def test_composite_session_passes_execution_identity_to_client_runtime() -> None:
    """本地工具路由将可信事件上下文独立传给 handler。"""
    captured: list[ClientToolRuntime] = []

    async def handler(arguments: dict, runtime: ClientToolRuntime):
        captured.append(runtime)
        return client_tool_result(tool="capture", ok=True, text="captured", args=arguments)

    registry = ClientToolRegistry([
        ClientTool(
            name="capture",
            description="capture runtime",
            input_schema={"type": "object", "properties": {}},
            handler=handler,
        )
    ])
    session = CompositeToolSession(client_registry=registry)
    execution = {"grantId": "grant"}

    asyncio.run(
        session.call_tool(
            "capture",
            {"value": 1},
            execution=execution,
            cid="cid",
            sid="sid",
            call_id="call",
        )
    )

    assert len(captured) == 1
    assert captured[0].session is session
    assert captured[0].execution is execution
    assert captured[0].cid == "cid"
    assert captured[0].sid == "sid"
    assert captured[0].call_id == "call"
