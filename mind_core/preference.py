# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import copy
import httpx
import typing
from engine.observability import (
    observe,
    observe_exception
)
from mind_core.config import config_to_preferences
from mind_core.config_session import ConfigSession
from mind_core.provider_config import (
    DEFAULT_PROVIDER_NAME,
    DEFAULT_REASONING_EFFORT,
    DEFAULT_ROUTE_NAME,
    SUPPORTED_REASONING_EFFORTS,
    default_route_for_provider

)
from mind_nova import const


def _default_slot() -> dict[str, typing.Any]:
    """返回单个模型槽位的默认配置。"""
    return {
        "provider"         : DEFAULT_PROVIDER_NAME,
        "route"            : DEFAULT_ROUTE_NAME,
        "model"            : "",
        "apikey"           : "",
        "base_url"         : "",
        "reasoning_effort" : DEFAULT_REASONING_EFFORT,
        "enabled"          : False
    }


def _default_prefs() -> dict[str, typing.Any]:
    """返回偏好配置的默认结构。"""
    return {
        "primary"      : _default_slot(),
        "hosted_tools" : _default_hosted_tools()
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


def _default_hosted_tools() -> dict[str, typing.Any]:
    """返回默认托管工具配置。"""
    return {
        "groups": {
            "perf_engine"   : False,
            "sandbox_cloud" : False
        }
    }


def _normalize_hosted_tools(value: typing.Any) -> dict[str, typing.Any]:
    """规范化托管工具偏好。"""
    data   = value if isinstance(value, dict) else {}
    groups = data.get("groups") if isinstance(data.get("groups"), dict) else {}

    return {
        "groups": {
            "perf_engine"   : _as_bool(groups.get("perf_engine"), False),
            "sandbox_cloud" : _as_bool(groups.get("sandbox_cloud"), False)
        }
    }


def _normalize_reasoning_effort(value: typing.Any) -> str:
    """规范化推理强度档位。"""
    text = str(value or "").strip().lower()
    return text if text in SUPPORTED_REASONING_EFFORTS else DEFAULT_REASONING_EFFORT


def apply_primary_model_override(
    pref_config: dict[str, typing.Any],
    model: str | None,
) -> dict[str, typing.Any]:
    """把临时模型选择合并到偏好配置副本。"""
    normalized = str(model or "").strip()
    if not normalized:
        return pref_config

    result = copy.deepcopy(pref_config)
    current = result.get("primary")
    primary = dict(current) if isinstance(current, dict) else {}
    primary["model"] = normalized
    primary["enabled"] = True
    result["primary"] = primary
    return result


class Preferences(object):
    """管理偏好配置的读取、规范化与落盘。"""

    def __init__(
        self,
        config_session: ConfigSession,
    ):
        """初始化配置来源和默认配置。"""
        self.config_session = config_session
        self.prefs          = _default_prefs()

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
        base_url: str = "",
        reasoning_effort: str = ""
    ) -> dict[str, typing.Any]:
        """基于当前配置生成运行时可用的配置副本。"""
        payload = copy.deepcopy(self.prefs)
        primary = payload.setdefault("primary", {})

        if provider:
            primary["provider"] = provider
            primary["enabled"]  = True
            if not route:
                primary["route"] = default_route_for_provider(provider)
        if route:
            primary["route"]   = route
            primary["enabled"] = True
        if model:
            primary["model"]   = model
            primary["enabled"] = True
        if apikey:
            primary["apikey"]  = apikey
            primary["enabled"] = True
        if base_url:
            primary["base_url"] = base_url
            primary["enabled"]  = True
        if reasoning_effort:
            primary["reasoning_effort"] = _normalize_reasoning_effort(reasoning_effort)
            primary["enabled"]          = True

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

        for key in ("provider", "route", "model", "apikey", "base_url", "reasoning_effort"):
            current  = str(merged.get(key) or "").strip()
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

        provider = str(
            slot.get("provider", DEFAULT_PROVIDER_NAME)
            or DEFAULT_PROVIDER_NAME
        )

        default_route = default_route_for_provider(provider)

        return {
            "provider": provider,
            "route": str(
                slot.get("route", default_route) or default_route
            ),
            "model": str(slot.get("model", "")),
            "apikey": str(slot.get("apikey", "")),
            "base_url": str(slot.get("base_url", "")),
            "reasoning_effort": _normalize_reasoning_effort(slot.get("reasoning_effort")),
            "enabled": _as_bool(slot.get("enabled"), False)
        }

    @classmethod
    def _normalize_pref_payload(
        cls,
        payload: dict[str, typing.Any]
    ) -> dict[str, typing.Any]:
        """将输入配置规范化为内部使用的偏好结构。"""
        primary = payload.get("primary") or {}

        prefs = {
            "primary"      : cls._normalize_slot(primary),
            "hosted_tools" : _normalize_hosted_tools(payload.get("hosted_tools"))
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
        merged["hosted_tools"] = _normalize_hosted_tools(
            merged.get("hosted_tools") or normalized_remote.get("hosted_tools")
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
            preferences = config_to_preferences(self.config_session.load())
            observe("preferences.source.loaded", source="local")
            return preferences
        except (OSError, TypeError, ValueError) as error:
            observe_exception(
                "preferences.source.fallback",
                error,
                level="WARNING",
                source="defaults",
            )
            return _default_prefs()

    async def load_pref(self) -> None:
        """读取本地配置并刷新运行时偏好。"""
        prefs = await self._load_config_pref()

        self.prefs    = self._normalize_pref_payload(prefs)
        primary       = self.prefs.get("primary") or {}
        hosted_groups = (self.prefs.get("hosted_tools") or {}).get("groups") or {}

        observe(
            "preferences.loaded",
            provider=primary.get("provider"),
            route=primary.get("route"),
            enabled=bool(primary.get("enabled")),
            model_configured=bool(primary.get("model")),
            hosted_groups_enabled=sum(bool(value) for value in hosted_groups.values()),
        )


if __name__ == '__main__':
    pass
