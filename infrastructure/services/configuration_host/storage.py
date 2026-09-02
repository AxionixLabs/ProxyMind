# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

from infrastructure.config.providers import (
    DEFAULT_PROVIDER_KIND,
    DEFAULT_REASONING_EFFORT,
    SUPPORTED_REASONING_EFFORTS,
    SUPPORTED_PROVIDER_OPTIONS,
    default_route_for_kind,
    is_valid_provider_id,
    supported_routes_for_kind
)
from infrastructure.config.schema import provider_profile_values
from infrastructure.config.session import ConfigSession
from infrastructure.services.service_config import normalize_domain

HOSTED_TOOL_GROUPS = ("perf_engine", "sandbox_cloud")


def load_pref(config_session: ConfigSession) -> dict[str, typing.Any]:
    """读取模型偏好配置。"""
    config = config_session.load()
    model = config.get("model") if isinstance(config, dict) else {}
    primary = model.get("primary") if isinstance(model, dict) else {}
    raw = config_session.store.read_raw()
    providers = raw.get("model_providers") if isinstance(raw, dict) else {}
    profiles = providers if isinstance(providers, dict) else {}
    active_id = clean_text(primary.get("provider")) if isinstance(primary, dict) else ""

    return {
        "active_provider": active_id,
        "provider_kinds": [provider_kind_to_pref(item) for item in SUPPORTED_PROVIDER_OPTIONS],
        "providers": [
            provider_profile_to_pref(provider_id, profile, active_id=active_id)
            for provider_id, profile in profiles.items()
            if isinstance(provider_id, str) and isinstance(profile, dict)
        ],
        "hosted_tools": hosted_tools_to_pref(config.get("hosted_tools"))
    }


def save_pref(
    config_session: ConfigSession,
    raw: typing.Any
) -> dict[str, typing.Any]:
    """保存共享偏好配置。"""
    payload = raw if isinstance(raw, dict) else {}
    hosted = pref_to_hosted_tools(payload.get("hosted_tools"))
    groups = hosted["groups"]
    values = {
        ("hosted_tools", "groups", name): value
        for name, value in groups.items()
    }

    config_session.update_user(values)

    return load_pref(config_session)


def create_provider(
    config_session: ConfigSession,
    raw: typing.Any
) -> dict[str, typing.Any]:
    """创建一个 Provider Profile。"""
    payload = raw if isinstance(raw, dict) else {}
    provider_id = normalize_provider_id(payload.get("id"))
    profiles = _raw_provider_profiles(config_session)

    if provider_id in profiles:
        raise ValueError(f"provider already exists: {provider_id}")

    profile = normalize_provider_profile(provider_id, payload)
    _validate_unique_provider_name(profiles, profile["name"])
    config_session.update_user(provider_profile_values(provider_id, profile))
    return load_pref(config_session)


def update_provider(
    config_session: ConfigSession,
    provider_id: str,
    raw: typing.Any
) -> dict[str, typing.Any]:
    """更新一个 Provider Profile。"""
    normalized_id = normalize_provider_id(provider_id)
    profiles = _raw_provider_profiles(config_session)
    current = profiles.get(normalized_id)
    if not isinstance(current, dict):
        raise ValueError(f"provider does not exist: {normalized_id}")

    payload = raw if isinstance(raw, dict) else {}
    merged = dict(current)
    for field in ("name", "kind", "model", "route", "reasoning_effort", "base_url"):
        if field in payload:
            merged[field] = payload[field]
    if payload.get("clear_api_key"):
        merged["api_key"] = ""
    elif clean_text(payload.get("api_key")):
        merged["api_key"] = payload["api_key"]

    profile = normalize_provider_profile(normalized_id, merged)
    _validate_unique_provider_name(
        profiles,
        profile["name"],
        excluded_id=normalized_id,
    )
    config_session.update_user(provider_profile_values(normalized_id, profile))
    return load_pref(config_session)


def set_active_provider(
    config_session: ConfigSession,
    provider_id: object
) -> dict[str, typing.Any]:
    """设置当前使用的 Provider Profile。"""
    normalized_id = normalize_provider_id(provider_id)
    profile = _raw_provider_profiles(config_session).get(normalized_id)
    if not isinstance(profile, dict):
        raise ValueError(f"provider does not exist: {normalized_id}")
    normalized = normalize_provider_profile(normalized_id, profile)
    if not normalized["model"]:
        raise ValueError(f"provider is incomplete: {normalized_id}")

    config_session.update_user({("model_provider",): normalized_id})
    return load_pref(config_session)


def delete_provider(
    config_session: ConfigSession,
    provider_id: str
) -> dict[str, typing.Any]:
    """删除一个 Provider Profile。"""
    normalized_id = normalize_provider_id(provider_id)
    profiles = _raw_provider_profiles(config_session)
    if normalized_id not in profiles:
        raise ValueError(f"provider does not exist: {normalized_id}")

    raw = config_session.store.read_raw()
    active_id = clean_text(raw.get("model_provider"))
    if normalized_id == active_id and len(profiles) > 1:
        raise ValueError("select another provider before deleting the active provider")

    paths: list[tuple[str, ...]] = [("model_providers", normalized_id)]
    if normalized_id == active_id:
        paths.insert(0, ("model_provider",))
    config_session.delete_user(paths)
    return load_pref(config_session)


def load_service_config(
    config_session: ConfigSession
) -> dict[str, typing.Any]:
    """读取远程服务域名配置。"""
    config = config_session.load()
    service = config.get("service") if isinstance(config, dict) else {}
    domain = normalize_domain(service.get("domain") if isinstance(service, dict) else "")

    return {
        "domain": domain,
        "configured": bool(domain)
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


def provider_kind_to_pref(option: dict[str, str]) -> dict[str, typing.Any]:
    """生成前端使用的 Provider 类型元数据。"""
    kind = clean_text(option.get("value"), DEFAULT_PROVIDER_KIND)
    return {
        "value": kind,
        "label": clean_text(option.get("label"), kind),
        "routes": list(supported_routes_for_kind(kind)),
    }


def provider_profile_to_pref(
    provider_id: str,
    raw: typing.Any,
    *,
    active_id: str
) -> dict[str, typing.Any]:
    """生成不包含密钥正文的 Provider Profile 展示数据。"""
    profile = normalize_provider_profile(provider_id, raw)
    return {
        "id": provider_id,
        "name": profile["name"],
        "kind": profile["kind"],
        "model": profile["model"],
        "route": profile["route"],
        "reasoning_effort": profile["reasoning_effort"],
        "base_url": profile["base_url"],
        "api_key_configured": bool(profile["api_key"]),
        "active": provider_id == active_id,
        "ready": bool(profile["model"]),
    }


def normalize_provider_id(value: object) -> str:
    """规范化并校验 Provider Profile 标识。"""
    provider_id = clean_text(value)
    if not is_valid_provider_id(provider_id):
        raise ValueError("provider id must use letters, numbers, underscores, or hyphens")
    return provider_id


def normalize_provider_profile(provider_id: str, raw: typing.Any) -> dict[str, str]:
    """规范化并校验一个 Provider Profile。"""
    data = raw if isinstance(raw, dict) else {}
    name = clean_text(data.get("name"), provider_id)
    kind = clean_text(data.get("kind"), DEFAULT_PROVIDER_KIND).lower()
    if kind not in {item["value"] for item in SUPPORTED_PROVIDER_OPTIONS}:
        raise ValueError(f"unsupported provider kind: {kind}")

    route = clean_text(data.get("route"), default_route_for_kind(kind)).lower()
    if route not in supported_routes_for_kind(kind):
        raise ValueError(f"route is not supported by {kind}: {route}")

    raw_effort = clean_text(data.get("reasoning_effort"), DEFAULT_REASONING_EFFORT).lower()
    if raw_effort not in SUPPORTED_REASONING_EFFORTS:
        raise ValueError(f"unsupported reasoning effort: {raw_effort}")

    return {
        "name": name,
        "kind": kind,
        "model": clean_text(data.get("model")),
        "route": route,
        "reasoning_effort": raw_effort,
        "api_key": clean_text(data.get("api_key", data.get("apikey"))),
        "base_url": clean_text(data.get("base_url")),
    }


def _raw_provider_profiles(config_session: ConfigSession) -> dict[str, typing.Any]:
    """读取用户配置中保存的 Provider Profile 表。"""
    raw = config_session.store.read_raw()
    providers = raw.get("model_providers") if isinstance(raw, dict) else {}
    return dict(providers) if isinstance(providers, dict) else {}


def _validate_unique_provider_name(
    profiles: dict[str, typing.Any],
    name: str,
    *,
    excluded_id: str = ""
) -> None:
    """确保 Provider 展示名称在用户配置中唯一。"""
    normalized = name.casefold()
    for provider_id, raw in profiles.items():
        if provider_id == excluded_id or not isinstance(raw, dict):
            continue
        existing = clean_text(raw.get("name"), provider_id)
        if existing.casefold() == normalized:
            raise ValueError(f"provider name already exists: {name}")


def hosted_tools_to_pref(raw: typing.Any) -> dict[str, typing.Any]:
    """把配置中的托管工具开关转换为偏好接口结构。"""
    data = raw if isinstance(raw, dict) else {}
    groups = data.get("groups") if isinstance(data.get("groups"), dict) else {}

    return {
        "groups": {
            name: bool(groups.get(name, False))
            for name in HOSTED_TOOL_GROUPS
        }
    }


def pref_to_hosted_tools(raw: typing.Any) -> dict[str, typing.Any]:
    """把偏好接口中的托管工具开关转换为配置结构。"""
    data = raw if isinstance(raw, dict) else {}
    groups = data.get("groups") if isinstance(data.get("groups"), dict) else {}

    return {
        "groups": {
            name: bool(groups.get(name, False))
            for name in HOSTED_TOOL_GROUPS
        }
    }


def clean_text(value: typing.Any, default: str = "") -> str:
    """把输入转换为去空白字符串。"""
    text = str(value if value is not None else default).strip()
    return text or default


if __name__ == '__main__':
    pass
