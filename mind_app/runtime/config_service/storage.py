# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_app.paths import (
    ensure_writable_file,
    mind_config_path
)
from mind_core.config import (
    ensure_config,
    load_config,
    normalize_config,
    write_config
)
from mind_core.provider_config import (
    DEFAULT_PROVIDER_NAME,
    DEFAULT_ROUTE_NAME
)
from mind_core.service_config import normalize_domain

DEFAULT_PROFILE_KEY = "default"
DEFAULT_MODEL_TYPE  = "Text"


def load_pref() -> dict[str, typing.Any]:
    """读取 Mind 模型偏好配置。"""
    config    = _load_mind_config()
    model     = config.get("model") if isinstance(config, dict) else {}
    primary   = model.get("primary") if isinstance(model, dict) else {}
    secondary = model.get("secondary") if isinstance(model, dict) else {}

    return {
        "profile_key" : DEFAULT_PROFILE_KEY,
        "primary"     : config_slot_to_pref(primary),
        "secondary"   : config_slot_to_pref(secondary) if secondary_enabled(secondary) else None
    }


def save_pref(raw: typing.Any) -> dict[str, typing.Any]:
    """保存 Mind 模型偏好配置。"""
    payload       = raw if isinstance(raw, dict) else {}
    config        = _load_mind_config()
    model         = config.setdefault("model", {})
    secondary_raw = payload.get("secondary")

    model["primary"] = pref_to_config_slot(payload.get("primary"), enabled=None)

    model["secondary"] = pref_to_config_slot(
        secondary_raw,
        enabled=pref_slot_configured(secondary_raw)
    )

    _write_mind_config(config)
    return load_pref()


def load_service_config() -> dict[str, typing.Any]:
    """读取 Mind 服务域名配置。"""
    config  = _load_mind_config()
    service = config.get("service") if isinstance(config, dict) else {}
    domain  = normalize_domain(service.get("domain") if isinstance(service, dict) else "")

    return {
        "domain"     : domain,
        "configured" : bool(domain)
    }


def save_service_config(raw: typing.Any) -> dict[str, typing.Any]:
    """保存 Mind 服务域名配置。"""
    payload = raw if isinstance(raw, dict) else {}
    config  = _load_mind_config()

    service = config.setdefault("service", {})
    service["domain"] = normalize_domain(payload.get("domain"))

    _write_mind_config(config)

    return load_service_config()


def config_slot_to_pref(slot: typing.Any) -> dict[str, typing.Any]:
    """把 config.toml 模型槽位转换为偏好接口结构。"""
    data = slot if isinstance(slot, dict) else {}

    return {
        "provider" : clean_text(data.get("provider"), DEFAULT_PROVIDER_NAME),
        "type"     : clean_text(data.get("type"), DEFAULT_MODEL_TYPE),
        "base_url" : clean_text(data.get("base_url")),
        "route"    : clean_text(data.get("route"), DEFAULT_ROUTE_NAME),
        "apikey"   : clean_text(data.get("apikey")),
        "model"    : clean_text(data.get("model")),
        "notes"    : clean_text(data.get("notes"))
    }


def pref_to_config_slot(slot: typing.Any, *, enabled: bool | None) -> dict[str, typing.Any]:
    """把偏好接口结构转换为 config.toml 模型槽位。"""
    data = slot if isinstance(slot, dict) else {}

    result: dict[str, typing.Any] = {
        "provider" : clean_text(data.get("provider"), DEFAULT_PROVIDER_NAME),
        "model"    : clean_text(data.get("model")),
        "apikey"   : clean_text(data.get("apikey")),
        "base_url" : clean_text(data.get("base_url")),
        "route"    : clean_text(data.get("route"), DEFAULT_ROUTE_NAME)
    }

    if enabled is not None:
        result["enabled"] = bool(enabled)
    return result


def pref_slot_configured(slot: typing.Any) -> bool:
    """判断偏好槽位是否有有效配置。"""
    if not isinstance(slot, dict):
        return False

    return bool(
        clean_text(slot.get("base_url"))
        or clean_text(slot.get("apikey"))
        or clean_text(slot.get("model"))
    )


def secondary_enabled(slot: typing.Any) -> bool:
    """判断 secondary 槽位是否启用。"""
    if not isinstance(slot, dict):
        return False
    return bool(slot.get("enabled")) and pref_slot_configured(config_slot_to_pref(slot))


def clean_text(value: typing.Any, default: str = "") -> str:
    """把输入转换为去空白字符串。"""
    text = str(value if value is not None else default).strip()
    return text or default


def _load_mind_config() -> dict[str, typing.Any]:
    """读取并规范化 Mind 配置文件。"""
    target = ensure_config(mind_config_path())
    return normalize_config(load_config(target))


def _write_mind_config(config: dict[str, typing.Any]) -> None:
    """写入 Mind 配置文件。"""
    target = ensure_writable_file(mind_config_path())
    write_config(target, config)


if __name__ == "__main__":
    pass
