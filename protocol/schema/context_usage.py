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

SESSION_TOKEN_USAGE_FIELDS = frozenset({
    "total_tokens",
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "reported_calls",
    "unreported_calls",
})


@dataclass(frozen=True, slots=True)
class ContextTokenUsage:
    """保存服务端归一化的总 token 数，不重复叠加缓存或 reasoning。"""

    total_tokens: int


@dataclass(frozen=True, slots=True)
class SessionTokenUsage:
    """保存服务端会话累计实报；未知明细和缺报调用不转换为完整统计。"""

    total_tokens: int
    input_tokens: int | None
    cached_input_tokens: int | None
    cache_write_input_tokens: int | None
    output_tokens: int | None
    reasoning_output_tokens: int | None
    reported_calls: int
    unreported_calls: int

    @property
    def is_complete(self) -> bool:
        """判断当前已记录调用是否具有退出统计所需的完整实报。"""
        return (
            self.unreported_calls == 0
            and self.input_tokens is not None
            and self.cached_input_tokens is not None
            and self.output_tokens is not None
        )

    def __post_init__(self) -> None:
        """约束总数、明细子集和无实报调用时的空累计。"""
        for value in (self.total_tokens, self.reported_calls, self.unreported_calls):
            _required_count(value)
        details = (
            self.input_tokens, self.cached_input_tokens, self.cache_write_input_tokens,
            self.output_tokens, self.reasoning_output_tokens,
        )
        for value in details:
            _nullable_count(value)
        cached = self.cached_input_tokens
        written = self.cache_write_input_tokens
        # 未知明细仅在一致性校验中提供零下界，不改写实报字段。
        minimum_input = max(self.input_tokens or 0, (cached or 0) + (written or 0))
        minimum_output = max(self.output_tokens or 0, self.reasoning_output_tokens or 0)
        if minimum_input + minimum_output > self.total_tokens:
            raise ValueError("session usage details exceed total_tokens")
        if self.reported_calls == 0 and (
            self.total_tokens != 0 or any(value not in (None, 0) for value in details)
        ):
            raise ValueError("session usage without reported calls must have no consumption")
        if self.input_tokens is not None:
            if (cached or 0) + (written or 0) > self.input_tokens:
                raise ValueError("session usage cache counts exceed input_tokens")
            if self.output_tokens is not None and (
                self.input_tokens + self.output_tokens != self.total_tokens
            ):
                raise ValueError("session usage total must equal input plus output")
        if self.output_tokens is not None and self.reasoning_output_tokens is not None:
            if self.reasoning_output_tokens > self.output_tokens:
                raise ValueError("session usage reasoning exceeds output_tokens")


@dataclass(frozen=True, slots=True)
class ContextUsageSnapshot:
    """保存同一次提交的窗口、最近占用、累计实报及模型归属。"""

    model_context_window: int | None
    last_token_usage: ContextTokenUsage | None
    total_token_usage: SessionTokenUsage | None
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
    total = _parse_session_token_usage(payload["total_token_usage"])
    source = payload["usage_source"]
    if source not in ("provider", "estimate", "unknown"):
        raise ValueError("context usage source is invalid")
    if (source == "unknown") != (last is None):
        raise ValueError("context usage source must match last_token_usage availability")
    model = payload["model"]
    route = payload["route"]
    if not isinstance(model, str) or not model.strip():
        raise ValueError("context usage model is required")
    if not isinstance(route, str) or route not in ("responses", "chat_completions", "messages"):
        raise ValueError("context usage route is invalid")
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


def _required_count(value: JsonValue) -> int:
    """校验非负严格整数，拒绝布尔值和隐式转换。"""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("session usage count must be a non-negative integer")
    return value


def _nullable_count(value: JsonValue) -> int | None:
    """保留未知明细并校验已报告计数。"""
    return None if value is None else _required_count(value)


def _parse_session_token_usage(value: JsonValue) -> SessionTokenUsage | None:
    """解析完整累计对象；字段缺失和未声明字段均属于协议错误。"""
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != SESSION_TOKEN_USAGE_FIELDS:
        raise ValueError("session usage requires exactly the declared counters or null")
    return SessionTokenUsage(
        total_tokens=_required_count(value["total_tokens"]),
        input_tokens=_nullable_count(value["input_tokens"]),
        cached_input_tokens=_nullable_count(value["cached_input_tokens"]),
        cache_write_input_tokens=_nullable_count(value["cache_write_input_tokens"]),
        output_tokens=_nullable_count(value["output_tokens"]),
        reasoning_output_tokens=_nullable_count(value["reasoning_output_tokens"]),
        reported_calls=_required_count(value["reported_calls"]),
        unreported_calls=_required_count(value["unreported_calls"]),
    )


if __name__ == '__main__':
    pass
