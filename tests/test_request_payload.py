# -*- coding: utf-8 -*-

import asyncio

from mind_nova.requests.payload import build_chat_payload


def test_build_chat_payload_strips_llm_enabled_fields() -> None:
    """请求载荷不包含本地偏好开关字段。"""
    payload = asyncio.run(build_chat_payload(
        "chat",
        {
            "profile_key": "default",
            "primary": {
                "provider": "openai_compatible",
                "route": "responses",
                "model": "custom-model",
                "reasoning_effort": "high",
                "enabled": True
            },
            "providers": [{"value": "openai_compatible"}],
            "secondary": {
                "provider": "",
                "route": "",
                "model": "",
                "enabled": False
            }
        },
        "hello",
        []
    ))

    assert payload["llm_conf"] == {
        "primary": {
            "provider": "openai_compatible",
            "route": "responses",
            "model": "custom-model",
            "apikey": "",
            "base_url": "",
            "reasoning_effort": "high"
        },
    }
    assert payload["hosted_tools"] == {
        "enabled_groups": []
    }


def test_build_chat_payload_uses_empty_primary_when_disabled() -> None:
    """关闭 primary 时请求侧发送完整空字段供服务端补齐。"""
    payload = asyncio.run(build_chat_payload(
        "chat",
        {
            "primary": {
                "provider": "",
                "route": "",
                "model": "",
                "enabled": False
            }
        },
        "hello",
        []
    ))

    assert payload["llm_conf"] == {
        "primary": {
            "provider": "",
            "route": "",
            "model": "",
            "apikey": "",
            "base_url": "",
            "reasoning_effort": ""
        }
    }


def test_build_chat_payload_maps_hosted_tool_groups() -> None:
    """请求载荷按偏好配置发送托管工具分组 allowlist。"""
    payload = asyncio.run(build_chat_payload(
        "chat",
        {
            "primary": {"enabled": False},
            "hosted_tools": {
                "groups": {
                    "perf_engine": True,
                    "sandbox_cloud": False
                }
            }
        },
        "hello",
        []
    ))

    assert payload["hosted_tools"] == {
        "enabled_groups": ["perf_engine"]
    }


def test_build_chat_payload_disables_all_hosted_tools_by_groups() -> None:
    """关闭全部托管工具分组时请求侧发送空分组。"""
    payload = asyncio.run(build_chat_payload(
        "chat",
        {
            "primary": {"enabled": False},
            "hosted_tools": {
                "groups": {
                    "perf_engine": False,
                    "sandbox_cloud": False
                }
            }
        },
        "hello",
        []
    ))

    assert payload["hosted_tools"] == {
        "enabled_groups": []
    }
