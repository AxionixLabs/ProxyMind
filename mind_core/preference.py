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


def _default_slot() -> dict[str, str]:
    """返回单个模型槽位的默认配置。"""
    return {
        "provider" : DEFAULT_PROVIDER_NAME,
        "model"    : "",
        "apikey"   : "",
        "base_url" : "",
        "route"    : DEFAULT_ROUTE_NAME
    }


def _default_prefs() -> dict[str, typing.Any]:
    """返回偏好配置的默认结构。"""
    return {
        "primary" : _default_slot()
    }


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
        model: str = "",
        apikey: str = "",
        base_url: str = "",
        route: str = ""
    ) -> dict[str, typing.Any]:
        """基于当前配置生成运行时可用的配置副本。"""
        payload = copy.deepcopy(self.prefs)
        primary = payload.setdefault("primary", {})

        if provider:
            primary["provider"] = provider
        if model:
            primary["model"] = model
        if apikey:
            primary["apikey"] = apikey
        if base_url:
            primary["base_url"] = base_url
        if route:
            primary["route"] = route

        return payload

    @property
    def pref_api(self) -> str:
        """返回偏好配置接口地址。"""
        return const.BASE_URL.rstrip("/") + "/api/pref"

    @staticmethod
    def _slot_configured(slot: typing.Any) -> bool:
        """判断 secondary 是否满足有效写入条件。"""
        if not isinstance(slot, dict):
            return False

        return bool(
            str(slot.get("model", "")).strip()
            and str(slot.get("apikey", "")).strip()
        )

    @staticmethod
    def _merge_missing_slot(
        base: dict[str, typing.Any],
        supplement: dict[str, typing.Any]
    ) -> dict[str, typing.Any]:
        """使用补充配置填充空字段，不覆盖已有值。"""
        merged = dict(base or {})

        for key in ("provider", "model", "apikey", "base_url", "route"):
            current = str(merged.get(key) or "").strip()
            incoming = str(supplement.get(key) or "").strip()
            if not current and incoming:
                merged[key] = incoming

        return merged

    @classmethod
    def _normalize_pref_payload(
        cls,
        payload: dict[str, typing.Any]
    ) -> dict[str, typing.Any]:
        """将输入配置规范化为内部使用的偏好结构。"""
        primary   = payload.get("primary") or {}
        secondary = payload.get("secondary")

        prefs = {
            "primary": {
                "provider" : str(primary.get("provider", DEFAULT_PROVIDER_NAME)),
                "model"    : str(primary.get("model", "")),
                "apikey"   : str(primary.get("apikey", "")),
                "base_url" : str(primary.get("base_url", "")),
                "route"    : str(primary.get("route", DEFAULT_ROUTE_NAME) or DEFAULT_ROUTE_NAME)
            }
        }

        if cls._slot_configured(secondary):
            prefs["secondary"] = {
                "provider" : str(secondary.get("provider", DEFAULT_PROVIDER_NAME)),
                "model"    : str(secondary.get("model", "")),
                "apikey"   : str(secondary.get("apikey", "")),
                "base_url" : str(secondary.get("base_url", "")),
                "route"    : str(secondary.get("route", DEFAULT_ROUTE_NAME) or DEFAULT_ROUTE_NAME)
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
