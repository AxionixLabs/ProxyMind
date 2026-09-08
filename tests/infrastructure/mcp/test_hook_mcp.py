# -*- coding: utf-8 -*-

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from infrastructure.hooks.discovery import resolve_hook_definitions
from infrastructure.mcp.hook_runner import HookMcpError, HookMcpRunner


def _definition(input_value=None):
    handler = {
        "type": "mcp_tool",
        "server": "files",
        "tool": "review",
    }
    if input_value is not None:
        handler["input"] = input_value
    return resolve_hook_definitions(
        {"UserPromptSubmit": [{"hooks": [handler]}]},
        source_scope="project",
        source_path=None,
    )[0]


class _Group:
    def __init__(self, result) -> None:
        self.result = result
        self.calls = []

    async def call_hook_tool(self, server, tool, arguments, *, read_timeout_seconds):
        self.calls.append((server, tool, arguments, read_timeout_seconds))
        return self.result


@pytest.mark.anyio
async def test_hook_mcp_runner_expands_static_input_and_structured_output() -> None:
    group = _Group(SimpleNamespace(
        isError=False,
        structuredContent={"systemMessage": "reviewed"},
        content=[],
    ))
    runner = HookMcpRunner(lambda: group)

    output = await runner.execute(
        _definition({
            "prompt": "${prompt}",
            "command": "prefix-${tool_input.command}",
            "count": "${tool_input.count}",
        }),
        {
            "prompt": "hello",
            "tool_input": {"command": "check", "count": 2},
        },
    )

    assert output.data == {"systemMessage": "reviewed"}
    assert group.calls == [(
        "files",
        "review",
        {
            "prompt": "hello",
            "command": "prefix-check",
            "count": 2,
        },
        None,
    )]


@pytest.mark.anyio
async def test_hook_mcp_runner_parses_json_text_and_rejects_invalid_results() -> None:
    group = _Group(SimpleNamespace(
        isError=False,
        structuredContent=None,
        content=[SimpleNamespace(text='{"systemMessage":"ok"}')],
    ))
    output = await HookMcpRunner(lambda: group).execute(
        _definition(),
        {},
    )
    assert output.data == {"systemMessage": "ok"}

    invalid_group = _Group(SimpleNamespace(
        isError=False,
        structuredContent=None,
        content=[SimpleNamespace(text="plain text")],
    ))
    with pytest.raises(HookMcpError, match="non-JSON"):
        await HookMcpRunner(lambda: invalid_group).execute(
            _definition(),
            {},
        )


@pytest.mark.anyio
async def test_hook_mcp_runner_reports_unavailable_error_and_timeout() -> None:
    with pytest.raises(HookMcpError, match="not started"):
        await HookMcpRunner(lambda: None).execute(_definition(), {})

    error_group = _Group(SimpleNamespace(
        isError=True,
        structuredContent=None,
        content=[SimpleNamespace(text="server rejected request")],
    ))
    with pytest.raises(HookMcpError, match="server rejected"):
        await HookMcpRunner(lambda: error_group).execute(_definition(), {})

    async def wait_forever(*_args, **_kwargs):
        await asyncio.sleep(10)

    timeout_group = _Group(SimpleNamespace(
        isError=False,
        structuredContent=None,
        content=[],
    ))
    timeout_group.call_hook_tool = wait_forever
    definition = _definition()
    definition = replace(
        definition,
        handler=replace(definition.handler, timeout_sec=0),
    )
    with pytest.raises(HookMcpError, match="timed out"):
        await HookMcpRunner(lambda: timeout_group).execute(definition, {})
