# -*- coding: utf-8 -*-

from types import SimpleNamespace

import pytest

from mind_app.controller import Mind


@pytest.mark.anyio
async def test_controller_stops_subagents_before_shared_resources() -> None:
    timeline = []
    controller = Mind.__new__(Mind)

    async def step(name):
        timeline.append(name)

    controller.cancel_service_runtime_startup = (
        lambda: step("service_startup")
    )
    controller.subagents = SimpleNamespace(
        shutdown=lambda: step("subagents"),
    )
    controller.hook_registry = SimpleNamespace(
        close=lambda: step("hooks"),
    )
    controller.native_coding = SimpleNamespace(
        close=lambda: step("native_coding"),
    )
    controller._native_coding_close_tasks = set()
    controller.stop_external_mcp_runtime = lambda: step("external_mcp")
    controller.stop_config_service = lambda: step("config_service")
    controller.stop_keepalive_supervisor = lambda: step("keepalive")
    controller.server_manager = None
    controller.stop_runtime_on_exit = False
    controller.report = SimpleNamespace(close=lambda: timeline.append("report"))

    await Mind.close_runtime_resources(controller)

    assert timeline == [
        "service_startup",
        "subagents",
        "hooks",
        "native_coding",
        "external_mcp",
        "config_service",
        "keepalive",
        "report",
    ]
