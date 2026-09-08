# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.application.tools.review_reads import (
    READ_FILE_TOOL,
    READ_REPOSITORY_TOOL,
    review_read_tools,
)
from agent.ports import (
    ReviewFileRead,
    ReviewRepositoryRead,
)


@pytest.mark.anyio
async def test_review_read_tools_project_bounded_repository_and_file_data() -> None:
    reader = SimpleNamespace(
        read_repository=AsyncMock(return_value=ReviewRepositoryRead(
            command="git status --short",
            exit_code=0,
            stdout=" M source.py\n",
            stderr="",
        )),
        read_file=AsyncMock(return_value=ReviewFileRead(
            path="source.py",
            content="changed\n",
            start_line=2,
            end_line=2,
            total_lines=3,
            truncated=True,
        )),
    )
    tools = {tool.name: tool for tool in review_read_tools(reader)}

    repository = await tools[READ_REPOSITORY_TOOL].handler(
        {"operation": "status"},
        SimpleNamespace(),
    )
    source = await tools[READ_FILE_TOOL].handler(
        {"path": "source.py", "start_line": 2, "max_lines": 1},
        SimpleNamespace(),
    )

    assert repository.ok is True
    assert repository.data["command"] == "git status --short"
    assert repository.data["stdout"] == " M source.py\n"
    assert source.ok is True
    assert source.data["content"] == "changed\n"
    assert all(tool.meta["review_read_only"] is True for tool in tools.values())


@pytest.mark.anyio
async def test_review_read_tools_reject_unknown_and_mistyped_arguments() -> None:
    reader = SimpleNamespace(
        read_repository=AsyncMock(),
        read_file=AsyncMock(),
    )
    tools = {tool.name: tool for tool in review_read_tools(reader)}

    repository = await tools[READ_REPOSITORY_TOOL].handler(
        {"operation": "status", "command": "git reset --hard"},
        SimpleNamespace(),
    )
    source = await tools[READ_FILE_TOOL].handler(
        {"path": "source.py", "start_line": True},
        SimpleNamespace(),
    )

    assert repository.ok is False
    assert "unsupported arguments" in repository.text
    assert source.ok is False
    assert "start_line must be an integer" in source.text
    reader.read_repository.assert_not_awaited()
    reader.read_file.assert_not_awaited()


if __name__ == '__main__':
    pass
