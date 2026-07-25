# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_app.paths import mind_config_path
from mind_core.config import model_config_values
from mind_core.config_session import ConfigSession
from mind_core.config_store import ConfigStore
from mind_core.provider_config import (
    DEFAULT_PROVIDER_NAME,
    DEFAULT_REASONING_EFFORT,
    DEFAULT_ROUTE_NAME,
    SUPPORTED_REASONING_EFFORTS,
    SUPPORTED_PROVIDER_OPTIONS
)
from mind_core.service_config import normalize_domain

DEFAULT_PROFILE_KEY = "default"
DEFAULT_MODEL_TYPE  = "Auto"
HOSTED_TOOL_GROUPS  = ("perf_engine", "sandbox_cloud")


def load_pref() -> dict[str, typing.Any]:
    """读取模型偏好配置。"""
    config  = _load_mind_config()
    model   = config.get("model") if isinstance(config, dict) else {}
    primary = model.get("primary") if isinstance(model, dict) else {}

    return {
        "profile_key"  : DEFAULT_PROFILE_KEY,
        "providers"    : [dict(item) for item in SUPPORTED_PROVIDER_OPTIONS],
        "primary"      : config_slot_to_pref(primary),
        "hosted_tools" : hosted_tools_to_pref(config.get("hosted_tools"))
    }


def save_pref(raw: typing.Any) -> dict[str, typing.Any]:
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

    _config_session().update(values)

    return load_pref()


def load_service_config() -> dict[str, typing.Any]:
    """读取远程服务域名配置。"""
    config  = _load_mind_config()
    service = config.get("service") if isinstance(config, dict) else {}
    domain  = normalize_domain(service.get("domain") if isinstance(service, dict) else "")

    return {
        "domain"     : domain,
        "configured" : bool(domain)
    }


def save_service_config(raw: typing.Any) -> dict[str, typing.Any]:
    """保存远程服务域名配置。"""
    payload = raw if isinstance(raw, dict) else {}
    _config_session().update({
        ("service", "domain"): normalize_domain(payload.get("domain")),
    })

    return load_service_config()


def config_slot_to_pref(slot: typing.Any) -> dict[str, typing.Any]:
    """把 config.toml 模型槽位转换为偏好接口结构。"""
    data = slot if isinstance(slot, dict) else {}

    is_enabled = bool(data.get("enabled"))

    return {
        "provider"         : clean_text(data.get("provider"), DEFAULT_PROVIDER_NAME),
        "route"            : clean_text(data.get("route"), DEFAULT_ROUTE_NAME),
        "model"            : clean_text(data.get("model")),
        "apikey"           : clean_text(data.get("apikey")),
        "base_url"         : clean_text(data.get("base_url")),
        "reasoning_effort" : normalize_reasoning_effort(data.get("reasoning_effort")),
        "enabled"          : is_enabled,
        "type"             : clean_text(data.get("type"), DEFAULT_MODEL_TYPE),
        "notes"            : clean_text(data.get("notes"))
    }


def pref_to_config_slot(slot: typing.Any, *, enabled: bool | None) -> dict[str, typing.Any]:
    """把偏好接口结构转换为 config.toml 模型槽位。"""
    data = slot if isinstance(slot, dict) else {}

    is_enabled = bool(enabled)

    result: dict[str, typing.Any] = {
        "provider"         : clean_text(data.get("provider"), DEFAULT_PROVIDER_NAME),
        "route"            : clean_text(data.get("route"), DEFAULT_ROUTE_NAME),
        "model"            : clean_text(data.get("model")),
        "apikey"           : clean_text(data.get("apikey")),
        "base_url"         : clean_text(data.get("base_url")),
        "reasoning_effort" : normalize_reasoning_effort(data.get("reasoning_effort")),
        "enabled"          : is_enabled
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


def _load_mind_config() -> dict[str, typing.Any]:
    """读取并规范化应用配置文件。"""
    return _config_session().load()


def _config_session() -> ConfigSession:
    """返回本地配置服务使用的配置会话。"""
    return ConfigSession(ConfigStore(mind_config_path()))


if __name__ == "__main__":
    pass
