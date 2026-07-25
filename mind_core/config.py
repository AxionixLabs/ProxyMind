# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import copy
import math
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


def _normalize_model_slot(data: dict[str, typing.Any]) -> dict[str, typing.Any]:
    """把外部模型配置转换为稳定的 primary 槽位。"""
    provider = (
        _as_str(data.get("model_provider"), DEFAULT_PROVIDER_NAME).strip()
        or DEFAULT_PROVIDER_NAME
    )
    providers       = _as_dict(data.get("model_providers"))
    provider_config = _as_dict(providers.get(provider))
    model           = _as_str(data.get("model")).strip()

    enabled = (
        _as_bool(data.get("model_enabled"), bool(model))
        if "model_enabled" in data
        else bool(model)
    )

    return {
        "provider": provider,
        "route": (
            _as_str(provider_config.get("route"), DEFAULT_ROUTE_NAME).strip()
            or DEFAULT_ROUTE_NAME
        ),
        "model": model,
        "apikey": _as_str(provider_config.get("api_key")).strip(),
        "base_url": _as_str(provider_config.get("base_url")).strip(),
        "reasoning_effort": _normalize_reasoning_effort(
            data.get("model_reasoning_effort"),
            default=DEFAULT_REASONING_EFFORT,
        ),
        "enabled": enabled
    }


def _default_model_slot(*, enabled: bool | None = None) -> dict[str, typing.Any]:
    """返回默认模型槽位配置。"""
    slot: dict[str, typing.Any] = {
        "provider": DEFAULT_PROVIDER_NAME,
        "route": DEFAULT_ROUTE_NAME,
        "model": "",
        "apikey": "",
        "base_url": "",
        "reasoning_effort": DEFAULT_REASONING_EFFORT
    }
    if enabled is not None:
        slot["enabled"] = bool(enabled)
    return slot


def _default_effective_config() -> dict[str, typing.Any]:
    """返回默认的有效配置结构。"""
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
        "mcp_servers": {},
        "hosted_tools": {
            "groups": {
                "perf_engine": False,
                "sandbox_cloud": False
            }
        }
    }


def normalize_config(raw: typing.Any) -> dict[str, typing.Any]:
    """把任意 TOML 数据规范化为稳定的应用配置结构。"""
    data = _as_dict(raw)
    validate_config(data)

    defaults    = _default_effective_config()
    service     = _as_dict(data.get("service"))
    skills      = _as_dict(data.get("skills"))
    features    = _as_dict(data.get("features"))
    hosted      = _as_dict(data.get("hosted_tools"))
    mcp_servers = _as_dict(data.get("mcp_servers"))

    _validate_effective_mcp_servers(mcp_servers)

    return {
        "service": {
            "domain": _as_str(
                service.get("domain"),
                defaults["service"]["domain"]
            ).strip() or defaults["service"]["domain"]
        },
        "model": {"primary": _normalize_model_slot(data)},
        "skills": {
            "enabled": _as_str_list(skills.get("enabled")),
            "disabled": _as_str_list(skills.get("disabled"))
        },
        "features": _normalize_features(features),
        "mcp_servers": copy.deepcopy(mcp_servers),
        "hosted_tools": _normalize_hosted_tools(hosted)
    }


def validate_config(raw: typing.Any) -> None:
    """校验一个可为部分配置的原始配置表。"""
    if not isinstance(raw, dict):
        raise ConfigValidationError("config root must be a table")
    _validate_known_config(raw)


STRING_CONFIG_PATHS = frozenset({
    ("model",),
    ("model_provider",),
    ("model_reasoning_effort",),
    ("service", "domain"),
})

BOOL_CONFIG_PATHS = frozenset({
    ("model_enabled",),
    ("hosted_tools", "groups", "perf_engine"),
    ("hosted_tools", "groups", "sandbox_cloud"),
})

STRING_LIST_CONFIG_PATHS = frozenset({
    ("project_root_markers",),
    ("skills", "enabled"),
    ("skills", "disabled"),
})

TABLE_CONFIG_PATHS = (
    ("service",),
    ("model_providers",),
    ("skills",),
    ("features",),
    ("hosted_tools",),
    ("hosted_tools", "groups"),
    ("mcp_servers",),
    ("projects",),
)

MODEL_PROVIDER_STRING_FIELDS = frozenset({
    "route",
    "base_url",
    "api_key",
})

MCP_STRING_FIELDS = frozenset({
    "command",
    "cwd",
    "url",
    "bearer_token_env_var",
})

MCP_BOOL_FIELDS = frozenset({
    "enabled",
    "required",
})

MCP_STRING_LIST_FIELDS = frozenset({
    "args",
    "allow",
    "deny",
})

MCP_STRING_MAP_FIELDS = frozenset({
    "env",
    "http_headers",
    "env_http_headers",
})

MCP_NUMBER_FIELDS = frozenset({
    "startup_timeout_sec",
    "tool_timeout_sec",
})

MCP_FIELDS = frozenset({
    *MCP_STRING_FIELDS,
    *MCP_BOOL_FIELDS,
    *MCP_STRING_LIST_FIELDS,
    *MCP_STRING_MAP_FIELDS,
    *MCP_NUMBER_FIELDS,
})


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
        if path == ("model_reasoning_effort",) and (
            value.strip().lower() not in SUPPORTED_REASONING_EFFORTS
        ):
            choices = ", ".join(sorted(SUPPORTED_REASONING_EFFORTS))
            raise ConfigValidationError(
                f"{dotted} must be one of: {choices}"
            )
        return None

    if (
        len(path) == 3
        and path[0] == "model_providers"
        and path[1]
        and path[2] in MODEL_PROVIDER_STRING_FIELDS
    ):
        if not isinstance(value, str):
            raise ConfigValidationError(f"{dotted} must be a string")
        return None

    if path == ("mcp_servers",):
        _validate_mcp_servers(value)
        return None

    if len(path) == 2 and path[0] == "mcp_servers" and path[1]:
        _validate_mcp_server(path[1], value)
        return None

    if len(path) == 3 and path[0] == "mcp_servers" and path[1]:
        _validate_mcp_field(path[1], path[2], value)
        return None

    if (
        len(path) == 4
        and path[0] == "mcp_servers"
        and path[1]
        and path[2] in MCP_STRING_MAP_FIELDS
        and path[3]
    ):
        if not isinstance(value, str):
            raise ConfigValidationError(f"{dotted} must be a string")
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

    providers = config.get("model_providers")
    if isinstance(providers, dict):
        for name, provider in providers.items():
            if not isinstance(name, str) or not name.strip():
                raise ConfigValidationError(
                    "model provider name must be a non-empty string"
                )
            if not isinstance(provider, dict):
                raise ConfigValidationError(
                    f"model_providers.{name} must be a table"
                )
            for field in MODEL_PROVIDER_STRING_FIELDS:
                if field in provider:
                    validate_config_value(
                        ("model_providers", name, field),
                        provider[field],
                    )

    mcp_servers = config.get("mcp_servers")
    if mcp_servers is not None:
        _validate_mcp_servers(mcp_servers)

    projects = config.get("projects")
    if isinstance(projects, dict):
        for path, project in projects.items():
            if not isinstance(path, str) or not path.strip():
                raise ConfigValidationError(
                    "project path must be a non-empty string"
                )
            if not isinstance(project, dict):
                raise ConfigValidationError(
                    f"projects.{path} must be a table"
                )
            trust_level = project.get("trust_level")
            if trust_level not in {"trusted", "untrusted"}:
                raise ConfigValidationError(
                    f"projects.{path}.trust_level must be trusted or untrusted"
                )


def _validate_mcp_servers(value: typing.Any) -> None:
    """校验 MCP 服务配置表。"""
    if not isinstance(value, dict):
        raise ConfigValidationError("mcp_servers must be a table")
    for name, server in value.items():
        _validate_mcp_server(str(name), server)


def _validate_mcp_server(name: str, value: typing.Any) -> None:
    """校验一个可为部分覆盖的 MCP 服务配置。"""
    if not name.strip():
        raise ConfigValidationError("MCP server name must be non-empty")
    if not isinstance(value, dict):
        raise ConfigValidationError(f"mcp_servers.{name} must be a table")
    unknown = sorted(set(value).difference(MCP_FIELDS))
    if unknown:
        raise ConfigValidationError(
            f"unknown MCP server key: mcp_servers.{name}.{unknown[0]}"
        )
    for field, field_value in value.items():
        _validate_mcp_field(name, field, field_value)


def _validate_effective_mcp_servers(
    servers: dict[str, typing.Any],
) -> None:
    """校验合并后的 MCP 服务目标。"""
    for name, value in servers.items():
        if not isinstance(value, dict):
            continue
        command = str(value.get("command") or "").strip()
        url = str(value.get("url") or "").strip()
        if bool(command) == bool(url):
            raise ConfigValidationError(
                f"mcp_servers.{name} must define exactly one of command or url"
            )


def _validate_mcp_field(
    name: str,
    field: str,
    value: typing.Any,
) -> None:
    """校验一个 MCP 服务字段。"""
    dotted = f"mcp_servers.{name}.{field}"
    if field in MCP_STRING_FIELDS:
        if not isinstance(value, str):
            raise ConfigValidationError(f"{dotted} must be a string")
        return None
    if field in MCP_BOOL_FIELDS:
        if not isinstance(value, bool):
            raise ConfigValidationError(f"{dotted} must be a boolean")
        return None
    if field in MCP_STRING_LIST_FIELDS:
        if not isinstance(value, list) or not all(
            isinstance(item, str) for item in value
        ):
            raise ConfigValidationError(f"{dotted} must be an array of strings")
        return None
    if field in MCP_STRING_MAP_FIELDS:
        if not isinstance(value, dict) or not all(
            isinstance(key, str) and isinstance(item, str)
            for key, item in value.items()
        ):
            raise ConfigValidationError(f"{dotted} must be a string table")
        return None
    if field in MCP_NUMBER_FIELDS:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0
        ):
            raise ConfigValidationError(f"{dotted} must be a positive number")
        return None
    raise ConfigValidationError(f"unknown MCP server key: {dotted}")


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


def model_config_values(
    slot: dict[str, typing.Any],
) -> dict[tuple[str, ...], object]:
    """把 primary 槽位转换为外部模型配置字段。"""
    provider = (
        _as_str(slot.get("provider"), DEFAULT_PROVIDER_NAME).strip()
        or DEFAULT_PROVIDER_NAME
    )
    return {
        ("model",): _as_str(slot.get("model")).strip(),
        ("model_provider",): provider,
        ("model_reasoning_effort",): _normalize_reasoning_effort(
            slot.get("reasoning_effort"),
            default=DEFAULT_REASONING_EFFORT,
        ),
        ("model_enabled",): _as_bool(slot.get("enabled"), False),
        ("model_providers", provider, "route"): (
            _as_str(slot.get("route"), DEFAULT_ROUTE_NAME).strip()
            or DEFAULT_ROUTE_NAME
        ),
        ("model_providers", provider, "api_key"): (
            _as_str(slot.get("apikey")).strip()
        ),
        ("model_providers", provider, "base_url"): (
            _as_str(slot.get("base_url")).strip()
        ),
    }


ModelConfigField = typing.Literal[
    "model",
    "apikey",
    "base_url",
    "reasoning_effort",
]


def model_config_field_values(
    slot: dict[str, typing.Any],
    field: ModelConfigField,
) -> dict[tuple[str, ...], object]:
    """把 primary 槽位的单个字段转换为外部配置字段。"""
    provider = (
        _as_str(slot.get("provider"), DEFAULT_PROVIDER_NAME).strip()
        or DEFAULT_PROVIDER_NAME
    )
    values: dict[tuple[str, ...], object] = {
        ("model_enabled",): True,
    }
    if field == "model":
        values[("model",)] = _as_str(slot.get(field)).strip()
    elif field == "reasoning_effort":
        values[("model_reasoning_effort",)] = _normalize_reasoning_effort(
            slot.get(field),
            default=DEFAULT_REASONING_EFFORT,
        )
    else:
        provider_field = "api_key" if field == "apikey" else field
        values[("model_providers", provider, provider_field)] = (
            _as_str(slot.get(field)).strip()
        )
    return values


def config_to_preferences(config: dict[str, typing.Any]) -> dict[str, typing.Any]:
    """把有效配置转换为 Preferences 运行时结构。"""
    cfg    = copy.deepcopy(config)
    model  = _as_dict(cfg.get("model"))
    hosted = _as_dict(cfg.get("hosted_tools"))

    def convert_slot(slot: dict[str, typing.Any]) -> dict[str, typing.Any]:
        return {
            "provider": _as_str(slot.get("provider"), DEFAULT_PROVIDER_NAME),
            "route": _as_str(slot.get("route"), DEFAULT_ROUTE_NAME),
            "model": _as_str(slot.get("model")),
            "apikey": _as_str(slot.get("apikey")),
            "base_url": _as_str(slot.get("base_url")),
            "reasoning_effort": _normalize_reasoning_effort(
                slot.get("reasoning_effort"),
                default=DEFAULT_REASONING_EFFORT
            ),
            "enabled": _as_bool(slot.get("enabled"), False)
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
