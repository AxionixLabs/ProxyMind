# -*- coding: utf-8 -*-

from mind_nova.requests.compact import (
    build_compact_payload,
    compact_failed_event
)
from mind_core.design.status.driver import DesignStatusLiveDriver


def test_build_compact_payload_defaults_strategy_and_mode() -> None:
    """手动压缩请求载荷补齐默认策略和模式。"""
    payload = build_compact_payload({
        "cid": " cid_test ",
        "sid": " sid_test ",
        "llm_conf": {
            "primary": {
                "model": "custom-model",
                "enabled": True
            }
        }
    })

    assert payload == {
        "mode"     : "chat",
        "cid"      : "cid_test",
        "sid"      : "sid_test",
        "llm_conf" : {
            "primary": {
                "provider": "",
                "route": "",
                "model": "custom-model",
                "apikey": "",
                "base_url": ""
            }
        },
        "strategy" : "memento"
    }


def test_build_compact_payload_omits_disabled_slot_values() -> None:
    """关闭的模型 slot 不向请求传递空 provider。"""
    payload = build_compact_payload({
        "cid": "cid_test",
        "sid": "sid_test",
        "llm_conf": {
            "primary": {
                "provider": "",
                "route": "",
                "model": "",
                "enabled": False
            }
        }
    })

    assert payload["llm_conf"] == {
        "primary": {
            "provider": "",
            "route": "",
            "model": "",
            "apikey": "",
            "base_url": ""
        }
    }


def test_compact_failed_event_uses_pending_tool_call_message() -> None:
    """409 压缩失败事件提示等待工具完成。"""
    event = compact_failed_event(
        status_code=409,
        error="pending tool calls: call_xxx"
    )

    assert event == {
        "type"        : "conversation.compact.failed",
        "status"      : "failed",
        "status_code" : 409,
        "message"     : "A tool call is still running. Try again after it finishes.",
        "error"       : "pending tool calls: call_xxx"
    }


def test_external_mcp_status_accepts_summary_override() -> None:
    """外部 MCP 动画可用自定义摘要渲染非 MCP 阶段消息。"""
    text = DesignStatusLiveDriver.external_mcp_status_text({
        "summary" : "Context compacted. · 120 -> 8 items",
        "done"    : True,
        "items"   : [{"name": "Compact", "state": "ready"}]
    })

    assert text == "Context compacted. · 120 -> 8 items"
