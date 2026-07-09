# -*- coding: utf-8 -*-

from pathlib import Path
import asyncio

import pytest
from mind_core.config import load_config
from mind_core.preference import Preferences
from mind_core.provider_config import (
    DEFAULT_PROVIDER_NAME,
    DEFAULT_REASONING_EFFORT,
    DEFAULT_ROUTE_NAME
)
from mind_app.modes.support.repl_prompt import save_primary_pref_field


def run_async(value: object) -> object:
    """同步测试中运行异步配置写入。"""
    return asyncio.run(value)


def test_save_primary_pref_field_updates_config_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """`/model` 底层写入 config.toml 并刷新运行时偏好。"""
    monkeypatch.setenv("MIND_HOME", str(tmp_path))

    snapshot = run_async(save_primary_pref_field("model", "gpt-5-codex"))
    config_file = tmp_path / "config.toml"
    config = load_config(config_file)

    assert snapshot["primary"]["model"] == "gpt-5-codex"
    assert snapshot["primary"]["enabled"] is True
    assert config["model"]["primary"]["model"] == "gpt-5-codex"
    assert config["model"]["primary"]["enabled"] is True


def test_save_primary_pref_field_accepts_empty_model_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """空模型名称会被持久化为空字符串。"""
    monkeypatch.setenv("MIND_HOME", str(tmp_path))

    run_async(save_primary_pref_field("model", "gpt-5-codex"))
    snapshot = run_async(save_primary_pref_field("model", " "))
    config_file = tmp_path / "config.toml"
    config = load_config(config_file)

    assert snapshot["primary"]["model"] == ""
    assert snapshot["primary"]["enabled"] is True
    assert config["model"]["primary"]["model"] == ""
    assert config["model"]["primary"]["enabled"] is True


def test_save_primary_pref_field_updates_reasoning_effort(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """`/effort` 底层写入推理强度。"""
    monkeypatch.setenv("MIND_HOME", str(tmp_path))

    snapshot = run_async(save_primary_pref_field("reasoning_effort", "high"))
    config_file = tmp_path / "config.toml"
    config = load_config(config_file)

    assert snapshot["primary"]["reasoning_effort"] == "high"
    assert snapshot["primary"]["enabled"] is True
    assert config["model"]["primary"]["reasoning_effort"] == "high"
    assert config["model"]["primary"]["enabled"] is True


def test_save_primary_pref_field_normalizes_reasoning_effort(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """非法推理强度会按默认档位持久化。"""
    monkeypatch.setenv("MIND_HOME", str(tmp_path))

    snapshot = run_async(save_primary_pref_field("reasoning_effort", "invalid"))
    config_file = tmp_path / "config.toml"
    config = load_config(config_file)

    assert snapshot["primary"]["reasoning_effort"] == DEFAULT_REASONING_EFFORT
    assert config["model"]["primary"]["reasoning_effort"] == DEFAULT_REASONING_EFFORT


def test_preferences_load_pref_uses_default_reasoning_effort(tmp_path: Path) -> None:
    """运行时偏好读取新建配置中的默认推理强度。"""
    prefs = Preferences(tmp_path / "config.toml")

    run_async(prefs.load_pref())

    assert prefs.prefs["primary"]["reasoning_effort"] == "medium"


def test_preferences_default_slot_uses_select_defaults(tmp_path: Path) -> None:
    """运行时偏好默认槽位使用下拉字段默认值。"""
    prefs = Preferences(tmp_path / "config.toml")

    assert prefs.prefs["primary"]["provider"] == DEFAULT_PROVIDER_NAME
    assert prefs.prefs["primary"]["route"] == DEFAULT_ROUTE_NAME
    assert prefs.prefs["primary"]["reasoning_effort"] == DEFAULT_REASONING_EFFORT
