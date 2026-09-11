# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import time
import typing
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agent.domain.policies import (
    PermissionSettings,
    resolve_permissions,
)
from infrastructure.config.layers import ConfigResolution
from infrastructure.config.preferences import (
    Preferences,
    config_to_preferences,
)
from infrastructure.config.session import ConfigSession
from observability import observe_exception

__all__ = ("SettingsSession",)


@dataclass(frozen=True)
class PreparedSettings:
    """保存目标权限和同步提交配置快照的操作；由 SettingsSession 创建。"""

    permissions: PermissionSettings
    commit: Callable[[], None]


class SettingsSession:
    """持有进程级配置、偏好快照与当前有效权限。

    配置写入和远端偏好刷新都通过本对象串行进入应用状态；调用方只读取已经解析的
    权限或偏好副本，不自行维护刷新时间和持久化后的有效值。
    """

    def __init__(
        self,
        config: ConfigSession,
        preferences: Preferences,
        permissions: PermissionSettings,
        *,
        refresh_ttl_sec: float = 1.0,
    ) -> None:
        """绑定配置资源和初始有效权限。"""
        if not isinstance(config, ConfigSession):
            raise TypeError("config session is required")
        if not isinstance(preferences, Preferences):
            raise TypeError("preferences are required")
        if not isinstance(permissions, PermissionSettings):
            raise TypeError("permission settings are required")
        self.config = config
        self.preferences = preferences
        self.permissions = permissions
        self._refreshed_at = time.monotonic()
        self._refresh_generation = 0
        self._refresh_ttl_sec = max(0.0, float(refresh_ttl_sec))
        self._refresh_lock = asyncio.Lock()

    def preference_config(self) -> dict[str, typing.Any]:
        """返回当前偏好的独立运行配置。"""
        return self.preferences.to_config()

    def prepare_workspace(
        self, workspace: Path, resolution: ConfigResolution,
    ) -> PreparedSettings:
        """准备目标偏好和权限，提交前保留当前配置上下文。"""
        permissions = resolve_permissions(resolution.config, interactive=True)
        preferences = config_to_preferences(resolution.config)

        def commit() -> None:
            """同步发布准备完成的工作区配置及其派生快照。"""
            self.config.bind_workspace(workspace)
            self.preferences.prefs = preferences
            self.permissions = permissions
            self._refreshed_at = time.monotonic()
            self._refresh_generation += 1

        return PreparedSettings(permissions, commit)

    def apply_permissions(
        self,
        settings: PermissionSettings,
    ) -> PermissionSettings:
        """持久化权限选择并保存重新解析后的有效权限。"""
        if not isinstance(settings, PermissionSettings):
            raise TypeError("permission settings are required")

        effective_config = self.config.update_user({
            ("sandbox_mode",): settings.sandbox_mode,
            ("approval_policy",): settings.approval_policy,
            ("approvals_reviewer",): settings.approvals_reviewer,
            ("network_access",): settings.network_access,
        }, ensure_effective={
            ("sandbox_mode",): settings.sandbox_mode,
            ("approval_policy",): settings.approval_policy,
            ("approvals_reviewer",): settings.approvals_reviewer,
            ("network_access",): settings.network_access,
        })
        effective = resolve_permissions(effective_config, interactive=True)
        self.permissions = effective
        return effective

    async def refresh_preferences_if_stale(
        self,
        *,
        ttl_sec: float | None = None,
    ) -> None:
        """在刷新窗口过期后重新加载偏好，失败时保留最后有效快照。"""
        refresh_ttl = (
            self._refresh_ttl_sec
            if ttl_sec is None
            else max(0.0, float(ttl_sec))
        )
        observed_generation = self._refresh_generation
        if self._refresh_is_current(refresh_ttl):
            return None

        async with self._refresh_lock:
            if self._refresh_generation != observed_generation:
                return None
            if self._refresh_is_current(refresh_ttl):
                return None
            try:
                await self.preferences.load_pref()
            except Exception as error:
                observe_exception(
                    "preferences.refresh.failed",
                    error,
                    level="WARNING",
                )
                return None
            self._refreshed_at = time.monotonic()
            self._refresh_generation += 1

    def _refresh_is_current(self, ttl_sec: float) -> bool:
        """判断当前偏好快照是否仍处于指定刷新窗口。"""
        if ttl_sec <= 0.0:
            return False
        return time.monotonic() - self._refreshed_at < ttl_sec

    async def fresh_preferences(
        self,
        *,
        ttl_sec: float | None = None,
    ) -> dict[str, typing.Any]:
        """刷新并返回当前偏好的独立运行配置。"""
        await self.refresh_preferences_if_stale(ttl_sec=ttl_sec)
        return self.preference_config()


if __name__ == '__main__':
    pass
