# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass


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
    total_tokens: int | None
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
        for value in (self.last_total_tokens, self.total_tokens):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise ValueError("context token counts must be non-negative integers or null")
        if self.usage_source not in ("provider", "estimate", "unknown"):
            raise ValueError("context usage source is invalid")
        if (self.usage_source == "unknown") != (self.last_total_tokens is None):
            raise ValueError("context usage source does not match the last count")


if __name__ == '__main__':
    pass
