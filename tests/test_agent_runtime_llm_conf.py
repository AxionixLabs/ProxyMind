# -*- coding: utf-8 -*-

import asyncio
import typing

from mind_app.modes.agent.ws import build_runtime_llm_conf


def run_async(value: object) -> object:
    """同步测试中运行异步逻辑。"""
    return asyncio.run(value)


class DummyMind(object):
    """提供 Agent runtime 绑定测试所需的偏好快照。"""

    def __init__(self, pref_config: dict[str, typing.Any]) -> None:
        """保存测试偏好配置。"""
        self.pref_config = pref_config

    async def fresh_pref_config(self, *, ttl_sec: float | None = None) -> dict[str, typing.Any]:
        """返回测试偏好配置。"""
        _ = ttl_sec
        return self.pref_config


def test_agent_runtime_llm_conf_keeps_default_route() -> None:
    """WS runtime.bind 按配置上报默认 route。"""
    result = run_async(build_runtime_llm_conf(DummyMind({
        "primary": {
            "provider": "openai_compatible",
            "model": "custom-model",
            "apikey": "test-key",
            "base_url": "https://api.example.com/v1",
            "reasoning_effort": "xhigh",
            "route": "responses"
        }
    })))

    assert result == {
        "primary": {
            "provider": "openai_compatible",
            "route": "responses",
            "model": "custom-model",
            "apikey": "test-key",
            "base_url": "https://api.example.com/v1",
            "reasoning_effort": "xhigh"
        }
    }
    assert list(result["primary"].keys()) == ["provider", "route", "model", "apikey", "base_url", "reasoning_effort"]


def test_agent_runtime_llm_conf_keeps_explicit_non_default_route() -> None:
    """WS runtime.bind 保留显式非默认 route。"""
    result = run_async(build_runtime_llm_conf(DummyMind({
        "primary": {
            "provider": "openai_compatible",
            "model": "custom-model",
            "route": "chat_completions"
        }
    })))

    assert result == {
        "primary": {
            "provider": "openai_compatible",
            "route": "chat_completions",
            "model": "custom-model"
        }
    }
    assert list(result["primary"].keys()) == ["provider", "route", "model"]


def test_agent_runtime_llm_conf_uses_empty_slot_for_disabled_primary() -> None:
    """WS runtime.bind 对关闭的 primary 使用完整空字段。"""
    result = run_async(build_runtime_llm_conf(DummyMind({
        "primary": {
            "enabled": False,
            "provider": "openai_compatible",
            "model": "custom-model",
            "route": "chat_completions"
        }
    })))

    assert result == {
        "primary": {
            "provider": "",
            "route": "",
            "model": "",
            "apikey": "",
            "base_url": "",
            "reasoning_effort": ""
        }
    }
