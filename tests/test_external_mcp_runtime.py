# -*- coding: utf-8 -*-

import asyncio
from mind_app.runtime.mcp import external as external_runtime


class DummyMind(object):
    """提供外部 MCP runtime 测试所需的最小 Mind 接口。"""

    src_opera_place = "."

    async def start_external_mcp_anim(self, snapshot):
        _ = snapshot

    async def stop_anim(self):
        return None

    async def await_cleanup(self, awaitable):
        return await awaitable


class DummyGroup(object):
    """记录测试连接关闭状态。"""

    def __init__(self) -> None:
        self.closed = False


class DummyContext(object):
    """模拟外部 MCP group 上下文。"""

    def __init__(self, group: DummyGroup) -> None:
        self.group = group
        self.entered_servers = None

    async def __aenter__(self):
        return self.group

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        _ = exc_type, exc_val, exc_tb
        self.group.closed = True


def test_external_mcp_stop_resets_started(monkeypatch) -> None:
    async def run_case() -> None:
        runtime = external_runtime.ExternalMcpRuntime(DummyMind())
        await runtime.start()

        assert runtime.started is True
        assert runtime.group is group

        await runtime.stop()

        assert runtime.started is False
        assert runtime.group is None
        assert group.closed is True

    group = DummyGroup()

    def fake_group(servers, status=None):
        _ = servers, status
        return DummyContext(group)

    monkeypatch.setattr(external_runtime, "load_mcp_servers_file", lambda root: [])
    monkeypatch.setattr(external_runtime, "open_optional_external_mcp_group", fake_group)

    asyncio.run(run_case())


def test_external_mcp_force_start_temporarily_enables_disabled(monkeypatch) -> None:
    async def run_case() -> None:
        runtime = external_runtime.ExternalMcpRuntime(DummyMind())
        await runtime.start(include_disabled=True)

        assert captured["servers"] == [
            {"name": "playwright", "enabled": True, "transport": "stdio"}
        ]

    captured = {}

    def fake_load(root):
        _ = root
        return [{"name": "playwright", "enabled": False, "transport": "stdio"}]

    def fake_group(servers, status=None):
        _ = status
        captured["servers"] = servers
        return DummyContext(DummyGroup())

    monkeypatch.setattr(external_runtime, "load_mcp_servers_file", fake_load)
    monkeypatch.setattr(external_runtime, "open_optional_external_mcp_group", fake_group)

    asyncio.run(run_case())
