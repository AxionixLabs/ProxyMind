# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import copy
import typing
import tomllib
from pathlib import Path
from mind_core.provider_config import (
    DEFAULT_PROVIDER_NAME,
    DEFAULT_ROUTE_NAME
)
from mind_nova import const

DEFAULT_CONFIG_TEXT = f"""[service]
domain = "{const.DOMAIN}"

[model.primary]
provider = "{DEFAULT_PROVIDER_NAME}"
model = ""
apikey = ""
base_url = ""
route = "{DEFAULT_ROUTE_NAME}"

[model.secondary]
enabled = false
provider = "{DEFAULT_PROVIDER_NAME}"
model = ""
apikey = ""
base_url = ""
route = "{DEFAULT_ROUTE_NAME}"

[skills]
enabled = []
disabled = []
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


def _normalize_model_slot(
    raw: typing.Any,
    *,
    include_enabled: bool = False,
    default_enabled: bool = False
) -> dict[str, typing.Any]:
    """规范化模型槽位配置。"""
    data = _as_dict(raw)
    slot = _default_model_slot(enabled=default_enabled if include_enabled else None)

    slot["provider"] = _as_str(data.get("provider"), DEFAULT_PROVIDER_NAME).strip() or DEFAULT_PROVIDER_NAME
    slot["model"]    = _as_str(data.get("model")).strip()
    slot["apikey"]   = _as_str(data.get("apikey")).strip()
    slot["base_url"] = _as_str(data.get("base_url")).strip()
    slot["route"]    = _as_str(data.get("route"), DEFAULT_ROUTE_NAME).strip() or DEFAULT_ROUTE_NAME

    if include_enabled:
        slot["enabled"] = _as_bool(data.get("enabled"), default_enabled)

    return slot


def _default_model_slot(*, enabled: bool | None = None) -> dict[str, typing.Any]:
    """返回默认模型槽位配置。"""
    slot: dict[str, typing.Any] = {
        "provider" : DEFAULT_PROVIDER_NAME,
        "model"    : "",
        "apikey"   : "",
        "base_url" : "",
        "route"    : DEFAULT_ROUTE_NAME
    }
    if enabled is not None:
        slot["enabled"] = bool(enabled)
    return slot


def default_config() -> dict[str, typing.Any]:
    """返回 Mind config.toml 的默认配置结构。"""
    return {
        "service" : {
            "domain" : const.DOMAIN
        },
        "model"   : {
            "primary"   : _default_model_slot(),
            "secondary" : _default_model_slot(enabled=False)
        },
        "skills"  : {
            "enabled"  : [],
            "disabled" : []
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

    return {
        "service" : {
            "domain" : _as_str(
                service.get("domain"),
                defaults["service"]["domain"]
            ).strip() or defaults["service"]["domain"]
        },
        "model"   : {
            "primary"   : _normalize_model_slot(model.get("primary")),
            "secondary" : _normalize_model_slot(
                model.get("secondary"),
                include_enabled=True,
                default_enabled=False
            )
        },
        "skills"  : {
            "enabled"  : _as_str_list(skills.get("enabled")),
            "disabled" : _as_str_list(skills.get("disabled"))
        }
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


def config_to_preferences(config: dict[str, typing.Any]) -> dict[str, typing.Any]:
    """把 config.toml 结构转换为现有 Preferences 运行时结构。"""
    cfg   = normalize_config(copy.deepcopy(config))
    model = _as_dict(cfg.get("model"))

    def convert_slot(slot: dict[str, typing.Any]) -> dict[str, str]:
        return {
            "api"      : _as_str(slot.get("provider"), DEFAULT_PROVIDER_NAME),
            "model"    : _as_str(slot.get("model")),
            "apikey"   : _as_str(slot.get("apikey")),
            "base_url" : _as_str(slot.get("base_url")),
            "route"    : _as_str(slot.get("route"), DEFAULT_ROUTE_NAME)
        }

    primary   = _as_dict(model.get("primary"))
    secondary = _as_dict(model.get("secondary"))

    prefs: dict[str, typing.Any] = {
        "primary" : convert_slot(primary)
    }

    if (
        _as_bool(secondary.get("enabled"), False)
        and _as_str(secondary.get("model")).strip()
        and _as_str(secondary.get("apikey")).strip()
    ):
        prefs["secondary"] = convert_slot(secondary)

    return prefs


if __name__ == "__main__":
    pass
