# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass


DEFAULT_JS_REPL_ENABLED: typing.Final[bool]   = True
DEFAULT_SUBAGENTS_ENABLED: typing.Final[bool] = True

FEATURE_DEFAULTS: typing.Final[dict[str, bool]] = {
    "js_repl": DEFAULT_JS_REPL_ENABLED,
    "subagents": DEFAULT_SUBAGENTS_ENABLED,
}

FEATURE_CONFIG_FIELDS = frozenset(FEATURE_DEFAULTS)


class FeatureConfigError(ValueError):
    """表示本地能力开关配置不符合约束。"""


@dataclass(frozen=True, slots=True)
class FeatureSettings:
    """保存启动时固定的本地能力开关。"""
    js_repl: bool = DEFAULT_JS_REPL_ENABLED
    subagents: bool = DEFAULT_SUBAGENTS_ENABLED

    @classmethod
    def from_config(cls, config: typing.Any) -> "FeatureSettings":
        """从有效配置快照读取能力开关。"""
        root = config if isinstance(config, dict) else {}
        values = normalize_feature_table(root.get("features"))
        return cls(
            js_repl=values["js_repl"],
            subagents=values["subagents"],
        )


def normalize_feature_table(raw: typing.Any) -> dict[str, bool]:
    """校验并规范化本地能力开关表。"""
    if raw is None:
        data: dict[str, typing.Any] = {}
    elif isinstance(raw, dict):
        data = dict(raw)
    else:
        raise FeatureConfigError("features must be a table")

    unknown = sorted(set(data).difference(FEATURE_CONFIG_FIELDS))
    if unknown:
        raise FeatureConfigError(f"unknown features key: {unknown[0]}")

    values: dict[str, bool] = {}
    for field, default in FEATURE_DEFAULTS.items():
        value = data.get(field, default)
        if not isinstance(value, bool):
            raise FeatureConfigError(f"features.{field} must be a boolean")
        values[field] = value
    return values


if __name__ == '__main__':
    pass
