# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import copy
import json
import typing
import tomllib
from pathlib import Path
from mind_core.provider_config import (
    DEFAULT_PROVIDER_NAME,
    DEFAULT_REASONING_EFFORT,
    DEFAULT_ROUTE_NAME,
    SUPPORTED_REASONING_EFFORTS
)
from mind_nova import const

DEFAULT_CONFIG_TEXT = f"""[service]
domain = ""

[model.primary]
provider = "{DEFAULT_PROVIDER_NAME}"
route = "{DEFAULT_ROUTE_NAME}"
model = ""
apikey = ""
base_url = ""
reasoning_effort = "{DEFAULT_REASONING_EFFORT}"
enabled = false

[skills]
enabled = []
disabled = []

[hosted_tools.groups]
perf_engine = false
sandbox_cloud = false
"""


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
    """返回 Mind config.toml 的默认配置结构。"""
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
        "hosted_tools": {
            "groups": {
                "perf_engine": False,
                "sandbox_cloud": False
            }
        }
    }


def default_config_path() -> Path:
    """返回默认配置文件路径。"""
    root = Path(os.environ.get("MIND_HOME") or Path.home() / ".mind").expanduser()
    return root / "config.toml"


def normalize_config(raw: typing.Any) -> dict[str, typing.Any]:
    """把任意 TOML 数据规范化为稳定的 Mind 配置结构。"""
    data     = _as_dict(raw)
    defaults = default_config()
    service  = _as_dict(data.get("service"))
    model    = _as_dict(data.get("model"))
    skills   = _as_dict(data.get("skills"))
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
        "hosted_tools": _normalize_hosted_tools(hosted)
    }


def load_config(path: typing.Any) -> dict[str, typing.Any]:
    """读取并规范化 config.toml。"""
    target = Path(path).expanduser()
    with target.open("rb") as file:
        return normalize_config(tomllib.load(file))


def ensure_config(path: typing.Any) -> Path:
    """确保 config.toml 存在；已存在时不改写。"""
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)

    if not target.exists():
        target.write_text(DEFAULT_CONFIG_TEXT, encoding=const.CHARSET)

    return target


def write_config(path: typing.Any, config: dict[str, typing.Any]) -> dict[str, typing.Any]:
    """规范化并写入 config.toml。"""
    target     = ensure_config(path)
    normalized = normalize_config(config)

    target.write_text(format_config(normalized), encoding=const.CHARSET)

    return normalized


def format_config(config: dict[str, typing.Any]) -> str:
    """把 Mind 配置格式化为 TOML 文本。"""
    normalized = normalize_config(config)
    service    = normalized["service"]
    model      = normalized["model"]
    skills     = normalized["skills"]
    hosted     = normalized["hosted_tools"]
    primary    = model["primary"]
    groups     = _as_dict(hosted.get("groups"))

    return "\n".join([
        "[service]",
        f"domain = {toml_string(service.get('domain'))}",
        "",
        "[model.primary]",
        f"provider = {toml_string(primary.get('provider'))}",
        f"route = {toml_string(primary.get('route'))}",
        f"model = {toml_string(primary.get('model'))}",
        f"apikey = {toml_string(primary.get('apikey'))}",
        f"base_url = {toml_string(primary.get('base_url'))}",
        f"reasoning_effort = {toml_string(primary.get('reasoning_effort'))}",
        f"enabled = {'true' if primary.get('enabled') else 'false'}",
        "",
        "[skills]",
        f"enabled = {toml_string_list(skills.get('enabled'))}",
        f"disabled = {toml_string_list(skills.get('disabled'))}",
        "",
        "[hosted_tools.groups]",
        f"perf_engine = {'true' if groups.get('perf_engine') else 'false'}",
        f"sandbox_cloud = {'true' if groups.get('sandbox_cloud') else 'false'}",
        ""
    ])


def toml_string(value: typing.Any) -> str:
    """返回 TOML 字符串字面量。"""
    return json.dumps(str(value or ""), ensure_ascii=False)


def toml_string_list(value: typing.Any) -> str:
    """返回 TOML 字符串列表字面量。"""
    items = value if isinstance(value, list) else []
    return "[" + ", ".join(toml_string(item) for item in items) + "]"


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
    """规范化 reasoning effort 档位。"""
    text = _as_str(value).strip().lower()

    fallback = _as_str(default).strip().lower()
    if fallback not in SUPPORTED_REASONING_EFFORTS:
        fallback = ""

    return text if text in SUPPORTED_REASONING_EFFORTS else fallback


if __name__ == "__main__":
    pass
