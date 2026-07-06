# -*- coding: utf-8 -*-

from pathlib import Path
import asyncio

import pytest
from mind_core.config import load_config
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
    assert config["model"]["primary"]["model"] == "gpt-5-codex"


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
    assert config["model"]["primary"]["model"] == ""
