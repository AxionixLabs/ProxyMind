# -*- coding: utf-8 -*-

from types import SimpleNamespace

import pytest
from mcp.server import FastMCP

from backend.mcp_core.core_memrix import Memrix
from backend.mcp_tools.bench import bench_memrix


class IdleStub(object):
    def __init__(self) -> None:
        self.sessions: list[dict] = []
        self.patches: list[tuple[str, dict]] = []
        self.finished: list[str] = []

    async def session_begin(self, **kwargs):
        self.sessions.append(kwargs)
        return "session-id"

    async def session_patch_args(self, key, patch):
        self.patches.append((key, patch))

    async def session_final(self, key):
        self.finished.append(key)

    async def job_begin(self, name, *, args=None):
        _ = name, args
        return "job-id"

    async def job_final(self, job_id):
        self.finished.append(job_id)


@pytest.mark.anyio
async def test_memrix_core_forwards_explicit_scene(monkeypatch) -> None:
    commands = []

    class TransportStub(object):
        async def wait(self):
            return 0

    async def engine(*args, **_kwargs):
        commands.append(args)
        memrix._Memrix__transports = TransportStub()
        return {
            "ok": True,
            "text": "started",
            "attachments": [],
            "data": {"token": "token-1"},
            "logs": [],
        }

    async def port_available(_port):
        return True

    memrix = Memrix()
    monkeypatch.setattr(memrix, "_Memrix__engine", engine)
    monkeypatch.setattr(
        "backend.mcp_core.core_memrix.port_listen",
        port_available,
    )
    result = await memrix.mx_task_begin(
        "--storm",
        "com.example.app",
        "artifacts/startup",
        "device-1",
        "startup",
    )

    assert commands == [(
        "--storm",
        "--scene",
        "artifacts/startup",
        "--focus",
        "com.example.app",
        "--imply",
        "device-1",
        "--title",
        "startup",
        "--watch",
    )]
    assert result["data"] == {
        "token": "token-1",
        "report_scene": "artifacts/startup_Storm",
    }
    assert not hasattr(memrix, "mx_report_store")
    assert not hasattr(memrix, "scene")
    assert not hasattr(memrix, "style")

    reported = await memrix.mx_mem_reporter(
        result["data"]["report_scene"],
        True,
    )

    assert commands[1] == (
        "--forge",
        "artifacts/startup_Storm",
        "--watch",
        "--layer",
    )
    assert reported["data"]["scene"] == "artifacts/startup_Storm"


@pytest.mark.anyio
async def test_memrix_core_rejects_blank_scene() -> None:
    with pytest.raises(RuntimeError, match="scene must be a non-empty"):
        await Memrix().mx_task_begin("--storm", "com.example.app", "  ")


@pytest.mark.anyio
async def test_memrix_tools_require_and_forward_scene(monkeypatch) -> None:
    begin_calls = []
    reporter_calls = []

    class MemrixStub(object):
        agent_id = "memrix"

        async def mx_task_begin(self, style, focus, scene, imply, title):
            begin_calls.append((style, focus, scene, imply, title))
            return {
                "ok": True,
                "text": "started",
                "attachments": [],
                "data": {
                    "token": "token-1",
                    "report_scene": (
                        f"{scene}_{style.removeprefix('--').capitalize()}"
                    ),
                },
                "logs": [],
            }

        async def mx_mem_reporter(self, scene, layer):
            reporter_calls.append((scene, layer))
            return {
                "ok": True,
                "text": "reported",
                "attachments": [],
                "data": {"scene": scene},
                "logs": [],
            }

        async def mx_gfx_reporter(self, scene):
            reporter_calls.append((scene, None))
            return {
                "ok": True,
                "text": "reported",
                "attachments": [],
                "data": {"scene": scene},
                "logs": [],
            }

    async def connect_memrix() -> None:
        return None

    monkeypatch.setattr(
        bench_memrix.Requires,
        "connect_memrix",
        connect_memrix,
    )

    idle = IdleStub()
    mcp = FastMCP("test")
    bench_memrix.bind(
        mcp,
        idle,
        SimpleNamespace(memrix=MemrixStub()),
    )

    mem_reporter = mcp._tool_manager.get_tool("mx_mem_reporter")
    gfx_reporter = mcp._tool_manager.get_tool("mx_gfx_reporter")
    mem_sampler = mcp._tool_manager.get_tool("mx_sample_mem")
    gfx_sampler = mcp._tool_manager.get_tool("mx_sample_gfx")
    assert mem_sampler is not None
    assert gfx_sampler is not None
    assert mem_reporter is not None
    assert gfx_reporter is not None
    assert mem_sampler.parameters["required"] == ["focus", "scene"]
    assert gfx_sampler.parameters["required"] == ["focus", "scene"]
    assert mem_reporter.parameters["required"] == ["scene"]
    assert gfx_reporter.parameters["required"] == ["scene"]

    await mcp._tool_manager.call_tool(
        "mx_sample_mem",
        {"focus": "com.example.app", "scene": "mem_run"},
    )
    await mcp._tool_manager.call_tool(
        "mx_sample_gfx",
        {"focus": "com.example.app", "scene": "gfx_run"},
    )
    await mcp._tool_manager.call_tool(
        "mx_mem_reporter",
        {"scene": "run_Storm", "layer": True},
    )
    await mcp._tool_manager.call_tool(
        "mx_gfx_reporter",
        {"scene": "run_Sleek"},
    )

    assert begin_calls == [
        ("--storm", "com.example.app", "mem_run", None, None),
        ("--sleek", "com.example.app", "gfx_run", None, None),
    ]
    assert [session["args"]["scene"] for session in idle.sessions] == [
        "mem_run",
        "gfx_run",
    ]
    assert idle.patches == [
        (
            "memrix",
            {"token": "token-1", "report_scene": "mem_run_Storm"},
        ),
        (
            "memrix",
            {"token": "token-1", "report_scene": "gfx_run_Sleek"},
        ),
    ]
    assert reporter_calls == [
        ("run_Storm", True),
        ("run_Sleek", None),
    ]
