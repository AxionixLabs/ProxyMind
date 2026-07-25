# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import copy
import typing
import tomllib
from dataclasses import dataclass
from mind_core.provider_config import (
    DEFAULT_PROVIDER_NAME,
    DEFAULT_REASONING_EFFORT,
    DEFAULT_ROUTE_NAME,
    SUPPORTED_REASONING_EFFORTS
)
from mind_core.features import FEATURE_REGISTRY


@dataclass(frozen=True, slots=True)
class ConfigOverride(object):
    """描述一次点路径配置覆盖。"""

    path: tuple[str, ...]
    value: typing.Any


class ConfigValidationError(ValueError):
    """表示配置字段或覆盖值不符合 schema。"""


def _as_str(value: typing.Any, default: str = "") -> str:
    """把配置值规范化为字符串。"""
    if value is None:
        return default
    return str(value)


def _as_dict(value: typing.Any) -> dict[str, typing.Any]:
    """返回 dict 副本；非 dict 时返回空字典。"""
    return dict(value) if isinstance(value, dict) else {}


def _as_bool(value: typing.Any, default: bool = False) -> bool:
    """把配置值规范化为布尔值。"""
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


def _as_str_list(value: typing.Any) -> list[str]:
    """把配置列表规范化为去空字符串列表。"""
    if not isinstance(value, list):
        return []
    return [item for raw in value if (item := str(raw or "").strip())]


def _normalize_hosted_tools(raw: typing.Any) -> dict[str, typing.Any]:
    """规范化云端托管工具配置。"""
    data   = _as_dict(raw)
    groups = _as_dict(data.get("groups"))

    return {
        "groups": {
            "perf_engine"   : _as_bool(groups.get("perf_engine"), False),
            "sandbox_cloud" : _as_bool(groups.get("sandbox_cloud"), False)
        }
    }


def _normalize_features(raw: typing.Any) -> dict[str, bool]:
    """规范化可扩展功能开关。"""
    return FEATURE_REGISTRY.resolve(raw)


def _normalize_model_slot(
    raw: typing.Any,
    *,
    default_enabled: bool = False
) -> dict[str, typing.Any]:
    """规范化模型槽位配置。"""
    data    = _as_dict(raw)
    enabled = _as_bool(data.get("enabled"), default_enabled)
    slot    = _default_model_slot(enabled=enabled)

    slot["provider"]         = _as_str(data.get("provider"), DEFAULT_PROVIDER_NAME).strip() or DEFAULT_PROVIDER_NAME
    slot["route"]            = _as_str(data.get("route"), DEFAULT_ROUTE_NAME).strip() or DEFAULT_ROUTE_NAME
    slot["model"]            = _as_str(data.get("model")).strip()
    slot["apikey"]           = _as_str(data.get("apikey")).strip()
    slot["base_url"]         = _as_str(data.get("base_url")).strip()
    slot["reasoning_effort"] = _normalize_reasoning_effort(
        data.get("reasoning_effort"),
        default=slot["reasoning_effort"]
    )

    return slot


def _default_model_slot(*, enabled: bool | None = None) -> dict[str, typing.Any]:
    """返回默认模型槽位配置。"""
    slot: dict[str, typing.Any] = {
        "provider"         : DEFAULT_PROVIDER_NAME,
        "route"            : DEFAULT_ROUTE_NAME,
        "model"            : "",
        "apikey"           : "",
        "base_url"         : "",
        "reasoning_effort" : DEFAULT_REASONING_EFFORT
    }
    if enabled is not None:
        slot["enabled"] = bool(enabled)
    return slot


def default_config() -> dict[str, typing.Any]:
    """返回 config.toml 的默认配置结构。"""
    return {
        "service" : {
            "domain" : ""
        },
        "model"   : {
            "primary" : _default_model_slot(enabled=False)
        },
        "skills"  : {
            "enabled"  : [],
            "disabled" : []
        },
        "features": {},
        "hosted_tools": {
            "groups": {
                "perf_engine": False,
                "sandbox_cloud": False
            }
        }
    }


def normalize_config(raw: typing.Any) -> dict[str, typing.Any]:
    """把任意 TOML 数据规范化为稳定的应用配置结构。"""
    data     = _as_dict(raw)
    _validate_known_config(data)
    defaults = default_config()
    service  = _as_dict(data.get("service"))
    model    = _as_dict(data.get("model"))
    skills   = _as_dict(data.get("skills"))
    features = _as_dict(data.get("features"))
    hosted   = _as_dict(data.get("hosted_tools"))

    return {
        "service": {
            "domain": _as_str(
                service.get("domain"),
                defaults["service"]["domain"]
            ).strip() or defaults["service"]["domain"]
        },
        "model": {
            "primary": _normalize_model_slot(
                model.get("primary"),
                default_enabled=False
            )
        },
        "skills": {
            "enabled"  : _as_str_list(skills.get("enabled")),
            "disabled" : _as_str_list(skills.get("disabled"))
        },
        "features": _normalize_features(features),
        "hosted_tools": _normalize_hosted_tools(hosted)
    }


STRING_CONFIG_PATHS = frozenset({
    ("service", "domain"),
    ("model", "primary", "provider"),
    ("model", "primary", "route"),
    ("model", "primary", "model"),
    ("model", "primary", "apikey"),
    ("model", "primary", "base_url"),
    ("model", "primary", "reasoning_effort"),
})
BOOL_CONFIG_PATHS = frozenset({
    ("model", "primary", "enabled"),
    ("hosted_tools", "groups", "perf_engine"),
    ("hosted_tools", "groups", "sandbox_cloud"),
})
STRING_LIST_CONFIG_PATHS = frozenset({
    ("skills", "enabled"),
    ("skills", "disabled"),
})
TABLE_CONFIG_PATHS = (
    ("service",),
    ("model",),
    ("model", "primary"),
    ("skills",),
    ("features",),
    ("hosted_tools",),
    ("hosted_tools", "groups"),
)


def _raw_path_value(
    config: dict[str, typing.Any],
    path: tuple[str, ...],
) -> tuple[bool, typing.Any]:
    """读取原始配置路径，并区分不存在和值为空。"""
    current: typing.Any = config
    for component in path:
        if not isinstance(current, dict) or component not in current:
            return False, None
        current = current[component]
    return True, current


def validate_config_value(
    path: tuple[str, ...],
    value: typing.Any,
) -> None:
    """按照应用配置 schema 校验一个点路径值。"""
    dotted = ".".join(path)
    if path in STRING_CONFIG_PATHS:
        if not isinstance(value, str):
            raise ConfigValidationError(f"{dotted} must be a string")
        if path[-1] == "reasoning_effort" and (
            value.strip().lower() not in SUPPORTED_REASONING_EFFORTS
        ):
            choices = ", ".join(sorted(SUPPORTED_REASONING_EFFORTS))
            raise ConfigValidationError(
                f"{dotted} must be one of: {choices}"
            )
        return None

    if path in BOOL_CONFIG_PATHS:
        if not isinstance(value, bool):
            raise ConfigValidationError(f"{dotted} must be a boolean")
        return None

    if path in STRING_LIST_CONFIG_PATHS:
        if not isinstance(value, list) or not all(
            isinstance(item, str) for item in value
        ):
            raise ConfigValidationError(f"{dotted} must be an array of strings")
        return None

    if len(path) == 2 and path[0] == "features":
        FEATURE_REGISTRY.require(path[1])
        if not isinstance(value, bool):
            raise ConfigValidationError(f"{dotted} must be a boolean")
        return None

    raise ConfigValidationError(f"unknown config key: {dotted or '<empty>'}")


def _validate_known_config(config: dict[str, typing.Any]) -> None:
    """校验文件中已经出现的受支持配置字段。"""
    for path in TABLE_CONFIG_PATHS:
        present, value = _raw_path_value(config, path)
        if present and not isinstance(value, dict):
            raise ConfigValidationError(
                f"{'.'.join(path)} must be a table"
            )

    for path in (
        *STRING_CONFIG_PATHS,
        *BOOL_CONFIG_PATHS,
        *STRING_LIST_CONFIG_PATHS,
    ):
        present, value = _raw_path_value(config, path)
        if present:
            validate_config_value(path, value)


def config_override(
    path: tuple[str, ...],
    value: typing.Any,
) -> ConfigOverride:
    """创建经过 schema 校验的配置覆盖。"""
    validate_config_value(path, value)
    return ConfigOverride(path=path, value=value)


def parse_config_override(expression: str) -> ConfigOverride:
    """解析 key=value 形式的 TOML 配置覆盖。"""
    key, separator, raw_value = str(expression or "").partition("=")
    path = tuple(part.strip() for part in key.split("."))
    if not separator or not path or any(not part for part in path):
        raise ValueError("config override must use a non-empty dotted key=value")

    try:
        value = tomllib.loads(f"value = {raw_value}")["value"]
    except tomllib.TOMLDecodeError:
        value = raw_value
    return config_override(path, value)


def apply_config_overrides(
    config: typing.Any,
    overrides: typing.Iterable[ConfigOverride],
) -> dict[str, typing.Any]:
    """按给定顺序把点路径覆盖应用到配置副本。"""
    result = copy.deepcopy(config) if isinstance(config, dict) else {}
    for override in overrides:
        validate_config_value(override.path, override.value)
        target = result
        for component in override.path[:-1]:
            child = target.get(component)
            if not isinstance(child, dict):
                child = {}
                target[component] = child
            target = child
        target[override.path[-1]] = copy.deepcopy(override.value)
    return result


def config_to_preferences(config: dict[str, typing.Any]) -> dict[str, typing.Any]:
    """把 config.toml 结构转换为 Preferences 运行时结构。"""
    cfg    = normalize_config(copy.deepcopy(config))
    model  = _as_dict(cfg.get("model"))
    hosted = _as_dict(cfg.get("hosted_tools"))

    def convert_slot(slot: dict[str, typing.Any]) -> dict[str, typing.Any]:
        return {
            "provider"         : _as_str(slot.get("provider"), DEFAULT_PROVIDER_NAME),
            "route"            : _as_str(slot.get("route"), DEFAULT_ROUTE_NAME),
            "model"            : _as_str(slot.get("model")),
            "apikey"           : _as_str(slot.get("apikey")),
            "base_url"         : _as_str(slot.get("base_url")),
            "reasoning_effort" : _normalize_reasoning_effort(
                slot.get("reasoning_effort"),
                default=DEFAULT_REASONING_EFFORT
            ),
            "enabled"          : _as_bool(slot.get("enabled"), False)
        }

    primary = _as_dict(model.get("primary"))

    return {
        "primary"      : convert_slot(primary),
        "hosted_tools" : _normalize_hosted_tools(hosted)
    }


def _normalize_reasoning_effort(value: typing.Any, *, default: str = "") -> str:
    """规范化推理强度档位。"""
    text = _as_str(value).strip().lower()

    fallback = _as_str(default).strip().lower()
    if fallback not in SUPPORTED_REASONING_EFFORTS:
        fallback = ""

    return text if text in SUPPORTED_REASONING_EFFORTS else fallback


if __name__ == "__main__":
    pass
