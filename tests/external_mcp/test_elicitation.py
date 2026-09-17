# -*- coding: utf-8 -*-

import asyncio
import contextlib
import json
import sys
from unittest.mock import AsyncMock

import pytest

from agent.domain.mcp_elicitation import (
    ElicitationResponse,
    McpInvocation,
)
from infrastructure.mcp.external_group import ExternalMcpGroup
from infrastructure.mcp.settings import normalize_mcp_servers
from tests.external_mcp.fixtures import (
    FixtureReply,
    FixtureSpec,
    read_facts,
    remote_fixture,
)


@pytest.mark.anyio
@pytest.mark.parametrize("transport", ["stdio", "streamable_http", "sse"])
@pytest.mark.parametrize("interactive", [True, False])
async def test_real_sdk_elicitation_capabilities_identity_and_return_value(tmp_path, repository_root, transport, interactive):
    spec = FixtureSpec(tmp_path, "input", transport, "elicitation", repository=repository_root)
    handler = AsyncMock()
    handler.request_elicitation.return_value = ElicitationResponse("accept", (("name", "Tester"),))
    group = ExternalMcpGroup(elicitation=handler if interactive else None)
    async with contextlib.AsyncExitStack() as stack:
        if transport == "stdio":
            server = {"command": sys.executable, "args": list(spec.arguments()), "cwd": str(repository_root)}
        else:
            remote = await stack.enter_async_context(remote_fixture(spec))
            server = {"url": remote.url, "transport": transport}
        try:
            assert await group.start(normalize_mcp_servers({"input": server})) == 1
            identity = McpInvocation("session", "turn", "call", "root")
            result = await group.call_tool("mcp__input__ping", {"value": "form"}, invocation=identity)
            assert not result.isError
            reply = FixtureReply.model_validate_json(result.content[0].text)
            answer = json.loads(reply.value)
            assert answer == ({"action": "accept", "content": {"name": "Tester"}} if interactive else {"action": "decline"})
            if interactive:
                request = handler.request_elicitation.call_args.args[0]
                assert request.invocation == identity and request.server == "input"
            else:
                handler.request_elicitation.assert_not_awaited()
        finally:
            await group.close()
    facts = read_facts(spec.facts_path)
    assert any(item.event == ("elicitation.capable" if interactive else "elicitation.unsupported") for item in facts)
    assert len([item for item in facts if item.event == "tool.started"]) == 1
    assert len([item for item in facts if item.event == "session.closed"]) == 1


@pytest.mark.anyio
async def test_real_sdk_concurrent_elicitations_and_call_cancellation(tmp_path, repository_root):
    spec = FixtureSpec(tmp_path, "input", mode="elicitation", repository=repository_root)
    entered = asyncio.Event()
    requests = []

    async def present(request):
        requests.append(request)
        if len(requests) == 2:
            entered.set()
        await asyncio.Event().wait()

    handler = AsyncMock()
    handler.request_elicitation.side_effect = present
    group = ExternalMcpGroup(elicitation=handler)
    try:
        await group.start(normalize_mcp_servers({"input": {"command": sys.executable, "args": list(spec.arguments()), "cwd": str(repository_root)}}))
        call = asyncio.create_task(group.call_tool("mcp__input__ping", {"value": "parallel"}, invocation=McpInvocation("s", "t", "c", "a")))
        await asyncio.wait_for(entered.wait(), 5)
        assert requests[0].request_id != requests[1].request_id
        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call
    finally:
        await group.close()
    assert not group.owned_keys
