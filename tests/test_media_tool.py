# -*- coding: utf-8 -*-

from pathlib import Path

import pytest

from agent.application.tools.media import media_tools
from agent.application.turns.context import (
    AgentContext,
    TurnContext,
)
from agent.domain.policies import preset_permissions
from infrastructure.mcp.composite_session import CompositeToolSession
from infrastructure.mcp.local_tool_registry import ToolRegistry
from infrastructure.platform.images import FileImageReader


def _turn(root: Path) -> TurnContext:
    return TurnContext.create(
        agent=AgentContext.root("sid_media"),
        cid="cid_media",
        sid="sid_media",
        source="test",
        pref_config={},
        cwd=str(root),
        permissions=preset_permissions("auto"),
        turn_id="turn_media",
    )


@pytest.mark.anyio
async def test_view_image_reads_through_workspace_port(tmp_path: Path) -> None:
    image_path = tmp_path / "sample.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\nimage")
    session = CompositeToolSession(
        client_registry=ToolRegistry(media_tools(FileImageReader(tmp_path))),
    )

    result = await session.call_tool(
        "view_image",
        {"path": "sample.png"},
        turn_context=_turn(tmp_path),
        pref_config={},
    )

    assert result.isError is False
    assert result.structuredContent["data"] == {
        "path": "sample.png",
        "mime_type": "image/png",
        "size": 13,
    }
    assert result.structuredContent["attachments"][0]["data_url"].startswith(
        "data:image/png;base64,"
    )


@pytest.mark.anyio
async def test_view_image_returns_stable_reader_failure(tmp_path: Path) -> None:
    session = CompositeToolSession(
        client_registry=ToolRegistry(media_tools(FileImageReader(tmp_path))),
    )

    result = await session.call_tool(
        "view_image",
        {"path": "missing.png"},
        turn_context=_turn(tmp_path),
        pref_config={},
    )

    assert result.isError is True
    assert result.structuredContent["data"] == {
        "path": "missing.png",
        "error": "path_not_file",
    }
