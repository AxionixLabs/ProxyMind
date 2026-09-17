"""使用真实子进程和 localhost HTTP 服务验证传输限制与重试边界。"""

import asyncio
import sys

import pytest

from mcp.shared.exceptions import McpError

from infrastructure.mcp.external_group import ExternalMcpGroup
from tests.external_mcp.fixtures import (
    FixtureMode,
    FixtureSpec,
    read_facts,
    remote_fixture,
    wait_for_fact,
)
from tests.external_mcp.test_fixture_services import reply


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["oversize-line", "oversize-unframed"])
async def test_actual_oversized_stdout_closes_connection_and_process(tmp_path, repository_root, mode: FixtureMode) -> None:
    spec = FixtureSpec(tmp_path, mode, mode=mode, repository=repository_root)
    group = ExternalMcpGroup()
    try:
        async with asyncio.timeout(15):
            assert not await group.start_service({
                "name": spec.name, "transport": "stdio", "command": sys.executable,
                "args": list(spec.arguments()), "cwd": str(repository_root), "startup_timeout_sec": 10,
            })
            assert not group.tools
            assert not group.owned_keys
            await wait_for_fact(spec.facts_path, "process.closed")
    finally:
        await group.close()


@pytest.mark.anyio
async def test_actual_large_unicode_response_and_graceful_exit(tmp_path, repository_root) -> None:
    spec = FixtureSpec(tmp_path, "large", mode="large-response", repository=repository_root)
    group = ExternalMcpGroup()
    try:
        assert await group.start_service({
            "name": spec.name, "transport": "stdio", "command": sys.executable,
            "args": list(spec.arguments()), "cwd": str(repository_root), "startup_timeout_sec": 10,
        })
        result = reply(await group.call_tool("mcp__large__ping", {}))
        assert result.value == "汉🙂" * 300_000
    finally:
        await group.close()
    await wait_for_fact(spec.facts_path, "process.closed")
    assert not group.owned_keys


@pytest.mark.anyio
@pytest.mark.parametrize("mode,attempts,success", [
    ("http-recover", 3, True), ("http-exhausted", 3, False),
    ("http-unauthorized", 1, False), ("http-protocol-error", 1, False),
])
async def test_actual_http_initialize_attempts(tmp_path, repository_root, mode: FixtureMode, attempts: int, success: bool) -> None:
    spec = FixtureSpec(tmp_path, "http", "streamable_http", mode, repository=repository_root)
    group = ExternalMcpGroup()
    async with remote_fixture(spec) as remote:
        try:
            assert await group.start_service({
                "name": "http", "transport": "streamable_http", "url": remote.url, "startup_timeout_sec": 10,
            }) is success
            initializations = [fact for fact in read_facts(spec.facts_path) if fact.event == "initialize.received"]
            assert len(initializations) == attempts
            assert len({fact.peer_port for fact in initializations}) == attempts
            if success:
                assert reply(await group.call_tool("mcp__http__ping", {})).call_count == 1
        finally:
            await group.close()
        assert not group.owned_keys
        if success:
            await wait_for_fact(spec.facts_path, "session.closed")
    assert remote.process.returncode is not None


@pytest.mark.anyio
@pytest.mark.parametrize("cancel", [False, True])
async def test_actual_http_retry_obeys_total_deadline_and_cancellation(tmp_path, repository_root, cancel: bool) -> None:
    spec = FixtureSpec(tmp_path, "bounded", "streamable_http", "http-exhausted", repository=repository_root)
    group = ExternalMcpGroup()
    async with remote_fixture(spec) as remote:
        task = asyncio.create_task(group.start_service({
            "name": "bounded", "transport": "streamable_http", "url": remote.url,
            "startup_timeout_sec": 10 if cancel else 0.7,
        }))
        try:
            if cancel:
                await wait_for_fact(spec.facts_path, "request.completed")
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                assert not await asyncio.wait_for(task, 3)
            await asyncio.sleep(0.3)
            attempts = [fact for fact in read_facts(spec.facts_path) if fact.event == "initialize.received"]
            if cancel:
                assert len(attempts) == 1
            else:
                assert 1 <= len(attempts) <= 2
            assert not group.tools and not group.owned_keys
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await group.close()
    assert remote.process.returncode is not None


@pytest.mark.anyio
async def test_actual_http_tool_failure_is_not_replayed(tmp_path, repository_root) -> None:
    spec = FixtureSpec(tmp_path, "call", "streamable_http", "http-call-failure", repository=repository_root)
    group = ExternalMcpGroup()
    async with remote_fixture(spec) as remote:
        try:
            assert await group.start_service({
                "name": "call", "transport": "streamable_http", "url": remote.url, "startup_timeout_sec": 10,
            })
            with pytest.raises(McpError):
                await group.call_tool("mcp__call__ping", {})
        finally:
            await group.close()
        facts = read_facts(spec.facts_path)
        assert sum(f.event == "initialize.received" for f in facts) == 1
        assert sum(f.event == "tool.started" for f in facts) == 1
    assert remote.process.returncode is not None
