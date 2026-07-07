# -*- coding: utf-8 -*-

from pathlib import Path

from mind_core.preference import Preferences


def test_preferences_to_config_keeps_llm_payload_field_order(tmp_path: Path) -> None:
    """运行时 llm_conf 按约定字段顺序生成。"""
    prefs = Preferences(tmp_path / "config.toml")

    result = prefs.to_config(
        provider="openai_compatible",
        route="chat_completions",
        model="custom-model",
        apikey="test-key",
        base_url="https://api.example.com/v1"
    )

    assert result == {
        "primary": {
            "provider": "openai_compatible",
            "route": "chat_completions",
            "model": "custom-model",
            "apikey": "test-key",
            "base_url": "https://api.example.com/v1",
            "enabled": True
        },
        "secondary": {
            "provider": "",
            "route": "",
            "model": "",
            "apikey": "",
            "base_url": "",
            "enabled": False
        }
    }
    assert list(result["primary"].keys()) == ["provider", "route", "model", "apikey", "base_url", "enabled"]
