# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import copy
import httpx
import typing
from mind_core.config import (
    config_to_preferences,
    ensure_config,
    load_config
)
from mind_core.provider_config import (
    DEFAULT_PROVIDER_NAME,
    DEFAULT_ROUTE_NAME
)
from mind_nova import const


def _default_slot() -> dict[str, typing.Any]:
    """返回单个模型槽位的默认配置。"""
    return {
        "provider" : "",
        "route"    : "",
        "model"    : "",
        "apikey"   : "",
        "base_url" : "",
        "enabled"  : False
    }


def _default_prefs() -> dict[str, typing.Any]:
    """返回偏好配置的默认结构。"""
    return {
        "primary"   : _default_slot(),
        "secondary" : _default_slot()
    }


def _as_bool(value: typing.Any, default: bool = False) -> bool:
    """把输入值规范化为布尔值。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return default


class Preferences(object):
    """管理偏好配置的读取、规范化与落盘。"""

    def __init__(
        self,
        config_file: typing.Any
    ):
        """初始化配置文件路径和默认配置。"""
        self.config_file = config_file
        self.prefs       = _default_prefs()

    def __getstate__(self):
        """提供序列化时的状态导出。"""
        return self.prefs

    def __setstate__(self, state):
        """在反序列化时恢复内部状态。"""
        self.prefs = state

    def to_config(
        self,
        *,
        provider: str = "",
        route: str = "",
        model: str = "",
        apikey: str = "",
        base_url: str = ""
    ) -> dict[str, typing.Any]:
        """基于当前配置生成运行时可用的配置副本。"""
        payload = copy.deepcopy(self.prefs)
        primary = payload.setdefault("primary", {})

        if provider:
            primary["provider"] = provider
            primary["enabled"] = True
        if route:
            primary["route"] = route
            primary["enabled"] = True
        if model:
            primary["model"] = model
            primary["enabled"] = True
        if apikey:
            primary["apikey"] = apikey
            primary["enabled"] = True
        if base_url:
            primary["base_url"] = base_url
            primary["enabled"] = True

        return payload

    @property
    def pref_api(self) -> str:
        """返回偏好配置接口地址。"""
        return const.BASE_URL.rstrip("/") + "/api/pref"

    @staticmethod
    def _merge_missing_slot(
        base: dict[str, typing.Any],
        supplement: dict[str, typing.Any]
    ) -> dict[str, typing.Any]:
        """使用补充配置填充空字段，不覆盖已有值。"""
        merged = dict(base or {})

        for key in ("provider", "route", "model", "apikey", "base_url"):
            current = str(merged.get(key) or "").strip()
            incoming = str(supplement.get(key) or "").strip()
            if not current and incoming:
                merged[key] = incoming

        return merged

    @classmethod
    def _normalize_slot(
        cls,
        raw: typing.Any
    ) -> dict[str, typing.Any]:
        """规范化单个模型槽位配置。"""
        slot = raw if isinstance(raw, dict) else {}
        enabled = _as_bool(slot.get("enabled"), False)
        if not enabled:
            return _default_slot()

        return {
            "provider" : str(slot.get("provider", DEFAULT_PROVIDER_NAME) or DEFAULT_PROVIDER_NAME),
            "route"    : str(slot.get("route", DEFAULT_ROUTE_NAME) or DEFAULT_ROUTE_NAME),
            "model"    : str(slot.get("model", "")),
            "apikey"   : str(slot.get("apikey", "")),
            "base_url" : str(slot.get("base_url", "")),
            "enabled"  : True
        }

    @classmethod
    def _normalize_pref_payload(
        cls,
        payload: dict[str, typing.Any]
    ) -> dict[str, typing.Any]:
        """将输入配置规范化为内部使用的偏好结构。"""
        primary   = payload.get("primary") or {}
        secondary = payload.get("secondary")

        prefs = {
            "primary"   : cls._normalize_slot(primary),
            "secondary" : cls._normalize_slot(secondary)
        }

        return prefs

    @classmethod
    def _merge_remote_supplement(
        cls,
        base: dict[str, typing.Any],
        remote: dict[str, typing.Any]
    ) -> dict[str, typing.Any]:
        """以本地配置为主，使用补充配置填充空字段。"""
        normalized_remote = cls._normalize_pref_payload(remote)

        merged = copy.deepcopy(base or _default_prefs())

        merged["primary"] = cls._merge_missing_slot(
            dict(merged.get("primary") or {}),
            dict(normalized_remote.get("primary") or {})
        )

        if isinstance(merged.get("secondary"), dict) and isinstance(normalized_remote.get("secondary"), dict):
            merged["secondary"] = cls._merge_missing_slot(
                dict(merged.get("secondary") or {}),
                dict(normalized_remote.get("secondary") or {})
            )

        return merged

    def _apply_primary_slot(self, payload: dict[str, typing.Any]) -> None:
        """将输入配置规范化为内部使用的偏好结构。"""
        self.prefs = self._normalize_pref_payload(payload)

    async def _fetch_remote_pref(self) -> dict[str, typing.Any]:
        """从配置接口读取偏好配置。"""
        async with httpx.AsyncClient(timeout=3.0, trust_env=False) as client:
            resp = await client.get(self.pref_api)
            resp.raise_for_status()
            payload = resp.json()

        if not isinstance(payload, dict):
            return {}
        return payload.get("data") or {}

    async def _load_config_pref(self) -> dict[str, typing.Any]:
        """读取本地 config.toml 并转换为运行时偏好结构。"""
        try:
            target = ensure_config(self.config_file)
            return config_to_preferences(load_config(target))
        except (OSError, TypeError, ValueError):
            return _default_prefs()

    async def load_pref(self) -> None:
        """读取本地配置并刷新运行时偏好。"""
        prefs = await self._load_config_pref()
        self.prefs = self._normalize_pref_payload(prefs)


if __name__ == '__main__':
    pass
