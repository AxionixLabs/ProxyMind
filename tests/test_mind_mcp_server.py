# -*- coding: utf-8 -*-

import os
import sys
import typing
import asyncio
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import anyio
import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from mind_app.mcp.server import (
    MindMcpRuntime,
    create_mind_mcp_server,
)
from mind_app.runtime.turns.result import RunResult
from mind_core.application_paths import ApplicationLayout
from mind_core.permissions import PermissionSettings


def _source_layout(tmp_path: Path) -> ApplicationLayout:
    return ApplicationLayout(
        mode="source",
        platform="win32",
        executable=tmp_path / "mind.py",
        root=tmp_path,
        supports=tmp_path / "schematic" / "supports" / "windows",
    )


def test_mind_mcp_server_exposes_one_structured_tool(tmp_path) -> None:
    server = create_mind_mcp_server(layout=_source_layout(tmp_path))

    tools = server._tool_manager.list_tools()

    assert [tool.name for tool in tools] == ["mind_exec"]
    assert tools[0].parameters["required"] == ["prompt"]
    properties = tools[0].parameters["properties"]
    assert "mode" not in properties
    assert properties["sandbox_mode"]["anyOf"][0]["enum"] == [
        "read-only",
        "workspace-write",
        "danger-full-access",
    ]
    assert properties["approval_policy"]["anyOf"][0]["enum"] == [
        "untrusted",
        "on-request",
        "never",
    ]
    assert properties["timeout_sec"]["default"] == 900.0
    assert properties["session_id"]["default"] is None


@pytest.mark.anyio
async def test_mind_mcp_runtime_executes_isolated_call(tmp_path) -> None:
    result = RunResult(status="completed", assistant_text="done")
    metadata = {
        "cid": "cid_test_12345678",
        "sid": "sid_test_1_abcdef",
    }
    mind = SimpleNamespace(
        history_workspace=str(tmp_path),
        set_history_workspace=Mock(),
        reset_conversation=AsyncMock(return_value=metadata),
        find_conversation_session=Mock(return_value=None),
        resume_conversation=AsyncMock(),
        calling=AsyncMock(return_value=result),
    )
    runtime = MindMcpRuntime(typing.cast(typing.Any, mind))

    actual = await runtime.execute(
        prompt="inspect",
        sandbox_mode="read-only",
        approval_policy="on-request",
        working_directory=str(tmp_path),
    )

    assert actual.run is result
    assert actual.session_id == metadata["sid"]
    mind.set_history_workspace.assert_called_once_with(tmp_path.resolve())
    mind.reset_conversation.assert_called_once_with(
        reason="mcp_tool_call",
        source="mcp_server",
    )
    mind.calling.assert_awaited_once_with(
        message="inspect",
        permissions=PermissionSettings("read-only", "on-request"),
    )


@pytest.mark.anyio
async def test_mind_mcp_runtime_uses_default_permissions(tmp_path) -> None:
    metadata = {
        "cid": "cid_test_12345678",
        "sid": "sid_test_1_abcdef",
    }
    mind = SimpleNamespace(
        history_workspace=str(tmp_path),
        permissions=PermissionSettings("workspace-write", "on-request"),
        set_history_workspace=Mock(),
        reset_conversation=AsyncMock(return_value=metadata),
        find_conversation_session=Mock(return_value=None),
        resume_conversation=AsyncMock(),
        calling=AsyncMock(return_value=RunResult(status="completed")),
    )
    runtime = MindMcpRuntime(typing.cast(typing.Any, mind))

    await runtime.execute(
        prompt="inspect",
        sandbox_mode=None,
        approval_policy=None,
        working_directory=str(tmp_path),
    )

    mind.calling.assert_awaited_once_with(
        message="inspect",
        permissions=PermissionSettings("workspace-write", "on-request"),
    )


@pytest.mark.anyio
async def test_mind_mcp_runtime_resumes_workspace_session(tmp_path) -> None:
    result = RunResult(status="completed", assistant_text="continued")
    metadata = {
        "cid": "cid_test_12345678",
        "sid": "sid_test_1_abcdef",
    }
    record = {
        **metadata,
        "workspace": str(tmp_path.resolve()),
    }
    mind = SimpleNamespace(
        history_workspace=str(tmp_path),
        set_history_workspace=Mock(),
        reset_conversation=AsyncMock(),
        find_conversation_session=Mock(return_value=record),
        resume_conversation=AsyncMock(return_value=metadata),
        calling=AsyncMock(return_value=result),
    )
    runtime = MindMcpRuntime(typing.cast(typing.Any, mind))

    actual = await runtime.execute(
        prompt="continue",
        sandbox_mode="danger-full-access",
        approval_policy="never",
        working_directory=str(tmp_path),
        session_id=metadata["sid"],
    )

    assert actual.run is result
    assert actual.session_id == metadata["sid"]
    mind.reset_conversation.assert_not_called()
    mind.find_conversation_session.assert_called_once_with(
        metadata["sid"],
        workspace=tmp_path.resolve(),
    )
    mind.resume_conversation.assert_called_once_with(
        record,
        source="mcp_server",
    )


@pytest.mark.anyio
async def test_mind_mcp_runtime_rejects_unknown_session(tmp_path) -> None:
    mind = SimpleNamespace(
        history_workspace=str(tmp_path),
        set_history_workspace=Mock(),
        reset_conversation=AsyncMock(),
        find_conversation_session=Mock(return_value=None),
        resume_conversation=AsyncMock(),
        calling=AsyncMock(),
    )
    runtime = MindMcpRuntime(typing.cast(typing.Any, mind))

    actual = await runtime.execute(
        prompt="continue",
        sandbox_mode="read-only",
        approval_policy="on-request",
        working_directory=str(tmp_path),
        session_id="sid_test_1_abcdef",
    )

    assert actual.run.status == "failed"
    assert actual.run.error == "session_id is unavailable for this working directory"
    assert actual.session_id is None
    mind.calling.assert_not_awaited()


@pytest.mark.anyio
async def test_mind_mcp_runtime_times_out_and_releases_call_lock(tmp_path) -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()
    metadata = {
        "cid": "cid_test_12345678",
        "sid": "sid_test_1_abcdef",
    }

    async def wait_forever(**_kwargs) -> RunResult:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    mind = SimpleNamespace(
        history_workspace=str(tmp_path),
        set_history_workspace=Mock(),
        reset_conversation=AsyncMock(return_value=metadata),
        find_conversation_session=Mock(return_value=None),
        resume_conversation=AsyncMock(),
        calling=AsyncMock(side_effect=wait_forever),
    )
    runtime = MindMcpRuntime(typing.cast(typing.Any, mind))

    timed_out = await runtime.execute(
        prompt="wait",
        sandbox_mode="read-only",
        approval_policy="on-request",
        working_directory=str(tmp_path),
        timeout_sec=0.01,
    )

    assert started.is_set()
    assert cancelled.is_set()
    assert timed_out.run.status == "failed"
    assert timed_out.run.error == "request timed out after 0.01 seconds"
    assert timed_out.session_id == metadata["sid"]

    mind.calling = AsyncMock(
        return_value=RunResult(status="completed", assistant_text="next")
    )
    following = await runtime.execute(
        prompt="next",
        sandbox_mode="read-only",
        approval_policy="on-request",
        working_directory=str(tmp_path),
    )
    assert following.run.status == "completed"


@pytest.mark.anyio
async def test_mind_mcp_runtime_propagates_cancellation(tmp_path) -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()
    metadata = {
        "cid": "cid_test_12345678",
        "sid": "sid_test_1_abcdef",
    }

    async def wait_forever(**_kwargs) -> RunResult:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    mind = SimpleNamespace(
        history_workspace=str(tmp_path),
        set_history_workspace=Mock(),
        reset_conversation=AsyncMock(return_value=metadata),
        find_conversation_session=Mock(return_value=None),
        resume_conversation=AsyncMock(),
        calling=AsyncMock(side_effect=wait_forever),
    )
    runtime = MindMcpRuntime(typing.cast(typing.Any, mind))

    task = asyncio.create_task(runtime.execute(
        prompt="wait",
        sandbox_mode="read-only",
        approval_policy="on-request",
        working_directory=str(tmp_path),
        timeout_sec=None,
    ))
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert cancelled.is_set()


@pytest.mark.anyio
async def test_mind_mcp_stdio_handshake(tmp_path) -> None:
    repository = Path(__file__).resolve().parents[1]
    environment = dict(os.environ)
    environment["MIND_HOME"] = str(tmp_path / ".mind")
    environment["HELIX_HOME"] = str(tmp_path / ".mind" / "helix")
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[str(repository / "mind.py"), "mcp-server"],
        cwd=repository,
        env=environment,
    )

    with anyio.fail_after(15):
        async with stdio_client(parameters) as streams:
            async with ClientSession(
                streams[0],
                streams[1],
                read_timeout_seconds=timedelta(seconds=10),
            ) as session:
                initialized = await session.initialize()
                tools = await session.list_tools()
                result = await session.call_tool("mind_exec", {"prompt": ""})

    assert initialized.serverInfo.name == "Mind"
    assert [tool.name for tool in tools.tools] == ["mind_exec"]
    assert result.isError is False
    assert result.structuredContent is not None
    assert result.structuredContent["status"] == "failed"
    assert result.structuredContent["error"] == "prompt is empty"
