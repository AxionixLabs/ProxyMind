# -*- coding: utf-8 -*-

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from agent.domain.policies import preset_permissions
from infrastructure.config.preferences import Preferences
from infrastructure.config.session import ConfigSession
from infrastructure.config.settings_session import SettingsSession
from infrastructure.config.store import ConfigStore


def _settings(tmp_path: Path, *, refresh_ttl_sec: float = 1.0) -> SettingsSession:
    """创建绑定独立配置文件的设置会话。"""
    config = ConfigSession(ConfigStore(tmp_path / "config.toml"))
    preferences = Preferences(config)
    return SettingsSession(
        config,
        preferences,
        preset_permissions("auto"),
        refresh_ttl_sec=refresh_ttl_sec,
    )


def test_settings_session_persists_effective_permissions(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    selected = preset_permissions("full-access")

    effective = settings.apply_permissions(selected)

    assert effective == selected
    assert settings.permissions == selected
    config = settings.config.load()
    assert {
        "sandbox_mode": config["sandbox_mode"],
        "approval_policy": config["approval_policy"],
        "approvals_reviewer": config["approvals_reviewer"],
        "network_access": config["network_access"],
    } == {
        "sandbox_mode": "danger-full-access",
        "approval_policy": "never",
        "approvals_reviewer": "user",
        "network_access": "restricted",
    }


@pytest.mark.anyio
async def test_settings_session_refreshes_only_after_ttl(tmp_path: Path) -> None:
    settings = _settings(tmp_path, refresh_ttl_sec=60.0)
    settings.preferences.load_pref = AsyncMock()

    await settings.refresh_preferences_if_stale()
    await settings.refresh_preferences_if_stale(ttl_sec=0.0)

    settings.preferences.load_pref.assert_awaited_once_with()


@pytest.mark.anyio
async def test_settings_session_serializes_concurrent_refreshes(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path, refresh_ttl_sec=60.0)
    refresh_started = asyncio.Event()
    release_refresh = asyncio.Event()

    async def refresh() -> None:
        refresh_started.set()
        await release_refresh.wait()

    settings.preferences.load_pref = AsyncMock(side_effect=refresh)
    first = asyncio.create_task(settings.refresh_preferences_if_stale(
        ttl_sec=0.0,
    ))
    await refresh_started.wait()
    second = asyncio.create_task(settings.refresh_preferences_if_stale(
        ttl_sec=0.0,
    ))
    await asyncio.sleep(0)
    release_refresh.set()
    await asyncio.gather(first, second)

    settings.preferences.load_pref.assert_awaited_once_with()


@pytest.mark.anyio
async def test_settings_session_keeps_last_snapshot_on_refresh_failure(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    settings.preferences.prefs["primary"]["model"] = "last-good"
    settings.preferences.load_pref = AsyncMock(
        side_effect=OSError("config unavailable"),
    )

    snapshot = await settings.fresh_preferences(ttl_sec=0.0)

    assert snapshot["primary"]["model"] == "last-good"
