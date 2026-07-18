# -*- coding: utf-8 -*-

import asyncio

from mind_app.modes.support import repl_commands
from mind_app.modes.support import repl_mcp


class DummyMind(object):
    """记录 /mcp 菜单动作调用。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, bool | None]] = []

    async def start_external_mcp_runtime(self) -> None:
        """记录非破坏性启动调用。"""
        self.calls.append(("start", None))

    async def restart_external_mcp_runtime(self, *, include_disabled: bool = False) -> None:
        """记录重启调用。"""
        self.calls.append(("restart", include_disabled))

    async def stop_external_mcp_runtime(self) -> None:
        """记录停止调用。"""
        self.calls.append(("stop", None))


def test_mcp_start_action_is_non_destructive(monkeypatch) -> None:
    """start 动作调用非破坏性启动，不重启当前连接。"""
    mind = DummyMind()
    monkeypatch.setattr(repl_mcp, "render_mcp_status", lambda value: None)

    asyncio.run(repl_mcp.run_mcp_action(mind, "start"))

    assert mind.calls == [("start", None)]


def test_mcp_force_and_restart_actions_are_rebuilds(monkeypatch) -> None:
    """force/restart 动作保持重建语义。"""
    mind = DummyMind()
    monkeypatch.setattr(repl_mcp, "render_mcp_status", lambda value: None)

    asyncio.run(repl_mcp.run_mcp_action(mind, "force"))
    asyncio.run(repl_mcp.run_mcp_action(mind, "restart"))

    assert mind.calls == [
        ("restart", True),
        ("restart", False),
    ]


class DummyServerManager(object):
    """提供 Helix home 测试需要的服务管理器字段。"""

    url = "http://127.0.0.1:3333"


def test_helix_home_url_uses_verified_server_manager() -> None:
    """Helix home 打开服务管理器确认过的地址。"""
    mind = type("MindStub", (), {"server_manager": DummyServerManager()})()

    assert repl_commands.helix_runtime_home_url(mind) == "http://127.0.0.1:3333"
