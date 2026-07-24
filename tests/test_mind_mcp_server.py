# -*- coding: utf-8 -*-

import os
import sys
import typing
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
from mind_app.modes.result import RunResult
from mind_core.application_paths import ApplicationLayout


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
    assert properties["mode"]["enum"] == ["chat", "fast", "xtra"]
    assert properties["access_mode"]["enum"] == ["safe", "full"]


@pytest.mark.anyio
async def test_mind_mcp_runtime_executes_isolated_call(tmp_path) -> None:
    result = RunResult(status="completed", assistant_text="done")
    mind = SimpleNamespace(
        history_workspace=str(tmp_path),
        set_history_workspace=Mock(),
        reset_conversation=Mock(),
        calling=AsyncMock(return_value=result),
    )
    runtime = MindMcpRuntime(typing.cast(typing.Any, mind))

    actual = await runtime.execute(
        prompt="inspect",
        mode="xtra",
        access_mode="safe",
        working_directory=str(tmp_path),
    )

    assert actual is result
    mind.set_history_workspace.assert_called_once_with(tmp_path.resolve())
    mind.reset_conversation.assert_called_once_with(
        reason="mcp_tool_call",
        source="mcp_server",
    )
    mind.calling.assert_awaited_once_with(
        message="inspect",
        mode="xtra",
        access_mode="safe",
    )


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
