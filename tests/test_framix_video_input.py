# -*- coding: utf-8 -*-

import json
import asyncio
from types import SimpleNamespace

import pytest
from mcp.server import FastMCP

from backend.mcp_core.core_framix import Framix
from backend.mcp_hub.hub_record import Record
from backend.mcp_tools.bench import bench_framix


class IdleStub(object):
    def __init__(self) -> None:
        self.started: dict | None = None
        self.finished: list[str] = []
        self.sessions: list[dict] = []

    async def job_begin(self, name, *, args=None):
        self.started = {"name": name, "args": args}
        return "job-id"

    async def job_final(self, job_id):
        self.finished.append(job_id)

    async def session_begin(self, **kwargs):
        self.sessions.append(kwargs)

    async def session_final(self, key):
        self.finished.append(key)


@pytest.mark.anyio
async def test_framix_analyzer_requires_and_forwards_explicit_videos(
    monkeypatch,
) -> None:
    calls = []

    class FramixStub(object):
        agent_id = "framix"

        async def fx_frame_analyzer(self, **kwargs):
            calls.append(kwargs)
            return {
                "ok": True,
                "text": "done",
                "attachments": [],
                "data": {},
                "logs": [],
            }

    async def connect_framix() -> None:
        return None

    monkeypatch.setattr(
        bench_framix.Requires,
        "connect_framix",
        connect_framix,
    )

    idle = IdleStub()
    mcp = FastMCP("test")
    bench_framix.bind(
        mcp,
        idle,
        SimpleNamespace(framix=FramixStub()),
    )

    analysis_tool = mcp._tool_manager.get_tool("fx_frame_analysis")
    assert analysis_tool is not None
    assert analysis_tool.parameters["required"] == ["video", "total"]

    tool = mcp._tool_manager.get_tool("fx_frame_analyzer")
    assert tool is not None
    assert tool.parameters["required"] == ["video", "title", "label", "total"]
    assert tool.parameters["properties"]["label"]["pattern"] == r"^\d{14}$"

    videos = ["C:/records/a.mkv", "C:/records/b.mkv"]
    await mcp._tool_manager.call_tool(
        "fx_frame_analyzer",
        {
            "video": videos,
            "title": "startup",
            "label": "20260722120000",
            "total": "C:/reports",
            "scale": 0.4,
        },
    )

    assert calls == [{
        "video": videos,
        "title": "startup",
        "label": "20260722120000",
        "total": "C:/reports",
        "scale": 0.4,
    }]
    assert idle.started == {
        "name": "framix.fx_frame_analyzer",
        "args": calls[0],
    }
    assert idle.finished == ["job-id"]


@pytest.mark.anyio
async def test_framix_reporter_requires_and_forwards_report_dir(
    monkeypatch,
) -> None:
    calls = []

    class FramixStub(object):
        agent_id = "framix"

        async def fx_frame_reporter(self, total):
            calls.append(total)
            return {
                "ok": True,
                "text": "done",
                "attachments": [],
                "data": {"report_dir": total},
                "logs": [],
            }

    async def connect_framix() -> None:
        return None

    monkeypatch.setattr(
        bench_framix.Requires,
        "connect_framix",
        connect_framix,
    )

    mcp = FastMCP("test")
    bench_framix.bind(
        mcp,
        IdleStub(),
        SimpleNamespace(framix=FramixStub()),
    )

    tool = mcp._tool_manager.get_tool("fx_frame_reporter")
    assert tool is not None
    assert tool.parameters["required"] == ["total"]
    assert "report_dir" not in tool.parameters["properties"]

    await mcp._tool_manager.call_tool(
        "fx_frame_reporter",
        {"total": "C:/reports/FX_run"},
    )

    assert calls == ["C:/reports/FX_run"]


@pytest.mark.anyio
async def test_framix_core_returns_explicit_report_directory(
    monkeypatch,
    tmp_path,
) -> None:
    video = tmp_path / "recording.mkv"
    video.write_bytes(b"video")
    commands = []

    async def engine(*args, **_kwargs):
        commands.append(args)
        return {
            "ok": True,
            "text": "done",
            "attachments": [],
            "data": {},
            "logs": [],
        }

    framix = Framix()
    monkeypatch.setattr(framix, "_Framix__engine", engine)

    result = await framix.fx_frame_analyzer(
        title="startup",
        video=[str(video)],
        label="20260722120000",
        total=str(tmp_path),
        scale=0.4,
    )

    label = result["data"]["label"]
    report_dir = tmp_path / f"FX_{label}"
    command = commands[0]
    frame_index = command.index("--frame")
    total_index = command.index("--total")

    assert json.loads(command[frame_index + 1]) == {
        "label": label,
        "title": "startup",
        "video": [str(video)],
    }
    assert command[total_index + 1] == str(tmp_path)
    assert result["data"]["total"] == str(tmp_path)
    assert result["data"]["report_dir"] == str(report_dir)
    assert not hasattr(framix, "total")
    assert not hasattr(framix, "label")

    report_dir.mkdir()
    reported = await framix.fx_frame_reporter(str(report_dir))

    assert commands[1] == ("--merge", str(report_dir), "--debug")
    assert reported["data"]["report_dir"] == str(report_dir)


@pytest.mark.anyio
async def test_framix_core_rejects_invalid_label_date(tmp_path) -> None:
    video = tmp_path / "recording.mkv"
    video.write_bytes(b"video")

    with pytest.raises(RuntimeError, match="invalid date or time"):
        await Framix().fx_frame_analyzer(
            title="startup",
            video=[str(video)],
            label="20261301120000",
            total=str(tmp_path),
        )


@pytest.mark.anyio
async def test_framix_serializes_external_processes(monkeypatch) -> None:
    active = 0
    max_active = 0

    async def empty_stream():
        if False:
            yield b""

    class ProcessStub(object):
        def __init__(self, pid):
            self.pid = pid
            self.stdout = empty_stream()
            self.stderr = empty_stream()
            self.returncode = None

        async def wait(self):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.01)
            active -= 1
            self.returncode = 0

    process_id = 0

    async def cmd_link_exec(_cmd, *, env=None):
        nonlocal process_id
        _ = env
        process_id += 1
        return ProcessStub(process_id)

    framix = Framix()
    monkeypatch.setattr(framix, "run_lock", asyncio.Lock())
    monkeypatch.setattr(
        "backend.mcp_core.core_framix.Flux.cmd_link_exec",
        cmd_link_exec,
    )

    await asyncio.gather(
        framix._Framix__engine("first"),
        framix._Framix__engine("second"),
    )

    assert process_id == 2
    assert max_active == 1


@pytest.mark.anyio
async def test_recording_path_stays_with_record_session(
    monkeypatch,
    tmp_path,
) -> None:
    idle = IdleStub()
    device = SimpleNamespace(serial="device-1", device_props={"brand": "test"})
    record = Record(device=device, idle=idle, version="scrcpy 3.0")

    async def launcher(_cmd) -> None:
        return None

    async def check_timer(video_temp=None):
        record.start_event.set()
        return video_temp

    monkeypatch.setattr(record, "launcher", launcher)
    monkeypatch.setattr(record, "check_timer", check_timer)

    started = await record.scrcpy_record(str(tmp_path), fps=30, silence=True)
    path = started.data["path"]

    assert record.recording_path == path
    assert started.data["finalized"] is False
    assert idle.sessions[0]["args"]["path"] == path

    with open(path, "wb") as file:
        file.write(b"video")
    record.close_event.set()

    closed = await record.scrcpy_close()

    assert closed.data["path"] == path
    assert closed.data["finalized"] is True
