# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_core.config import model_config_values
from mind_core.config_session import ConfigSession
from mind_core.provider_config import (
    DEFAULT_PROVIDER_NAME,
    DEFAULT_REASONING_EFFORT,
    SUPPORTED_REASONING_EFFORTS,
    SUPPORTED_PROVIDER_OPTIONS,
    default_route_for_provider
)
from mind_core.service_config import normalize_domain

DEFAULT_PROFILE_KEY = "default"
DEFAULT_MODEL_TYPE  = "Auto"
HOSTED_TOOL_GROUPS  = ("perf_engine", "sandbox_cloud")


def load_pref(config_session: ConfigSession) -> dict[str, typing.Any]:
    """读取模型偏好配置。"""
    config  = config_session.load()
    model   = config.get("model") if isinstance(config, dict) else {}
    primary = model.get("primary") if isinstance(model, dict) else {}

    return {
        "profile_key"  : DEFAULT_PROFILE_KEY,
        "providers"    : [dict(item) for item in SUPPORTED_PROVIDER_OPTIONS],
        "primary"      : config_slot_to_pref(primary),
        "hosted_tools" : hosted_tools_to_pref(config.get("hosted_tools"))
    }


def save_pref(
    config_session: ConfigSession,
    raw: typing.Any
) -> dict[str, typing.Any]:
    """保存模型偏好配置。"""
    payload = raw if isinstance(raw, dict) else {}

    primary = pref_to_config_slot(
        payload.get("primary"),
        enabled=pref_slot_enabled(payload.get("primary"))
    )

    hosted = pref_to_hosted_tools(payload.get("hosted_tools"))
    groups = hosted["groups"]
    values = model_config_values(primary)

    values.update({
        ("hosted_tools", "groups", name): value
        for name, value in groups.items()
    })

    config_session.update_user(values)

    return load_pref(config_session)


def load_service_config(
    config_session: ConfigSession
) -> dict[str, typing.Any]:
    """读取远程服务域名配置。"""
    config  = config_session.load()
    service = config.get("service") if isinstance(config, dict) else {}
    domain  = normalize_domain(service.get("domain") if isinstance(service, dict) else "")

    return {
        "domain"     : domain,
        "configured" : bool(domain)
    }


def save_service_config(
    config_session: ConfigSession,
    raw: typing.Any
) -> dict[str, typing.Any]:
    """保存远程服务域名配置。"""
    payload = raw if isinstance(raw, dict) else {}

    config_session.update_user({
        ("service", "domain"): normalize_domain(payload.get("domain")),
    })

    return load_service_config(config_session)


def config_slot_to_pref(slot: typing.Any) -> dict[str, typing.Any]:
    """把 config.toml 模型槽位转换为偏好接口结构。"""
    data       = slot if isinstance(slot, dict) else {}
    is_enabled = bool(data.get("enabled"))
    provider   = clean_text(data.get("provider"), DEFAULT_PROVIDER_NAME)

    return {
        "provider": provider,
        "route": clean_text(
            data.get("route"),
            default_route_for_provider(provider)
        ),
        "model": clean_text(data.get("model")),
        "apikey": clean_text(data.get("apikey")),
        "base_url": clean_text(data.get("base_url")),
        "reasoning_effort": normalize_reasoning_effort(data.get("reasoning_effort")),
        "enabled": is_enabled,
        "type": clean_text(data.get("type"), DEFAULT_MODEL_TYPE),
        "notes": clean_text(data.get("notes"))
    }


def pref_to_config_slot(slot: typing.Any, *, enabled: bool | None) -> dict[str, typing.Any]:
    """把偏好接口结构转换为 config.toml 模型槽位。"""
    data       = slot if isinstance(slot, dict) else {}
    is_enabled = bool(enabled)
    provider   = clean_text(data.get("provider"), DEFAULT_PROVIDER_NAME)

    result: dict[str, typing.Any] = {
        "provider": provider,
        "route": clean_text(
            data.get("route"),
            default_route_for_provider(provider)
        ),
        "model": clean_text(data.get("model")),
        "apikey": clean_text(data.get("apikey")),
        "base_url": clean_text(data.get("base_url")),
        "reasoning_effort": normalize_reasoning_effort(data.get("reasoning_effort")),
        "enabled": is_enabled
    }

    return result


def hosted_tools_to_pref(raw: typing.Any) -> dict[str, typing.Any]:
    """把配置中的托管工具开关转换为偏好接口结构。"""
    data   = raw if isinstance(raw, dict) else {}
    groups = data.get("groups") if isinstance(data.get("groups"), dict) else {}

    return {
        "groups": {
            name: bool(groups.get(name, False))
            for name in HOSTED_TOOL_GROUPS
        }
    }


def pref_to_hosted_tools(raw: typing.Any) -> dict[str, typing.Any]:
    """把偏好接口中的托管工具开关转换为配置结构。"""
    data   = raw if isinstance(raw, dict) else {}
    groups = data.get("groups") if isinstance(data.get("groups"), dict) else {}

    return {
        "groups": {
            name: bool(groups.get(name, False))
            for name in HOSTED_TOOL_GROUPS
        }
    }


def normalize_reasoning_effort(value: typing.Any) -> str:
    """规范化 reasoning effort 档位。"""
    text = str(value or "").strip().lower()
    return text if text in SUPPORTED_REASONING_EFFORTS else DEFAULT_REASONING_EFFORT


def pref_slot_enabled(slot: typing.Any) -> bool:
    """判断偏好槽位是否启用。"""
    if not isinstance(slot, dict):
        return False
    return bool(slot.get("enabled"))


def clean_text(value: typing.Any, default: str = "") -> str:
    """把输入转换为去空白字符串。"""
    text = str(value if value is not None else default).strip()
    return text or default


if __name__ == "__main__":
    pass
