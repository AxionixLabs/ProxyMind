# -*- coding: utf-8 -*-

from pathlib import Path

import pytest

from server.storage import load_pref, save_pref
from mind_core.config import ensure_config, load_config
from mind_core.provider_config import (
    DEFAULT_PROVIDER_NAME,
    DEFAULT_REASONING_EFFORT,
    DEFAULT_ROUTE_NAME
)


def test_config_service_pref_supports_openai_compatible_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """偏好配置支持 OpenAI compatible provider。"""
    monkeypatch.setenv("MIND_HOME", str(tmp_path))

    saved = save_pref({
        "primary": {
            "enabled": True,
            "provider": "openai_compatible",
            "type": "Auto",
            "base_url": "https://api.example.com/v1",
            "route": "chat_completions",
            "apikey": "test-key",
            "reasoning_effort": "high",
            "model": "custom-model"
        }
    })
    loaded = load_pref()
    config = load_config(tmp_path / "config.toml")

    assert {"value": "openai_compatible", "label": "OpenAI Compatible"} in loaded["providers"]
    assert saved["primary"]["provider"] == "openai_compatible"
    assert saved["primary"]["enabled"] is True
    assert loaded["primary"]["provider"] == "openai_compatible"
    assert loaded["primary"]["enabled"] is True
    assert loaded["primary"]["reasoning_effort"] == "high"
    assert config["model"]["primary"]["provider"] == "openai_compatible"
    assert config["model"]["primary"]["enabled"] is True
    assert config["model"]["primary"]["reasoning_effort"] == "high"
    assert list(saved["primary"].keys())[:5] == ["provider", "route", "model", "apikey", "base_url"]
    assert list(loaded["primary"].keys())[:5] == ["provider", "route", "model", "apikey", "base_url"]
    assert list(config["model"]["primary"].keys())[:5] == ["provider", "route", "model", "apikey", "base_url"]


def test_config_service_pref_defaults_to_disabled_slots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """新建配置默认关闭模型槽位。"""
    monkeypatch.setenv("MIND_HOME", str(tmp_path))

    ensure_config(tmp_path / "config.toml")
    loaded = load_pref()
    config = load_config(tmp_path / "config.toml")

    assert loaded["providers"][0] == {
        "value": "openai_compatible",
        "label": "OpenAI Compatible"
    }
    assert loaded["primary"]["enabled"] is False
    assert loaded["primary"]["provider"] == "openai_compatible"
    assert loaded["primary"]["route"] == "responses"
    assert loaded["primary"]["reasoning_effort"] == "medium"
    assert "secondary" not in loaded
    assert config["model"]["primary"]["enabled"] is False
    assert config["model"]["primary"]["provider"] == "openai_compatible"
    assert config["model"]["primary"]["route"] == "responses"
    assert config["model"]["primary"]["reasoning_effort"] == "medium"
    assert "secondary" not in config["model"]
    assert list(loaded["primary"].keys())[:5] == ["provider", "route", "model", "apikey", "base_url"]
    assert list(config["model"]["primary"].keys())[:5] == ["provider", "route", "model", "apikey", "base_url"]


def test_default_config_file_writes_select_defaults(tmp_path: Path) -> None:
    """新建 config.toml 写入下拉字段默认值。"""
    target = ensure_config(tmp_path / "config.toml")

    text = target.read_text(encoding="utf-8")

    assert f'provider = "{DEFAULT_PROVIDER_NAME}"' in text
    assert f'route = "{DEFAULT_ROUTE_NAME}"' in text
    assert f'reasoning_effort = "{DEFAULT_REASONING_EFFORT}"' in text


def test_config_service_pref_disabled_primary_keeps_model_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """关闭 primary 只影响是否用于请求，不清空模型槽位字段。"""
    monkeypatch.setenv("MIND_HOME", str(tmp_path))

    save_pref({
        "primary": {
            "enabled": True,
            "provider": "openai_compatible",
            "route": "chat_completions",
            "model": "custom-model",
            "apikey": "test-key",
            "base_url": "https://api.example.com/v1"
        }
    })
    saved = save_pref({
        "primary": {
            "enabled": False,
            "provider": "openai_compatible",
            "route": "chat_completions",
            "model": "custom-model",
            "apikey": "test-key",
            "base_url": "https://api.example.com/v1"
        }
    })
    config = load_config(tmp_path / "config.toml")

    assert saved["primary"]["enabled"] is False
    assert saved["primary"]["provider"] == "openai_compatible"
    assert saved["primary"]["route"] == "chat_completions"
    assert saved["primary"]["model"] == "custom-model"
    assert saved["primary"]["apikey"] == "test-key"
    assert saved["primary"]["base_url"] == "https://api.example.com/v1"
    assert saved["primary"]["reasoning_effort"] == "medium"
    assert config["model"]["primary"]["enabled"] is False
    assert config["model"]["primary"]["provider"] == "openai_compatible"
    assert config["model"]["primary"]["route"] == "chat_completions"
    assert config["model"]["primary"]["reasoning_effort"] == "medium"


def test_config_service_pref_ignores_secondary_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """保存偏好时忽略旧客户端传入的 secondary 槽位。"""
    monkeypatch.setenv("MIND_HOME", str(tmp_path))

    saved = save_pref({
        "primary": {"enabled": False},
        "secondary": {
            "enabled": False,
            "provider": "openai_compatible",
            "route": "responses",
            "model": "secondary-model",
            "apikey": "secondary-key",
            "base_url": "https://api.example.com/v1"
        }
    })
    config = load_config(tmp_path / "config.toml")

    assert "secondary" not in saved
    assert "secondary" not in config["model"]
