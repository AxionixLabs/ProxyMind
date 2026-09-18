# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SessionTokenUsageRecord:
    """保留服务端累计实报及缺报事实；传输与缓存边界共同校验，不在本地累加。"""

    total_tokens: int
    input_tokens: int | None
    cached_input_tokens: int | None
    cache_write_input_tokens: int | None
    output_tokens: int | None
    reasoning_output_tokens: int | None
    reported_calls: int
    unreported_calls: int

    def __post_init__(self) -> None:
        """拒绝非法计数及不满足父子计数关系的持久化事实。"""
        for value in (self.total_tokens, self.reported_calls, self.unreported_calls):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("usage totals and call counts must be non-negative integers")
        for value in (
            self.input_tokens, self.cached_input_tokens, self.cache_write_input_tokens,
            self.output_tokens, self.reasoning_output_tokens,
        ):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise ValueError("usage details must be non-negative integers or null")
        cached = (self.cached_input_tokens or 0) + (self.cache_write_input_tokens or 0)
        input_floor = max(self.input_tokens or 0, cached)
        output_floor = max(self.output_tokens or 0, self.reasoning_output_tokens or 0)
        if input_floor + output_floor > self.total_tokens:
            raise ValueError("usage details exceed total")
        if self.input_tokens is not None and cached > self.input_tokens:
            raise ValueError("cached tokens exceed input")
        if self.output_tokens is not None and (self.reasoning_output_tokens or 0) > self.output_tokens:
            raise ValueError("reasoning tokens exceed output")
        if self.input_tokens is not None and self.output_tokens is not None:
            if self.input_tokens + self.output_tokens != self.total_tokens:
                raise ValueError("input and output must sum to total")
        if self.reported_calls == 0 and self.total_tokens != 0:
            raise ValueError("unreported usage cannot contain a total")

    @property
    def is_complete(self) -> bool:
        """判断核心展示计数是否完整；额外缓存写入及推理明细可以未知。"""
        return (
            self.unreported_calls == 0
            and self.input_tokens is not None
            and self.cached_input_tokens is not None
            and self.output_tokens is not None
        )


@dataclass(frozen=True, slots=True)
class ContextUsageRecord:
    """保存已校验的上下文事实；adapter 映射线上身份，应用层只替换完整记录。

    最近占用和累计用量分别保存；窗口、模型及来源属于同一服务端提交。
    本地缓存不得累加、估算或修改这些值，也不能推进传输确认水位。
    """

    cid: str
    sid: str
    turn_id: str
    event_seq: int
    presentation_epoch: int
    model_context_window: int | None
    last_total_tokens: int | None
    total_token_usage: SessionTokenUsageRecord | None
    usage_source: typing.Literal["provider", "estimate", "unknown"]
    model: str
    route: str

    def __post_init__(self) -> None:
        """校验本地持久化与传输映射共用的不变量。"""
        for value in (self.cid, self.sid, self.model, self.route):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("context usage identity and model are required")
        if not isinstance(self.turn_id, str) or self.turn_id != self.turn_id.strip():
            raise ValueError("context usage turn_id must be a string")
        for value in (self.event_seq, self.presentation_epoch):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError("context usage coordinates must be positive integers")
        window = self.model_context_window
        if window is not None and (
            isinstance(window, bool) or not isinstance(window, int) or window <= 1
        ):
            raise ValueError("context window must be greater than one or null")
        last_total = self.last_total_tokens
        if last_total is not None and (
            isinstance(last_total, bool) or not isinstance(last_total, int) or last_total < 0
        ):
            raise ValueError("context token counts must be non-negative integers or null")
        if self.total_token_usage is not None and not isinstance(self.total_token_usage, SessionTokenUsageRecord):
            raise ValueError("cumulative usage must be a validated record or null")
        if self.usage_source not in ("provider", "estimate", "unknown"):
            raise ValueError("context usage source is invalid")
        if (self.usage_source == "unknown") != (self.last_total_tokens is None):
            raise ValueError("context usage source does not match the last count")


if __name__ == '__main__':
    pass
