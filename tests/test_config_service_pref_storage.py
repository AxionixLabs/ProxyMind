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
    assert loaded["primary"]["provider"] == "openai_compatible"
    assert config["model"]["primary"]["provider"] == "openai_compatible"
    assert list(saved["primary"].keys())[:5] == ["provider", "route", "model", "apikey", "base_url"]
    assert list(loaded["primary"].keys())[:5] == ["provider", "route", "model", "apikey", "base_url"]
    assert list(config["model"]["primary"].keys())[:5] == ["provider", "route", "model", "apikey", "base_url"]


def test_config_service_pref_defaults_to_openai_compatible_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """新建配置默认使用 OpenAI compatible provider。"""
    monkeypatch.setenv("MIND_HOME", str(tmp_path))

    ensure_config(tmp_path / "config.toml")
    loaded = load_pref()
    config = load_config(tmp_path / "config.toml")

    assert loaded["providers"][0] == {
        "value": "openai_compatible",
        "label": "OpenAI Compatible"
    }
    assert loaded["primary"]["provider"] == "openai_compatible"
    assert config["model"]["primary"]["provider"] == "openai_compatible"
    assert list(loaded["primary"].keys())[:5] == ["provider", "route", "model", "apikey", "base_url"]
    assert list(config["model"]["primary"].keys())[:5] == ["provider", "route", "model", "apikey", "base_url"]
