# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from collections.abc import Mapping
from dataclasses import dataclass

from protocol.schema.json_value import JsonValue

UsageSource: typing.TypeAlias = typing.Literal[
    "provider",
    "estimate",
    "unknown"
]

CONTEXT_USAGE_FIELDS = frozenset({
    "model_context_window",
    "last_token_usage",
    "total_token_usage",
    "usage_source",
    "model",
    "route",
})


@dataclass(frozen=True, slots=True)
class ContextTokenUsage:
    """保存服务端归一化的总 token 数，不重复叠加缓存或 reasoning。"""

    total_tokens: int


@dataclass(frozen=True, slots=True)
class ContextUsageSnapshot:
    """保存同一次提交的窗口、最近占用、累计计费及模型归属。"""

    model_context_window: int | None
    last_token_usage: ContextTokenUsage | None
    total_token_usage: ContextTokenUsage | None
    usage_source: UsageSource
    model: str
    route: str


def parse_context_usage(payload: Mapping[str, JsonValue]) -> ContextUsageSnapshot:
    """严格解析完整快照；缺失不等于显式未知，计数不做隐式转换。"""
    if set(payload) != CONTEXT_USAGE_FIELDS:
        raise ValueError("context usage requires exactly the declared snapshot fields")
    window = payload["model_context_window"]
    if window is not None and (
        isinstance(window, bool) or not isinstance(window, int) or window <= 1
    ):
        raise ValueError("model_context_window must be an integer greater than one or null")
    last = _parse_token_usage(payload["last_token_usage"])
    total = _parse_token_usage(payload["total_token_usage"])
    source = payload["usage_source"]
    if source not in ("provider", "estimate", "unknown"):
        raise ValueError("context usage source is invalid")
    if (source == "unknown") != (last is None):
        raise ValueError("context usage source must match last_token_usage availability")
    model = payload["model"]
    route = payload["route"]
    if not isinstance(model, str) or not model.strip():
        raise ValueError("context usage model is required")
    if not isinstance(route, str) or not route.strip():
        raise ValueError("context usage route is required")
    if source == "provider":
        usage_source: UsageSource = "provider"
    elif source == "estimate":
        usage_source = "estimate"
    else:
        usage_source = "unknown"
    return ContextUsageSnapshot(window, last, total, usage_source, model, route)


def _parse_token_usage(value: JsonValue) -> ContextTokenUsage | None:
    """校验非负总计数或显式未知值。"""
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"total_tokens"}:
        raise ValueError("token usage requires exactly total_tokens or null")
    total = value["total_tokens"]
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise ValueError("total_tokens must be a non-negative integer")
    return ContextTokenUsage(total)


if __name__ == '__main__':
    pass
