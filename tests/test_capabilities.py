# -*- coding: utf-8 -*-

import sys
from pathlib import Path

import pytest

from agent.capabilities import (
    InMemoryFilesystemCapability,
    InMemoryHelixCapability,
    InMemoryMcpCapability,
    InMemoryProcessCapability,
    LocalFilesystemCapability,
    LocalProcessCapability,
)
from agent.ports import CapabilityError, ProcessSpec
from agent.protocol import McpToolDefinition, McpToolResult


@pytest.mark.anyio
async def test_in_memory_mcp_discovers_calls_and_closes() -> None:
    seen: list[dict[str, object]] = []

    def echo(arguments):
        seen.append(dict(arguments))
        return McpToolResult(
            ok=True,
            text="echoed",
            data={"value": arguments.get("value")},
        )

    capability = InMemoryMcpCapability(
        [McpToolDefinition(name="echo", description="Echo input")],
        {"echo": echo},
    )

    tools = await capability.list_tools()
    result = await capability.call_tool(
        "echo",
        arguments={"value": "hello"},
        call_id="call-1",
    )

    assert [tool.to_dict() for tool in tools] == [{
        "name": "echo",
        "description": "Echo input",
        "inputSchema": {},
        "meta": {},
    }]
    assert result.to_dict() == {
        "ok": True,
        "text": "echoed",
        "data": {"value": "hello"},
    }
    assert seen == [{"value": "hello"}]

    await capability.aclose()
    with pytest.raises(CapabilityError) as captured:
        await capability.list_tools()
    assert captured.value.code == "mcp_closed"


@pytest.mark.anyio
async def test_in_memory_mcp_normalizes_missing_and_failed_tools() -> None:
    async def fail(_arguments):
        raise RuntimeError("backend unavailable")

    capability = InMemoryMcpCapability(
        [
            McpToolDefinition(name="fail"),
            McpToolDefinition(name="no_handler"),
        ],
        {"fail": fail},
    )

    with pytest.raises(CapabilityError) as missing:
        await capability.call_tool("unknown")
    assert missing.value.code == "mcp_tool_not_found"

    with pytest.raises(CapabilityError) as unavailable:
        await capability.call_tool("no_handler")
    assert unavailable.value.code == "mcp_tool_unavailable"

    with pytest.raises(CapabilityError) as failed:
        await capability.call_tool("fail")
    assert failed.value.code == "mcp_tool_failed"
    assert failed.value.details == {"exception_type": "RuntimeError"}

    await capability.aclose()


@pytest.mark.anyio
async def test_in_memory_helix_serializes_lifecycle_and_close() -> None:
    calls: list[str] = []

    async def start() -> None:
        calls.append("start")

    async def restart() -> None:
        calls.append("restart")

    async def stop() -> None:
        calls.append("stop")

    async def close() -> None:
        calls.append("close")

    capability = InMemoryHelixCapability(
        on_start=start,
        on_restart=restart,
        on_stop=stop,
        on_close=close,
    )

    assert capability.state == "stopped"
    await capability.ensure_ready()
    await capability.ensure_ready()
    await capability.restart()
    await capability.stop()
    await capability.aclose()
    await capability.aclose()

    assert capability.state == "closed"
    assert calls == ["start", "restart", "stop", "close"]
    with pytest.raises(CapabilityError) as captured:
        await capability.ensure_ready()
    assert captured.value.code == "helix_closed"


@pytest.mark.anyio
async def test_in_memory_helix_normalizes_start_failure() -> None:
    async def fail() -> None:
        raise RuntimeError("not ready")

    capability = InMemoryHelixCapability(on_start=fail)
    with pytest.raises(CapabilityError) as captured:
        await capability.ensure_ready()

    assert captured.value.code == "helix_start_failed"
    assert captured.value.retryable is True
    assert capability.state == "failed"


@pytest.mark.anyio
async def test_local_filesystem_is_root_confined_and_atomic(tmp_path: Path) -> None:
    capability = LocalFilesystemCapability(tmp_path)

    await capability.write_text("nested/value.txt", "hello")
    assert await capability.exists("nested/value.txt") is True
    assert await capability.read_text("nested/value.txt") == "hello"
    assert await capability.list_files("nested") == ("nested/value.txt",)

    with pytest.raises(CapabilityError) as captured:
        await capability.read_text("../outside.txt")
    assert captured.value.code == "filesystem_path_forbidden"

    with pytest.raises(CapabilityError) as absolute:
        await capability.exists(str(tmp_path / "nested" / "value.txt"))
    assert absolute.value.code == "filesystem_path_invalid"

    await capability.aclose()


@pytest.mark.anyio
async def test_local_filesystem_rejects_directory_as_file(tmp_path: Path) -> None:
    capability = LocalFilesystemCapability(tmp_path)
    (tmp_path / "directory").mkdir()

    with pytest.raises(CapabilityError) as captured:
        await capability.read_text("directory")
    assert captured.value.code == "filesystem_not_file"

    with pytest.raises(CapabilityError) as listed:
        await capability.list_files("directory/missing.txt")
    assert listed.value.code == "filesystem_not_found"

    await capability.aclose()


@pytest.mark.anyio
async def test_in_memory_filesystem_preserves_root_and_error_semantics() -> None:
    capability = InMemoryFilesystemCapability((("nested/value.txt", "hello"),))

    assert await capability.exists("nested") is True
    assert await capability.list_files("nested") == ("nested/value.txt",)
    assert await capability.read_text("nested/value.txt") == "hello"
    await capability.write_text("new.txt", "created")
    assert await capability.list_files() == ("new.txt",)

    with pytest.raises(CapabilityError) as forbidden:
        await capability.exists("../outside.txt")
    assert forbidden.value.code == "filesystem_path_forbidden"

    await capability.aclose()
    with pytest.raises(CapabilityError) as closed:
        await capability.exists("nested")
    assert closed.value.code == "filesystem_closed"


@pytest.mark.anyio
async def test_local_process_reads_writes_and_reclaims_process(tmp_path: Path) -> None:
    source = (
        "import sys; "
        "value = sys.stdin.read(); "
        "print(value.upper(), end=''); "
        "print('diagnostic', file=sys.stderr)"
    )
    capability = LocalProcessCapability()
    handle = await capability.spawn(ProcessSpec(
        argv=(sys.executable, "-c", source),
        cwd=tmp_path,
        stdin_open=True,
    ))

    await handle.write("hello", eof=True)
    stdout = "".join([chunk async for chunk in handle.read_stdout()])
    stderr = "".join([chunk async for chunk in handle.read_stderr()])
    exit_code = await handle.wait()

    assert stdout == "HELLO"
    assert stderr.replace("\r\n", "\n") == "diagnostic\n"
    assert exit_code == 0
    assert handle.returncode == 0

    await handle.aclose()
    await capability.aclose()


@pytest.mark.anyio
async def test_local_process_requires_explicit_sandbox_launcher(tmp_path: Path) -> None:
    capability = LocalProcessCapability()
    with pytest.raises(CapabilityError) as captured:
        await capability.spawn(ProcessSpec(
            argv=(sys.executable, "-c", "pass"),
            cwd=tmp_path,
            sandbox_mode="workspace-read",
        ))
    assert captured.value.code == "process_sandbox_unavailable"
    await capability.aclose()


@pytest.mark.anyio
async def test_in_memory_process_exposes_input_output_and_close_semantics(
    tmp_path: Path,
) -> None:
    capability = InMemoryProcessCapability(
        stdout="ready\n",
        stderr="warning\n",
        returncode=3,
    )
    spec = ProcessSpec(
        argv=("fake",),
        cwd=tmp_path,
        sandbox_mode="workspace-write",
        sandbox_permissions="with_additional_permissions",
        additional_permissions={"network": False},
    )
    handle = await capability.spawn(spec)

    await handle.write("input", eof=True)
    assert handle.inputs == ("input",)
    assert handle.stdin_closed is True
    assert "".join([chunk async for chunk in handle.read_stdout()]) == "ready\n"
    assert "".join([chunk async for chunk in handle.read_stderr()]) == "warning\n"
    assert await handle.wait() == 3

    await handle.aclose()
    with pytest.raises(CapabilityError) as closed:
        await handle.wait()
    assert closed.value.code == "process_closed"
    await capability.aclose()


def test_capability_error_is_stable_and_copyable() -> None:
    details = {"attempt": 2, "nested": {"ok": True}}
    error = CapabilityError(
        "process_spawn_failed",
        "spawn failed",
        retryable=True,
        details=details,
    )

    details["attempt"] = 9
    snapshot = error.to_dict()
    assert snapshot == {
        "code": "process_spawn_failed",
        "message": "spawn failed",
        "retryable": True,
        "details": {"attempt": 2, "nested": {"ok": True}},
    }
    snapshot["details"]["attempt"] = 7
    assert error.details["attempt"] == 2


def test_process_spec_requires_structured_permission_payload() -> None:
    with pytest.raises(ValueError, match="additional_permissions"):
        ProcessSpec(
            argv=("fake",),
            cwd=".",
            sandbox_permissions="with_additional_permissions",
        )

    with pytest.raises(ValueError, match="additional_permissions"):
        ProcessSpec(
            argv=("fake",),
            cwd=".",
            sandbox_permissions="use_default",
            additional_permissions={"network": False},
        )

    with pytest.raises(TypeError, match="argv"):
        ProcessSpec(argv="python", cwd=".")

    with pytest.raises(TypeError, match="env values"):
        ProcessSpec(argv=("python",), cwd=".", env={"COUNT": 1})
