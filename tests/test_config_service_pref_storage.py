# -*- coding: utf-8 -*-

from pathlib import Path

import pytest

from server.storage import load_pref, save_pref
from mind_core.config import ensure_config, load_config


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
            "type": "Text",
            "base_url": "https://api.example.com/v1",
            "route": "chat_completions",
            "apikey": "test-key",
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
    assert config["model"]["primary"]["provider"] == "openai_compatible"
    assert config["model"]["primary"]["enabled"] is True
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
    assert loaded["primary"]["provider"] == ""
    assert loaded["primary"]["route"] == ""
    assert loaded["secondary"]["enabled"] is False
    assert loaded["secondary"]["provider"] == ""
    assert loaded["secondary"]["route"] == ""
    assert config["model"]["primary"]["enabled"] is False
    assert config["model"]["primary"]["provider"] == ""
    assert config["model"]["primary"]["route"] == ""
    assert config["model"]["secondary"]["enabled"] is False
    assert config["model"]["secondary"]["provider"] == ""
    assert config["model"]["secondary"]["route"] == ""
    assert list(loaded["primary"].keys())[:5] == ["provider", "route", "model", "apikey", "base_url"]
    assert list(config["model"]["primary"].keys())[:5] == ["provider", "route", "model", "apikey", "base_url"]


def test_config_service_pref_disabled_primary_clears_model_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """关闭 primary 会清空模型槽位字段。"""
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
    assert saved["primary"]["provider"] == ""
    assert saved["primary"]["route"] == ""
    assert saved["primary"]["model"] == ""
    assert saved["primary"]["apikey"] == ""
    assert saved["primary"]["base_url"] == ""
    assert config["model"]["primary"]["enabled"] is False
    assert config["model"]["primary"]["provider"] == ""
    assert config["model"]["primary"]["route"] == ""


def test_config_service_pref_secondary_returns_disabled_slot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """secondary 关闭时接口仍返回槽位对象。"""
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

    assert isinstance(saved["secondary"], dict)
    assert saved["secondary"]["enabled"] is False
    assert saved["secondary"]["provider"] == ""
    assert saved["secondary"]["route"] == ""
    assert saved["secondary"]["model"] == ""
