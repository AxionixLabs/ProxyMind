# -*- coding: utf-8 -*-

import asyncio

from mind_app.runtime.environment import exec_env as runtime_env
from mind_nova.requests.payload import build_chat_payload


def run_async(value: object) -> object:
    """同步测试中运行异步逻辑。"""
    return asyncio.run(value)


def test_build_runtime_exec_env_keeps_helix_under_provider(
    monkeypatch
) -> None:
    """Helix 环境只挂在 providers.helix，不污染 Mind 顶层。"""
    monkeypatch.setattr(
        runtime_env,
        "exec_env",
        lambda: {
            "platform": {"system": "darwin"},
            "tools": {"rg": {"available": True}},
            "providers": {"other": {"ok": True}}
        }
    )

    source = {"tools": {"adb": {"available": True}}}
    data = runtime_env.build_runtime_exec_env(service_exec_env=source)

    assert data["platform"] == {"system": "darwin"}
    assert data["tools"] == {"rg": {"available": True}}
    assert data["providers"]["other"] == {"ok": True}
    assert data["providers"]["helix"] == source

    source["tools"]["adb"]["available"] = False
    assert data["providers"]["helix"]["tools"]["adb"]["available"] is True


def test_build_chat_payload_does_not_probe_runtime_env() -> None:
    """请求层只组装载荷，未注入 exec_env 时使用空对象。"""
    payload = run_async(
        build_chat_payload(
            "chat",
            {"model": "test"},
            "hello",
            [],
            skills=[]
        )
    )

    assert payload["exec_env"] == {}
